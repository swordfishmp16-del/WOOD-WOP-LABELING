"""Special pieces: L shelves, double shelves, angled and curved shelves, cutouts."""

import math
from pathlib import Path

import pytest

import shapes
import woodwop_label as wl

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
LSHELF = SAMPLES / "RIST_BAR_OAK_1087516_HIS_L_SHELF_TOP_ES_UNIT_7_X1.mpr"
DOUBLE = SAMPLES / "HILI_161091_LIGHT_COMBO_A_X1.mpr"
ALL_PARTS = sorted(p for p in SAMPLES.iterdir() if "BOARD" not in p.name)


def labeled(path: Path, name: str | None = None) -> wl.Result:
    return wl.label_bytes(path.read_bytes(), name or path.name)


def lines_of(data: bytes) -> list[str]:
    return wl.split_lines(data)[0]


def visible(text: str) -> int:
    return sum(c != " " for c in text)


def make_mpr(outlines: list[list[tuple[str, dict]]], routed: list, bsx: float, bsy: float,
             variables: dict[str, str] | None = None) -> bytes:
    """A minimal part file: the given contours, with an operation on each in ``routed``:
    a contour number (through-cut routing), or (number, operation line, extra values)."""
    lines = ["[H", 'VERSION="4.0 Alpha"', f"_BSX={bsx:.6f}", f"_BSY={bsy:.6f}", "_BSZ=19.500000", "", "[001"]
    for k, v in (variables or {"t": "19.5"}).items():
        lines += [f'{k}="{v}"', 'KM=""']
    lines.append("")
    for n, elements in enumerate(outlines, 1):
        lines.append(f"]{n}")
        for i, (kind, values) in enumerate(elements):
            lines += [f"$E{i}", kind + " "] + [f"{k}={v}" for k, v in values.items()] + [""]
    lines += ["<100 \\WerkStck\\", f'LA="{bsx}"', f'BR="{bsy}"', 'DI="19.5"', ""]
    for item in routed:
        n, op, extra = (item, "<105 \\Konturfraesen\\", {}) if isinstance(item, int) else item
        lines += [op, f'EA="{n}:0"', f'EE="{n}:{len(outlines[n - 1]) - 1}"']
        lines += [f'{k}="{v}"' for k, v in extra.items()] + ['MNM="op"', ""]
    lines.append("!")
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


def poly(points: list[tuple[float, float]], arcs: set[int] = frozenset()) -> list[tuple[str, dict]]:
    """A closed outline through ``points``; the segment ending at index i is an arc if i is in ``arcs``."""
    out = [("KP", {"X": points[0][0], "Y": points[0][1], "Z": "0.0", "KO": "00"})]
    for i, (x, y) in enumerate(points[1:] + points[:1], 1):
        out.append(("KA" if i in arcs else "KL", {"X": x, "Y": y}))
    return out


def all_inside(drawing, piece_points) -> bool:
    return all(shapes.inside(p, piece_points) for line in drawing for p in line)


# ---------------------------------------------------------------- arithmetic


@pytest.mark.parametrize("expr, value", [
    ("4*25.4", 101.6), ("bsd+tsd+ma", 679.5), ("-rw+ld-eb", -403.225), ("(2+3)*4", 20.0), ("2+3*4", 14.0),
    ("10/4", 2.5), ("-(-3)", 3.0), ("lw", 657.225),
])
def test_evaluate(expr, value):
    variables = {"bsd": "330", "tsd": "330", "ma": "19.5", "rw": "809.625", "ld": "406.4", "eb": "0",
                 "l": "657.225", "lw": "l"}
    assert shapes.evaluate(expr, variables) == pytest.approx(value)


@pytest.mark.parametrize("expr", ["IF dt THEN 20 ELSE 12", "nope+1", "2+", "(1", "1/0", "", "a", "LW"])
def test_evaluate_refuses_what_it_cant_work_out(expr):
    with pytest.raises(shapes.EvalError):
        shapes.evaluate(expr, {"a": "b", "b": "a", "lw": "1"})


