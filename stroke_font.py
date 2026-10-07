"""Single-line font for drawing label text as woodWOP contour lines.

Every character is ONE open polyline (it may retrace itself) on a 4 x 6 grid,
so each character becomes exactly one contour. No character ever closes on
itself: shapes like 0, 6, 8, 9, A, B, D, O, P, R leave a small gap, so woodNest
can never mistake a letter for a closed cutout.
"""

from __future__ import annotations

Point = tuple[float, float]

CAP = 6.0  # glyph height in grid units
ADVANCE = 5.5  # glyph width (4) plus spacing
SPACE = 3.0

GLYPHS: dict[str, list[Point]] = {
    "0": [(1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1.8, 0)],
    "1": [(1, 5), (2, 6), (2, 0), (1, 0), (3, 0)],
    "2": [(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (0, 0), (4, 0)],
    "3": [(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1.5, 3), (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)],
    "4": [(0, 6), (0, 2), (4, 2), (3, 2), (3, 6), (3, 0)],
    "5": [(4, 6), (0, 6), (0, 3.5), (3, 3.5), (4, 2.5), (4, 1), (3, 0), (1, 0), (0, 1)],
    "6": [(3.5, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2.5), (3, 3.5), (0.8, 3.5)],
    "7": [(0, 6), (4, 6), (1.5, 0)],
    "8": [(3.6, 3.6), (4, 4), (4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (3, 3), (4, 2), (4, 1), (3, 0),
          (1, 0), (0, 1), (0, 2), (0.6, 2.6)],
    "9": [(0.5, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 3.5), (1, 2.5), (3.2, 2.5)],
    "-": [(0.5, 3), (3.5, 3)],
    "/": [(0.5, 0), (3.5, 6)],
    ".": [(1.7, 0), (2.3, 0), (2.3, 0.6)],
    "x": [(0.5, 0), (3.5, 4), (2, 2), (0.5, 4), (3.5, 0)],  # small x between sizes
    "?": [(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (2, 2.5), (2, 1.2)],
    "A": [(0, 0), (2, 6), (4, 0), (10 / 3, 2), (1.2, 2)],
    "B": [(0.8, 3), (3, 3), (4, 4), (4, 5), (3, 6), (0, 6), (0, 0), (3, 0), (4, 1), (4, 2), (3.6, 2.4)],
    "C": [(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1)],
    "D": [(0, 0), (0, 6), (2.5, 6), (4, 4.5), (4, 1.5), (2.5, 0), (0.8, 0)],
    "E": [(4, 6), (0, 6), (0, 3), (3, 3), (0, 3), (0, 0), (4, 0)],
    "F": [(4, 6), (0, 6), (0, 3), (3, 3), (0, 3), (0, 0)],
    "G": [(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 3), (2, 3)],
    "H": [(0, 6), (0, 0), (0, 3), (4, 3), (4, 6), (4, 0)],
    "I": [(1, 6), (3, 6), (2, 6), (2, 0), (1, 0), (3, 0)],
    "J": [(1, 6), (4, 6), (3, 6), (3, 1), (2, 0), (1, 0), (0, 1)],
    "K": [(0, 6), (0, 0), (0, 2), (4, 6), (1.5, 3.5), (4, 0)],
    "L": [(0, 6), (0, 0), (4, 0)],
    "M": [(0, 0), (0, 6), (2, 3), (4, 6), (4, 0)],
    "N": [(0, 0), (0, 6), (4, 0), (4, 6)],
    "O": [(1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1.8, 0)],
    "P": [(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0.8, 3)],
    "Q": [(2.6, 1.4), (4, 0), (3.5, 0.5), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (2.2, 0)],
    "R": [(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0.8, 3), (2, 3), (4, 0)],
    "S": [(4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)],
    "T": [(0, 6), (4, 6), (2, 6), (2, 0)],
    "U": [(0, 6), (0, 1), (1, 0), (3, 0), (4, 1), (4, 6)],
    "V": [(0, 6), (2, 0), (4, 6)],
    "W": [(0, 6), (1, 0), (2, 4), (3, 0), (4, 6)],
    "X": [(0, 0), (4, 6), (2, 3), (0, 6), (4, 0)],
    "Y": [(0, 6), (2, 3), (4, 6), (2, 3), (2, 0)],
    "Z": [(0, 6), (4, 6), (0, 0), (4, 0)],
}


def text_width(text: str) -> float:
    """Width of ``text`` in grid units."""
    if not text:
        return 0.0
    w = sum(SPACE if c == " " else ADVANCE for c in text)
    return w - (ADVANCE - 4.0 if text[-1] != " " else 0.0)


def text_polylines(text: str) -> list[list[Point]]:
    """One polyline per visible character, in grid units, baseline at y=0."""
    out = []
    x = 0.0
    for c in text:
        if c == " ":
            x += SPACE
            continue
        glyph = GLYPHS.get(c) or GLYPHS.get(c.upper()) or GLYPHS["?"]
        out.append([(x + gx, gy) for gx, gy in glyph])
        x += ADVANCE
    return out


def layout(length: float, width: float, text: str, sub: str = "") -> list[list[Point]]:
    """Polylines (mm, part coordinates) for ``text`` centered on a length x width part,
    with ``sub`` (if any) smaller and centered under it.

    Text runs along the longer side. The main line is as big as fits, up to 50 mm
    tall and 30% of the part's short side, so smaller parts get smaller text; the
    second line is 60% of that. Returns [] if the main line can't be at least
    3 mm tall; leaves the second line off if it can't be at least 3 mm tall.
    """
    rotate = width > length
    long_side, short_side = (width, length) if rotate else (length, width)
    max_w = long_side * 0.85
    h = min(50.0, short_side * 0.3, max_w * CAP / text_width(text))
    if h < 3.0:
        return []
    lines = [(text, h)]
    if sub:
        h2 = min(h * 0.6, max_w * CAP / text_width(sub))
        if h2 >= 3.0:
            lines.append((sub, h2))

    # The block is at most 2 x h tall (h + gap 0.4h + 0.6h), centered on the part.
    gap = 0.4 * h
    top = (sum(hh for _, hh in lines) + gap * (len(lines) - 1)) / 2
    out: list[list[Point]] = []
    for line, hh in lines:
        scale = hh / CAP
        x0, y0 = -text_width(line) * scale / 2, top - hh
        out += [[(x0 + gx * scale, y0 + gy * scale) for gx, gy in poly] for poly in text_polylines(line)]
        top = y0 - gap

    cx, cy = length / 2, width / 2
    if rotate:  # read bottom to top along the part's Y
        return [[(round(cx - v, 4), round(cy + u, 4)) for u, v in poly] for poly in out]
    return [[(round(cx + u, 4), round(cy + v, 4)) for u, v in poly] for poly in out]
