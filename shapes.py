"""Find the real pieces a part file cuts, and lay out their labels.

A part file's routing operations cut along outline contours. Each closed
outline a router cuts all the way through is one finished piece: one rectangle
for a plain part, two rectangles for a double shelf, an L or angled outline for
special shelves. A through-cut outline whose middle sits on another piece is a
cutout (or edge notch) in that piece, unless it sits in one of that piece's
cutouts, in which case it's a piece of its own. Closed outlines only routed
part way down (pockets, shallow grooves) aren't pieces; labels stay clear of
them. The outlines are written with the file's variables
(``X=@lw``, ``Y=@-rw+ld-eb``), so they're worked out here with a small
arithmetic evaluator. If anything can't be worked out, the caller labels the
plain blank size instead of guessing.

Labels (sizes are depth x width = the piece's Y size x its X size):

- Rectangle: its size, with the unit under it, in its center.
- Any other shape: every straight side labeled with its length, just inside
  that side, plus the overall size (and unit) in the middle of the piece.
- Every cutout: its size (or diameter) with CUTOUT under it, inside the cutout,
  or next to it if it doesn't fit. Labels keep off grooves (the line a shallow
  route follows) and pockets.
  Curved sides get no number. A curve with a radius and a turn direction is
  followed exactly. Any other curve (no radius or direction, or one going more
  than half way round) isn't measured: labels keep clear of the whole area it
  could bow into, a single piece with one gets the blank's overall size, and
  several such pieces fall back to the blank.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import stroke_font

Point = tuple[float, float]

MIN_H = 3.0  # smallest text drawn, mm
MAX_MAIN_H = 50.0
MAX_SIDE_H = 25.0


# ---------------------------------------------------------------- arithmetic


class EvalError(Exception):
    """An outline or size that can't be worked out."""


_TOKEN = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)|([A-Za-z_][A-Za-z0-9_]*)|(\S))")


def evaluate(expr: str, variables: dict[str, str], _depth: int = 0) -> float:
    """``+ - * /``, parentheses, numbers and variables (which may refer to other
    variables). Anything else (IF, functions, ...) raises EvalError."""
    if _depth > 25:
        raise EvalError("variables refer to each other in a loop")
    tokens = []
    for num, name, other in _TOKEN.findall(expr):
        tokens.append(("n", float(num)) if num else ("v", name) if name else ("o", other))
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else ("end", None)

    def take():
        nonlocal pos
        tok = peek()
        pos += 1
        return tok

    def factor() -> float:
        kind, val = take()
        if kind == "o" and val in ("+", "-"):
            f = factor()
            return f if val == "+" else -f
        if kind == "n":
            return val
        if kind == "v":  # exact name: woodWOP treats L and l as different variables
            if val in variables:
                return evaluate(variables[val], variables, _depth + 1)
            raise EvalError(f"unknown name {val}")
        if kind == "o" and val == "(":
            v = expression()
            if take() != ("o", ")"):
                raise EvalError("missing )")
            return v
        raise EvalError(f"can't read {expr!r}")

    def term() -> float:
        v = factor()
        while peek() in (("o", "*"), ("o", "/")):
            op = take()[1]
            rhs = factor()
            if op == "/" and rhs == 0:
                raise EvalError("divide by zero")
            v = v * rhs if op == "*" else v / rhs
        return v

    def expression() -> float:
        v = term()
        while peek() in (("o", "+"), ("o", "-")):
            op = take()[1]
            v = v + term() if op == "+" else v - term()
        return v

    if not tokens:
        raise EvalError("empty")
    result = expression()
    if pos != len(tokens):
        raise EvalError(f"can't read {expr!r}")
    return result


# ---------------------------------------------------------------- pieces


def _key(p: Point) -> tuple[float, float]:
    return round(p[0], 3), round(p[1], 3)


@dataclass
class Piece:
    points: list[Point]  # counter-clockwise, no repeated closing point
    curved: frozenset = frozenset()  # edges (as {start, end} keys) that are arcs
    holes: list[list[Point]] = field(default_factory=list)  # routed cutouts (drawn in the preview)
    keepout: list[list[Point]] = field(default_factory=list)  # areas labels avoid: cutouts, pockets, curves
    overall: tuple[float, float] | None = None  # (depth, width) when it can't come from the outline (curves)
    cutouts: list["Cutout"] = field(default_factory=list)  # each routed cutout, for its size label
    unknown_curves: bool = False  # has curves whose shape isn't known (no radius or turn direction)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs, ys = [p[0] for p in self.points], [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def width(self) -> float:  # X size
        if self.overall:
            return self.overall[1]
        x0, _, x1, _ = self.bbox
        return x1 - x0

    @property
    def depth(self) -> float:  # Y size
        if self.overall:
            return self.overall[0]
        _, y0, _, y1 = self.bbox
        return y1 - y0

    def edges(self) -> list[tuple[Point, Point]]:
        return list(zip(self.points, self.points[1:] + self.points[:1]))

    def is_curved(self, a: Point, b: Point) -> bool:
        return frozenset((_key(a), _key(b))) in self.curved

    @property
    def is_rectangle(self) -> bool:
        return len(self.points) == 4 and not self.curved and all(
            abs(a[0] - b[0]) < 0.01 or abs(a[1] - b[1]) < 0.01 for a, b in self.edges()
        )

    @property
    def straight_sides(self) -> list[float]:
        return [math.dist(a, b) for a, b in self.edges() if not self.is_curved(a, b)]


