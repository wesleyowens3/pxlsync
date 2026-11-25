"""Template tokens shared by RENAME and ORGANIZE.

Syntax: ``{token}`` or ``{token:arg}``.

  {project} {client}     values typed in the app
  {name}                 original file name without extension
  {ext}                  extension without the dot (mov, wav, ...)
  {folder}               name of the folder the file is in
  {date} {date:%Y-%m-%d} today's date (default format YYYYMMDD)
  {mdate} {mdate:fmt}    the file's modified date
  {v} {v:3}              version number, zero-padded to N digits (default 3)
  {seq} {seq:4}          running counter across the batch
  {codec} {profile}      video codec / profile (audio codec for audio files)
  {res} {w} {h} {fps}    resolution and frame rate
  {kind}                 video / audio / other
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .probe import AUDIO_EXTS, VIDEO_EXTS, MediaInfo

TOKEN_RE = re.compile(r"\{(\w+)(?::([^{}]*))?\}")
VERSION_TOKENS = ("v", "version")

# Characters Windows refuses in file names; we strip them everywhere so
# Mac and Windows machines can share the same folders.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class TemplateError(ValueError):
    pass


def safe_component(text: str) -> str:
    text = _ILLEGAL.sub("_", text).strip().rstrip(". ")
    if text.split(".")[0].upper() in _RESERVED:
        text = "_" + text
    return text or "_"


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


def build_context(path: Path, info: MediaInfo | None, fields: dict[str, str],
                  stat_path: Path | None = None) -> dict[str, object]:
    """Token values for ``path``. ``stat_path`` is where the file really is, when ``path``
    is a planned new name that doesn't exist yet (used for {mdate})."""
    ctx: dict[str, object] = {k: v for k, v in fields.items()}
    ctx.update(
        name=path.stem,
        ext=path.suffix.lstrip("."),
        folder=path.parent.name,
        kind=kind_of(path),
        _now=datetime.now(),
    )
    try:
        ctx["_mtime"] = datetime.fromtimestamp((stat_path or path).stat().st_mtime)
    except OSError:
        ctx["_mtime"] = ctx["_now"]
    if info:
        ctx.update(
            codec=info.video_codec or info.audio_codec or "",
            profile=(info.video_profile if info.video_codec else info.audio_profile) or "",
            res=info.resolution,
            w=info.width or "",
            h=info.height or "",
            fps=info.fps_text,
        )
    return ctx


def render(template: str, ctx: dict[str, object]) -> str:
    def sub(m: re.Match) -> str:
        key, arg = m.group(1), m.group(2)
        if key in ("date", "mdate"):
            when = ctx["_now"] if key == "date" else ctx["_mtime"]
            return when.strftime(arg or "%Y%m%d")  # type: ignore[union-attr]
        if key in VERSION_TOKENS or key == "seq":
            num = ctx.get(key if key != "version" else "v")
            if num is None:
                raise TemplateError(f"{{{key}}} has no value here")
            if isinstance(num, str):  # placeholder used while working out versions
                return num
            width = int(arg) if arg and arg.isdigit() else 3
            return str(num).zfill(width)
        if key not in ctx or key.startswith("_"):
            raise TemplateError(f"unknown token {{{key}}}")
        value = str(ctx[key])
        if arg == "upper":
            value = value.upper()
        elif arg == "lower":
            value = value.lower()
        return value

    return TOKEN_RE.sub(sub, template)


def has_version_token(template: str) -> bool:
    return any(m.group(1) in VERSION_TOKENS for m in TOKEN_RE.finditer(template))
