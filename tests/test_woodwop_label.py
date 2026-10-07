import difflib
from pathlib import Path

import pytest

import stroke_font as sf
import woodwop_label as wl

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PARTS = sorted(p for p in SAMPLES.iterdir() if "BOARD" not in p.name)
BOARD = next(SAMPLES.glob("*BOARD*"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def lines_of(data: bytes) -> list[str]:
    return wl.split_lines(data)[0]


def labeled(path: Path) -> wl.Result:
    return wl.label_bytes(path.read_bytes())


# ---------------------------------------------------------------- what gets written


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_label_only_inserts_label_lines(path):
    old = path.read_bytes()
    r = wl.label_bytes(old)
    assert r.status == wl.LABELED
    km = 'KM="SIZE 95 1/4 x 12"'
    assert len(r.drawing) == 8  # 9 5 1 / 4 x 1 2

    # Build the expected file by hand: the original lines with the label put in.
    expected = lines_of(old)
    workpiece = expected.index("<100 \\WerkStck\\")
    drawn = []
    for k, poly in enumerate(r.drawing):
        drawn += wl.contour_lines(2 + k, poly)
    expected[workpiece:workpiece] = drawn
    end_of_vars = expected.index("]1") - 1  # the blank line closing [001
    expected[end_of_vars:end_of_vars] = [f'LABEL="{len(r.drawing)}"', km]
    assert lines_of(r.new_data) == expected

    # and a plain diff sees nothing but added lines
    ops = difflib.SequenceMatcher(a=lines_of(old), b=lines_of(r.new_data), autojunk=False).get_opcodes()
    assert {op[0] for op in ops} == {"equal", "insert"}
    assert sum(j2 - j1 for tag, _, _, j1, j2 in ops if tag == "insert") == 2 + len(drawn)


def test_contour_format_matches_woodwop():
    assert wl.contour_lines(7, [(10, 20), (10, 30.5)]) == [
        "]7",
        "$E0", "KP ", "X=10.0000", "Y=20.0000", "Z=0.0", "KO=00",
        ".X=10.000000", ".Y=20.000000", ".Z=0.000000", ".KO=00", "",
        "$E1", "KL ", "X=10.0000", "Y=30.5000",
        ".X=10.000000", ".Y=30.500000", ".Z=0.000000", ".WI=1.570796", ".WZ=0.000000", "",
    ]


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_label_placement(path):
    r = labeled(path)
    new = lines_of(r.new_data)
    # LABEL is the last variable, right before the blank line closing the list; its value = drawn contours
    i = new.index(f'LABEL="{len(r.drawing)}"')
    assert new[i - 1].startswith("KM=") and new[i + 2] == "" and new[i + 3] == "]1"
    # drawn label contours come right after the part outline (]1), before the first operation
    cs = wl.contours(new)
    assert [c.number for c in cs] == list(range(1, 2 + len(r.drawing)))
    assert not wl.is_label_contour(cs[0])
    assert all(wl.is_label_contour(c) for c in cs[1:])
    assert new[cs[-1].end] == "<100 \\WerkStck\\"
    assert wl.COMMENT_HEADER not in new


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_keeps_crlf_header_and_ending(path):
    old = path.read_bytes()
    new = labeled(path).new_data
    added = len(lines_of(new)) - len(lines_of(old))
    assert new.count(b"\r\n") == old.count(b"\r\n") + added
    assert b"\n" not in new.replace(b"\r\n", b"")
    assert new.endswith(old[old.index(b"<100 ") :])  # workpiece and every operation byte-identical
    assert new.startswith(old[: old.index(b"[001")])  # header untouched


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_drawing_sits_inside_the_part(path):
    r = labeled(path)
    for poly in r.drawing:
        assert poly[0] != poly[-1]  # never closed
        for x, y in poly:
            assert 0.05 * r.info.length_mm < x < 0.95 * r.info.length_mm
            assert 0.1 * r.info.width_mm < y < 0.9 * r.info.width_mm


# ---------------------------------------------------------------- router safety


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_routing_still_cuts_only_the_part_outline(path):
    old, new = lines_of(path.read_bytes()), lines_of(labeled(path).new_data)
    assert wl.contour_refs(old) == wl.contour_refs(new) == [("<105 \\Konturfraesen\\", 1)] * 2  # EA + EE
    assert [x for x in new if x.startswith("<")] == [x for x in old if x.startswith("<")]


def test_verify_catches_a_router_on_the_label():
    path = PARTS[0]
    old = path.read_bytes()
    r = wl.label_bytes(old)
    for bad in (
        r.new_data.replace(b'EA="1:0"', b'EA="2:0"'),  # existing routing moved onto the label
        r.new_data.replace(b"!\r\n", b'<105 \\Konturfraesen\\\r\nEA="5:0"\r\nEE="5:3"\r\n\r\n!\r\n'),  # router added
    ):
        with pytest.raises(wl.LabelError):
            wl.verify(old, bad, r.info.label, r.drawing)


def test_remove_refuses_if_label_is_routed():
    r = labeled(PARTS[0])
    routed = r.new_data.replace(b'EA="1:0"', b'EA="2:0"').replace(b'EE="1:4"', b'EE="2:4"')
    u = wl.unlabel_bytes(routed)
    assert u.status == wl.PROBLEM and "DANGER" in u.message


def test_sheet_check_clean_board():
    check = wl.check_sheet(lines_of(BOARD.read_bytes()))
    assert check.dangers == [] and check.labeled_parts == 0
    assert wl.label_bytes(BOARD.read_bytes()).status == wl.SHEET


def fake_labeled_sheet() -> bytes:
    """The real board with one drawn label contour (]4) and LABEL_0 added, as woodNest might produce."""
    data = BOARD.read_bytes()
    contour = "\r\n".join(wl.contour_lines(4, [(100.0, 100.0), (150.0, 100.0), (150.0, 140.0)])).encode()
    data = data.replace(b"<100 \\WerkStck\\", contour + b"\r\n<100 \\WerkStck\\", 1)
    return data.replace(b'bfb_0="57"\r\n', b'bfb_0="57"\r\nLABEL_0="1"\r\nKM="SIZE 1 x 2"\r\n')


def test_sheet_check_labeled_board_ok():
    r = wl.label_bytes(fake_labeled_sheet())
    assert r.status == wl.SHEET and r.message.startswith("sheet OK: 1 labeled part(s), 1 label lines")


def test_sheet_check_flags_router_on_label():
    bad = fake_labeled_sheet().replace(b'EA="3:0"', b'EA="4:0"')
    r = wl.label_bytes(bad)
    assert r.status == wl.PROBLEM and r.message.startswith("DANGER - DO NOT RUN")
    assert "label contour 4" in r.message


def test_sheet_check_flags_missing_contour():
    bad = BOARD.read_bytes().replace(b'EA="3:0"', b'EA="9:0"')
    assert "missing" in wl.check_sheet(lines_of(bad)).dangers[0]


# ---------------------------------------------------------------- round trips and refusals


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_remove_restores_exact_original(path):
    old = path.read_bytes()
    r = wl.unlabel_bytes(wl.label_bytes(old).new_data)
    assert r.status == wl.REMOVED and r.new_data == old


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_labeling_twice_changes_nothing(path):
    assert wl.label_bytes(labeled(path).new_data).status == wl.ALREADY


def test_relabel_replaces_old_label():
    path = PARTS[0]
    old = path.read_bytes()
    bigger = old.replace(b"_BSX=2419.350000", b"_BSX=2438.400000").replace(b'l="2419.35"', b'l="2438.4"')
    first = wl.label_bytes(bigger).new_data  # labeled as 96 x 12
    back = first.replace(b"_BSX=2438.400000", b"_BSX=2419.350000").replace(b'l="2438.4"', b'l="2419.35"')
    r = wl.label_bytes(back)  # size went back to 95 1/4: label must follow
    assert r.status == wl.LABELED and r.message == "label updated"
    assert r.new_data == wl.label_bytes(old).new_data


@pytest.mark.parametrize("fixture", ["v1_comment_label_CEP_U12.mpr", "v2_drawn_label_CEP_U12.mpr"])
def test_upgrades_files_labeled_by_earlier_test_versions(fixture):
    """The test files sent earlier (comment-only, and size+job+part drawing) upgrade cleanly."""
    path = PARTS[0]
    old = path.read_bytes()
    earlier = (FIXTURES / fixture).read_bytes()
    r = wl.label_bytes(earlier)
    assert r.status == wl.LABELED and r.message == "label updated"
    assert r.new_data == wl.label_bytes(old).new_data
    assert wl.unlabel_bytes(earlier).new_data == old


def test_original_files_untouched_by_planning():
    before = {p: p.read_bytes() for p in SAMPLES.iterdir()}
    for p in PARTS:
        wl.plan(p)
    assert {p: p.read_bytes() for p in SAMPLES.iterdir()} == before


def test_size_and_label_text():
    r = labeled(PARTS[0])
    assert (r.info.length_mm, r.info.width_mm) == (2419.35, 304.8)
    assert r.info.label == "SIZE 95 1/4 x 12" and r.info.size_text == "95 1/4 x 12"


def test_stale_header_is_refused():
    old = PARTS[0].read_bytes().replace(b"_BSX=2419.350000", b"_BSX=2400.000000")
    r = wl.label_bytes(old)
    assert r.status == wl.PROBLEM and "doesn't match" in r.message


def test_foreign_label_variable_is_refused():
    old = PARTS[0].read_bytes().replace(b'bfb="57"\r\n', b'bfb="57"\r\nLABEL="0"\r\nKM="mine"\r\n')
    assert wl.label_bytes(old).status == wl.PROBLEM


def test_own_label_variable_with_other_value_is_refused():
    old = PARTS[0].read_bytes().replace(b'bfb="57"\r\n', b'bfb="57"\r\nlabel="5"\r\nKM="theirs"\r\n')
    r = wl.label_bytes(old)
    assert r.status == wl.PROBLEM and "LABEL" in r.message


def test_not_a_woodwop_file():
    assert wl.label_bytes(b"hello\r\n").status == wl.PROBLEM


def test_incomplete_file_is_refused():
    """A file still being written (no ! end mark) is never labeled."""
    old = PARTS[0].read_bytes()
    r = wl.label_bytes(old[: len(old) // 2])
    assert r.status == wl.PROBLEM and "incomplete" in r.message


def test_file_without_variable_list_round_trips():
    old = PARTS[0].read_bytes()
    start, end = old.index(b"[001"), old.index(b"]1")
    no_vars = old[:start] + old[end:]
    no_vars = no_vars.replace(b'LA="l"', b'LA="2419.35"').replace(b'BR="w"', b'BR="304.8"').replace(b'DI="t"', b'DI="19.5"')
    r = wl.label_bytes(no_vars)
    assert r.status == wl.LABELED
    assert b"[001\r\nLABEL=" in r.new_data
    assert wl.unlabel_bytes(r.new_data).new_data == no_vars


def test_verify_catches_a_lost_line():
    r = labeled(PARTS[0])
    with pytest.raises(wl.LabelError):
        wl.verify(PARTS[0].read_bytes(), r.new_data.replace(b'XA="42"\r\n', b"", 1), r.info.label, r.drawing)


def test_verify_catches_a_changed_value():
    r = labeled(PARTS[0])
    bad = r.new_data.replace(b'TI="IF dt THEN 20 ELSE 12"', b'TI="IF dt THEN 21 ELSE 12"', 1)
    with pytest.raises(wl.LabelError):
        wl.verify(PARTS[0].read_bytes(), bad, r.info.label, r.drawing)


def test_verify_catches_an_extra_line():
    r = labeled(PARTS[0])
    with pytest.raises(wl.LabelError):
        wl.verify(PARTS[0].read_bytes(), r.new_data.replace(b"!\r\n", b'KM="x"\r\n!\r\n'), r.info.label, r.drawing)


def test_old_comment_block_resaved_by_woodwop_is_still_removed():
    """woodWOP may add fields to the first version's comment block when it re-saves the file."""
    path = PARTS[0]
    v1 = (FIXTURES / "v1_comment_label_CEP_U12.mpr").read_bytes()
    km = b'KM="SIZE 95-1/4 x 12 x 3/4 IN | JOB 1071128 | PART CEP U12 X1"'
    resaved = v1.replace(b"<101 \\Kommentar\\\r\n" + km + b"\r\n",
                         b"<101 \\Kommentar\\\r\n" + km + b'\r\nKAT="Kommentar"\r\nMNM="Comment"\r\n')
    assert resaved != v1
    assert wl.label_bytes(resaved).new_data == labeled(path).new_data
    assert wl.unlabel_bytes(resaved).new_data == path.read_bytes()


# ---------------------------------------------------------------- text and font


@pytest.mark.parametrize(
    "mm, text",
    [(2419.35, "95 1/4"), (304.8, "12"), (19.5, "3/4"), (19.05, "3/4"), (2438.4, "96"),
     (3.175, "1/8"), (1.5875, "1/16"), (0.7, "0"), (609.6 - 0.5, "24"), (31.75, "1 1/4"), (365.125, "14 3/8")],
)
def test_inches_text(mm, text):
    assert wl.inches_text(mm) == text


def test_every_glyph_is_one_open_line():
    for char, glyph in sf.GLYPHS.items():
        assert len(glyph) >= 2 and glyph[0] != glyph[-1], char
        assert all(0 <= x <= 4 and 0 <= y <= 6 for x, y in glyph), char


def test_layout_fits_and_rotates():
    wide = sf.layout(2419.35, 304.8, "95 1/4 x 12")
    xs, ys = [x for p in wide for x, _ in p], [y for p in wide for _, y in p]
    assert max(xs) - min(xs) < 2419.35 * 0.85 and max(ys) - min(ys) == pytest.approx(50, abs=0.01)
    tall = sf.layout(304.8, 2419.35, "12 x 95 1/4")
    xs, ys = [x for p in tall for x, _ in p], [y for p in tall for _, y in p]
    assert max(ys) - min(ys) > max(xs) - min(xs)  # runs along Y


@pytest.mark.parametrize("length, width", [(2419.35, 304.8), (762, 304.8), (600, 50), (300, 76.2), (100, 60), (60, 40)])
def test_text_gets_smaller_on_smaller_parts_and_stays_inside(length, width):
    info = wl.PartInfo(length, width)
    d = info.drawing
    assert d, "should fit"
    ys = [y for p in d for _, y in p]
    height = max(ys) - min(ys)
    assert 3.0 <= height <= min(50.0, width * 0.3) + 0.01
    for poly in d:
        assert all(0 < x < length and 0 < y < width for x, y in poly)


def test_tiny_part_gets_size_in_variables_only():
    assert sf.layout(20, 8, "13/16 x 5/16") == []
    old = PARTS[0].read_bytes()
    small = (old.replace(b"_BSX=2419.350000", b"_BSX=20.000000").replace(b"_BSY=304.800000", b"_BSY=8.000000")
             .replace(b'l="2419.35"', b'l="20"').replace(b'w="304.8"', b'w="8"'))
    r = wl.label_bytes(small)
    assert r.status == wl.LABELED and r.drawing == [] and "too small" in r.message
    assert b'LABEL="0"\r\nKM="SIZE 13/16 x 5/16"' in r.new_data and wl.unlabel_bytes(r.new_data).new_data == small


# ---------------------------------------------------------------- saving


def test_save_in_place_with_backup(tmp_path):
    work = tmp_path / "job"
    work.mkdir()
    for p in SAMPLES.iterdir():
        (work / p.name).write_bytes(p.read_bytes())
    backups = tmp_path / "backups"

    assert wl.main([str(work), "--backup-dir", str(backups)]) == 0
    for p in PARTS:
        assert (work / p.name).read_bytes() == labeled(p).new_data
    assert (work / BOARD.name).read_bytes() == BOARD.read_bytes()
    assert sorted(f.name for f in work.iterdir()) == sorted(p.name for p in SAMPLES.iterdir())  # no temp files left
    (run,) = backups.iterdir()
    assert sorted(f.name for f in run.iterdir()) == sorted(p.name for p in PARTS)
    for p in PARTS:
        assert (run / p.name).read_bytes() == p.read_bytes()

    # remove puts every file back exactly
    assert wl.main([str(work), "--remove", "--backup-dir", str(backups)]) == 0
    for p in SAMPLES.iterdir():
        assert (work / p.name).read_bytes() == p.read_bytes()


def test_save_refuses_if_file_changed(tmp_path):
    f = tmp_path / PARTS[0].name
    f.write_bytes(PARTS[0].read_bytes())
    r = wl.plan(f)
    f.write_bytes(PARTS[0].read_bytes() + b" ")
    with pytest.raises(wl.LabelError):
        wl.save(f, r, tmp_path / "b")
    assert f.read_bytes() == PARTS[0].read_bytes() + b" "
