"""Version-aware batch renaming.

Example template: ``{project}_{name}_v{v:3}`` turns ``shot010.mov`` into
``PXL_shot010_v001.mov``. If ``PXL_shot010_v001.mov`` already exists in the
source or destination, the new file becomes ``..._v002`` instead.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .fileops import Move, validate
from .probe import MediaInfo
from .tokens import TOKEN_RE, VERSION_TOKENS, TemplateError, build_context, has_version_token, render, safe_component

# Trailing version markers in the original name, e.g. "_v002", "-V3", " v12".
STRIP_VERSION_RE = re.compile(r"[ _\-.]?v\d+$", re.IGNORECASE)
_MARK = "~~PXLVER~~"  # plain text so filename cleanup leaves it intact


def scheme_regex(template: str) -> re.Pattern:
    """Regex matching any name that already follows ``template`` (any token values)."""
    out, pos = [], 0
    for m in TOKEN_RE.finditer(template):
        out.append(re.escape(template[pos:m.start()]))
        out.append(r"\d+" if m.group(1) in VERSION_TOKENS else ".+?")
        pos = m.end()
    out.append(re.escape(template[pos:]))
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def existing_names(*roots: Path) -> set[str]:
    names: set[str] = set()
    for root in roots:
        if root and root.is_dir():
            for _dirpath, _dirs, files in os.walk(root):
                names.update(f.lower() for f in files)
    return names


def plan_renames(
    files: list[Path],
    template: str,
    fields: dict[str, str],
    infos: dict[Path, MediaInfo] | None = None,
    strip_version: bool = True,
    known_names: set[str] | None = None,
    lowercase_ext: bool = False,
) -> list[Move]:
    if not template.strip():
        raise TemplateError("template is empty")
    infos = infos or {}
    known = set(known_names or ())
    known.update(f.name.lower() for f in files)
    versioned = has_version_token(template)
    follows_scheme = scheme_regex(template)
    next_version: dict[str, int] = {}
    moves: list[Move] = []

    for seq, path in enumerate(files, start=1):
        ctx = build_context(path, infos.get(path), fields)
        if strip_version:
            ctx["name"] = STRIP_VERSION_RE.sub("", path.stem) or path.stem
        ctx["seq"] = seq
        ext = path.suffix.lower() if lowercase_ext else path.suffix

        if not versioned:
            new = safe_component(render(template, ctx)) + ext
            moves.append(Move(path, path.with_name(new)))
            continue

        if follows_scheme.match(path.stem):  # already named this way: leave it alone
            moves.append(Move(path, path))
            continue
        ctx["v"] = _MARK
        base = safe_component(render(template, ctx)) + ext
        pattern = re.compile(
            "^" + re.escape(base).replace(re.escape(_MARK), r"(\d+)") + "$", re.IGNORECASE
        )
        if base not in next_version:
            found = [int(m.group(1)) for n in known if (m := pattern.match(n))]
            next_version[base] = max(found, default=0) + 1
        ctx["v"] = next_version[base]
        next_version[base] += 1
        moves.append(Move(path, path.with_name(safe_component(render(template, ctx)) + ext)))

    return validate(moves)
