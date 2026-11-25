"""Folder comparison: what differs between LEFT (source) and RIGHT (destination),
and what each sync mode will do about it.

Files are matched by relative path, ignoring upper/lower case (macOS and Windows
disks are case-insensitive by default).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable

# 2 s tolerance: FAT32/exFAT drives store modification times in 2-second steps.
TIME_TOLERANCE = 2.0

DEFAULT_EXCLUDES = [
    ".DS_Store", "._*", "Thumbs.db", "desktop.ini", "*.pxlsync-part",
    ".pxlsync-trash/", ".Trashes/", ".Spotlight-V100/", ".fseventsd/", ".TemporaryItems/",
    "$RECYCLE.BIN/", "System Volume Information/",
]


class Category(Enum):
    EQUAL = "equal"
    LEFT_ONLY = "left only"
    RIGHT_ONLY = "right only"
    LEFT_NEWER = "left newer"
    RIGHT_NEWER = "right newer"
    DIFFERENT = "different"


class Action(Enum):
    NONE = "none"
    TO_RIGHT = "to right"  # create or overwrite on the right
    TO_LEFT = "to left"
    DELETE_RIGHT = "delete right"
    DELETE_LEFT = "delete left"


class Method(Enum):
    TIME_SIZE = "File time and size"
    CONTENT = "File content (slow)"
    SIZE = "File size"


class Mode(Enum):
    MIRROR = "Mirror  →"
    UPDATE = "Update  →"
    TWO_WAY = "Two way  ⇄"


MODE_HELP = {
    Mode.MIRROR: "Destination becomes an exact copy of the source. Extra files on the destination are removed.",
    Mode.UPDATE: "Copy new and changed files to the destination. Never removes anything.",
    Mode.TWO_WAY: "Copy both ways; the newer file wins. Never removes anything. Same-date clashes are flagged.",
}


@dataclass
class Entry:
    rel: str  # forward-slash relative path, original case
    size: int
    mtime: float


@dataclass
class Row:
    left: Entry | None
    right: Entry | None
    category: Category
    action: Action = Action.NONE
    note: str = ""
    conflict: bool = False
    held: bool = False  # action switched off by a rule (e.g. failed codec check)
    overridden: bool = False  # action chosen by hand in the grid

    @property
    def rel(self) -> str:
        return (self.left or self.right).rel  # type: ignore[union-attr]

    @property
    def size(self) -> int:
        """Bytes this row's action will copy."""
        if self.action == Action.TO_RIGHT and self.left:
            return self.left.size
        if self.action == Action.TO_LEFT and self.right:
            return self.right.size
        return 0


@dataclass
class Comparison:
    left_root: Path
    right_root: Path
    rows: list[Row] = field(default_factory=list)
    left_missing: bool = False
    right_missing: bool = False


class Cancelled(Exception):
    pass


def parse_excludes(text: str) -> list[str]:
    return [p.strip() for p in text.replace(";", "\n").replace(",", "\n").splitlines() if p.strip()]


