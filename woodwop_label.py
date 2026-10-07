"""Add a part-size label to woodWOP .mpr part programs without touching any machining.

The label goes in three places, none of which the machine ever runs:

1. **Drawn on the part** as contour lines spelling the size (and job / part
   under it). A woodWOP contour is only geometry; the machine cuts a contour
   only when a routing operation points at it, and nothing ever points at
   these. Each character is one open line, so it can never look like a cutout.
2. A ``LABEL`` entry in the part's variable list (``[001``) with the label text
   as its comment. Its value is the number of drawn label contours, which is
   how the drawing is found again for updating or removing it.
3. A woodWOP comment (``<101 \\Kommentar\\``) right after the workpiece block,
   shown in woodWOP's list of operations.

Every other byte of the file stays exactly as it was. Before anything is
saved, the new file is checked line by line against the original: only label
lines may be added, no operation may be added or changed, and no operation may
point at a label contour. If any check fails, nothing is written.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import stroke_font

# latin-1 maps every byte to one character and back, so decoding and
# re-encoding never alters a single byte of the original file.
ENCODING = "latin-1"
MM_PER_INCH = 25.4
FRACTION_DENOMINATOR = 16  # round inch sizes to the nearest 1/16"

LABEL_VAR_RE = re.compile(r'^LABEL="(\d+)"$')  # value = number of drawn label contours
COMMENT_HEADER = "<101 \\Kommentar\\"
LABEL_KM_RE = re.compile(r'^KM="SIZE [^"]* \| JOB [^"]*"$')
SECTION_START = ("[", "]", "<", "$", "!")
MPR_SUFFIXES = (".mpr",)

CONTOUR_RE = re.compile(r"^\](\d+)$")
LABEL_COORD_RE = re.compile(r"^-?\d+\.\d{4}$")  # drawn label points: absolute mm, exactly 4 decimals
CONTOUR_REF_RE = re.compile(r'^([A-Za-z_]+)="(\d+):(\d+)"$')  # e.g. EA="1:0" = contour 1, element 0

# Result statuses
LABELED = "labeled"  # label added or updated
ALREADY = "already"  # file already carries the correct label
REMOVED = "removed"  # label taken out
NO_LABEL = "no-label"  # nothing to remove
SHEET = "sheet"  # nested sheet (board) file, checked, left alone
PROBLEM = "problem"  # could not be labeled safely (or a sheet failed its check), left alone

Point = tuple[float, float]


class LabelError(Exception):
    """A file that cannot be labeled safely."""


@dataclass
class PartInfo:
    length_mm: float
    width_mm: float
    thickness_mm: float
    job: str | None
    part: str

    @property
    def size_text(self) -> str:
        return " x ".join(inches_text(v) for v in (self.length_mm, self.width_mm, self.thickness_mm))

    @property
    def label(self) -> str:
        return f"SIZE {self.size_text} IN | JOB {self.job or 'UNKNOWN'} | PART {self.part}"

    @property
    def drawing(self) -> list[list[Point]]:
        """The label as polylines on the part (mm), one per character. [] if the part is too small."""
        second = f"JOB {self.job} {self.part}" if self.job else self.part
        return stroke_font.layout(self.length_mm, self.width_mm, self.size_text, second)


@dataclass
class Result:
    status: str
    message: str
    info: PartInfo | None = None
    new_data: bytes | None = None  # set only when the file should be rewritten
    old_data: bytes | None = None  # the bytes new_data was built from
    drawing: list[list[Point]] = field(default_factory=list)


# ---------------------------------------------------------------- formatting


def inches_text(mm: float) -> str:
    """2419.35 -> '95-1/4', 304.8 -> '12', 19.5 -> '3/4' (nearest 1/16")."""
    steps = int(math.floor(mm / MM_PER_INCH * FRACTION_DENOMINATOR + 0.5))
    whole, rem = divmod(steps, FRACTION_DENOMINATOR)
    if rem == 0:
        return str(whole)
    g = math.gcd(rem, FRACTION_DENOMINATOR)
    frac = f"{rem // g}/{FRACTION_DENOMINATOR // g}"
    return f"{whole}-{frac}" if whole else frac


def clean_text(s: str) -> str:
    """Keep label text to plain printable ASCII with no double quotes."""
    return re.sub(r"[^\x20-\x7E]", "?", s).replace('"', "'")


def parse_file_name(stem: str) -> tuple[str | None, str]:
    """Job number and part name from a file name.

    WHE_TABBY_1071128_ROSENBAUM_SHARI_L_CEP_U12_X1 -> ('1071128', 'CEP U12 X1')

    The job number is the first run of 5+ digits. The part name starts at the
    token just before the unit (U12) and runs to the end. With no unit token
    the part name is everything after the job number.
    """
    tokens = [t for t in re.split(r"[_\s]+", stem) if t]
    job_i = next((i for i, t in enumerate(tokens) if re.fullmatch(r"\d{5,}", t)), None)
    if job_i is None:
        return None, clean_text(" ".join(tokens)) or "?"
    rest = tokens[job_i + 1 :]
    unit_i = next(
        (i for i in range(len(rest) - 1, -1, -1) if re.fullmatch(r"U\d+[A-Z]?", rest[i], re.IGNORECASE)),
        None,
    )
    part = rest[unit_i - 1 :] if unit_i else rest
    return tokens[job_i], clean_text(" ".join(part)) or "?"


# ---------------------------------------------------------------- file structure


def split_lines(data: bytes) -> tuple[list[str], str]:
    text = data.decode(ENCODING)
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.split(nl), nl


def join_lines(lines: list[str], nl: str) -> bytes:
    return nl.join(lines).encode(ENCODING)


def block_end(lines: list[str], start: int) -> int:
    """Index of the first line after ``start`` that is blank or opens a new section."""
    j = start + 1
    while j < len(lines) and lines[j] != "" and not lines[j].startswith(SECTION_START):
        j += 1
    return j


def find_line(lines: list[str], text: str) -> int | None:
    return next((i for i, line in enumerate(lines) if line == text), None)


def block_values(lines: list[str], header: str) -> dict[str, str]:
    """``name="value"`` (or ``name=value``) entries of the block that starts with ``header``."""
    i = find_line(lines, header)
    if i is None:
        return {}
    values = {}
    for line in lines[i + 1 : block_end(lines, i)]:
        m = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)="?(.*?)"?', line)
        if m and m.group(1) != "KM":
            values.setdefault(m.group(1), m.group(2))
    return values


