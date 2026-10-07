import difflib
from pathlib import Path

import pytest

import stroke_font as sf
import woodwop_label as wl

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PARTS = sorted(p for p in SAMPLES.iterdir() if "BOARD" not in p.name)
BOARD = next(SAMPLES.glob("*BOARD*"))


def lines_of(data: bytes) -> list[str]:
    return wl.split_lines(data)[0]


def labeled(path: Path) -> wl.Result:
    return wl.label_bytes(path.read_bytes(), path.name)


# ---------------------------------------------------------------- what gets written


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_label_only_inserts_label_lines(path):
    old = path.read_bytes()
    r = wl.label_bytes(old, path.name)
    assert r.status == wl.LABELED
    km = f'KM="{r.info.label}"'
    assert len(r.drawing) == 31

    # Build the expected file by hand: the original lines with the label put in.
    expected = lines_of(old)
    first_op = next(i for i, x in enumerate(expected) if x.startswith("<102 "))
    expected[first_op:first_op] = [wl.COMMENT_HEADER, km, ""]
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
    assert sum(j2 - j1 for tag, _, _, j1, j2 in ops if tag == "insert") == 5 + len(drawn)


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
    # comment sits between the workpiece block and the first operation
    c = new.index(wl.COMMENT_HEADER)
    assert new[c - 2] == 'AY="0"' and new[c - 1] == "" and new[c + 3].startswith("<102 ")


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_keeps_crlf_header_and_ending(path):
    old = path.read_bytes()
    new = labeled(path).new_data
    added = len(lines_of(new)) - len(lines_of(old))
    assert new.count(b"\r\n") == old.count(b"\r\n") + added
    assert b"\n" not in new.replace(b"\r\n", b"")
    assert new.endswith(old[old.index(b"<102 ") :])  # every operation byte-identical, in place
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
    assert sorted(x for x in new if x.startswith("<")) == sorted([x for x in old if x.startswith("<")] + [wl.COMMENT_HEADER])


def test_verify_catches_a_router_on_the_label():
    path = PARTS[0]
    old = path.read_bytes()
    r = wl.label_bytes(old, path.name)
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
    assert wl.label_bytes(BOARD.read_bytes(), BOARD.name).status == wl.SHEET


def fake_labeled_sheet() -> bytes:
    """The real board with one drawn label contour (]4) and LABEL_0 added, as woodNest might produce."""
    data = BOARD.read_bytes()
    contour = "\r\n".join(wl.contour_lines(4, [(100.0, 100.0), (150.0, 100.0), (150.0, 140.0)])).encode()
    data = data.replace(b"<100 \\WerkStck\\", contour + b"\r\n<100 \\WerkStck\\", 1)
    return data.replace(b'bfb_0="57"\r\n', b'bfb_0="57"\r\nLABEL_0="1"\r\nKM="SIZE 1 x 2 x 3 IN | JOB 1 | PART X"\r\n')


def test_sheet_check_labeled_board_ok():
    r = wl.label_bytes(fake_labeled_sheet(), BOARD.name)
    assert r.status == wl.SHEET and r.message.startswith("sheet OK: 1 labeled part(s), 1 label lines")


def test_sheet_check_flags_router_on_label():
    bad = fake_labeled_sheet().replace(b'EA="3:0"', b'EA="4:0"')
    r = wl.label_bytes(bad, BOARD.name)
    assert r.status == wl.PROBLEM and r.message.startswith("DANGER - DO NOT RUN")
    assert "label contour 4" in r.message


def test_sheet_check_flags_missing_contour():
    bad = BOARD.read_bytes().replace(b'EA="3:0"', b'EA="9:0"')
    assert "missing" in wl.check_sheet(lines_of(bad)).dangers[0]


# ---------------------------------------------------------------- round trips and refusals


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_remove_restores_exact_original(path):
    old = path.read_bytes()
    r = wl.unlabel_bytes(wl.label_bytes(old, path.name).new_data)
    assert r.status == wl.REMOVED and r.new_data == old


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_labeling_twice_changes_nothing(path):
    assert wl.label_bytes(labeled(path).new_data, path.name).status == wl.ALREADY


def test_relabel_replaces_old_label():
    path = PARTS[0]
    old = path.read_bytes()
    first = wl.label_bytes(old, "OTHER_9999999_X_FS_U1.mpr").new_data
    r = wl.label_bytes(first, path.name)
    assert r.status == wl.LABELED and r.message == "label updated"
    assert r.new_data == wl.label_bytes(old, path.name).new_data


def test_upgrades_first_test_version_label():
    """Files labeled by the first test version (LABEL="0", no drawing) get the drawing added."""
    path = PARTS[0]
    old = path.read_bytes()
    km = b'KM="SIZE 95-1/4 x 12 x 3/4 IN | JOB 1071128 | PART CEP U12 X1"'
    v1 = old.replace(b'KM="bore from back"\r\n', b'KM="bore from back"\r\nLABEL="0"\r\n' + km + b"\r\n", 1)
    v1 = v1.replace(b'AY="0"\r\n\r\n', b'AY="0"\r\n\r\n<101 \\Kommentar\\\r\n' + km + b"\r\n\r\n", 1)
    r = wl.label_bytes(v1, path.name)
    assert r.status == wl.LABELED and r.message == "label updated"
    assert r.new_data == wl.label_bytes(old, path.name).new_data
    assert wl.unlabel_bytes(v1).new_data == old


def test_original_files_untouched_by_planning():
    before = {p: p.read_bytes() for p in SAMPLES.iterdir()}
    for p in PARTS:
        wl.plan(p)
    assert {p: p.read_bytes() for p in SAMPLES.iterdir()} == before