def test_variable_names_are_exact():
    """woodWOP's sheet files have both L (sheet length) and l (part length)."""
    assert shapes.evaluate("L", {"L": "2438", "l": "2419.35"}) == 2438
    assert shapes.evaluate("l", {"L": "2438", "l": "2419.35"}) == 2419.35


# ---------------------------------------------------------------- your files


def test_l_shelf_every_side_numbered_and_overall_in_the_middle():
    r = labeled(LSHELF)
    assert r.status == wl.LABELED and r.info.notes == []
    (piece,) = r.info.pieces
    assert not piece.is_rectangle
    sides = [wl.inches_text(s) for s in piece.straight_sides]
    assert sorted(sides) == sorted(["31 7/8", "16", "15 7/8", "9 7/8", "16", "25 7/8"])
    assert r.info.label == "SIZE 31 7/8 x 25 7/8 (SIDES 31 7/8, 16, 15 7/8, 9 7/8, 16, 25 7/8) | U7"
    # every side number, the overall size and the unit are all drawn, all on the piece
    assert len(r.drawing) == sum(visible(s) for s in sides) + visible("31 7/8 x 25 7/8") + visible("U7")
    assert all_inside(r.drawing, piece.points)


def test_double_shelf_gets_each_shelfs_own_size():
    r = labeled(DOUBLE)
    assert r.status == wl.LABELED and r.info.notes == []
    assert [p.is_rectangle for p in r.info.pieces] == [True, True]
    assert r.info.label == "SIZE 13 x 35 + 13 x 35"  # not the 35 x 26 3/4 board
    assert r.info.size_text == "13 x 35 + 13 x 35"
    # each shelf's label sits entirely on that shelf (nothing crosses the cut between them)
    for piece in r.info.pieces:
        on_it = [line for line in r.drawing if shapes.inside(line[0], piece.points)]
        assert len(on_it) == visible("13 x 35") and all_inside(on_it, piece.points)
    assert len(r.drawing) == 2 * visible("13 x 35")


@pytest.mark.parametrize("path", ALL_PARTS, ids=lambda p: p.name)
def test_routing_unchanged_and_never_on_a_label(path):
    old, new = lines_of(path.read_bytes()), lines_of(labeled(path).new_data)
    assert wl.contour_refs(old) == wl.contour_refs(new)
    assert [x for x in new if x.startswith("<")] == [x for x in old if x.startswith("<")]
    labels = {c.number for c in wl.contours(new) if wl.is_label_contour(c)}
    assert labels and not labels & {n for _, n in wl.contour_refs(new)}


@pytest.mark.parametrize("path", ALL_PARTS, ids=lambda p: p.name)
def test_every_label_line_is_on_a_piece(path):
    r = labeled(path)
    assert all(any(shapes.inside(p, piece.points) for piece in r.info.pieces) for line in r.drawing for p in line)


@pytest.mark.parametrize("path", [LSHELF, DOUBLE], ids=lambda p: p.name)
def test_special_files_round_trip(path):
    r = labeled(path)
    assert wl.unlabel_bytes(r.new_data).new_data == path.read_bytes()
    assert wl.label_bytes(r.new_data, path.name).status == wl.ALREADY


@pytest.mark.parametrize("path", [LSHELF, DOUBLE], ids=lambda p: p.name)
def test_files_labeled_by_the_size_only_version_upgrade(path):
    earlier = (FIXTURES / ("v3_size_only_" + path.name)).read_bytes()
    r = wl.label_bytes(earlier, path.name)
    assert r.status == wl.LABELED and r.message.startswith("label updated")
    assert r.new_data == labeled(path).new_data
    assert wl.unlabel_bytes(earlier).new_data == path.read_bytes()


def test_rounded_inside_corner_keeps_nominal_sides():
    rounded = LSHELF.read_bytes().replace(b'r="0"', b'r="127"')
    r = wl.label_bytes(rounded, LSHELF.name)
    assert r.info.label == labeled(LSHELF).info.label