def is_sheet(lines: list[str]) -> bool:
    """woodNest sheet files carry placement coordinate systems and per-part blank sizes."""
    return any(line.startswith("<00 \\Koordinatensystem\\") or re.match(r"_BSX_\d", line) for line in lines)


def to_mm(value: str | None, variables: dict[str, str]) -> float | None:
    """A number, or a variable name whose value is a number. Anything else -> None."""
    if value is None:
        return None
    value = variables.get(value.strip(), value).strip()
    try:
        return float(value)
    except ValueError:
        return None


def read_size(lines: list[str]) -> tuple[float, float, float]:
    """Rough exterior size (L, W, T in mm) from the woodWOP header.

    Cross-checked against the workpiece block when its sizes resolve to plain
    numbers, so a stale header can't produce a wrong label.
    """
    header = block_values(lines, "[H")
    size = [to_mm(header.get(k), {}) for k in ("_BSX", "_BSY", "_BSZ")]
    if None in size:
        raise LabelError("part size not found in the file header")
    variables = block_values(lines, "[001")
    workpiece = block_values(lines, "<100 \\WerkStck\\")
    for name, key, header_mm in zip(("length", "width", "thickness"), ("LA", "BR", "DI"), size):
        piece_mm = to_mm(workpiece.get(key), variables)
        if piece_mm is not None and abs(piece_mm - header_mm) > 0.01:
            raise LabelError(
                f"part {name} in the header ({header_mm:g} mm) doesn't match the workpiece "
                f"({piece_mm:g} mm); open and re-save it in woodWOP first"
            )
    return size[0], size[1], size[2]


