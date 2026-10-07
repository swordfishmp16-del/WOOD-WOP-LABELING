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
SPACE = 4.0

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


def layout(length: float, width: float, line1: str, line2: str = "") -> list[list[Point]]:
    """Polylines (mm, part coordinates) for a label centered on a length x width part.

    Text runs along the longer side. Line 1 (the size) is the big line; line 2
    goes under it, smaller, when there's room. Returns [] if the part is too
    small to draw on legibly.
    """
    rotate = width > length
    long_side, short_side = (width, length) if rotate else (length, width)
    max_w, max_h = long_side * 0.85, short_side * 0.8

    w1 = text_width(line1)
    h1 = min(50.0, short_side * 0.2, max_w * CAP / w1)
    if h1 < 5.0:
        return []

    lines = [(line1, h1)]
    if line2:
        w2 = text_width(line2)
        h2 = min(h1 * 0.6, max_w * CAP / w2)
        if h2 >= 5.0 and h1 + 0.5 * h1 + h2 <= max_h:
            lines.append((line2, h2))

    gap = 0.5 * h1
    total_h = sum(h for _, h in lines) + gap * (len(lines) - 1)
    out: list[list[Point]] = []
    top = total_h / 2  # text block centered on the part, line 1 on top
    for text, h in lines:
        scale = h / CAP
        x0 = -text_width(text) * scale / 2
        y0 = top - h
        for poly in text_polylines(text):
            out.append([(x0 + gx * scale, y0 + gy * scale) for gx, gy in poly])
        top = y0 - gap

    cx, cy = length / 2, width / 2
    if rotate:  # read bottom to top along the part's Y
        return [[(round(cx - v, 4), round(cy + u, 4)) for u, v in poly] for poly in out]
    return [[(round(cx + u, 4), round(cy + v, 4)) for u, v in poly] for poly in out]