def test_size_and_label_text():
    r = labeled(PARTS[0])
    assert (r.info.length_mm, r.info.width_mm, r.info.thickness_mm) == (2419.35, 304.8, 19.5)
    assert r.info.label == "SIZE 95-1/4 x 12 x 3/4 IN | JOB 1071128 | PART CEP U12 X1"


def test_stale_header_is_refused():
    old = PARTS[0].read_bytes().replace(b"_BSX=2419.350000", b"_BSX=2400.000000")
    r = wl.label_bytes(old, PARTS[0].name)
    assert r.status == wl.PROBLEM and "doesn't match" in r.message


def test_foreign_label_variable_is_refused():
    old = PARTS[0].read_bytes().replace(b'bfb="57"\r\n', b'bfb="57"\r\nLABEL="0"\r\nKM="mine"\r\n')
    assert wl.label_bytes(old, PARTS[0].name).status == wl.PROBLEM


def test_own_label_variable_with_other_value_is_refused():
    old = PARTS[0].read_bytes().replace(b'bfb="57"\r\n', b'bfb="57"\r\nlabel="5"\r\nKM="theirs"\r\n')
    r = wl.label_bytes(old, PARTS[0].name)
    assert r.status == wl.PROBLEM and "LABEL" in r.message


def test_not_a_woodwop_file():
    assert wl.label_bytes(b"hello\r\n", "x.mpr").status == wl.PROBLEM


def test_file_without_variable_list_round_trips():
    old = PARTS[0].read_bytes()
    start, end = old.index(b"[001"), old.index(b"]1")
    no_vars = old[:start] + old[end:]
    no_vars = no_vars.replace(b'LA="l"', b'LA="2419.35"').replace(b'BR="w"', b'BR="304.8"').replace(b'DI="t"', b'DI="19.5"')
    r = wl.label_bytes(no_vars, PARTS[0].name)
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


def test_label_survives_woodwop_resaving_comment_block():
    """woodWOP may add fields to the comment block when it re-saves the file."""
    path = PARTS[0]
    r = labeled(path)
    km = f'KM="{r.info.label}"'.encode()
    resaved = r.new_data.replace(b"<101 \\Kommentar\\\r\n" + km + b"\r\n",
                                 b"<101 \\Kommentar\\\r\n" + km + b'\r\nKAT="Kommentar"\r\nMNM="Comment"\r\n')
    assert resaved != r.new_data
    assert wl.label_bytes(resaved, path.name).status in (wl.LABELED, wl.ALREADY)
    assert wl.unlabel_bytes(resaved).new_data == path.read_bytes()


# ---------------------------------------------------------------- text and font


@pytest.mark.parametrize(
    "mm, text",
    [(2419.35, "95-1/4"), (304.8, "12"), (19.5, "3/4"), (19.05, "3/4"), (2438.4, "96"),
     (3.175, "1/8"), (1.5875, "1/16"), (0.7, "0"), (609.6 - 0.5, "24"), (31.75, "1-1/4"), (365.125, "14-3/8")],
)
def test_inches_text(mm, text):
    assert wl.inches_text(mm) == text


@pytest.mark.parametrize(
    "stem, job, part",
    [
        ("WHE_TABBY_1071128_ROSENBAUM_SHARI_L_CEP_U12_X1", "1071128", "CEP U12 X1"),
        ("WHE_TABBY_1071128_ROSENBAUM_SHARI_L_REP_U13_X1", "1071128", "REP U13 X1"),
        ("WHEAT_TAB_1071128_BOARD_2", "1071128", "BOARD 2"),
        ("WHITE_1234567_SMITH_J_FS_U3", "1234567", "FS U3"),
        ("no_job_here", None, "no job here"),
        ('ODD"NAME_12345_A_B', "12345", "A B"),
    ],
)
def test_parse_file_name(stem, job, part):
    assert wl.parse_file_name(stem) == (job, part)


def test_every_glyph_is_one_open_line():
    for char, glyph in sf.GLYPHS.items():
        assert len(glyph) >= 2 and glyph[0] != glyph[-1], char
        assert all(0 <= x <= 4 and 0 <= y <= 6 for x, y in glyph), char


def test_layout_fits_and_rotates():
    wide = sf.layout(2419.35, 304.8, "95-1/4 x 12 x 3/4", "JOB 1071128 CEP U12 X1")
    xs = [x for p in wide for x, _ in p]
    assert max(xs) - min(xs) < 2419.35 * 0.85
    tall = sf.layout(304.8, 2419.35, "12 x 95-1/4 x 3/4", "JOB 1071128 CEP U12 X1")
    ys = [y for p in tall for _, y in p]
    assert max(ys) - min(ys) > max(x for p in tall for x, _ in p) - min(x for p in tall for x, _ in p)  # runs along Y
    assert sf.layout(60, 40, "2-3/8 x 1-9/16 x 3/4", "JOB 1 X") == []  # too small: hidden label only


def test_small_part_gets_hidden_label_only():
    old = PARTS[0].read_bytes()
    small = (old.replace(b"_BSX=2419.350000", b"_BSX=60.000000").replace(b"_BSY=304.800000", b"_BSY=40.000000")
             .replace(b'l="2419.35"', b'l="60"').replace(b'w="304.8"', b'w="40"'))
    r = wl.label_bytes(small, PARTS[0].name)
    assert r.status == wl.LABELED and r.drawing == [] and "too small" in r.message
    assert b'LABEL="0"' in r.new_data and wl.unlabel_bytes(r.new_data).new_data == small


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