# ---------------------------------------------------------------- contours and routing


@dataclass
class Contour:
    number: int
    start: int  # index of the "]n" line
    end: int  # index just past the contour's last line
    elements: list[tuple[str, dict[str, str]]]  # (type e.g. "KP"/"KL"/"KA", values)


def contours(lines: list[str]) -> list[Contour]:
    """Every contour block (``]n`` ... up to the next contour or operation)."""
    out = []
    i = 0
    while i < len(lines):
        m = CONTOUR_RE.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not CONTOUR_RE.match(lines[j]) and not lines[j].startswith(("<", "!", "[")):
            j += 1
        elements: list[tuple[str, dict[str, str]]] = []
        expect_type = False
        for line in lines[i + 1 : j]:
            if re.fullmatch(r"\$E\d+", line):
                elements.append(("", {}))
                expect_type = True
            elif elements and expect_type and line.strip():
                elements[-1] = (line.strip(), elements[-1][1])
                expect_type = False
            elif elements and "=" in line:
                key, value = line.split("=", 1)
                elements[-1][1].setdefault(key, value)
        out.append(Contour(int(m.group(1)), i, j, elements))
        i = j
    return out


def is_label_contour(c: Contour) -> bool:
    """Looks exactly like a drawn label character: a start point plus straight
    lines, all at absolute 4-decimal coordinates, not closed."""
    if len(c.elements) < 2 or c.elements[0][0] != "KP" or any(t != "KL" for t, _ in c.elements[1:]):
        return False
    pts = []
    for _, values in c.elements:
        x, y = values.get("X", ""), values.get("Y", "")
        if not (LABEL_COORD_RE.match(x) and LABEL_COORD_RE.match(y)):
            return False
        pts.append((x, y))
    return pts[0] != pts[-1]


def contour_refs(lines: list[str]) -> list[tuple[str, int]]:
    """(operation, contour number) for every operation value that points at a
    contour, like a routing operation's ``EA="1:0"``."""
    refs = []
    op = None
    for line in lines:
        if line.startswith("<"):
            op = line
        elif line == "" or line.startswith(("]", "$", "[", "!")):
            op = None
        elif op:
            m = CONTOUR_REF_RE.match(line)
            if m:
                refs.append((op, int(m.group(2))))
    return refs


def contour_lines(number: int, poly: list[Point]) -> list[str]:
    """One drawn label character as a woodWOP contour: a start point, then lines."""
    x0, y0 = poly[0]
    out = [f"]{number}", "$E0", "KP ", f"X={x0:.4f}", f"Y={y0:.4f}", "Z=0.0", "KO=00",
           f".X={x0:.6f}", f".Y={y0:.6f}", ".Z=0.000000", ".KO=00", ""]
    for k, ((xa, ya), (xb, yb)) in enumerate(zip(poly, poly[1:]), 1):
        if (xa, ya) == (xb, yb):
            raise LabelError("label drawing has a zero-length line")
        angle = math.atan2(yb - ya, xb - xa) % (2 * math.pi)
        out += [f"$E{k}", "KL ", f"X={xb:.4f}", f"Y={yb:.4f}",
                f".X={xb:.6f}", f".Y={yb:.6f}", ".Z=0.000000", f".WI={angle:.6f}", ".WZ=0.000000", ""]
    return out


@dataclass
class SheetCheck:
    labeled_parts: int
    label_contours: int
    dangers: list[str]

    @property
    def message(self) -> str:
        if self.dangers:
            return "DANGER - DO NOT RUN: " + "; ".join(self.dangers)
        if not self.labeled_parts:
            return "sheet file, no labeled parts"
        return (f"sheet OK: {self.labeled_parts} labeled part(s), {self.label_contours} label lines, "
                f"no router on any label")