def _excluded(name: str, rel: str, is_dir: bool, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if is_dir and (fnmatch(name, pat[:-1]) or fnmatch(rel, pat[:-1])):
                return True
        elif not is_dir and (fnmatch(name, pat) or ("/" in pat and fnmatch(rel, pat))):
            return True
    return False


def scan_tree(
    root: Path,
    excludes: list[str],
    cancelled: Callable[[], bool] = lambda: False,
    on_file: Callable[[int], None] | None = None,
) -> dict[str, Entry]:
    entries: dict[str, Entry] = {}
    if not root.is_dir():
        return entries
    for dirpath, dirnames, filenames in os.walk(root):
        if cancelled():
            raise Cancelled
        base = Path(dirpath).relative_to(root).as_posix()
        base = "" if base == "." else base + "/"
        dirnames[:] = sorted(d for d in dirnames if not _excluded(d, base + d, True, excludes))
        for name in sorted(filenames):
            rel = base + name
            if _excluded(name, rel, False, excludes):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            entries[rel.casefold()] = Entry(rel, st.st_size, st.st_mtime)
            if on_file:
                on_file(len(entries))
    return entries


def same_content(a: Path, b: Path, cancelled: Callable[[], bool] = lambda: False) -> bool:
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            if cancelled():
                raise Cancelled
            ca, cb = fa.read(4 << 20), fb.read(4 << 20)
            if ca != cb:
                return False
            if not ca:
                return True


def categorize(left: Entry, right: Entry, method: Method, left_root: Path, right_root: Path,
               cancelled: Callable[[], bool] = lambda: False) -> Category:
    if method == Method.SIZE:
        return Category.EQUAL if left.size == right.size else Category.DIFFERENT
    if method == Method.CONTENT:
        if left.size != right.size:
            return Category.DIFFERENT
        same = same_content(left_root / left.rel, right_root / right.rel, cancelled)
        return Category.EQUAL if same else Category.DIFFERENT
    dt = left.mtime - right.mtime
    if abs(dt) <= TIME_TOLERANCE:
        return Category.EQUAL if left.size == right.size else Category.DIFFERENT
    return Category.LEFT_NEWER if dt > 0 else Category.RIGHT_NEWER


def compare(
    left_root: Path,
    right_root: Path,
    method: Method = Method.TIME_SIZE,
    excludes: list[str] | None = None,
    progress: Callable[[str, int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Comparison:
    excludes = DEFAULT_EXCLUDES + (excludes or [])
    result = Comparison(left_root, right_root,
                        left_missing=not left_root.is_dir(), right_missing=not right_root.is_dir())
    say = progress or (lambda _msg, _n: None)
    left = scan_tree(left_root, excludes, cancelled, lambda n: n % 200 == 0 and say("Scanning left", n))
    right = scan_tree(right_root, excludes, cancelled, lambda n: n % 200 == 0 and say("Scanning right", n))
    keys = sorted(set(left) | set(right))
    for i, key in enumerate(keys):
        le, re_ = left.get(key), right.get(key)
        if le and re_:
            if method == Method.CONTENT and i % 20 == 0:
                say("Comparing content", i)
            try:
                cat = categorize(le, re_, method, left_root, right_root, cancelled)
            except OSError:
                cat = Category.DIFFERENT
        else:
            cat = Category.LEFT_ONLY if le else Category.RIGHT_ONLY
        result.rows.append(Row(le, re_, cat))
    return result


def plan(rows: list[Row], mode: Mode) -> None:
    """Set the default action on every row for the chosen mode (keeps manual overrides)."""
    for row in rows:
        if row.overridden:
            continue
        row.conflict, row.note, row.held = False, "", False
        c = row.category
        if c == Category.EQUAL:
            row.action = Action.NONE
        elif mode == Mode.MIRROR:
            row.action = Action.DELETE_RIGHT if c == Category.RIGHT_ONLY else Action.TO_RIGHT
        elif mode == Mode.UPDATE:
            if c in (Category.LEFT_ONLY, Category.LEFT_NEWER):
                row.action = Action.TO_RIGHT
            else:
                row.action = Action.NONE
                if c == Category.RIGHT_NEWER:
                    row.conflict, row.note = True, "destination copy is newer"
                elif c == Category.DIFFERENT:
                    row.conflict, row.note = True, "same date, different file"
        else:  # TWO_WAY
            row.action = {
                Category.LEFT_ONLY: Action.TO_RIGHT,
                Category.LEFT_NEWER: Action.TO_RIGHT,
                Category.RIGHT_ONLY: Action.TO_LEFT,
                Category.RIGHT_NEWER: Action.TO_LEFT,
            }.get(c, Action.NONE)
            if c == Category.DIFFERENT:
                row.conflict, row.note = True, "same date, different file: pick a direction"


def hold(rows: list[Row], rels: set[str], note: str) -> int:
    """Switch off copies of the given left-side files (e.g. failed codec check)."""
    keys = {r.casefold() for r in rels}
    n = 0
    for row in rows:
        if row.left and row.left.rel.casefold() in keys and row.action == Action.TO_RIGHT and not row.overridden:
            row.action, row.held, row.note = Action.NONE, True, note
            n += 1
    return n


def possible_actions(row: Row) -> list[Action]:
    acts = [Action.NONE]
    if row.left:
        acts.append(Action.TO_RIGHT)
    if row.right:
        acts.append(Action.TO_LEFT)
        if not row.left:
            acts.append(Action.DELETE_RIGHT)
    if row.left and not row.right:
        acts.append(Action.DELETE_LEFT)
    return acts
