"""Carry out the actions from a Comparison.

Safety rules:
- New data is written to ``name.pxlsync-part`` first and only renamed into place
  once fully copied, so a cancelled or failed copy never leaves a half file
  under the real name.
- Files that get deleted or overwritten are handed to ``remove`` (by default
  moved into a dated ``.pxlsync-trash`` folder on the same side), never just erased.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .compare import Action, Cancelled, Comparison, Row

CHUNK = 8 << 20
TRASH_DIR = ".pxlsync-trash"


@dataclass
class SyncStats:
    copied: int = 0
    deleted: int = 0
    bytes: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    done_rows: list[Row] = field(default_factory=list)


class TrashFolder:
    """Move removed files to <side root>/.pxlsync-trash/<timestamp>/<relative path>."""

    def __init__(self) -> None:
        self.stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    def __call__(self, root: Path, rel: str) -> None:
        target = root / TRASH_DIR / self.stamp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(root / rel, target)


def delete_permanently(root: Path, rel: str) -> None:
    (root / rel).unlink()


def copy_file(src: Path, dst: Path, on_bytes: Callable[[int], None], cancelled: Callable[[], bool],
              remove_existing: Callable[[], None]) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".pxlsync-part")
    try:
        with open(src, "rb") as fin, open(part, "wb") as fout:
            while chunk := fin.read(CHUNK):
                if cancelled():
                    raise Cancelled
                fout.write(chunk)
                on_bytes(len(chunk))
        shutil.copystat(src, part)  # keep modification time so the next compare says "equal"
        if dst.exists():
            remove_existing()
        os.replace(part, dst)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def total_bytes(rows: list[Row]) -> int:
    return sum(r.size for r in rows)


def run(
    comparison: Comparison,
    remove: Callable[[Path, str], None] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    prune_empty_right_dirs: bool = False,
) -> SyncStats:
    remove = remove or TrashFolder()
    stats = SyncStats()
    L, R = comparison.left_root, comparison.right_root
    rows = [r for r in comparison.rows if r.action != Action.NONE]
    total = max(total_bytes(rows), 1)
    say = progress or (lambda _d, _t, _m: None)

    def add_bytes(n: int) -> None:
        stats.bytes += n
        say(stats.bytes, total, current)

    # Deletions first: frees space on the destination before copying.
    deletes = [r for r in rows if r.action in (Action.DELETE_RIGHT, Action.DELETE_LEFT)]
    copies = [r for r in rows if r.action in (Action.TO_RIGHT, Action.TO_LEFT)]
    current = ""
    for row in deletes:
        if cancelled():
            stats.cancelled = True
            return stats
        root, entry = (R, row.right) if row.action == Action.DELETE_RIGHT else (L, row.left)
        current = f"Removing {entry.rel}"
        say(stats.bytes, total, current)
        try:
            remove(root, entry.rel)
            stats.deleted += 1
            stats.done_rows.append(row)
        except OSError as exc:
            stats.errors.append(f"{entry.rel}: {exc.strerror or exc}")

    for row in copies:
        to_right = row.action == Action.TO_RIGHT
        src_root, dst_root = (L, R) if to_right else (R, L)
        src_entry = row.left if to_right else row.right
        # Keep the existing name on the other side (case may differ); otherwise use the source name.
        dst_entry = row.right if to_right else row.left
        rel_dst = dst_entry.rel if dst_entry else src_entry.rel
        current = f"Copying {src_entry.rel}"
        try:
            copy_file(src_root / src_entry.rel, dst_root / rel_dst, add_bytes, cancelled,
                      lambda: remove(dst_root, rel_dst))
            stats.copied += 1
            stats.done_rows.append(row)
        except Cancelled:
            stats.cancelled = True
            return stats
        except OSError as exc:
            stats.errors.append(f"{src_entry.rel}: {exc.strerror or exc}")

    if prune_empty_right_dirs:
        _prune_empty_dirs(R, keep=L)
    return stats


def _prune_empty_dirs(root: Path, keep: Path) -> None:
    """Remove folders on ``root`` that are empty and don't exist under ``keep``."""
    for dirpath, _dirs, _files in os.walk(root, topdown=False):
        d = Path(dirpath)
        if d == root or TRASH_DIR in d.relative_to(root).parts:
            continue
        if not (keep / d.relative_to(root)).is_dir():
            try:
                d.rmdir()
            except OSError:
                pass