def check_sheet(lines: list[str]) -> SheetCheck:
    """Make sure no operation on a nested sheet points at a drawn label."""
    cs = contours(lines)
    numbers = {c.number for c in cs}
    labels = {c.number for c in cs if is_label_contour(c)}
    dangers = []
    for op, n in contour_refs(lines):
        if n in labels:
            dangers.append(f"{op.strip()} is set to cut label contour {n}")
        elif n not in numbers:
            dangers.append(f"{op.strip()} points at contour {n}, which is missing")
    labeled = sum(1 for x in lines if re.match(r'^LABEL(_\d+)?="\d+"$', x))
    return SheetCheck(labeled, len(labels), dangers)


# ---------------------------------------------------------------- label in / out


def strip_label(lines: list[str]) -> list[str]:
    """The file's lines with any label this tool added taken out."""
    var_at = None
    drawn = 0
    for i, line in enumerate(lines):
        m = LABEL_VAR_RE.match(line)
        if m:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not LABEL_KM_RE.match(nxt):
                raise LabelError("file already has its own LABEL variable")
            if var_at is not None:
                raise LabelError("file has more than one LABEL entry")
            var_at, drawn = i, int(m.group(1))

    remove: set[int] = set()
    if var_at is not None:
        remove |= {var_at, var_at + 1}
        # a [001 block that holds nothing but our label goes too
        if var_at >= 1 and lines[var_at - 1] == "[001" and block_end(lines, var_at - 1) == var_at + 2:
            remove.add(var_at - 1)
            if var_at + 2 < len(lines) and lines[var_at + 2] == "":
                remove.add(var_at + 2)
    if drawn:
        cs = contours(lines)
        ours = cs[-drawn:] if len(cs) >= drawn else []
        if len(ours) != drawn or not all(is_label_contour(c) for c in ours):
            raise LabelError("the drawn label doesn't look like one this tool made; left alone")
        routed = {n for _, n in contour_refs(lines)}
        if any(c.number in routed for c in ours):
            raise LabelError("DANGER: an operation is set to cut the drawn label; left alone")
        for c in ours:
            remove |= set(range(c.start, c.end))
    lines = [x for i, x in enumerate(lines) if i not in remove]

    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line == COMMENT_HEADER:
            end = block_end(lines, i)
            kms = [x for x in lines[i + 1 : end] if x.startswith("KM=")]
            if len(kms) == 1 and LABEL_KM_RE.match(kms[0]):
                i = end + 1 if end < len(lines) and lines[end] == "" else end
                continue
        out.append(line)
        i += 1
    return out


def label_lines(label: str, drawing: list[list[Point]], first_contour: int) -> list[str]:
    """Every line the label adds, for checking what was inserted."""
    km = f'KM="{label}"'
    out = [f'LABEL="{len(drawing)}"', km, COMMENT_HEADER, km, ""]
    for k, poly in enumerate(drawing):
        out += contour_lines(first_contour + k, poly)
    return out


def insert_label(lines: list[str], label: str, drawing: list[list[Point]]) -> list[str]:
    km = f'KM="{label}"'
    out = list(lines)

    # Drawn label: new contours numbered after the existing ones, placed right
    # after the last contour (before the first operation).
    if drawing:
        cs = contours(out)
        first = max((c.number for c in cs), default=0) + 1
        if cs:
            at = cs[-1].end
        else:
            at = next((i for i, x in enumerate(out) if x.startswith("<") or x == "!"), None)
            if at is None:
                raise LabelError("no place found for the drawn label")
        block = []
        for k, poly in enumerate(drawing):
            block += contour_lines(first + k, poly)
        out[at:at] = block

    # Comment component right after the workpiece block.
    w = find_line(out, "<100 \\WerkStck\\")
    if w is not None:
        e = block_end(out, w)
        at = e + 1 if e < len(out) and out[e] == "" else e
    else:
        at = next((i for i, x in enumerate(out) if x.startswith("<") or x == "!"), None)
        if at is None:
            raise LabelError("no place found for the comment")
    out[at:at] = [COMMENT_HEADER, km, ""]

    # LABEL variable at the end of the variable list.
    var = [f'LABEL="{len(drawing)}"', km]
    v = find_line(out, "[001")
    if v is not None:
        e = block_end(out, v)
        out[e:e] = var
    else:
        h = find_line(out, "[H")
        if h is None:
            raise LabelError("not a woodWOP file (no [H header)")
        e = block_end(out, h)
        while e < len(out) and out[e] == "":
            e += 1
        out[e:e] = ["[001", *var, ""]
    return out


