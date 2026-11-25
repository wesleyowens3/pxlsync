"""Compare probed media against the selected codec presets."""

from __future__ import annotations

from dataclasses import dataclass, field

from .codecs import CodecPreset
from .probe import VIDEO_EXTS, MediaInfo


@dataclass
class CheckRule:
    video: CodecPreset | None = None
    audio: CodecPreset | None = None
    require_audio: bool = False


@dataclass
class CheckResult:
    info: MediaInfo
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reasons


def check(info: MediaInfo, rule: CheckRule) -> CheckResult:
    result = CheckResult(info)
    if info.error:
        result.reasons.append(info.error)
        return result

    is_video_file = info.path.suffix.lower() in VIDEO_EXTS
    if rule.video and not rule.video.is_any:
        if info.video_codec is None:
            if is_video_file:
                result.reasons.append("no video stream")
        elif not rule.video.matches(info.video_codec, info.video_profile):
            got = info.video_codec + (f" {info.video_profile}" if info.video_profile else "")
            result.reasons.append(f"video is {got}, expected {rule.video.label}")

    if rule.audio and not rule.audio.is_any:
        if info.audio_codec is None:
            if rule.require_audio:
                result.reasons.append("no audio stream")
        elif not rule.audio.matches(info.audio_codec, info.audio_profile):
            result.reasons.append(f"audio is {info.audio_codec}, expected {rule.audio.label}")
    elif rule.require_audio and info.audio_codec is None:
        result.reasons.append("no audio stream")
    return result
