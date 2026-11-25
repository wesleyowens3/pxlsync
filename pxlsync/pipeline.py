"""Plan the combined rename + organize step for every scanned source file.

The result is one Move per file (current path -> planned path). Compare uses it
to show the destination as it will be *after* renaming/organizing, and Sync
applies the moves in the source (journaled, undoable) right before copying.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .codecs import CodecPreset
from .fileops import Move, validate
from .organize import OrganizeRule, plan_organize
from .probe import MediaInfo
from .rename import plan_renames


def plan_source_moves(
    files: list[Path],
    root_of: Callable[[Path], Path | None],
    infos: dict[Path, MediaInfo],
    fields: dict[str, str],
    *,
    template: str | None = None,
    strip_version: bool = True,
    lowercase_ext: bool = False,
    known_names: set[str] | None = None,
    rules: list[OrganizeRule] | None = None,
    presets: list[CodecPreset] | None = None,
) -> list[Move]:
    current = {f: f for f in files}
    problems: dict[Path, str] = {}

    if template:
        for m in plan_renames(files, template, fields, infos, strip_version, known_names, lowercase_ext):
            if m.problem:
                problems[m.src] = m.problem
            elif m.changes:
                current[m.src] = m.dst

    if rules:
        # Organize works on the already-renamed names; codec info follows the file.
        renamed_infos = {current[f]: infos[f] for f in files if f in infos}
        groups: dict[Path, list[Path]] = {}
        for f in files:
            root = root_of(f)
            if root is not None:
                groups.setdefault(root, []).append(f)
        for root, group in groups.items():
            planned = plan_organize([current[f] for f in group], root, rules, fields, renamed_infos, presets,
                                    real_paths={current[f]: f for f in group})
            for f, m in zip(group, planned):
                if m.changes:
                    current[f] = m.dst

    moves = [Move(f, current[f], problems.get(f)) for f in files]
    return validate(moves)


def names_for_root(moves: list[Move], root: Path) -> dict[str, str]:
    """``compare(left_names=...)`` mapping for one source folder."""
    out = {}
    for m in moves:
        if m.changes and not m.problem and m.src.is_relative_to(root):
            out[m.src.relative_to(root).as_posix().casefold()] = m.dst.relative_to(root).as_posix()
    return out