@dataclass
class Cutout:
    """A routed cutout (or edge notch) in a piece."""
    pts: list[Point]  # chord outline (fewer than 3 points for a round cutout made of two arcs)
    curved: frozenset
    arcs: list["Arc"]
    occupied: bool = False  # another piece is cut from inside it (its label goes beside it)

    def _extent(self) -> list[Point]:
        """Its points plus the farthest its untraced curves could bow."""
        return self.pts + [q for a, b, r in self.arcs for q in arc_extremes(a, b, r)]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs, ys = [p[0] for p in self._extent()], [p[1] for p in self._extent()]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def is_round(self) -> bool:
        """A circle: all curve, as wide as it is tall. (Two untraced half circles
        count when their radius is unknown or is half the distance between them.)"""
        if len(self.pts) < 3:
            c = math.dist(self.pts[0], self.pts[-1]) if len(self.pts) == 2 else 0.0
            return bool(self.arcs) and c > 0.01 and all(r is None or abs(2 * r - c) < 1.0 for _, _, r in self.arcs)
        ring = list(zip(self.pts, self.pts[1:] + self.pts[:1]))
        if self.arcs or not all(frozenset((_key(a), _key(b))) in self.curved for a, b in ring):
            return False
        x0, y0, x1, y1 = self.bbox
        return abs((x1 - x0) - (y1 - y0)) < 1.0

    @property
    def center(self) -> Point:
        x0, y0, x1, y1 = self.bbox
        return (x0 + x1) / 2, (y0 + y1) / 2

    @property
    def diameter(self) -> float:
        x0, y0, x1, y1 = self.bbox
        return max(x1 - x0, y1 - y0)

    def size_text(self, inches) -> str:
        if self.is_round:
            return f"{inches(self.diameter)} DIA"
        x0, y0, x1, y1 = self.bbox
        return f"{inches(y1 - y0)} x {inches(x1 - x0)}"

    def region(self) -> list[Point]:
        """Where its label can go inside it: its chord outline, or for a round
        cutout the square that fits inside the circle."""
        if self.is_round:
            (cx, cy), half = self.center, self.diameter / 2 * 0.7
            return [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]
        return self.pts