def test_expression_sizes_are_cross_checked():
    stale = DOUBLE.read_bytes().replace(b"_BSY=679.500000", b"_BSY=600.000000")  # BR="bsd+tsd+ma" = 679.5
    r = wl.label_bytes(stale, DOUBLE.name)
    assert r.status == wl.PROBLEM and "doesn't match" in r.message


# ---------------------------------------------------------------- other shapes


def test_angled_shelf():
    pts = [(0, 0), (914.4, 0), (914.4, 203.2), (762, 355.6), (0, 355.6)]
    r = wl.label_bytes(make_mpr([poly(pts)], [1], 914.4, 355.6), "X_1234567_ANGLE_U4.mpr")
    assert r.status == wl.LABELED and r.info.notes == []
    (piece,) = r.info.pieces
    sides = [wl.inches_text(s) for s in piece.straight_sides]
    assert sorted(sides) == sorted(["36", "8", "8 1/2", "30", "14"])
    assert len(r.drawing) == sum(visible(s) for s in sides) + visible("14 x 36") + visible("U4")
    assert all_inside(r.drawing, piece.points)
    # the angled side's number runs along the angled side
    diag = [line for line in r.drawing if all(x > 700 and y > 150 for x, y in line)]
    assert diag


def test_curved_side_gets_no_number():
    pts = [(0, 0), (900, 0), (900, 250), (800, 350), (0, 350)]
    r = wl.label_bytes(make_mpr([poly(pts, arcs={3})], [1], 900, 350), "X_1234567_RAD_U2.mpr")
    (piece,) = r.info.pieces
    assert len(piece.straight_sides) == 4 and not piece.is_rectangle
    sides = [wl.inches_text(s) for s in piece.straight_sides]
    assert r.info.label == f"SIZE 13 3/4 x 35 7/16 (SIDES {', '.join(sides)}) | U2"
    assert len(r.drawing) == sum(visible(s) for s in sides) + visible("13 3/4 x 35 7/16") + visible("U2")


def test_cutout_is_not_a_piece_and_labels_avoid_it():
    outer = poly([(0, 0), (900, 0), (900, 400), (0, 400)])
    hole_pts = [(380, 140), (520, 140), (520, 260), (380, 260)]
    r = wl.label_bytes(make_mpr([outer, poly(hole_pts)], [1, 2], 900, 400), "X_1234567_GROMMET_U1.mpr")
    (piece,) = r.info.pieces
    assert len(piece.holes) == 1 and r.info.label == "SIZE 15 3/4 x 35 7/16 | U1"
    assert r.drawing and all_inside(r.drawing, piece.points)
    assert not any(shapes.inside(p, hole_pts) for line in r.drawing for p in line)


def test_groove_along_an_open_path_is_ignored():
    outer = poly([(0, 0), (900, 0), (900, 400), (0, 400)])
    groove = [("KP", {"X": 0, "Y": 200, "Z": "0.0", "KO": "00"}), ("KL", {"X": 900, "Y": 200})]
    r = wl.label_bytes(make_mpr([outer, groove], [1, 2], 900, 400), "X_1234567_U1.mpr")
    assert [p.is_rectangle for p in r.info.pieces] == [True]


@pytest.mark.parametrize("case", ["if", "off-blank"])
def test_falls_back_to_blank_size_when_outline_cant_be_trusted(case):
    if case == "if":
        outline = [("KP", {"X": 0, "Y": 0, "Z": "0.0", "KO": "00"}), ("KL", {"X": "IF a THEN 900 ELSE 800", "Y": 0}),
                   ("KL", {"X": 900, "Y": 400}), ("KL", {"X": 0, "Y": 400}), ("KL", {"X": 0, "Y": 0})]
        data = make_mpr([outline], [1], 900, 400)
    else:
        data = make_mpr([poly([(0, 0), (1200, 0), (1200, 400), (0, 400)])], [1], 900, 400)
    r = wl.label_bytes(data, "X_1234567_U1.mpr")
    assert r.status == wl.LABELED
    assert r.info.label == "SIZE 15 3/4 x 35 7/16 (BOARD - PIECES UNCLEAR) | U1"  # the 900 x 400 blank, marked
    assert r.message.startswith("label added (board size shown:")
    (piece,) = r.info.pieces
    assert piece.bbox == (0, 0, 900, 400)


