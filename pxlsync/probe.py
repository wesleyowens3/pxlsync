"""Read codec information from media files with ffprobe."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

VIDEO_EXTS = {
    ".mov", ".mp4", ".m4v", ".mxf", ".mkv", ".avi", ".webm", ".mts", ".m2ts",
    ".r3d", ".braw", ".ari", ".mpg", ".mpeg", ".ts",
}
AUDIO_EXTS = {".wav", ".aif", ".aiff", ".mp3", ".aac", ".m4a", ".flac", ".ogg", ".opus", ".ac3", ".bwf"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

# Windows: keep ffprobe from flashing a console window for every file.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class MediaInfo:
    path: Path
    video_codec: str | None = None
    video_profile: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    pix_fmt: str | None = None
    audio_codec: str | None = None
    audio_profile: str | None = None
    audio_channels: int | None = None
    sample_rate: int | None = None
    duration: float | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width and self.height else ""

    @property
    def fps_text(self) -> str:
        if not self.fps:
            return ""
        return f"{self.fps:.3f}".rstrip("0").rstrip(".")


def find_ffprobe() -> str | None:
    """Locate ffprobe: env override, bundled copy, PATH, then common install dirs."""
    exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    candidates: list[Path] = []
    if env := os.environ.get("PXLSYNC_FFPROBE"):
        candidates.append(Path(env))
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    candidates += [bundle_root / "vendor" / exe, Path(sys.executable).parent / exe]
    for c in candidates:
        if c.is_file():
            return str(c)
    if found := shutil.which("ffprobe"):
        return found
    for c in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", r"C:\ffmpeg\bin\ffprobe.exe"):
        if Path(c).is_file():
            return c
    return None


def _parse_rate(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            return float(num) / float(den) if float(den) else None
        return float(rate)
    except ValueError:
        return None


def parse_ffprobe_json(path: Path, data: dict) -> MediaInfo:
    info = MediaInfo(path=path)
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        # Skip embedded cover art / thumbnails.
        if kind == "video" and info.video_codec is None and not s.get("disposition", {}).get("attached_pic"):
            info.video_codec = s.get("codec_name")
            info.video_profile = s.get("profile")
            info.width = s.get("width")
            info.height = s.get("height")
            info.pix_fmt = s.get("pix_fmt")
            info.fps = _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate"))
        elif kind == "audio" and info.audio_codec is None:
            info.audio_codec = s.get("codec_name")
            info.audio_profile = s.get("profile")
            info.audio_channels = s.get("channels")
            sr = s.get("sample_rate")
            info.sample_rate = int(sr) if sr and str(sr).isdigit() else None
    try:
        info.duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    return info


def probe(path: Path, ffprobe: str, timeout: float = 60) -> MediaInfo:
    cmd = [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    try:
        out = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return MediaInfo(path=path, error=f"ffprobe failed: {exc}")
    if out.returncode != 0:
        msg = out.stderr.decode(errors="replace").strip().splitlines()
        return MediaInfo(path=path, error=msg[-1] if msg else f"ffprobe exit {out.returncode}")
    try:
        data = json.loads(out.stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        return MediaInfo(path=path, error=f"unreadable ffprobe output: {exc}")
    return parse_ffprobe_json(path, data)


def scan_media(root: Path, exts: Iterable[str] = MEDIA_EXTS) -> list[Path]:
    """All media files under ``root`` (recursive), skipping hidden files/folders."""
    wanted = {e.lower() for e in exts}
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if not name.startswith(".") and Path(name).suffix.lower() in wanted:
                files.append(Path(dirpath) / name)
    return files


def probe_many(
    paths: list[Path],
    ffprobe: str,
    progress: Callable[[int, int], None] | None = None,
    workers: int = 4,
) -> list[MediaInfo]:
    results: list[MediaInfo | None] = [None] * len(paths)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, p, ffprobe): i for i, p in enumerate(paths)}
        done = 0
        for fut in futures:
            results[futures[fut]] = fut.result()
            done += 1
            if progress:
                progress(done, len(paths))
    return [r for r in results if r is not None]