def rectangle(x0: float, y0: float, x1: float, y1: float) -> Piece:
    return Piece([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


Arc = tuple[Point, Point, "float | None"]  # start, end, radius if it could be read

ARC_STEP = math.radians(10)  # curves are traced in straight steps of at most 10 degrees


def arc_points(a: Point, b: Point, radius: float | None, ccw: bool) -> list[Point] | None:
    """Points along the (shorter) arc from a to b, not including a, ending exactly
    at b, including its farthest points left, right, up and down (so a size
    taken from the points is exact). Counter-clockwise turns left: its center
    is on the left of a -> b (checked against the rounded corners of the
    light-groove files, which are DS=1). None if the radius can't span a to b."""
    c = math.dist(a, b)
    if radius is None or c < 0.01 or radius < c / 2 - 0.01:
        return None
    h = math.sqrt(max(radius * radius - (c / 2) ** 2, 0.0))
    ux, uy = (b[0] - a[0]) / c, (b[1] - a[1]) / c
    side = 1.0 if ccw else -1.0
    cx, cy = (a[0] + b[0]) / 2 - uy * h * side, (a[1] + b[1]) / 2 + ux * h * side
    start = math.atan2(a[1] - cy, a[0] - cx)
    sweep = math.atan2(b[1] - cy, b[0] - cx) - start
    sweep = sweep % (2 * math.pi) if ccw else -((-sweep) % (2 * math.pi))
    steps = max(1, math.ceil(abs(sweep) / ARC_STEP))
    ts = {i / steps for i in range(1, steps)}
    lo, hi = min(start, start + sweep), max(start, start + sweep)
    k = math.ceil(lo / (math.pi / 2))
    while k * math.pi / 2 < hi:  # every quarter-turn the arc passes through
        t = (k * math.pi / 2 - start) / sweep
        if 1e-6 < t < 1 - 1e-6:
            ts.add(t)
        k += 1
    out = [(cx + radius * math.cos(start + sweep * t), cy + radius * math.sin(start + sweep * t)) for t in sorted(ts)]
    return out + [b]


def outline(elements: list[tuple[str, dict[str, str]]],
            variables: dict[str, str]) -> tuple[list[Point], set, list[Arc]]:
    """Corner points of a contour, which edges are curved, and the curves whose
    shape isn't known. A curve with a radius (R) and a turn direction (DS: 1 =
    counter-clockwise, 0 = clockwise) is traced in short steps, so the outline
    follows it exactly. Without both, or with a negative radius (the long way
    round), it's kept as a straight chord and returned in the last list (labels
    then keep well clear of it). A corner rounding (KR with only a radius) is
    skipped so the corner stays at its nominal point."""
    pts: list[Point] = []
    curved: set = set()
    arcs: list[Arc] = []
    cur = (0.0, 0.0)

    def coord(raw: str | None, now: float) -> float:
        if raw is None or raw.strip() == "":
            return now
        raw = raw.strip()
        return now + evaluate(raw[1:], variables) if raw.startswith("@") else evaluate(raw, variables)

    for kind, values in elements:
        has_end = "X" in values or "Y" in values
        if kind == "KP":
            if pts:
                raise EvalError("second start point")
            cur = (coord(values.get("X"), cur[0]), coord(values.get("Y"), cur[1]))
            pts.append(cur)
        elif kind == "KL" or kind == "KA" or (kind == "KR" and has_end):
            if not pts:
                raise EvalError("line before start point")
            if kind != "KL" and not has_end:
                raise EvalError("arc without an end point")
            new = (coord(values.get("X"), cur[0]), coord(values.get("Y"), cur[1]))
            if kind != "KL":
                try:
                    radius = evaluate(values["R"], variables) if "R" in values else None
                except EvalError:
                    radius = None
                ds = values.get("DS", "").strip()
                long_way = radius is not None and radius < 0  # more than half way round: not traced
                traced = arc_points(cur, new, radius, ds == "1") if ds in ("0", "1") and not long_way else None
                if traced:
                    for q in traced:
                        curved.add(frozenset((_key(cur), _key(q))))
                        cur = q
                        pts.append(cur)
                    continue
                curved.add(frozenset((_key(cur), _key(new))))
                arcs.append((cur, new, radius))
            cur = new
            pts.append(cur)
        elif kind == "KR":
            continue
        else:
            raise EvalError(f"unknown contour element {kind}")
    return pts, curved, arcs


def clean(points: list[Point], curved: set) -> list[Point]:
    """Drop repeated points and straight-through corners between straight
    sides; make counter-clockwise."""
    pts: list[Point] = []
    for p in points:
        if not pts or math.dist(p, pts[-1]) > 0.01:
            pts.append(p)
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) <= 0.01:
        pts.pop()

    def is_curved(a, b):
        return frozenset((_key(a), _key(b))) in curved

    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            if is_curved(a, b) or is_curved(b, c):
                continue
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            dot = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])
            if abs(cross) < 1e-6 * max(1.0, math.dist(a, b) * math.dist(b, c)) and dot > 0:
                pts.pop(i)
                changed = True
                break
    if signed_area(pts) < 0:
        pts.reverse()
    return pts


def signed_area(pts: list[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])) / 2


GROOVE_HALF = 10.0  # labels keep this far (mm) from the line a groove or shallow route follows


def _band(a: Point, b: Point, half_width: float, end: float) -> list[Point]:
    """A rectangle along a -> b, ``half_width`` to each side, running ``end`` past both ends."""
    c = math.dist(a, b)
    ux, uy = (b[0] - a[0]) / c, (b[1] - a[1]) / c
    nx, ny = -uy * half_width, ux * half_width
    ax, ay = a[0] - ux * end, a[1] - uy * end
    bx, by = b[0] + ux * end, b[1] + uy * end
    return [(ax - nx, ay - ny), (bx - nx, by - ny), (bx + nx, by + ny), (ax + nx, ay + ny)]


def _bulge(c: float, radius: float | None) -> tuple[float, float]:
    """How far an arc over a chord of length c can bow out from the chord, and
    how far past each end of the chord it can reach. A negative radius is an
    arc going the long way round; no radius (or one too small for the chord)
    is taken as up to a half circle."""
    half = c / 2
    r = abs(radius) if radius is not None else 0.0
    if r < half:
        return half, 0.0
    h = math.sqrt(r * r - half * half)
    return (r + h, r - half) if radius < 0 else (r - h, 0.0)


