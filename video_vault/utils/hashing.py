"""
Fast incremental and buffer-level hashing utilities (SHA-256 and CRC32).
"""

from __future__ import annotations

import hashlib
import zlib
from pathlib import Path
from typing import Union


def crc32_bytes(data: bytes, seed: int = 0) -> int:
    """Computes standard IEEE 802.3 32-bit CRC as an unsigned 32-bit integer."""
    return zlib.crc32(data, seed) & 0xFFFFFFFF


def sha256_bytes(data: bytes) -> str:
    """Returns SHA-256 hex digest for byte data."""
    return hashlib.sha256(data).hexdigest()


def sha256_bytes_raw(data: bytes) -> bytes:
    """Returns raw 32-byte SHA-256 digest."""
    return hashlib.sha256(data).digest()


def sha256_file(filepath: Union[str, Path], chunk_size: int = 1024 * 1024) -> str:
    """
    Computes SHA-256 hex digest for a file on disk in chunks,
    minimizing memory footprint.
    """
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


class StreamHashTracker:
    """
    Tracks both CRC32 and SHA-256 incrementally across a streaming data flow.
    """

    def __init__(self) -> None:
        self._sha256 = hashlib.sha256()
        self._crc32 = 0
        self._total_bytes = 0

    def update(self, data: bytes) -> None:
        self._sha256.update(data)
        self._crc32 = zlib.crc32(data, self._crc32) & 0xFFFFFFFF
        self._total_bytes += len(data)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def crc32(self) -> int:
        return self._crc32

    @property
    def sha256_hex(self) -> str:
        return self._sha256.hexdigest()

    @property
    def sha256_digest(self) -> bytes:
        return self._sha256.digest()