def label_bytes(data: bytes, file_name: str) -> Result:
    """Work out the labeled version of one part file. Nothing is written here."""
    try:
        lines, nl = split_lines(data)
        if find_line(lines, "[H") is None:
            raise LabelError("not a woodWOP file (no [H header)")
        if is_sheet(lines):
            check = check_sheet(lines)
            return Result(PROBLEM if check.dangers else SHEET, check.message)
        length, width, thick = read_size(lines)
        job, part = parse_file_name(Path(file_name).stem)
        info = PartInfo(length, width, thick, job, part)
        drawing = info.drawing

        base = strip_label(lines)
        if any(re.match(r"label\s*=", x, re.IGNORECASE) for x in base):
            raise LabelError("file already has its own LABEL variable")
        new_lines = insert_label(base, info.label, drawing)
        if new_lines == lines:
            return Result(ALREADY, "already labeled", info, drawing=drawing)

        new_data = join_lines(new_lines, nl)
        verify(data, new_data, info.label, drawing)
        status = "label added" if base == lines else "label updated"
        if not drawing:
            status += " (part too small to draw on)"
        return Result(LABELED, status, info, new_data, data, drawing)
    except LabelError as e:
        return Result(PROBLEM, str(e))


def unlabel_bytes(data: bytes) -> Result:
    try:
        lines, nl = split_lines(data)
        base = strip_label(lines)
        if base == lines:
            return Result(NO_LABEL, "no label to remove")
        return Result(REMOVED, "label removed", new_data=join_lines(base, nl), old_data=data)
    except LabelError as e:
        return Result(PROBLEM, str(e))


def extra_lines(sub: list[str], full: list[str]) -> list[str] | None:
    """Lines of ``full`` left over once ``sub`` is matched in order; None if ``sub`` isn't all there."""
    extra, i = [], 0
    for line in full:
        if i < len(sub) and line == sub[i]:
            i += 1
        else:
            extra.append(line)
    return extra if i == len(sub) else None


def is_label_line(line: str) -> bool:
    """Lines an old label of ours can contain (including fields woodWOP may add to
    its comment block on re-save, and drawn-contour lines)."""
    return (
        line in (COMMENT_HEADER, "", "[001", "KP ", "KL ", "Z=0.0", "KO=00", ".KO=00")
        or bool(LABEL_VAR_RE.match(line) or LABEL_KM_RE.match(line))
        or bool(re.fullmatch(r'[A-Z_]+="[^"]*"', line) and not line.startswith("KM="))
        or bool(re.fullmatch(r"\]\d+|\$E\d+|\.?[A-Z]+=-?\d+\.\d+", line))
    )