def arc_band(a: Point, b: Point, radius: float | None, extra: float = 2.0) -> list[Point]:
    """The area an arc from a to b could cover, bowing either way: a band along
    its chord as wide as the arc's bulge (a half circle's if the radius is
    unknown), plus ``extra`` all round."""
    c = math.dist(a, b)
    if c < 0.01:
        return []
    bulge, past = _bulge(c, radius)
    return _band(a, b, bulge + extra, past + extra)


def path_bands(raw: list[Point], curved: set, arcs: list["Arc"], half_width: float = GROOVE_HALF) -> list[list[Point]]:
    """Bands along every line and arc of a routed path (a groove), so labels stay off the groove."""
    unknown = {frozenset((_key(a), _key(b))): r for a, b, r in arcs}
    out = []
    for a, b in zip(raw, raw[1:]):
        if math.dist(a, b) < 0.01:
            continue
        k = frozenset((_key(a), _key(b)))
        out.append(arc_band(a, b, unknown[k], half_width) if k in unknown else _band(a, b, half_width, half_width))
    return out


def arc_extremes(a: Point, b: Point, radius: float | None) -> list[Point]:
    """The farthest an untraced curve from a to b could bow, either way (a half
    circle's if the radius is unknown)."""
    c = math.dist(a, b)
    if c < 0.01:
        return []
    bulge, past = _bulge(c, radius)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    nx, ny = -(b[1] - a[1]) / c * bulge, (b[0] - a[0]) / c * bulge
    if past:  # a long-way-round arc also reaches past both ends of its chord
        ux, uy = (b[0] - a[0]) / c, (b[1] - a[1]) / c
        a_end, b_end = (a[0] - ux * past, a[1] - uy * past), (b[0] + ux * past, b[1] + uy * past)
        return [(mx + nx, my + ny), (mx - nx, my - ny), a_end, b_end]
    return [(mx + nx, my + ny), (mx - nx, my - ny)]


def interior_point(pts: list[Point]) -> Point:
    """A point in the middle of an outline (its centroid, or its corners' average)."""
    area = signed_area(pts) if len(pts) >= 3 else 0.0
    if abs(area) > 1.0:
        ring = list(zip(pts, pts[1:] + pts[:1]))
        cx = sum((a[0] + b[0]) * (a[0] * b[1] - b[0] * a[1]) for a, b in ring) / (6 * area)
        cy = sum((a[1] + b[1]) * (a[0] * b[1] - b[0] * a[1]) for a, b in ring) / (6 * area)
        if inside((cx, cy), pts):
            return cx, cy
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


class Unclear(Exception):
    """Routing that cuts pieces out, but not in a way this can follow for sure."""


def routing_depth(values: dict[str, str], variables: dict[str, str], thickness: float) -> str:
    """'through' or 'shallow' for a routing operation's depth ``ZA``.

    A leading ``@`` means measured down from the top (``@8.1`` = 8.1 mm deep).
    ``sbc`` (spoil board cut: a little into the spoil board), any other plain
    setting name, no depth, or a depth at least the part's thickness is through.
    A formula that can't be worked out is Unclear.
    """
    raw = values.get("ZA", "").strip()
    za = raw[1:].strip() if raw.startswith("@") else raw
    if not za or za.lower() == "sbc":
        return "through"
    try:
        depth = evaluate(za, variables)
    except EvalError:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", za):
            return "through"  # another machine setting name
        raise Unclear(f"routing depth {raw} can't be worked out") from None
    return "shallow" if 0 < depth < thickness - 0.1 else "through"


def cuts_through(op: str, values: dict[str, str], variables: dict[str, str], thickness: float) -> bool:
    """Contour routing (``<105``) at full depth."""
    return op.startswith("<105 ") and routing_depth(values, variables, thickness) == "through"


def _covers_whole(values: dict[str, str], contour) -> bool:
    """The routing runs the whole contour (``EA`` start element to ``EE`` end
    element; the same element for both means once all the way round)."""
    ea, ee = values.get("EA", ""), values.get("EE", "")
    ma, mb = re.fullmatch(r"(\d+):(\d+)", ea), re.fullmatch(r"(\d+):(\d+)", ee)
    if not ma or not mb:
        return True
    if int(ma.group(1)) != int(mb.group(1)):
        return False
    a, b, last = int(ma.group(2)), int(mb.group(2)), len(contour.elements) - 1
    if a == b:
        return a == 0  # once all the way round from the start point
    covered = set(range(a, b + 1)) if a < b else set(range(a, last + 1)) | set(range(0, b + 1))
    return set(range(1, last + 1)) <= covered


