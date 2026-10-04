"""
FFmpeg discovery and verification utility.
Finds system or bundled FFmpeg binary for lossless video encoding and decoding.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from typing import Optional


class FFmpegNotFoundError(RuntimeError):
    """Raised when FFmpeg executable cannot be located."""
    pass


@lru_cache(maxsize=1)
def get_ffmpeg_path() -> str:
    """
    Locates FFmpeg executable.
    Checks in order:
    1. Custom environment variable VIDEO_VAULT_FFMPEG or FFMPEG_PATH
    2. imageio-ffmpeg bundled binary
    3. System PATH (shutil.which('ffmpeg'))

    Returns:
        Absolute path to ffmpeg executable.

    Raises:
        FFmpegNotFoundError: If FFmpeg binary cannot be found.
    """
    # 1. Custom env var
    for env_var in ("VIDEO_VAULT_FFMPEG", "FFMPEG_PATH", "FFMPEG_BINARY"):
        val = os.environ.get(env_var)
        if val and os.path.isfile(val) and os.access(val, os.X_OK):
            return os.path.abspath(val)

    # 2. Bundled via imageio-ffmpeg
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return os.path.abspath(exe)
    except Exception:
        pass

    # 3. System PATH
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg and os.path.isfile(which_ffmpeg):
        return os.path.abspath(which_ffmpeg)

    raise FFmpegNotFoundError(
        "FFmpeg executable not found! Please install imageio-ffmpeg (pip install imageio-ffmpeg) "
        "or ensure ffmpeg is available in your system PATH, or set the VIDEO_VAULT_FFMPEG environment variable."
    )


def verify_ffmpeg() -> dict[str, str]:
    """
    Verifies FFmpeg presence and returns version string and path.
    """
    ffmpeg_exe = get_ffmpeg_path()
    res = subprocess.run([ffmpeg_exe, "-version"], capture_output=True, text=True, check=True)
    first_line = res.stdout.splitlines()[0] if res.stdout else "Unknown version"
    return {"path": ffmpeg_exe, "version": first_line}
