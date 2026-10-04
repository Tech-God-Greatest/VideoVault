"""
FFmpeg-based lossless video I/O backends.
Provides high-performance pipe-streamed frame writing and reading with zero generational loss.
Supports FFV1 (IETF archival), Rawvideo, PNG, and HuffYUV.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import List, Optional, Union

from video_vault.core.config import (
    CODEC_FFV1,
    CODEC_H264,
    CODEC_HUFFYUV,
    CODEC_PNG,
    CODEC_RAW,
    PIX_FMT_BGR24,
    PIX_FMT_BGRA,
    PIX_FMT_RGB24,
    PIX_FMT_RGBA,
    PIX_FMT_YUV420P,
    StreamConfig,
)
from video_vault.utils.ffmpeg_finder import get_ffmpeg_path


class VideoBackendError(Exception):
    """Raised when FFmpeg video encoding or decoding encounters an error."""
    pass


class LosslessVideoWriter:
    """
    Encodes raw pixel frames directly into a lossless video container via FFmpeg stdin pipe.
    """

    def __init__(self, output_path: Union[str, Path], config: StreamConfig):
        self.output_path = Path(output_path).resolve()
        self.config = config
        self.config.validate()

        self._ffmpeg_exe = get_ffmpeg_path()
        self._proc: Optional[subprocess.Popen] = None
        self._stderr_lines: List[str] = []
        self._stderr_thread: Optional[threading.Thread] = None
        self._closed = False
        self._frames_written = 0

        self._start_process()

    def _build_command(self) -> List[str]:
        cmd = [
            self._ffmpeg_exe,
            "-y",  # Overwrite output
            "-loglevel", "warning",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.config.width}x{self.config.height}",
            "-pix_fmt", self.config.pixel_format,
            "-r", str(self.config.fps),
            "-i", "-",  # Input from stdin
        ]

        codec = self.config.codec
        if codec == CODEC_FFV1:
            cmd.extend([
                "-c:v", "ffv1",
                "-level", "3",
                "-coder", "0",     # 0 = Golomb-Rice / fast, 1 = Range coder
                "-context", "0",
                "-g", "1",         # Keyframe every frame (intra-only)
                "-slices", "16",   # Multi-threaded slice encoding
                "-slicecrc", "1",  # Intra-slice CRC for archival safety
            ])
        elif codec in (CODEC_RAW, "rawvideo"):
            cmd.extend([
                "-c:v", "rawvideo",
            ])
            if str(self.output_path).lower().endswith(".avi"):
                cmd.extend(["-vtag", "DIB "])
        elif codec in (CODEC_H264, "libx264", "h264_lossless"):
            cmd.extend([
                "-c:v", "libx264",
                "-crf", "0",
                "-preset", "ultrafast",
                "-tune", "fastdecode",
            ])
        elif codec == CODEC_PNG:
            cmd.extend([
                "-c:v", "png",
            ])
        elif codec == CODEC_HUFFYUV:
            cmd.extend([
                "-c:v", "huffyuv",
            ])
        else:
            raise VideoBackendError(f"Unsupported codec: {codec}")

        cmd.append(str(self.output_path))
        return cmd

    def _drain_stderr(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        for line in iter(self._proc.stderr.readline, b""):
            try:
                self._stderr_lines.append(line.decode("utf-8", errors="replace"))
            except Exception:
                pass

    def _start_process(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_command()
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=10 * 1024 * 1024,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def write_frame(self, frame_bytes: bytes) -> None:
        """
        Writes a single complete frame to FFmpeg stdin.
        """
        if self._closed:
            raise VideoBackendError("Cannot write to closed VideoWriter")

        # If YUV420P, pad neutral gray chroma if only data plane was provided
        if self.config.pixel_format == PIX_FMT_YUV420P:
            if len(frame_bytes) == self.config.data_capacity_per_frame:
                uv_size = (self.config.width * self.config.height) // 2
                frame_bytes = frame_bytes + bytes([128] * uv_size)

        expected_size = self.config.frame_total_bytes
        if len(frame_bytes) != expected_size:
            raise VideoBackendError(
                f"Invalid frame size: expected {expected_size} bytes, got {len(frame_bytes)}"
            )

        if not self._proc or not self._proc.stdin:
            raise VideoBackendError("FFmpeg process is not running")

        try:
            self._proc.stdin.write(frame_bytes)
            self._frames_written += 1
        except (BrokenPipeError, OSError) as e:
            stderr_out = "".join(self._stderr_lines[-20:])
            raise VideoBackendError(f"FFmpeg process pipe broke unexpectedly: {e}\nFFmpeg log: {stderr_out}") from e

    def close(self) -> None:
        """Flushes stdin, waits for FFmpeg to finish writing, and verifies exit code."""
        if self._closed:
            return
        self._closed = True

        if self._proc:
            if self._proc.stdin:
                try:
                    self._proc.stdin.flush()
                    self._proc.stdin.close()
                except Exception:
                    pass

            returncode = self._proc.wait()
            if self._stderr_thread:
                self._stderr_thread.join(timeout=2.0)

            if returncode != 0:
                stderr_out = "".join(self._stderr_lines[-30:])
                raise VideoBackendError(
                    f"FFmpeg encoding failed with exit code {returncode}.\nDetails:\n{stderr_out}"
                )

    def __enter__(self) -> "LosslessVideoWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def frames_written(self) -> int:
        return self._frames_written


class LosslessVideoReader:
    """
    Decodes raw pixel frames sequentially from a lossless video container via FFmpeg stdout pipe.
    """

    def __init__(self, video_path: Union[str, Path], config: StreamConfig):
        self.video_path = Path(video_path).resolve()
        if not self.video_path.is_file():
            raise FileNotFoundError(f"Video file not found: {self.video_path}")

        self.config = config
        self.config.validate()

        self._ffmpeg_exe = get_ffmpeg_path()
        self._proc: Optional[subprocess.Popen] = None
        self._stderr_lines: List[str] = []
        self._stderr_thread: Optional[threading.Thread] = None
        self._closed = False
        self._frames_read = 0

        self._start_process()

    def _build_command(self) -> List[str]:
        return [
            self._ffmpeg_exe,
            "-loglevel", "warning",
            "-i", str(self.video_path),
            "-f", "rawvideo",
            "-pix_fmt", self.config.pixel_format,
            "-s", f"{self.config.width}x{self.config.height}",
            "-",
        ]

    def _drain_stderr(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        for line in iter(self._proc.stderr.readline, b""):
            try:
                self._stderr_lines.append(line.decode("utf-8", errors="replace"))
            except Exception:
                pass

    def _start_process(self) -> None:
        cmd = self._build_command()
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10 * 1024 * 1024,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def read_frame(self) -> Optional[bytes]:
        """
        Reads a single complete frame from FFmpeg stdout.
        Returns None on EOF.
        """
        if self._closed or not self._proc or not self._proc.stdout:
            return None

        frame_size = self.config.frame_total_bytes
        # Read exact frame size
        buf = bytearray()
        while len(buf) < frame_size:
            chunk = self._proc.stdout.read(frame_size - len(buf))
            if not chunk:
                break
            buf.extend(chunk)

        if len(buf) == 0:
            return None  # Clean EOF

        if len(buf) < frame_size:
            stderr_out = "".join(self._stderr_lines[-10:])
            raise VideoBackendError(
                f"Unexpected end of stream: read partial frame ({len(buf)} of {frame_size} bytes). "
                f"Video may be corrupted or truncated.\nFFmpeg: {stderr_out}"
            )

        self._frames_read += 1
        raw_bytes = bytes(buf)
        if self.config.pixel_format == PIX_FMT_YUV420P:
            return raw_bytes[: self.config.data_capacity_per_frame]
        return raw_bytes

    def close(self) -> None:
        """Closes stdout and terminates FFmpeg reader process."""
        if self._closed:
            return
        self._closed = True

        if self._proc:
            if self._proc.stdout:
                try:
                    self._proc.stdout.close()
                except Exception:
                    pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
            except Exception:
                pass

            if self._stderr_thread:
                self._stderr_thread.join(timeout=1.0)

    def __enter__(self) -> "LosslessVideoReader":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def frames_read(self) -> int:
        return self._frames_read


def probe_video_container(video_path: Union[str, Path]) -> Optional[StreamConfig]:
    """
    Probes video container file using FFmpeg and returns a matching StreamConfig.
    Auto-detects codec, resolution, and pixel format for transparent decoding.
    """
    try:
        import re
        ffmpeg_exe = get_ffmpeg_path()
        res = subprocess.run([ffmpeg_exe, "-i", str(video_path)], capture_output=True, text=True)
        for line in res.stderr.splitlines():
            if "Video:" in line:
                m = re.search(r"Video:\s*(\w+)[^,]*,\s*(\w+)[^,]*,\s*(\d+)x(\d+)", line)
                if m:
                    codec_name, pix_fmt, w_str, h_str = m.groups()
                    w, h = int(w_str), int(h_str)
                    pix_fmt = pix_fmt.lower()
                    codec = codec_name.lower()
                    if "h264" in codec or "avc" in codec:
                        codec = CODEC_H264
                    elif "ffv1" in codec:
                        codec = CODEC_FFV1
                    elif "raw" in codec:
                        codec = CODEC_RAW
                    elif "png" in codec:
                        codec = CODEC_PNG
                    elif "huffyuv" in codec:
                        codec = CODEC_HUFFYUV

                    return StreamConfig(
                        width=w,
                        height=h,
                        pixel_format=pix_fmt,
                        codec=codec,
                    )
    except Exception:
        pass
    return None