@dataclass
class _Shape:
    pts: list[Point]  # chord outline (may be fewer than 3 points for an all-curve shape)
    curved: frozenset
    bands: list[list[Point]]  # areas its arcs could cover
    arcs: list["Arc"]

    @property
    def area(self) -> float:
        return abs(signed_area(self.pts)) if len(self.pts) >= 3 else 0.0

    @property
    def keepout(self) -> list[list[Point]]:
        return ([self.pts] if self.area >= 1.0 else []) + self.bands


def _closed(raw: list[Point]) -> bool:
    return len(raw) >= 2 and math.dist(raw[0], raw[-1]) <= 0.1


def _shape(contour, variables: dict[str, str]) -> _Shape | None:
    """A closed outline, or None for an open path."""
    raw, curved, arcs = outline(contour.elements, variables)
    if not _closed(raw):
        return None
    pts = clean(raw, curved)
    bands = [band for a, b, r in arcs if (band := arc_band(a, b, r))]
    if (len(pts) < 3 or abs(signed_area(pts)) < 1.0) and not bands:
        return None
    return _Shape(pts, frozenset(curved), bands, arcs)


def _touching(polys: list[list[Point]], pieces: list[Piece]) -> None:
    """Add keep-out areas to the pieces they overlap (by bounding box)."""
    for poly in polys:
        xs, ys = [q[0] for q in poly], [q[1] for q in poly]
        for p in pieces:
            x0, y0, x1, y1 = p.bbox
            if min(xs) <= x1 and max(xs) >= x0 and min(ys) <= y1 and max(ys) >= y0:
                p.keepout.append(poly)


def _avoid(op: str, contour, variables: dict[str, str], pieces: list[Piece]) -> None:
    """Keep labels off what a non-piece-cutting operation works on: the line a
    contour route (or any open path) follows, or the area of a closed pocket."""
    try:
        raw, curved, arcs = outline(contour.elements, variables)
    except EvalError:
        return
    if op.startswith("<105 ") or not _closed(raw):
        _touching(path_bands(raw, curved, arcs), pieces)
    elif sh := _shape(contour, variables):
        _touching(sh.keepout, pieces)


def find_pieces(contours, ops, variables: dict[str, str], blank: tuple[float, float, float],
                is_label) -> list[Piece] | None:
    """The pieces the part's through-cut routing cuts out, with their cutouts and
    the areas labels must avoid. ``ops`` is (operation line, its values, contour
    numbers it points at) for every operation that points at a contour.

    None when no operation cuts a piece out (the blank is the piece). Raises
    Unclear when routing cuts pieces out in a way this can't follow for sure;
    the caller then labels the blank and says why.
    """
    length, width, thickness = blank
    by_number = {c.number: c for c in contours}
    through: list[int] = []
    marks: list[tuple[int, str, dict[str, str]]] = []
    for op, values, numbers in ops:
        for n in dict.fromkeys(numbers):
            if n not in by_number:
                raise Unclear(f"an operation points at contour {n}, which isn't there")
            if is_label(by_number[n]):
                continue
            if cuts_through(op, values, variables, thickness):
                if not _covers_whole(values, by_number[n]):
                    raise Unclear(f"a router cuts only part of contour {n}")
                through.append(n)
            else:
                marks.append((n, op, values))
    through = list(dict.fromkeys(through))
    if not through:
        return None
    seen: set[int] = set(through)
    marks = [m for m in marks if not (m[0] in seen or seen.add(m[0]))]  # each contour once

    cuts: list[_Shape] = []
    for n in through:
        try:
            sh = _shape(by_number[n], variables)
        except EvalError as e:
            raise Unclear(f"contour {n} can't be worked out ({e})") from None
        if sh is None:
            raise Unclear(f"a router cuts along an open line (contour {n})")
        cuts.append(sh)

    # Biggest first. Each outline belongs to the smallest piece its middle sits
    # on (not in one of that piece's cutouts): it's a cutout of that piece. If
    # its middle sits only in cutouts, or on no piece, it's a piece of its own.
    cuts.sort(key=lambda sh: -max(sh.area, 1.0))
    pieces: list[Piece] = []
    for sh in cuts:
        mid = interior_point(sh.pts)
        hosts = [p for p in pieces if inside(mid, p.points) and not any(inside(mid, h) for h in p.holes)]
        if hosts:
            host = min(hosts, key=lambda p: abs(signed_area(p.points)))
            host.holes.append(sh.pts)
            host.keepout += sh.keepout
            host.cutouts.append(Cutout(sh.pts, sh.curved, sh.arcs))
        elif sh.area >= 1.0:
            pieces.append(Piece(sh.pts, sh.curved, keepout=list(sh.bands), unknown_curves=bool(sh.arcs)))
        else:
            raise Unclear("a round piece can't be laid out")
    for n, op, _ in marks:  # grooves, pockets, shallow routes: keep labels off them
        _avoid(op, by_number[n], variables, pieces)

    for p in pieces:  # a cutout with a (smaller) piece cut from inside it
        for cut in p.cutouts:
            x0, y0, x1, y1 = cut.bbox
            cut.occupied = any(
                q is not p and all(x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1 for x, y in q.points)
                for q in pieces
            )

    unknown = [p for p in pieces if p.unknown_curves]
    if unknown:
        if len(pieces) > 1:
            raise Unclear("curves without a radius or direction on more than one piece")
        unknown[0].overall = (width, length)  # its size can only come from the board
    for p in pieces:  # every piece must sit on the blank, or something was misread
        x0, y0, x1, y1 = p.bbox
        if x0 < -1 or y0 < -1 or x1 > length + 1 or y1 > width + 1:
            raise Unclear("a piece runs off the board")
    pieces.sort(key=lambda p: (round(p.bbox[1], 1), round(p.bbox[0], 1)))  # bottom to top, left to right
    return pieces