def test_operation_pointing_at_a_missing_contour_is_left_alone():
    """A broken program: never label it, and never give a label contour that number."""
    data = make_mpr([poly([(0, 0), (500, 0), (500, 400), (0, 400)])], [1], 900, 400).replace(b'EA="1:0"', b'EA="7:0"')
    r = wl.label_bytes(data, "X_1234567_U1.mpr")
    assert r.status == wl.PROBLEM and "contour 7" in r.message and r.new_data is None
    lines = lines_of(data)
    assert wl.first_label_number(lines) == 8


def test_side_too_short_to_number_is_reported():
    pts = [(0, 0), (900, 0), (900, 400), (453, 400), (453, 397), (447, 397), (447, 400), (0, 400)]  # 3 mm notch
    r = wl.label_bytes(make_mpr([poly(pts)], [1], 900, 400), "X_1234567_U1.mpr")
    assert r.status == wl.LABELED and any("no room" in n for n in r.info.notes)
    assert "no room" in r.message


def test_text_never_upside_down():
    for deg in range(-360, 361, 15):
        a = shapes.readable(math.radians(deg))
        assert -math.pi / 2 < a <= math.pi / 2 + 1e-9


# ---------------------------------------------------------------- unit names


@pytest.mark.parametrize("name, unit", [
    ("RIST_BAR_OAK_1087516_HIS_L_SHELF_TOP_ES_UNIT_7_X1.mpr", "U7"),
    ("A_1234567_B_UNIT12_X1.mpr", "U12"),
    ("A_1234567_B_unit_3a_X1.mpr", "U3A"),
    ("A_1234567_B_UNIT_X1.mpr", None),
])
def test_unit_spelled_out(name, unit):
    assert wl.unit_from_name(name) == unit


# ---------------------------------------------------------------- review fixes


def points_of(drawing):
    return [p for line in drawing for p in line]


def test_bow_front_shelf_size_comes_from_the_blank_and_labels_stay_off_the_curve():
    """Front corners at y=300, front bows out to y=400: the chord would say 300 deep."""
    pts = [(0, 0), (600, 0), (600, 300), (0, 300)]
    outline = poly(pts, arcs={3})  # (600,300) -> (0,300) is the bowed front
    outline[3][1]["R"] = "500"  # 600 chord, radius 500: bulge 100
    r = wl.label_bytes(make_mpr([outline], [1], 600, 400), "X_1234567_BOW_U1.mpr")
    (piece,) = r.info.pieces
    assert r.info.size_text.startswith("15 3/4 x 23 5/8")  # 400 x 600 blank, not 300 deep
    band_bottom = 300 - 100 - 2
    assert r.drawing and all(y < band_bottom for _, y in points_of(r.drawing))


def test_curve_bowing_inward_keeps_labels_off_the_cut_away_wood():
    pts = [(0, 0), (900, 0), (900, 400), (0, 400)]
    outline = poly(pts, arcs={3})  # front (900,400) -> (0,400) is an arc
    outline[3][1]["R"] = "1500"  # 900 chord: bulge about 68.6 either way
    r = wl.label_bytes(make_mpr([outline], [1], 900, 400), "X_1234567_SCOOP_U1.mpr")
    bulge = 1500 - math.sqrt(1500 ** 2 - 450 ** 2)
    assert r.drawing and all(y < 400 - bulge - 2 for _, y in points_of(r.drawing))
    assert r.info.notes == []


def test_curve_with_unknown_radius_plays_safe():
    """No radius: the curve could bow in as far as a half circle, so nothing fits; say so."""
    outline = poly([(0, 0), (900, 0), (900, 400), (0, 400)], arcs={3})
    r = wl.label_bytes(make_mpr([outline], [1], 900, 400), "X_1234567_SCOOP_U1.mpr")
    assert r.status == wl.LABELED and r.drawing == [] and "no room" in r.message
    assert r.info.label.startswith("SIZE 15 3/4 x 35 7/16")  # still in the variable list