def verify(old: bytes, new: bytes, label: str, drawing: list[list[Point]]) -> None:
    """The new file must be the old file plus exactly one label, nothing else.

    Checked directly against the original lines, independent of strip_label:
    every original line must still be there in order (except an old label of
    ours), the only added lines must be the label lines, and no operation may
    be added, changed, or pointed at a label contour.
    """
    old_lines, _ = split_lines(old)
    new_lines, _ = split_lines(new)
    kept = strip_label(old_lines)

    removed = extra_lines(kept, old_lines)
    if removed is None or not all(is_label_line(x) for x in removed):
        raise LabelError("safety check failed: something other than an old label would be removed")

    first = max((c.number for c in contours(kept)), default=0) + 1
    expected = label_lines(label, drawing, first)
    if find_line(kept, "[001") is None:
        expected += ["[001", ""]
    added = extra_lines(kept, new_lines)
    if added is None or sorted(added) != sorted(expected):
        raise LabelError("safety check failed: something other than the label would change")

    # Router safety: the operations and what they cut must be exactly as before.
    ops_now = sorted(x for x in new_lines if x.startswith("<"))
    if ops_now != sorted([x for x in kept if x.startswith("<")] + [COMMENT_HEADER]):
        raise LabelError("safety check failed: operations would change")
    if contour_refs(new_lines) != contour_refs(kept):
        raise LabelError("safety check failed: what an operation cuts would change")
    drawn_numbers = set(range(first, first + len(drawing)))
    if any(n in drawn_numbers for _, n in contour_refs(new_lines)):
        raise LabelError("safety check failed: an operation would cut the drawn label")
    new_contours = contours(new_lines)
    if sorted(c.number for c in new_contours if is_label_contour(c) and c.number >= first) != sorted(drawn_numbers):
        raise LabelError("safety check failed: drawn label contours not as expected")

    if read_size(new_lines) != read_size(old_lines):
        raise LabelError("safety check failed: part size would change")


# ---------------------------------------------------------------- files on disk


def find_mpr_files(paths: list[Path]) -> list[Path]:
    """The .mpr files named, plus the .mpr files directly inside any folders named."""
    found: list[Path] = []
    for p in paths:
        if p.is_dir():
            found.extend(sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in MPR_SUFFIXES))
        elif p.is_file() and p.suffix.lower() in MPR_SUFFIXES:
            found.append(p)
    unique: dict[Path, Path] = {}
    for f in found:
        unique.setdefault(f.resolve(), f)
    return list(unique.values())


def default_backup_root() -> Path:
    return Path.home() / "Documents" / "WoodWOP Label Backups"


def new_backup_dir(root: Path | None = None) -> Path:
    return (root or default_backup_root()) / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def plan(path: Path, remove: bool = False) -> Result:
    data = path.read_bytes()
    return unlabel_bytes(data) if remove else label_bytes(data, path.name)


def save(path: Path, result: Result, backup_dir: Path) -> None:
    """Back up the original, then replace the file in its own folder.

    Refuses if the file changed since it was read for ``result``.
    """
    assert result.new_data is not None and result.old_data is not None
    original = path.read_bytes()
    if original != result.old_data:
        raise LabelError("file changed since it was checked; check it again")

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / path.name
    n = 1
    while backup.exists():
        backup = backup_dir / f"{path.stem} ({n}){path.suffix}"
        n += 1
    backup.write_bytes(original)
    if backup.read_bytes() != original:
        raise LabelError("backup copy could not be verified; file left alone")

    tmp = path.with_name(path.name + ".labeling-tmp")
    try:
        tmp.write_bytes(result.new_data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    if path.read_bytes() != result.new_data:
        raise LabelError(f"file did not save correctly; the original is in {backup}")


# ---------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Add part-size labels to woodWOP .mpr part files, saved in place.")
    ap.add_argument("paths", nargs="+", type=Path, help=".mpr files or folders")
    ap.add_argument("--remove", action="store_true", help="take labels out instead of adding them")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--backup-dir", type=Path, help="where originals are copied before saving")
    args = ap.parse_args(argv)

    files = find_mpr_files(args.paths)
    if not files:
        print("No .mpr files found.")
        return 1
    backup_dir = new_backup_dir(args.backup_dir)
    failed = 0
    for f in files:
        try:
            r = plan(f, args.remove)
            if r.new_data is not None and not args.dry_run:
                save(f, r, backup_dir)
        except (OSError, LabelError) as e:
            r = Result(PROBLEM, str(e))
        failed += r.status == PROBLEM
        label = f"  [{r.info.label}]" if r.info else ""
        print(f"{r.status:9} {f.name}: {r.message}{label}")
    if not args.dry_run and backup_dir.exists():
        print(f"Originals backed up to {backup_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