def blank_piece(contours, ops, variables: dict[str, str], blank: tuple[float, float, float],
                is_label) -> Piece:
    """The whole blank as one piece (when no routing cuts pieces out, or it can't
    be followed). Labels keep off the lines the routers follow, off closed
    pockets, and out of through-cut holes, but never off the board's own outline
    or anything big enough to be a piece."""
    length, width, thickness = blank
    piece = rectangle(0, 0, length, width)
    by_number = {c.number: c for c in contours}
    done: set[int] = set()
    for op, values, numbers in ops:
        for n in dict.fromkeys(numbers):
            if n in done or n not in by_number or is_label(by_number[n]):
                continue
            done.add(n)
            if not op.startswith("<105 "):
                _avoid(op, by_number[n], variables, [piece])
                continue
            try:
                raw, curved, arcs = outline(by_number[n].elements, variables)
                shallow = routing_depth(values, variables, thickness) == "shallow"
            except (EvalError, Unclear):
                continue
            piece.keepout += path_bands(raw, curved, arcs)
            sh = None if shallow else _shape(by_number[n], variables)
            if sh and sh.area < 0.25 * length * width:
                piece.keepout += sh.keepout
    return piece


# ---------------------------------------------------------------- geometry tests


def inside(pt: Point, poly: list[Point]) -> bool:
    x, y = pt
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def seg_dist(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (orient(a, b, c) * orient(a, b, d) < 0) and (orient(c, d, a) * orient(c, d, b) < 0)


def _ring(poly: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(poly, poly[1:] + poly[:1]))


def _near(box: list[Point], poly: list[Point], margin: float) -> bool:
    """Some corner of either one is within ``margin`` of the other's outline."""
    return any(seg_dist(p, a, b) < margin for a, b in _ring(poly) for p in box) or any(
        seg_dist(q, c, d) < margin for c, d in _ring(box) for q in poly)


def clear_of(box: list[Point], poly: list[Point], margin: float) -> bool:
    """``box`` doesn't overlap or touch ``poly`` (a hole or another label)."""
    if any(inside(p, poly) for p in box) or any(inside(p, box) for p in poly):
        return False
    for a, b in _ring(poly):
        if any(segments_cross(a, b, c, d) for c, d in _ring(box)):
            return False
    return not (margin and _near(box, poly, margin))


def box_fits(box: list[Point], piece: Piece, margin: float, taken: list[list[Point]]) -> bool:
    """A text box sits inside the piece, ``margin`` clear of its edges and of
    every keep-out area, and clear of labels already placed."""
    if not all(inside(p, piece.points) for p in box):
        return False
    for a, b in piece.edges():
        if any(segments_cross(a, b, c, d) for c, d in _ring(box)):
            return False
    if any(inside(v, box) for v in piece.points) or _near(box, piece.points, margin):
        return False
    return all(clear_of(box, k, margin) for k in piece.keepout) and all(clear_of(box, t, 0.0) for t in taken)


# ---------------------------------------------------------------- text placement


def text_box(width: float, height: float, center: Point, angle: float, pad: float = 0.0) -> list[Point]:
    w, h = width / 2 + pad, height / 2 + pad
    c, s = math.cos(angle), math.sin(angle)
    return [(center[0] + u * c - v * s, center[1] + u * s + v * c) for u, v in ((-w, -h), (w, -h), (w, h), (-w, h))]


def text_lines(lines: list[tuple[str, float]], center: Point, angle: float) -> list[list[Point]]:
    """Polylines for centered text lines (text, height) stacked top to bottom with
    a gap of 0.4 x the first line's height, centered on ``center``, turned by ``angle``."""
    gap = 0.4 * lines[0][1]
    top = (sum(h for _, h in lines) + gap * (len(lines) - 1)) / 2
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for text, h in lines:
        scale = h / stroke_font.CAP
        x0, y0 = -stroke_font.text_width(text) * scale / 2, top - h
        for poly in stroke_font.text_polylines(text):
            pts = []
            for gx, gy in poly:
                u, v = x0 + gx * scale, y0 + gy * scale
                pts.append((round(center[0] + u * c - v * s, 4), round(center[1] + u * s + v * c, 4)))
            out.append(pts)
        top = y0 - gap
    return out


def block_size(lines: list[tuple[str, float]]) -> tuple[float, float]:
    width = max(stroke_font.text_width(t) * h / stroke_font.CAP for t, h in lines)
    height = sum(h for _, h in lines) + 0.4 * lines[0][1] * (len(lines) - 1)
    return width, height


def readable(angle: float) -> float:
    """Turn a direction so text along it is never upside down."""
    angle = math.atan2(math.sin(angle), math.cos(angle))
    if angle > math.pi / 2 + 1e-9:
        angle -= math.pi
    elif angle <= -math.pi / 2 + 1e-9:
        angle += math.pi
    return angle


def side_labels(piece: Piece, inches, taken: list[list[Point]]) -> tuple[list[list[Point]], int]:
    """Each straight side's length, centered just inside that side and running
    along it. Returns the drawing and how many sides had no room for a label."""
    out: list[list[Point]] = []
    missed = 0
    base_h = min(MAX_SIDE_H, max(piece.width, piece.depth) * 0.05)
    for a, b in piece.edges():
        if piece.is_curved(a, b):
            continue
        length = math.dist(a, b)
        text = inches(length)
        tw = stroke_font.text_width(text)
        direction = math.atan2(b[1] - a[1], b[0] - a[0])
        inward = (-math.sin(direction), math.cos(direction))  # left of the edge = inside (piece is CCW)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        angle = readable(direction)
        placed = False
        # First try to keep the text within the side's length, then let it run past.
        fit_h = min(base_h, 0.9 * length * stroke_font.CAP / tw)
        for h_start in (fit_h, base_h) if fit_h < base_h else (base_h,):
            h = max(h_start, 0.0)
            while h >= MIN_H and not placed:
                offset = max(6.0, 0.4 * h) + h / 2
                center = (mid[0] + inward[0] * offset, mid[1] + inward[1] * offset)
                width = tw * h / stroke_font.CAP
                if box_fits(text_box(width, h, center, angle), piece, 2.0, taken):
                    taken.append(text_box(width, h, center, angle, pad=max(3.0, 0.3 * h)))
                    out += text_lines([(text, h)], center, angle)
                    placed = True
                h *= 0.85
            if placed:
                break
        missed += not placed
    return out, missed


def center_label(piece: Piece, size: str, unit: str | None, taken: list[list[Point]]) -> list[list[Point]]:
    """The size (and unit under it) as big as fits somewhere in the middle of the piece."""
    x0, y0, x1, y1 = piece.bbox
    steps = 24
    candidates = [((x0 + x1) / 2, (y0 + y1) / 2)] + [
        (x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * j / steps) for i in range(1, steps) for j in range(1, steps)
    ]
    candidates = [c for c in candidates if inside(c, piece.points) and not any(inside(c, k) for k in piece.keepout)]
    if not candidates:
        return []
    walls = piece.edges() + [e for k in piece.keepout for e in _ring(k)]
    candidates.sort(key=lambda c: -min(seg_dist(c, a, b) for a, b in walls))  # roomiest spots first
    candidates = candidates[:150]
    angles = (0.0, math.pi / 2) if x1 - x0 >= y1 - y0 else (math.pi / 2, 0.0)

    h = min(MAX_MAIN_H, min(x1 - x0, y1 - y0) * 0.3)
    while h >= MIN_H:
        lines = [(size, h)] + ([(unit, h * 0.6)] if unit and h * 0.6 >= MIN_H else [])
        bw, bh = block_size(lines)
        for angle in angles:
            for c in candidates:
                if box_fits(text_box(bw, bh, c, angle), piece, 3.0, taken):
                    taken.append(text_box(bw, bh, c, angle, pad=2.0))
                    return text_lines(lines, c, angle)
        h *= 0.9
    return []


def piece_size(piece: Piece, inches) -> str:
    """Depth x width: the piece's Y size x its X size."""
    return f"{inches(piece.depth)} x {inches(piece.width)}"


def piece_description(piece: Piece, inches) -> str:
    """The size, plus each straight side for shaped pieces and each cutout's
    size (for the hidden label)."""
    text = piece_size(piece, inches)
    if not piece.is_rectangle:
        text += f" (SIDES {', '.join(inches(s) for s in piece.straight_sides)})"
    if piece.cutouts:
        text += f" (CUTOUTS {', '.join(c.size_text(inches) for c in piece.cutouts)})"
    return text


MAX_CUTOUT_H = 25.0


def _cutout_block(cut: "Cutout", inches, h: float) -> list[tuple[str, float]]:
    """A cutout's size (round: diameter) with CUTOUT (DIA CUTOUT) under it."""
    if cut.is_round:
        return [(inches(cut.diameter), h), ("DIA CUTOUT", h * 0.6)]
    return [(cut.size_text(inches), h), ("CUTOUT", h * 0.6)]


def cutout_inside(piece: Piece, cut: "Cutout", inches, taken: list[list[Point]]) -> list[list[Point]]:
    """The cutout's size written inside the cutout (on the scrap, so it's plainly
    about the cutout), and inside the part. [] if it doesn't fit or a piece is
    cut from inside it."""
    if cut.occupied:
        return []
    region = Piece(cut.region())
    rx0, ry0, rx1, ry1 = region.bbox
    angles = (0.0, math.pi / 2) if rx1 - rx0 >= ry1 - ry0 else (math.pi / 2, 0.0)
    centers = [((rx0 + rx1) / 2, (ry0 + ry1) / 2), interior_point(region.points)]
    h = min(MAX_CUTOUT_H, min(rx1 - rx0, ry1 - ry0) * 0.35)
    while h * 0.6 >= MIN_H:
        lines = _cutout_block(cut, inches, h)
        bw, bh = block_size(lines)
        for angle in angles:
            for c in centers:
                box = text_box(bw, bh, c, angle)
                if box_fits(box, region, 2.0, taken) and all(inside(q, piece.points) for q in box):
                    taken.append(text_box(bw, bh, c, angle, pad=2.0))
                    return text_lines(lines, c, angle)
        h *= 0.9
    return []


def cutout_beside(piece: Piece, cut: "Cutout", inches, taken: list[list[Point]]) -> list[list[Point]]:
    """The cutout's size just outside it, on the piece. [] if there's no room."""
    cx0, cy0, cx1, cy1 = cut.bbox
    mx, my = (cx0 + cx1) / 2, (cy0 + cy1) / 2
    h = MAX_CUTOUT_H
    while h * 0.6 >= MIN_H:
        lines = _cutout_block(cut, inches, h)
        bw, bh = block_size(lines)
        gap = 8.0
        for c in ((mx, cy1 + gap + bh / 2), (mx, cy0 - gap - bh / 2),
                  (cx1 + gap + bw / 2, my), (cx0 - gap - bw / 2, my)):
            if box_fits(text_box(bw, bh, c, 0.0), piece, 2.0, taken):
                taken.append(text_box(bw, bh, c, 0.0, pad=2.0))
                return text_lines(lines, c, 0.0)
        h *= 0.85
    return []


def label_drawing(pieces: list[Piece], unit: str | None, inches) -> tuple[list[list[Point]], list[str]]:
    """Every piece's label as polylines (mm, part coordinates), plus notes about
    anything there was no room to draw. Order: cutout sizes that fit inside their
    cutouts, then side numbers, then the piece's own size, then any cutout sizes
    that have to go beside their cutouts (so they never take the size's room)."""
    out: list[list[Point]] = []
    notes: list[str] = []
    for piece in pieces:
        size = piece_size(piece, inches)
        taken: list[list[Point]] = []
        drawn: list[list[Point]] = []
        outside: list[Cutout] = []
        for cut in piece.cutouts:
            lines = cutout_inside(piece, cut, inches, taken)
            drawn += lines
            if not lines:
                outside.append(cut)
        if piece.is_rectangle and not piece.keepout and not piece.cutouts:
            x0, y0, _, _ = piece.bbox
            main = [[(round(x + x0, 4), round(y + y0, 4)) for x, y in poly]
                    for poly in stroke_font.layout(piece.width, piece.depth, size, unit or "")]
        else:
            if not piece.is_rectangle:
                sides, missed = side_labels(piece, inches, taken)
                drawn += sides
                if missed:
                    notes.append(f"{missed} side(s) had no room for their number")
            main = center_label(piece, size, unit, taken)
        if not main:
            notes.append("no room to draw the label on a piece")
        for cut in outside:
            lines = cutout_beside(piece, cut, inches, taken)
            if not lines:
                notes.append("no room for a cutout's size")
            drawn += lines
        out += drawn + main
    return out, notes
