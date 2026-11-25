"""Rule-based folder organization inside the source folder.

Rules are checked top to bottom; the first matching rule decides the folder.
Files that match no rule stay where they are.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fnmatch import fnmatch
from pathlib import Path

from .codecs import CodecPreset
from .fileops import Move, validate
from .probe import MediaInfo
from .tokens import build_context, render, safe_component


@dataclass
class OrganizeRule:
    pattern: str = "*"  # file name globs, separated by ; or ,
    folder: str = "{kind}"  # folder template, / separates levels
    codec: str = ""  # optional video/audio preset label the file must match

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_RULES = [
    OrganizeRule("*.mov;*.mp4;*.mxf", "{date:%Y-%m-%d}/VIDEO/{codec:upper}"),
    OrganizeRule("*.wav;*.aif;*.aiff", "{date:%Y-%m-%d}/AUDIO"),
]


def _globs(pattern: str) -> list[str]:
    return [g.strip().lower() for g in pattern.replace(",", ";").split(";") if g.strip()]


def rule_matches(rule: OrganizeRule, path: Path, info: MediaInfo | None, presets: list[CodecPreset]) -> bool:
    name = path.name.lower()
    if not any(fnmatch(name, g) for g in _globs(rule.pattern) or ["*"]):
        return False
    if rule.codec:
        preset = next((p for p in presets if p.label == rule.codec), None)
        if preset and not preset.is_any:
            if info is None:
                return False
            codec, profile = (
                (info.video_codec, info.video_profile)
                if preset.kind == "video"
                else (info.audio_codec, info.audio_profile)
            )
            if not preset.matches(codec, profile):
                return False
    return True


def plan_organize(
    files: list[Path],
    root: Path,
    rules: list[OrganizeRule],
    fields: dict[str, str],
    infos: dict[Path, MediaInfo] | None = None,
    presets: list[CodecPreset] | None = None,
) -> list[Move]:
    infos = infos or {}
    presets = presets or []
    moves = []
    for seq, path in enumerate(files, start=1):
        info = infos.get(path)
        rule = next((r for r in rules if rule_matches(r, path, info, presets)), None)
        if rule is None:
            moves.append(Move(path, path))
            continue
        ctx = build_context(path, info, fields)
        ctx["seq"] = seq
        rendered = render(rule.folder, ctx)
        parts = [safe_component(p) for p in rendered.replace("\\", "/").split("/") if p.strip()]
        moves.append(Move(path, root.joinpath(*parts, path.name)))
    return validate(moves)
