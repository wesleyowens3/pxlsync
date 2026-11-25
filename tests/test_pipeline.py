import os
import time
from pathlib import Path

from pxlsync import fileops, syncer
from pxlsync.compare import Action, Category, Mode, compare, hold, plan
from pxlsync.organize import OrganizeRule
from pxlsync.pipeline import names_for_root, plan_source_moves
from pxlsync.rename import existing_names


def make(root: Path, rel: str, data: str = "x", age: float = 0):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_compare_shows_result_after_rename_and_organize(tmp_path):
    L, R = tmp_path / "L", tmp_path / "R"
    files = [make(L, "A001/shot010_v002.mov", "a"), make(L, "vo.wav", "b"), make(L, "bad.mov", "c")]
    make(R, "PXL_shot010_v003.mov", "old", age=50)
    root_of = lambda p: L  # noqa: E731
    moves = plan_source_moves(
        files, root_of, {}, {"project": "PXL"},
        template="{project}_{name}_v{v:3}", known_names=existing_names(L, R),
        rules=[OrganizeRule("*.mov", "VIDEO"), OrganizeRule("*.wav", "AUDIO")],
    )
    got = {m.src.relative_to(L).as_posix(): m.dst.relative_to(L).as_posix() for m in moves}
    assert got == {
        "A001/shot010_v002.mov": "VIDEO/PXL_shot010_v004.mov",
        "vo.wav": "AUDIO/PXL_vo_v001.wav",
        "bad.mov": "VIDEO/PXL_bad_v001.mov",
    }

    cmp = compare(L, R, left_names=names_for_root(moves, L))
    rows = {r.rel: r for r in cmp.rows}
    assert rows["VIDEO/PXL_shot010_v004.mov"].left.orig == "A001/shot010_v002.mov"
    assert rows["VIDEO/PXL_shot010_v004.mov"].category == Category.LEFT_ONLY
    plan(cmp.rows, Mode.MIRROR)
    assert hold(cmp.rows, {"bad.mov"}, "codec") == 1  # held by its current name
    assert rows["PXL_shot010_v003.mov"].action == Action.DELETE_RIGHT

    # Sync = apply the source moves (except held files), then copy.
    fileops.apply([m for m in moves if m.src.name != "bad.mov"], tmp_path / "journal")
    stats = syncer.run(cmp)
    assert not stats.errors and stats.copied == 2
    assert (R / "VIDEO/PXL_shot010_v004.mov").read_text() == "a"
    assert (L / "bad.mov").exists() and not (R / "VIDEO/PXL_bad_v001.mov").exists()

    # Comparing again with a fresh plan is stable: renamed files keep their names.
    files2 = [p for p in L.rglob("*") if p.is_file()]
    moves2 = plan_source_moves(files2, root_of, {}, {"project": "PXL"}, template="{project}_{name}_v{v:3}",
                               known_names=existing_names(L, R),
                               rules=[OrganizeRule("*.mov", "VIDEO"), OrganizeRule("*.wav", "AUDIO")])
    changed = sorted(m.src.name for m in moves2 if m.changes)
    assert changed == ["bad.mov"]


def test_organize_by_file_date_is_stable_and_survives_rename(tmp_path):
    L = tmp_path / "L"
    f = make(L, "clip.mov")
    old = time.mktime((2024, 3, 9, 12, 0, 0, 0, 0, -1))
    os.utime(f, (old, old))
    rules = [OrganizeRule("*.mov", "{mdate:%Y-%m-%d}/VIDEO")]
    moves = plan_source_moves([f], lambda p: L, {}, {"project": "P"}, template="{project}_{name}_v{v}", rules=rules)
    assert moves[0].dst == L / "2024-03-09/VIDEO/P_clip_v001.mov"  # file's date, not today, even while renaming
    fileops.apply(moves, tmp_path / "j")
    again = plan_source_moves([moves[0].dst], lambda p: L, {}, {"project": "P"}, template="{project}_{name}_v{v}",
                              rules=rules)
    assert not again[0].changes
