"""Add a part-size label to woodWOP .mpr part programs without touching any machining.

The label is plain text, written in two places that the machine never runs:

1. A ``LABEL`` entry in the part's variable list (``[001``) with the label as its
   comment (``KM="..."``). Nothing in the program refers to ``LABEL``. woodNest
   copies every part's variables and their comments onto the sheet, so the label
   rides along with the part.
2. A woodWOP comment component (``<101 \\Kommentar\\``) right after the
   workpiece block, which shows up in woodWOP's list of operations.

Every other byte of the file stays exactly as it was. After building the new
file we strip the label back out and require the result to match the original
byte for byte; if it does not, nothing is written.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# latin-1 maps every byte to one character and back, so decoding and
# re-encoding never alters a single byte of the original file.
ENCODING = "latin-1"
MM_PER_INCH = 25.4
FRACTION_DENOMINATOR = 16  # round inch sizes to the nearest 1/16"

LABEL_VAR_LINE = 'LABEL="0"'
COMMENT_HEADER = "<101 \\Kommentar\\"
LABEL_KM_RE = re.compile(r'^KM="SIZE [^"]* \| JOB [^"]*"$')
SECTION_START = ("[", "]", "<", "$", "!")
MPR_SUFFIXES = (".mpr",)

# Result statuses
LABELED = "labeled"  # label added or updated
ALREADY = "already"  # file already carries the correct label
REMOVED = "removed"  # label taken out
NO_LABEL = "no-label"  # nothing to remove
SHEET = "sheet"  # nested sheet (board) file, left alone
PROBLEM = "problem"  # could not be labeled safely, left alone


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


@dataclass
class Result:
    status: str
    message: str
    info: PartInfo | None = None
    new_data: bytes | None = None  # set only when the file should be rewritten
    old_data: bytes | None = None  # the bytes new_data was built from


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


# ---------------------------------------------------------------- label in / out


def strip_label(lines: list[str]) -> list[str]:
    """The file's lines with any label this tool added taken out."""
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line == "[001" and nxt == LABEL_VAR_LINE and block_end(lines, i) == i + 3:
            km = lines[i + 2]
            if LABEL_KM_RE.match(km):
                i += 4 if i + 3 < len(lines) and lines[i + 3] == "" else 3
                continue
        if line == LABEL_VAR_LINE:
            if not LABEL_KM_RE.match(nxt):
                raise LabelError("file already has its own LABEL variable")
            i += 2
            continue
        if line == COMMENT_HEADER:
            end = block_end(lines, i)
            kms = [x for x in lines[i + 1 : end] if x.startswith("KM=")]
            if len(kms) == 1 and LABEL_KM_RE.match(kms[0]):
                i = end + 1 if end < len(lines) and lines[end] == "" else end
                continue
        out.append(line)
        i += 1
    return out


def insert_label(lines: list[str], label: str) -> list[str]:
    km = f'KM="{label}"'
    out = list(lines)

    # Comment component right after the workpiece block (inserted first so the
    # variable-block index found below is unaffected either way).
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
    v = find_line(out, "[001")
    if v is not None:
        e = block_end(out, v)
        out[e:e] = [LABEL_VAR_LINE, km]
    else:
        h = find_line(out, "[H")
        if h is None:
            raise LabelError("not a woodWOP file (no [H header)")
        e = block_end(out, h)
        while e < len(out) and out[e] == "":
            e += 1
        out[e:e] = ["[001", LABEL_VAR_LINE, km, ""]
    return out


def label_bytes(data: bytes, file_name: str) -> Result:
    """Work out the labeled version of one part file. Nothing is written here."""
    try:
        lines, nl = split_lines(data)
        if find_line(lines, "[H") is None:
            raise LabelError("not a woodWOP file (no [H header)")
        if is_sheet(lines):
            return Result(SHEET, "sheet file, left alone")
        length, width, thick = read_size(lines)
        job, part = parse_file_name(Path(file_name).stem)
        info = PartInfo(length, width, thick, job, part)

        base = strip_label(lines)
        if any(re.match(r"label\s*=", x, re.IGNORECASE) for x in base):
            raise LabelError("file already has its own LABEL variable")
        new_lines = insert_label(base, info.label)
        if new_lines == lines:
            return Result(ALREADY, "already labeled", info)

        new_data = join_lines(new_lines, nl)
        verify(data, new_data, info.label)
        status = "label added" if base == lines else "label updated"
        return Result(LABELED, status, info, new_data, data)
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
    """Lines this tool writes, plus fields woodWOP may add to its comment block on re-save."""
    return (
        line in (LABEL_VAR_LINE, COMMENT_HEADER, "", "[001")
        or bool(LABEL_KM_RE.match(line))
        or bool(re.fullmatch(r'[A-Z_]+="[^"]*"', line) and not line.startswith("KM="))
    )


def verify(old: bytes, new: bytes, label: str) -> None:
    """The new file must be the old file plus exactly one label, nothing else.

    Checked directly against the original lines, independent of strip_label:
    every original line must still be there in order (except an old label of
    ours), and the only added lines must be the label lines.
    """
    old_lines, _ = split_lines(old)
    new_lines, _ = split_lines(new)
    kept = strip_label(old_lines)

    removed = extra_lines(kept, old_lines)
    if removed is None or not all(is_label_line(x) for x in removed):
        raise LabelError("safety check failed: something other than an old label would be removed")

    km = f'KM="{label}"'
    expected = sorted([LABEL_VAR_LINE, km, COMMENT_HEADER, km, ""])
    if find_line(kept, "[001") is None:
        expected = sorted(expected + ["[001", ""])
    added = extra_lines(kept, new_lines)
    if added is None or sorted(added) != expected:
        raise LabelError("safety check failed: something other than the label would change")

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
