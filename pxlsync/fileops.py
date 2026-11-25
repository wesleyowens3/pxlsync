"""Safe, undoable file moves used by RENAME and ORGANIZE."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Move:
    src: Path
    dst: Path
    problem: str | None = None  # set when the move must not be applied

    @property
    def changes(self) -> bool:
        return self.src != self.dst


def validate(moves: list[Move]) -> list[Move]:
    """Mark conflicts: two files to one name, or a target that already exists."""
    sources = {m.src.resolve() for m in moves}
    seen: dict[str, Move] = {}
    for m in moves:
        if not m.changes:
            continue
        key = os.path.normcase(str(m.dst.resolve()))
        if key in seen:
            m.problem = m.problem or f"same new name as {seen[key].src.name}"
            continue
        seen[key] = m
        # Case-only renames (a.mov -> A.mov) are fine on case-insensitive disks.
        same_file = os.path.normcase(str(m.src.resolve())) == key
        if m.dst.exists() and not same_file and m.dst.resolve() not in sources:
            m.problem = m.problem or "a file with that name already exists"
    return moves


def apply(moves: list[Move], journal_dir: Path) -> Path | None:
    """Apply moves that changed and have no problem. Returns the undo journal path.

    Uses a two-step rename through temporary names so swaps and chains
    (a->b, b->a) work, and writes the journal before touching any file.
    """
    todo = [m for m in validate(moves) if m.changes and not m.problem]
    if not todo:
        return None
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = journal_dir / f"moves-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.json"
    journal.write_text(
        json.dumps([{"src": str(m.src), "dst": str(m.dst)} for m in todo], indent=2),
        encoding="utf-8",
    )
    staged: list[tuple[Path, Move]] = []
    for m in todo:
        tmp = m.src.with_name(f".pxlsync-tmp-{uuid.uuid4().hex[:8]}{m.src.suffix}")
        os.rename(m.src, tmp)
        staged.append((tmp, m))
    finished = 0
    try:
        for tmp, m in staged:
            m.dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(tmp, m.dst)
            finished += 1
    except OSError:
        # Roll back: put every file back where it started, then re-raise.
        for tmp, m in staged[finished:]:
            os.rename(tmp, m.src)
        for _tmp, m in reversed(staged[:finished]):
            os.rename(m.dst, m.src)
        journal.unlink(missing_ok=True)
        raise
    return journal


def undo(journal: Path) -> int:
    entries = json.loads(journal.read_text(encoding="utf-8"))
    moves = [Move(Path(e["dst"]), Path(e["src"])) for e in entries if Path(e["dst"]).exists()]
    moves = validate(moves)
    blocked = [m for m in moves if m.problem]
    if blocked:
        raise RuntimeError(f"cannot undo, {blocked[0].dst.name}: {blocked[0].problem}")
    apply(moves, journal.parent / "undone")
    journal.rename(journal.with_suffix(".undone.json"))
    _prune_empty_dirs([Path(e["dst"]).parent for e in entries])
    return len(moves)


def _prune_empty_dirs(dirs: list[Path]) -> None:
    for d in sorted(set(dirs), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()  # only succeeds when empty
        except OSError:
            pass


def latest_journal(journal_dir: Path) -> Path | None:
    files = sorted(p for p in journal_dir.glob("moves-*.json") if not p.name.endswith(".undone.json"))
    return files[-1] if files else None
