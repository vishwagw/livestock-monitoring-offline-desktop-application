#!/usr/bin/env python3
"""Convert the recorded WebM to a shareable H.264 MP4, trimmed to the title card.

    pip install imageio-ffmpeg
    python demo/video/to_mp4.py demo/video/out
"""

import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo/video/out")
meta = json.loads((out / "recording.json").read_text())
src, dst = out / "livestock-counter-demo.webm", out / "livestock-counter-demo.mp4"
start = max(0.0, float(meta.get("introAtS", 0)) + 0.3)
subprocess.run(
    [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
     "-ss", f"{start:.2f}", "-i", str(src),
     "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
     "-movflags", "+faststart", "-an", str(dst)],
    check=True,
)
print(f"{dst}: {dst.stat().st_size / 1024 ** 2:.1f} MB (trimmed {start:.1f} s)")
