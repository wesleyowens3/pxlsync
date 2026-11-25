"""Codec presets shown in the CODEC CHECK dropdowns.

Each preset lists the ffprobe ``codec_name`` values it accepts and, optionally,
the ffprobe ``profile`` strings it accepts (case-insensitive). An empty
``profiles`` tuple means "any profile of this codec".

Studio-specific presets can be added without touching code by dropping a
``codec_presets.json`` file in the pxlsync config folder (see README).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ANY_LABEL = "Any codec"


@dataclass(frozen=True)
class CodecPreset:
    label: str
    kind: str  # "video" or "audio"
    codec_names: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()

    @property
    def is_any(self) -> bool:
        return not self.codec_names

    def matches(self, codec_name: str | None, profile: str | None) -> bool:
        if self.is_any:
            return True
        if not codec_name or codec_name.lower() not in {c.lower() for c in self.codec_names}:
            return False
        if not self.profiles:
            return True
        return (profile or "").strip().lower() in {p.lower() for p in self.profiles}

    def describe(self) -> str:
        if self.is_any:
            return "any"
        text = "/".join(self.codec_names)
        if self.profiles:
            text += f" ({', '.join(self.profiles)})"
        return text


def _v(label: str, codecs: tuple[str, ...] = (), profiles: tuple[str, ...] = ()) -> CodecPreset:
    return CodecPreset(label, "video", codecs, profiles)


def _a(label: str, codecs: tuple[str, ...] = (), profiles: tuple[str, ...] = ()) -> CodecPreset:
    return CodecPreset(label, "audio", codecs, profiles)


PCM_ALL = (
    "pcm_s16le", "pcm_s16be", "pcm_s24le", "pcm_s24be",
    "pcm_s32le", "pcm_s32be", "pcm_f32le", "pcm_f32be",
)

BUILTIN_VIDEO: list[CodecPreset] = [
    _v(ANY_LABEL),
    _v("Apple ProRes (any)", ("prores",)),
    _v("ProRes 422 Proxy", ("prores",), ("Proxy",)),
    _v("ProRes 422 LT", ("prores",), ("LT",)),
    _v("ProRes 422", ("prores",), ("Standard",)),
    _v("ProRes 422 HQ", ("prores",), ("HQ",)),
    _v("ProRes 4444", ("prores",), ("4444",)),
    _v("ProRes 4444 XQ", ("prores",), ("XQ",)),
    _v("Avid DNxHD / DNxHR (any)", ("dnxhd",)),
    _v("DNxHD", ("dnxhd",), ("DNXHD",)),
    _v("DNxHR LB", ("dnxhd",), ("DNXHR LB",)),
    _v("DNxHR SQ", ("dnxhd",), ("DNXHR SQ",)),
    _v("DNxHR HQ", ("dnxhd",), ("DNXHR HQ",)),
    _v("DNxHR HQX", ("dnxhd",), ("DNXHR HQX",)),
    _v("DNxHR 444", ("dnxhd",), ("DNXHR 444",)),
    _v("H.264 / AVC (any)", ("h264",)),
    _v("H.264 High", ("h264",), ("High",)),
    _v("H.265 / HEVC (any)", ("hevc",)),
    _v("H.265 / HEVC Main 10", ("hevc",), ("Main 10",)),
    _v("AV1", ("av1",)),
    _v("VP9", ("vp9",)),
    _v("GoPro CineForm", ("cfhd",)),
    _v("QuickTime Animation", ("qtrle",)),
]

BUILTIN_AUDIO: list[CodecPreset] = [
    _a(ANY_LABEL),
    _a("PCM / WAV (any bit depth)", PCM_ALL),
    _a("PCM 24-bit", ("pcm_s24le", "pcm_s24be")),
    _a("PCM 16-bit", ("pcm_s16le", "pcm_s16be")),
    _a("PCM 32-bit float", ("pcm_f32le", "pcm_f32be")),
    _a("AAC", ("aac",)),
    _a("MP3", ("mp3",)),
    _a("Dolby AC-3", ("ac3",)),
    _a("Dolby E-AC-3", ("eac3",)),
    _a("Opus", ("opus",)),
]


def load_presets(custom_file: Path | None = None) -> tuple[list[CodecPreset], list[CodecPreset]]:
    """Return (video, audio) presets: built-ins plus any from ``custom_file``.

    The custom file is a JSON list like::

        [{"label": "Client X master", "kind": "video",
          "codecs": ["prores"], "profiles": ["HQ", "4444"]}]
    """
    video, audio = list(BUILTIN_VIDEO), list(BUILTIN_AUDIO)
    if custom_file and custom_file.is_file():
        for entry in json.loads(custom_file.read_text(encoding="utf-8")):
            preset = CodecPreset(
                label=str(entry["label"]),
                kind=str(entry.get("kind", "video")),
                codec_names=tuple(entry.get("codecs", ())),
                profiles=tuple(entry.get("profiles", ())),
            )
            (audio if preset.kind == "audio" else video).append(preset)
    return video, audio


def find_preset(presets: list[CodecPreset], label: str) -> CodecPreset:
    for p in presets:
        if p.label == label:
            return p
    return presets[0]
