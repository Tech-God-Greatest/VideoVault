"""
Binary framing protocol and header serialization (VAULT4K specification).
Defines the 64-byte frame header, frame envelope validation, CRC32/SHA-256 checks,
and Global Vault Metadata block for Frame 0.
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass
from typing import Any, Optional, Tuple

from video_vault.core.config import (
    FLAG_ENCRYPTED,
    FLAG_FIRST_FRAME,
    FLAG_LAST_FRAME,
    FLAG_RGBA_PIXELS,
    FORMAT_VERSION,
    FRAME_HEADER_SIZE,
    FRAME_MAGIC,
    GLOBAL_META_MAGIC,
)
from video_vault.utils.hashing import crc32_bytes, sha256_bytes_raw


class FramingError(ValueError):
    """Base exception for framing errors."""
    pass


class FrameCorruptionError(FramingError):
    """Raised when frame CRC32 or SHA-256 validation fails."""
    pass


class InvalidMagicError(FramingError):
    """Raised when magic header bytes do not match VAULT4K signature."""
    pass


# Format string for the 76 bytes preceding the 4-byte header CRC32:
# 8s: magic, H: version, H: flags, I: frame_seq, I: total_frames, I: payload_len, I: payload_crc32, 16s: salt, 32s: payload_sha256
_HEADER_PREFIX_FORMAT = ">8sHHIIII16s32s"
_HEADER_FORMAT = ">8sHHIIII16s32sI"


@dataclass
class FrameHeader:
    magic: bytes
    version: int
    flags: int
    frame_seq: int
    total_frames: int
    payload_len: int
    payload_crc32: int
    salt: bytes
    payload_sha256: bytes
    header_crc32: int = 0

    @property
    def is_encrypted(self) -> bool:
        return bool(self.flags & FLAG_ENCRYPTED)

    @property
    def is_first_frame(self) -> bool:
        return bool(self.flags & FLAG_FIRST_FRAME)

    @property
    def is_last_frame(self) -> bool:
        return bool(self.flags & FLAG_LAST_FRAME)

    @property
    def is_rgba(self) -> bool:
        return bool(self.flags & FLAG_RGBA_PIXELS)


def pack_frame_header(
    frame_seq: int,
    total_frames: int,
    payload_len: int,
    payload_crc32: int,
    payload_sha256: bytes,
    salt: bytes = b"\x00" * 16,
    flags: int = 0,
    version: int = FORMAT_VERSION,
) -> bytes:
    """
    Packs an 80-byte frame header with self-verifying CRC32 checksum.
    """
    if len(payload_sha256) != 32:
        raise ValueError(f"payload_sha256 must be 32 bytes, got {len(payload_sha256)}")
    if len(salt) != 16:
        raise ValueError(f"salt must be 16 bytes, got {len(salt)}")

    prefix = struct.pack(
        _HEADER_PREFIX_FORMAT,
        FRAME_MAGIC,
        version,
        flags,
        frame_seq,
        total_frames,
        payload_len,
        payload_crc32,
        salt,
        payload_sha256,
    )
    header_crc = crc32_bytes(prefix)
    return prefix + struct.pack(">I", header_crc)


def unpack_frame_header(header_bytes: bytes) -> FrameHeader:
    """
    Unpacks and validates an 80-byte frame header.
    Validates magic bytes, salt, and header CRC32.
    """
    if len(header_bytes) < FRAME_HEADER_SIZE:
        raise FramingError(f"Header too short: expected {FRAME_HEADER_SIZE} bytes, got {len(header_bytes)}")

    prefix = header_bytes[:76]
    expected_header_crc = struct.unpack(">I", header_bytes[76:80])[0]
    actual_header_crc = crc32_bytes(prefix)

    if actual_header_crc != expected_header_crc:
        raise FrameCorruptionError(
            f"Frame header CRC32 mismatch! Corrupted header (expected {expected_header_crc:#010x}, got {actual_header_crc:#010x})"
        )

    magic, version, flags, frame_seq, total_frames, payload_len, payload_crc32, salt, payload_sha256 = struct.unpack(
        _HEADER_PREFIX_FORMAT, prefix
    )

    if magic != FRAME_MAGIC:
        raise InvalidMagicError(f"Invalid frame magic: {magic!r}, expected {FRAME_MAGIC!r}")

    return FrameHeader(
        magic=magic,
        version=version,
        flags=flags,
        frame_seq=frame_seq,
        total_frames=total_frames,
        payload_len=payload_len,
        payload_crc32=payload_crc32,
        salt=salt,
        payload_sha256=payload_sha256,
        header_crc32=expected_header_crc,
    )


def assemble_frame(
    header_bytes: bytes,
    payload: bytes,
    frame_total_size: int,
    padding_bytes: Optional[bytes] = None,
) -> bytes:
    """
    Assembles a full video frame from header + payload + padding.
    Guarantees the returned frame is exactly `frame_total_size` bytes.
    """
    header_len = len(header_bytes)
    payload_len = len(payload)
    current_len = header_len + payload_len

    if current_len > frame_total_size:
        raise FramingError(f"Payload exceeds frame capacity: {current_len} > {frame_total_size}")

    needed_padding = frame_total_size - current_len
    if needed_padding > 0:
        if padding_bytes is not None and len(padding_bytes) >= needed_padding:
            pad = padding_bytes[:needed_padding]
        else:
            # Deterministic pseudo-random padding pattern
            pad = (b"\xaa\x55\x33\xcc\x0f\xf0\x5a\xa5" * ((needed_padding // 8) + 1))[:needed_padding]
    else:
        pad = b""

    return header_bytes + payload + pad


def disect_frame(
    frame_bytes: bytes,
) -> Tuple[FrameHeader, bytes]:
    """
    Parses a raw video frame:
    1. Unpacks and verifies header CRC32.
    2. Extracts payload of exact `payload_len`.
    3. Verifies payload CRC32 and SHA-256.

    Returns:
        (FrameHeader, payload_bytes)

    Raises:
        FrameCorruptionError if any checksum fails.
    """
    if len(frame_bytes) < FRAME_HEADER_SIZE:
        raise FramingError(f"Frame data too small: {len(frame_bytes)} bytes")

    header = unpack_frame_header(frame_bytes[:FRAME_HEADER_SIZE])
    payload = frame_bytes[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + header.payload_len]

    if len(payload) != header.payload_len:
        raise FrameCorruptionError(
            f"Truncated frame payload in frame {header.frame_seq}: expected {header.payload_len} bytes, got {len(payload)}"
        )

    # 1. Check CRC32
    actual_crc = crc32_bytes(payload)
    if actual_crc != header.payload_crc32:
        raise FrameCorruptionError(
            f"Payload CRC32 mismatch in frame {header.frame_seq}: expected {header.payload_crc32:#010x}, got {actual_crc:#010x}"
        )

    # 2. Check SHA-256
    actual_sha = sha256_bytes_raw(payload)
    if actual_sha != header.payload_sha256:
        raise FrameCorruptionError(
            f"Payload SHA-256 mismatch in frame {header.frame_seq}: expected {header.payload_sha256.hex()}, got {actual_sha.hex()}"
        )

    return header, payload


@dataclass
class GlobalVaultMeta:
    """
    High-level metadata serialized in Frame 0.
    Self-describes the entire archive, encoding parameters, and manifest.
    """
    version: int
    created_at: str
    codec: str
    pixel_format: str
    width: int
    height: int
    fps: int
    total_payload_bytes: int
    total_frames: int
    total_files: int
    total_dirs: int
    encrypted: bool
    kdf_salt: Optional[str]
    global_sha256: str
    manifest: list[dict[str, Any]]

    def serialize(self) -> bytes:
        """
        Serializes metadata into:
        [GLOBAL_META_MAGIC (8B)][JSON_LEN (4B)][CRC32 (4B)][JSON_BYTES]
        """
        json_bytes = json.dumps(asdict(self), ensure_ascii=False, indent=None).encode("utf-8")
        meta_len = len(json_bytes)
        meta_crc = crc32_bytes(json_bytes)
        return GLOBAL_META_MAGIC + struct.pack(">II", meta_len, meta_crc) + json_bytes

    @classmethod
    def deserialize(cls, data: bytes) -> Tuple[GlobalVaultMeta, int]:
        """
        Deserializes metadata from stream.
        Returns:
            (GlobalVaultMeta, bytes_consumed)
        """
        if len(data) < 16:
            raise FramingError(f"Data too short for GlobalVaultMeta header: {len(data)} bytes")

        magic = data[:8]
        if magic != GLOBAL_META_MAGIC:
            raise InvalidMagicError(f"Invalid GlobalVaultMeta magic: {magic!r}, expected {GLOBAL_META_MAGIC!r}")

        meta_len, expected_crc = struct.unpack(">II", data[8:16])
        total_len = 16 + meta_len
        if len(data) < total_len:
            raise FramingError(f"Incomplete GlobalVaultMeta: expected {total_len} bytes, got {len(data)}")

        json_bytes = data[16:total_len]
        actual_crc = crc32_bytes(json_bytes)
        if actual_crc != expected_crc:
            raise FrameCorruptionError(
                f"GlobalVaultMeta CRC32 mismatch! Expected {expected_crc:#010x}, got {actual_crc:#010x}"
            )

        meta_dict = json.loads(json_bytes.decode("utf-8"))
        return cls(**meta_dict), total_len
