from pathlib import Path

import pytest

from pxlsync import fileops
from pxlsync.codecs import BUILTIN_VIDEO
from pxlsync.organize import OrganizeRule, plan_organize
from pxlsync.rename import existing_names, plan_renames
from pxlsync.tokens import TemplateError, safe_component


def touch(d: Path, *names):
    for n in names:
        p = d / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(n)
    return [d / n for n in names]


def test_versions_continue_from_destination(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    files = touch(src, "shot010_v002.mov", "shot020.mov")
    touch(dst, "PXL_shot010_v001.mov", "PXL_shot010_v004.mov")
    moves = plan_renames(files, "{project}_{name}_v{v:3}", {"project": "PXL"},
                         known_names=existing_names(src, dst))
    assert [m.dst.name for m in moves] == ["PXL_shot010_v005.mov", "PXL_shot020_v001.mov"]


def test_already_named_files_are_left_alone(tmp_path):
    files = touch(tmp_path, "PXL_shot010_v003.mov")
    moves = plan_renames(files, "PXL_{name}_v{v}", {})
    assert not moves[0].changes


def test_conflicts_and_bad_tokens(tmp_path):
    files = touch(tmp_path, "a.mov", "b.mov", "taken.mov")
    moves = plan_renames(files[:2], "taken", {})
    assert all(m.problem for m in moves)
    with pytest.raises(TemplateError):
        plan_renames(files, "{nope}", {})


def test_safe_names_for_windows():
    assert safe_component('cut: "final"?') == "cut_ _final__"
    assert safe_component("CON") == "_CON"


def test_apply_and_undo_including_swap(tmp_path):
    a, b = touch(tmp_path, "a.mov", "b.mov")
    moves = [fileops.Move(a, b), fileops.Move(b, a)]
    journal = fileops.apply(moves, tmp_path / ".journal")
    assert a.read_text() == "b.mov" and b.read_text() == "a.mov"
    assert fileops.undo(journal) == 2
    assert a.read_text() == "a.mov" and b.read_text() == "b.mov"


def test_organize_rules(tmp_path, media_dir):
    from pxlsync.probe import find_ffprobe, probe
    import shutil
    for n in ("prores_hq.mov", "h264.mp4", "vo.wav"):
        shutil.copy(media_dir / n, tmp_path / n)
    files = sorted(p for p in tmp_path.iterdir())
    infos = {p: probe(p, find_ffprobe()) for p in files}
    rules = [
        OrganizeRule("*.mov;*.mp4", "MASTERS/{codec:upper}", codec="Apple ProRes (any)"),
        OrganizeRule("*.mov;*.mp4", "REVIEW"),
        OrganizeRule("*.wav", "AUDIO/{client}"),
    ]
    moves = plan_organize(files, tmp_path, rules, {"client": "acme"}, infos, BUILTIN_VIDEO)
    got = {m.src.name: m.dst.relative_to(tmp_path).as_posix() for m in moves}
    assert got == {
        "h264.mp4": "REVIEW/h264.mp4",
        "prores_hq.mov": "MASTERS/PRORES/prores_hq.mov",
        "vo.wav": "AUDIO/acme/vo.wav",
    }
    fileops.apply(moves, tmp_path / ".journal")
    assert (tmp_path / "MASTERS/PRORES/prores_hq.mov").is_file()