def test_round_cutout_keeps_labels_off_it():
    outer = poly([(0, 0), (900, 0), (900, 400), (0, 400)])
    circle = [("KP", {"X": 400, "Y": 200, "Z": "0.0", "KO": "00"}),
              ("KA", {"X": 500, "Y": 200, "R": "50"}), ("KA", {"X": 400, "Y": 200, "R": "50"})]
    r = wl.label_bytes(make_mpr([outer, circle], [1, 2], 900, 400), "X_1234567_GROMMET_U1.mpr")
    (piece,) = r.info.pieces
    assert r.info.label == "SIZE 15 3/4 x 35 7/16 | U1" and piece.keepout
    assert r.drawing and not any(math.dist(p, (450, 200)) < 52 for p in points_of(r.drawing))


def test_shallow_routed_outline_is_not_a_piece():
    outer = poly([(0, 0), (900, 0), (900, 400), (0, 400)])
    pocket = poly([(100, 100), (300, 100), (300, 300), (100, 300)])
    shallow = (2, "<105 \\Konturfraesen\\", {"ZA": "5"})
    r = wl.label_bytes(make_mpr([outer, pocket], [1, shallow], 900, 400), "X_1234567_U1.mpr")
    (piece,) = r.info.pieces
    assert r.info.label == "SIZE 15 3/4 x 35 7/16 | U1"
    assert not any(100 <= x <= 300 and 100 <= y <= 300 for x, y in points_of(r.drawing))


def test_only_shallow_routing_labels_the_blank_and_avoids_the_pocket():
    pocket = poly([(100, 100), (300, 100), (300, 300), (100, 300)])
    r = wl.label_bytes(make_mpr([pocket], [(1, "<105 \\Konturfraesen\\", {"ZA": "5"})], 900, 400), "X_1234567_U1.mpr")
    assert r.info.label == "SIZE 15 3/4 x 35 7/16 | U1"
    assert r.drawing and not any(100 <= x <= 300 and 100 <= y <= 300 for x, y in points_of(r.drawing))


def test_other_operations_on_a_closed_outline_are_not_pieces():
    outer = poly([(0, 0), (900, 0), (900, 400), (0, 400)])
    inner = poly([(100, 100), (300, 100), (300, 300), (100, 300)])
    r = wl.label_bytes(make_mpr([outer, inner], [1, (2, "<109 \\Nuten\\", {})], 900, 400), "X_1234567_U1.mpr")
    assert [p.is_rectangle for p in r.info.pieces] == [True]


def test_notch_routed_on_an_edge_is_a_cutout_not_a_piece():
    outer = poly([(0, 0), (900, 0), (900, 400), (0, 400)])
    notch = poly([(400, 350), (500, 350), (500, 400), (400, 400)])
    r = wl.label_bytes(make_mpr([outer, notch], [1, 2], 900, 400), "X_1234567_U1.mpr")
    (piece,) = r.info.pieces
    assert len(piece.holes) == 1 and r.info.label == "SIZE 15 3/4 x 35 7/16 | U1"


def test_piece_cut_from_a_cutout_is_its_own_piece():
    desk = poly([(0, 0), (1200, 0), (1200, 600), (0, 600)])
    hole = poly([(400, 150), (800, 150), (800, 450), (400, 450)])
    insert = poly([(420, 170), (780, 170), (780, 430), (420, 430)])
    r = wl.label_bytes(make_mpr([desk, hole, insert], [1, 2, 3], 1200, 600), "X_1234567_U1.mpr")
    assert len(r.info.pieces) == 2
    desk_piece = max(r.info.pieces, key=lambda p: p.width)
    assert len(desk_piece.holes) == 1
    assert "10 1/4 x 14 3/16" in r.info.label  # the 260 x 360 insert


