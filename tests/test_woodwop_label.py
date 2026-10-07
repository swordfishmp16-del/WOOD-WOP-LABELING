import difflib
from pathlib import Path

import pytest

import woodwop_label as wl

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PARTS = sorted(p for p in SAMPLES.iterdir() if "BOARD" not in p.name)
BOARD = next(SAMPLES.glob("*BOARD*"))


def lines_of(data: bytes) -> list[str]:
    return wl.split_lines(data)[0]


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_label_only_inserts_label_lines(path):
    old = path.read_bytes()
    r = wl.label_bytes(old, path.name)
    assert r.status == wl.LABELED
    km = f'KM="{r.info.label}"'

    # Build the expected file by hand: the original lines with the label put in.
    expected = lines_of(old)
    first_op = next(i for i, x in enumerate(expected) if x.startswith("<102 "))
    expected[first_op:first_op] = [wl.COMMENT_HEADER, km, ""]
    end_of_vars = expected.index("]1") - 1  # the blank line closing [001
    expected[end_of_vars:end_of_vars] = [wl.LABEL_VAR_LINE, km]
    assert lines_of(r.new_data) == expected

    # and a plain diff sees nothing but added lines
    ops = difflib.SequenceMatcher(a=lines_of(old), b=lines_of(r.new_data), autojunk=False).get_opcodes()
    assert {op[0] for op in ops} == {"equal", "insert"}
    assert sum(j2 - j1 for tag, _, _, j1, j2 in ops if tag == "insert") == 5


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_label_placement(path):
    new = lines_of(wl.label_bytes(path.read_bytes(), path.name).new_data)
    # LABEL is the last entry of the variable list, right before its closing blank line
    i = new.index(wl.LABEL_VAR_LINE)
    assert new[i - 1].startswith("KM=") and new[i + 2] == "" and new[i + 3] == "]1"
    # comment sits between the workpiece block and the first operation
    c = new.index(wl.COMMENT_HEADER)
    assert new[c - 2] == 'AY="0"' and new[c - 1] == "" and new[c + 3].startswith("<102 ")


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_keeps_crlf_and_trailing_bytes(path):
    old = path.read_bytes()
    new = wl.label_bytes(old, path.name).new_data
    assert new.count(b"\r\n") == old.count(b"\r\n") + 5
    assert b"\n" not in new.replace(b"\r\n", b"")
    assert new.endswith(old[-20:])
    assert new.startswith(old[: old.index(b"[001")])  # header untouched


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_remove_restores_exact_original(path):
    old = path.read_bytes()
    labeled = wl.label_bytes(old, path.name).new_data
    r = wl.unlabel_bytes(labeled)
    assert r.status == wl.REMOVED and r.new_data == old


@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_labeling_twice_changes_nothing(path):
    labeled = wl.label_bytes(path.read_bytes(), path.name).new_data
    assert wl.label_bytes(labeled, path.name).status == wl.ALREADY


def test_relabel_replaces_old_label():
    path = PARTS[0]
    old = path.read_bytes()
    labeled = wl.label_bytes(old, "OTHER_9999999_X_FS_U1.mpr").new_data
    r = wl.label_bytes(labeled, path.name)
    assert r.status == wl.LABELED and r.message == "label updated"
    assert r.new_data == wl.label_bytes(old, path.name).new_data


def test_sheet_file_left_alone():
    assert wl.label_bytes(BOARD.read_bytes(), BOARD.name).status == wl.SHEET


def test_original_files_untouched_by_planning():
    before = {p: p.read_bytes() for p in SAMPLES.iterdir()}
    for p in PARTS:
        wl.plan(p)
    assert {p: p.read_bytes() for p in SAMPLES.iterdir()} == before


def test_size_and_label_text():
    r = wl.label_bytes(PARTS[0].read_bytes(), PARTS[0].name)
    assert (r.info.length_mm, r.info.width_mm, r.info.thickness_mm) == (2419.35, 304.8, 19.5)
    assert r.info.label == "SIZE 95-1/4 x 12 x 3/4 IN | JOB 1071128 | PART CEP U12 X1"


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


def test_stale_header_is_refused():
    old = PARTS[0].read_bytes().replace(b"_BSX=2419.350000", b"_BSX=2400.000000")
    r = wl.label_bytes(old, PARTS[0].name)
    assert r.status == wl.PROBLEM and "doesn't match" in r.message


def test_foreign_label_variable_is_refused():
    old = PARTS[0].read_bytes().replace(b'bfb="57"\r\n', b'bfb="57"\r\nLABEL="0"\r\nKM="mine"\r\n')
    assert wl.label_bytes(old, PARTS[0].name).status == wl.PROBLEM


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


def test_save_in_place_with_backup(tmp_path):
    work = tmp_path / "job"
    work.mkdir()
    for p in SAMPLES.iterdir():
        (work / p.name).write_bytes(p.read_bytes())
    backups = tmp_path / "backups"

    assert wl.main([str(work), "--backup-dir", str(backups)]) == 0
    for p in PARTS:
        assert (work / p.name).read_bytes() == wl.label_bytes(p.read_bytes(), p.name).new_data
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


def test_own_label_variable_with_other_value_is_refused():
    old = PARTS[0].read_bytes().replace(b'bfb="57"\r\n', b'bfb="57"\r\nlabel="5"\r\nKM="theirs"\r\n')
    r = wl.label_bytes(old, PARTS[0].name)
    assert r.status == wl.PROBLEM and "LABEL" in r.message


def test_verify_catches_a_lost_line():
    path = PARTS[0]
    old = path.read_bytes()
    r = wl.label_bytes(old, path.name)
    damaged = r.new_data.replace(b'XA="42"\r\n', b"", 1)
    with pytest.raises(wl.LabelError):
        wl.verify(old, damaged, r.info.label)


def test_verify_catches_a_changed_value():
    path = PARTS[0]
    old = path.read_bytes()
    r = wl.label_bytes(old, path.name)
    damaged = r.new_data.replace(b'TI="IF dt THEN 20 ELSE 12"', b'TI="IF dt THEN 21 ELSE 12"', 1)
    with pytest.raises(wl.LabelError):
        wl.verify(old, damaged, r.info.label)


def test_verify_catches_an_extra_line():
    path = PARTS[0]
    old = path.read_bytes()
    r = wl.label_bytes(old, path.name)
    with pytest.raises(wl.LabelError):
        wl.verify(old, r.new_data.replace(b"!\r\n", b'KM="x"\r\n!\r\n'), r.info.label)


def test_label_survives_woodwop_resaving_comment_block():
    """woodWOP may add fields to the comment block when it re-saves the file."""
    path = PARTS[0]
    labeled = wl.label_bytes(path.read_bytes(), path.name).new_data
    km = f'KM="{wl.label_bytes(path.read_bytes(), path.name).info.label}"'.encode()
    resaved = labeled.replace(b"<101 \\Kommentar\\\r\n" + km + b"\r\n",
                              b"<101 \\Kommentar\\\r\n" + km + b'\r\nKAT="Kommentar"\r\nMNM="Comment"\r\n')
    assert resaved != labeled
    assert wl.label_bytes(resaved, path.name).status in (wl.LABELED, wl.ALREADY)
    assert wl.unlabel_bytes(resaved).new_data == path.read_bytes()
