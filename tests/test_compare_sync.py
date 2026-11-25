import os
import time
from pathlib import Path

import pytest

from pxlsync import syncer
from pxlsync.compare import Action, Category, Method, Mode, compare, hold, plan


def make(root: Path, rel: str, data: str, age: float = 0):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


@pytest.fixture
def pair(tmp_path):
    L, R = tmp_path / "L", tmp_path / "R"
    make(L, "same.mov", "aaa", age=100); make(R, "same.mov", "aaa", age=100)
    make(L, "new/only_left.mov", "new")
    make(R, "only_right.mov", "old")
    make(L, "edited.mov", "v2", age=10); make(R, "edited.mov", "v1", age=500)
    make(L, "stale.mov", "x", age=500); make(R, "stale.mov", "yy", age=10)
    make(L, "Case.MOV", "c", age=100); make(R, "case.mov", "c", age=100)
    make(L, ".DS_Store", "junk"); make(L, "._edited.mov", "junk")
    make(L, "skip/me.tmp", "t")
    return L, R


def cats(cmp):
    return {r.rel: r.category for r in cmp.rows}


def test_categories_and_excludes(pair):
    L, R = pair
    cmp = compare(L, R, excludes=["*.tmp"])
    assert cats(cmp) == {
        "Case.MOV": Category.EQUAL,  # matched ignoring case
        "edited.mov": Category.LEFT_NEWER,
        "new/only_left.mov": Category.LEFT_ONLY,
        "only_right.mov": Category.RIGHT_ONLY,
        "same.mov": Category.EQUAL,
        "stale.mov": Category.RIGHT_NEWER,
    }


def test_content_method(pair):
    L, R = pair
    make(R, "same.mov", "aab", age=100)  # same size + time, different bytes
    assert cats(compare(L, R))["same.mov"] == Category.EQUAL
    assert cats(compare(L, R, Method.CONTENT))["same.mov"] == Category.DIFFERENT


def test_modes(pair):
    L, R = pair
    cmp = compare(L, R)
    acts = lambda: {r.rel: r.action for r in cmp.rows}  # noqa: E731
    plan(cmp.rows, Mode.MIRROR)
    assert acts()["only_right.mov"] == Action.DELETE_RIGHT and acts()["stale.mov"] == Action.TO_RIGHT
    plan(cmp.rows, Mode.UPDATE)
    assert acts()["only_right.mov"] == Action.NONE and acts()["stale.mov"] == Action.NONE
    assert next(r for r in cmp.rows if r.rel == "stale.mov").conflict
    plan(cmp.rows, Mode.TWO_WAY)
    assert acts()["only_right.mov"] == Action.TO_LEFT and acts()["stale.mov"] == Action.TO_LEFT


def test_mirror_sync_end_to_end(pair):
    L, R = pair
    cmp = compare(L, R, excludes=["*.tmp"])
    plan(cmp.rows, Mode.MIRROR)
    assert hold(cmp.rows, {"edited.mov"}, "codec check failed") == 1
    stats = syncer.run(cmp, prune_empty_right_dirs=True)
    assert not stats.errors and stats.copied == 2 and stats.deleted == 1
    assert (R / "new/only_left.mov").read_text() == "new"
    assert (R / "stale.mov").read_text() == "x"
    assert (R / "edited.mov").read_text() == "v1"  # held back
    assert not (R / "only_right.mov").exists()
    trash = list((R / syncer.TRASH_DIR).rglob("*.mov"))
    assert sorted(p.name for p in trash) == ["only_right.mov", "stale.mov"]  # deleted + overwritten kept
    # Times copied too, so comparing again finds nothing left to do (except the held file).
    again = compare(L, R, excludes=["*.tmp"])
    plan(again.rows, Mode.MIRROR)
    assert [r.rel for r in again.rows if r.action != Action.NONE] == ["edited.mov"]
    assert not list(R.rglob("*.pxlsync-part"))


def test_cancel_leaves_no_partial_file(tmp_path):
    L, R = tmp_path / "L", tmp_path / "R"
    (L).mkdir(); (R).mkdir()
    (L / "big.mov").write_bytes(os.urandom(syncer.CHUNK * 2 + 10))
    cmp = compare(L, R)
    plan(cmp.rows, Mode.MIRROR)
    calls = {"n": 0}

    def cancel_after_first_chunk():
        calls["n"] += 1
        return calls["n"] > 1

    stats = syncer.run(cmp, cancelled=cancel_after_first_chunk)
    assert stats.cancelled and not list(R.iterdir())