def test_several_curved_pieces_fall_back_to_the_blank():
    a = poly([(0, 0), (400, 0), (400, 300), (0, 300)], arcs={3})
    b = poly([(500, 0), (900, 0), (900, 300), (500, 300)], arcs={3})
    r = wl.label_bytes(make_mpr([a, b], [1, 2], 900, 400), "X_1234567_U1.mpr")
    (piece,) = r.info.pieces
    assert piece.bbox == (0, 0, 900, 400)


def test_contour_ops_reads_operation_values():
    lines = lines_of(DOUBLE.read_bytes())
    ops = wl.contour_ops(lines)
    assert [(op, numbers) for op, _, numbers in ops] == [("<105 \\Konturfraesen\\", [1, 1]), ("<105 \\Konturfraesen\\", [2, 2])]
    assert ops[0][1]["ZA"] == "sbc"


# ---------------------------------------------------------------- second review

ROUTE = "<105 \\Konturfraesen\\"


def test_cutout_in_an_insert_cut_from_a_cutout_belongs_to_the_insert():
    desk = poly([(0, 0), (1200, 0), (1200, 600), (0, 600)])
    hole = poly([(400, 150), (800, 150), (800, 450), (400, 450)])
    insert = poly([(420, 170), (780, 170), (780, 430), (420, 430)])
    grommet = poly([(550, 250), (650, 250), (650, 350), (550, 350)])
    r = wl.label_bytes(make_mpr([desk, hole, insert, grommet], [1, 2, 3, 4], 1200, 600), "X_1234567_U1.mpr")
    assert len(r.info.pieces) == 2
    insert_piece = min(r.info.pieces, key=lambda p: p.width)
    assert len(insert_piece.holes) == 1
    assert not any(550 <= x <= 650 and 250 <= y <= 350 for x, y in points_of(r.drawing))


def test_open_line_cut_through_labels_the_board_and_says_so():
    outer = poly([(0, 0), (900, 0), (900, 660), (0, 660)])
    split = [("KP", {"X": 0, "Y": 330, "Z": "0.0", "KO": "00"}), ("KL", {"X": 900, "Y": 330})]
    r = wl.label_bytes(make_mpr([outer, split], [1, 2], 900, 660), "X_1234567_U1.mpr")
    assert "(BOARD - PIECES UNCLEAR)" in r.info.label and "open line" in r.message


def test_router_on_part_of_an_outline_labels_the_board_and_says_so():
    lshape = poly([(0, 0), (900, 0), (900, 600), (400, 600), (400, 300), (0, 300)])
    data = make_mpr([lshape], [1], 900, 600).replace(b'EE="1:6"', b'EE="1:1"')
    r = wl.label_bytes(data, "X_1234567_U1.mpr")
    assert "(BOARD - PIECES UNCLEAR)" in r.info.label and "part of contour 1" in r.message


def test_depth_formula_labels_the_board_and_says_so():
    outer = poly([(0, 0), (900, 0), (900, 400), (0, 400)])
    r = wl.label_bytes(make_mpr([outer], [(1, ROUTE, {"ZA": "dz-6"})], 900, 400), "X_1234567_U1.mpr")
    assert "(BOARD - PIECES UNCLEAR)" in r.info.label and "depth" in r.message


@pytest.mark.parametrize("za", ["sbc", "dz", "19.5", "25", ""])
def test_through_depths(za):
    assert shapes.cuts_through(ROUTE, {"ZA": za} if za else {}, {}, 19.5)


def test_shallow_depth_or_other_operation_is_not_through():
    assert not shapes.cuts_through(ROUTE, {"ZA": "10"}, {}, 19.5)
    assert not shapes.cuts_through("<112 \\Tasche\\", {}, {}, 19.5)


def test_contour_refs_and_ops_agree():
    for path in ALL_PARTS:
        lines = lines_of(path.read_bytes())
        assert wl.contour_refs(lines) == [(op, n) for op, _, ns in wl.contour_ops(lines) for n in ns]
