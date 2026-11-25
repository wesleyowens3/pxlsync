import shutil
import subprocess
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")

CLIPS = {
    "prores_hq.mov": ["-c:v", "prores_ks", "-profile:v", "3", "-c:a", "pcm_s24le"],
    "prores_lt.mov": ["-c:v", "prores_ks", "-profile:v", "1", "-c:a", "pcm_s24le"],
    "dnxhr_hq.mxf": ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p", "-c:a", "pcm_s24le"],
    "h264.mp4": ["-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac"],
}


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    if not FFMPEG:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("media")
    for name, args in CLIPS.items():
        subprocess.run(
            [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=256x144:rate=24000/1001:duration=0.3",
             "-f", "lavfi", "-i", "sine=duration=0.3", "-shortest", "-ar", "48000", *args, str(d / name)],
            check=True,
        )
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=duration=0.3",
                    "-c:a", "pcm_s16le", str(d / "vo.wav")], check=True)
    (d / "broken.mov").write_bytes(b"not a movie")
    return d
