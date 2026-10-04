"""
Configuration, constants, and resolution parameters for Video Vault 4K.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Frame Magic constants
FRAME_MAGIC = b"VAULT4K\x01"  # 8 bytes
GLOBAL_META_MAGIC = b"VLT_META"  # 8 bytes
FORMAT_VERSION = 1

# Header sizes
FRAME_HEADER_SIZE = 80  # Fixed 80 bytes per frame (includes 16-byte salt)

# Frame flags bitmask
FLAG_ENCRYPTED = 1 << 0    # bit 0: Payload is AES-256-GCM encrypted
FLAG_FIRST_FRAME = 1 << 1   # bit 1: Frame 0, contains Global Vault Metadata
FLAG_LAST_FRAME = 1 << 2    # bit 2: End of stream (EOS)
FLAG_RGBA_PIXELS = 1 << 3   # bit 3: 1 for RGBA/BGRA (4 bpp), 0 for RGB/BGR (3 bpp)
FLAG_YUV_LUMA = 1 << 4      # bit 4: 1 for YUV420p Luma storage (Microsoft MP4 mode)

# Video Resolutions
DEFAULT_WIDTH = 3840
DEFAULT_HEIGHT = 2160
DEFAULT_FPS = 30

# Supported Codecs
CODEC_FFV1 = "ffv1"
CODEC_RAW = "rawvideo"
CODEC_H264 = "libx264"
CODEC_PNG = "png"
CODEC_HUFFYUV = "huffyuv"

SUPPORTED_CODECS = [CODEC_FFV1, CODEC_RAW, CODEC_H264, CODEC_PNG, CODEC_HUFFYUV, "h264_lossless"]

# Pixel formats
PIX_FMT_RGBA = "rgba"
PIX_FMT_RGB24 = "rgb24"
PIX_FMT_BGR24 = "bgr24"
PIX_FMT_BGRA = "bgra"
PIX_FMT_YUV420P = "yuv420p"

VALID_PIX_FMTS = [PIX_FMT_RGBA, PIX_FMT_RGB24, PIX_FMT_BGR24, PIX_FMT_BGRA, PIX_FMT_YUV420P]


@dataclass(frozen=True)
class StreamConfig:
    """Configuration settings for video encoding/decoding."""
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS
    pixel_format: str = PIX_FMT_RGBA
    codec: str = CODEC_FFV1
    container_ext: str = ".mkv"

    @property
    def bytes_per_pixel(self) -> float:
        if self.pixel_format == PIX_FMT_YUV420P:
            return 1.0
        return 4 if self.pixel_format in (PIX_FMT_RGBA, PIX_FMT_BGRA) else 3

    @property
    def data_capacity_per_frame(self) -> int:
        """Total bytes available for storage in this frame."""
        if self.pixel_format == PIX_FMT_YUV420P:
            return self.width * self.height
        return self.width * self.height * int(self.bytes_per_pixel)

    @property
    def frame_total_bytes(self) -> int:
        """Total raw bytes piped to/from FFmpeg per frame."""
        if self.pixel_format == PIX_FMT_YUV420P:
            return self.width * self.height * 3 // 2  # Y plane + U plane + V plane
        return self.width * self.height * int(self.bytes_per_pixel)

    @property
    def max_payload_per_frame(self) -> int:
        return self.data_capacity_per_frame - FRAME_HEADER_SIZE

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Invalid dimensions: {self.width}x{self.height}")
        if self.pixel_format not in VALID_PIX_FMTS:
            raise ValueError(f"Unsupported pixel format: {self.pixel_format}")
        if self.codec not in SUPPORTED_CODECS:
            raise ValueError(f"Unsupported codec: {self.codec}")


# Default 4K RGBA configuration: 3840 x 2160 x 4 bytes = 33,177,600 bytes/frame (~31.64 MB)
DEFAULT_CONFIG = StreamConfig()
