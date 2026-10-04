"""
High-level streaming VaultEncoder engine.
Orchestrates scanning, packaging, crypto/whitening, framing, FFmpeg lossless encoding,
live progress reporting, pause/cancel controls, and thumbnail generation.
"""

from __future__ import annotations

import datetime
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Union

import numpy as np

from video_vault.core.config import (
    DEFAULT_CONFIG,
    FLAG_ENCRYPTED,
    FLAG_FIRST_FRAME,
    FLAG_LAST_FRAME,
    FLAG_RGBA_PIXELS,
    FLAG_YUV_LUMA,
    PIX_FMT_BGRA,
    PIX_FMT_RGBA,
    PIX_FMT_YUV420P,
    StreamConfig,
)
from video_vault.core.crypto import (
    derive_key,
    encrypt_payload_gcm,
    generate_salt,
    whiten_stream,
)
from video_vault.core.framing import (
    GlobalVaultMeta,
    assemble_frame,
    pack_frame_header,
)
from video_vault.core.packager import (
    ArchiveManifest,
    scan_inputs,
    stream_package_archive,
)
from video_vault.core.video_backend import LosslessVideoWriter
from video_vault.utils.hashing import (
    StreamHashTracker,
    crc32_bytes,
    sha256_bytes_raw,
)


@dataclass
class EncodeProgress:
    current_frame: int
    total_frames_estimate: int
    bytes_processed: int
    total_bytes: int
    mb_per_sec: float
    fps: float
    elapsed_seconds: float
    estimated_remaining_seconds: float
    current_file: str
    preview_thumbnail: Optional[np.ndarray] = None


@dataclass
class EncodeResult:
    success: bool
    output_path: Path
    total_frames: int
    total_payload_bytes: int
    video_size_bytes: int
    duration_seconds: float
    average_speed_mb_s: float
    manifest: ArchiveManifest
    global_sha256: str
    details: str = ""


class VaultEncoder:
    """
    Encodes arbitrary files/folders into a lossless 4K video container.
    """

    def __init__(
        self,
        inputs: Sequence[Union[str, Path]],
        output_path: Union[str, Path],
        config: StreamConfig = DEFAULT_CONFIG,
        password: Optional[str] = None,
        progress_callback: Optional[Callable[[EncodeProgress], None]] = None,
    ):
        self.inputs = [Path(p).resolve() for p in inputs]
        self.output_path = Path(output_path).resolve()
        self.config = config
        self.password = password
        self.progress_callback = progress_callback

        self._is_paused = False
        self._is_cancelled = False
        self._current_file = ""

    def pause(self) -> None:
        self._is_paused = True

    def resume(self) -> None:
        self._is_paused = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def _on_file_start(self, rel_path: str) -> None:
        self._current_file = rel_path

    def encode(self) -> EncodeResult:
        """
        Executes the full encoding process.
        """
        t_start = time.time()

        # 1. Pre-flight scanning
        manifest = scan_inputs(self.inputs)

        # 2. Key derivation & Crypto setup
        is_encrypted = bool(self.password)
        salt = generate_salt(16) if is_encrypted else (b"\x00" * 16)
        aes_key = derive_key(self.password, salt) if is_encrypted else None

        # Effective maximum payload capacity per frame
        # AES-GCM produces a 16-byte auth tag
        gcm_overhead = 16 if is_encrypted else 0
        max_frame_payload = self.config.max_payload_per_frame - gcm_overhead

        # 3. Build GlobalVaultMeta placeholder (manifest items included)
        meta_items = [asdict(item) for item in manifest.items]
        meta = GlobalVaultMeta(
            version=1,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            codec=self.config.codec,
            pixel_format=self.config.pixel_format,
            width=self.config.width,
            height=self.config.height,
            fps=self.config.fps,
            total_payload_bytes=manifest.total_uncompressed_bytes,
            total_frames=0,  # Will be finalized or read from stream
            total_files=manifest.total_files,
            total_dirs=manifest.total_dirs,
            encrypted=is_encrypted,
            kdf_salt=salt.hex() if is_encrypted else None,
            global_sha256="",  # Updated after stream pass
            manifest=meta_items,
        )
        serialized_meta = meta.serialize()
        meta_len = len(serialized_meta)

        # Estimate frame count: tar headers add roughly 1KB per file + 10KB end-of-tar blocks
        estimated_tar_bytes = manifest.total_uncompressed_bytes + (manifest.total_files + manifest.total_dirs + 5) * 1024
        estimated_total_payload = estimated_tar_bytes + meta_len
        estimated_frames = max(1, (estimated_total_payload + max_frame_payload - 1) // max_frame_payload)

        # 4. Stream Tar Packaging & Video Encoding
        stream_hasher = StreamHashTracker()
        frame_seq = 0
        total_bytes_streamed = 0
        last_progress_time = time.time()

        writer = LosslessVideoWriter(self.output_path, self.config)

        try:
            # We yield chunks from stream_package_archive
            tar_stream = stream_package_archive(
                self.inputs,
                chunk_size=max_frame_payload,
                manifest=manifest,
                on_file_start=self._on_file_start,
            )

            # Frame 0 buffer begins with serialized_meta
            frame_buffer = bytearray()
            frame_buffer.extend(serialized_meta)
            stream_hasher.update(serialized_meta)
            total_bytes_streamed += len(serialized_meta)

            stream_active = True
            while stream_active or frame_buffer:
                if self._is_cancelled:
                    raise InterruptedError("Encoding was cancelled by user.")

                while self._is_paused:
                    time.sleep(0.1)
                    if self._is_cancelled:
                        raise InterruptedError("Encoding was cancelled by user.")

                # Pull from tar stream until frame_buffer has max_frame_payload or stream ends
                while len(frame_buffer) < max_frame_payload and stream_active:
                    try:
                        chunk = next(tar_stream)
                        stream_hasher.update(chunk)
                        total_bytes_streamed += len(chunk)
                        frame_buffer.extend(chunk)
                    except StopIteration:
                        stream_active = False
                        break

                # Extract payload for this frame
                take_len = min(len(frame_buffer), max_frame_payload)
                raw_payload = bytes(frame_buffer[:take_len])
                del frame_buffer[:take_len]

                is_first = (frame_seq == 0)
                # It is last frame if stream has finished AND frame_buffer is now empty
                is_last = (not stream_active and len(frame_buffer) == 0)

                # Encrypt or Whiten payload
                if is_encrypted and aes_key:
                    transformed_payload = encrypt_payload_gcm(
                        raw_payload, aes_key, salt, frame_seq, associated_data=frame_seq.to_bytes(4, "big")
                    )
                else:
                    transformed_payload = whiten_stream(raw_payload, frame_seq, salt)

                # Build frame header
                flags = 0
                if is_encrypted:
                    flags |= FLAG_ENCRYPTED
                if is_first:
                    flags |= FLAG_FIRST_FRAME
                if is_last:
                    flags |= FLAG_LAST_FRAME
                if self.config.pixel_format in (PIX_FMT_RGBA, PIX_FMT_BGRA):
                    flags |= FLAG_RGBA_PIXELS
                elif self.config.pixel_format == PIX_FMT_YUV420P:
                    flags |= FLAG_YUV_LUMA

                payload_crc = crc32_bytes(transformed_payload)
                payload_sha = sha256_bytes_raw(transformed_payload)

                header_bytes = pack_frame_header(
                    frame_seq=frame_seq,
                    total_frames=estimated_frames,
                    payload_len=len(transformed_payload),
                    payload_crc32=payload_crc,
                    payload_sha256=payload_sha,
                    salt=salt,
                    flags=flags,
                )

                # Assemble complete frame data plane
                full_frame = assemble_frame(
                    header_bytes=header_bytes,
                    payload=transformed_payload,
                    frame_total_size=self.config.data_capacity_per_frame,
                )

                # Write frame to video
                writer.write_frame(full_frame)
                frame_seq += 1

                # Generate low-res thumbnail for GUI preview (e.g. 120x68)
                now = time.time()
                elapsed = max(0.001, now - t_start)
                speed_mb_s = (total_bytes_streamed / (1024 * 1024)) / elapsed
                fps = frame_seq / elapsed
                remaining_frames = max(0, estimated_frames - frame_seq)
                eta = remaining_frames / fps if fps > 0 else 0.0

                if self.progress_callback and (now - last_progress_time >= 0.1 or is_last):
                    last_progress_time = now
                    # Fast downsample 4K frame to 160x90 for preview
                    # 3840/160 = 24, 2160/90 = 24
                    if self.config.pixel_format == PIX_FMT_YUV420P:
                        arr = np.frombuffer(full_frame, dtype=np.uint8).reshape(
                            (self.config.height, self.config.width)
                        )
                        gray_sub = arr[::24, ::24]
                        thumb = np.stack([gray_sub, gray_sub, gray_sub], axis=-1).copy()
                    else:
                        bpp = int(self.config.bytes_per_pixel)
                        arr = np.frombuffer(full_frame, dtype=np.uint8).reshape(
                            (self.config.height, self.config.width, bpp)
                        )
                        thumb = arr[::24, ::24, :3].copy()  # RGB thumbnail

                    self.progress_callback(
                        EncodeProgress(
                            current_frame=frame_seq,
                            total_frames_estimate=max(frame_seq, estimated_frames),
                            bytes_processed=total_bytes_streamed,
                            total_bytes=manifest.total_uncompressed_bytes,
                            mb_per_sec=speed_mb_s,
                            fps=fps,
                            elapsed_seconds=elapsed,
                            estimated_remaining_seconds=eta,
                            current_file=self._current_file,
                            preview_thumbnail=thumb,
                        )
                    )

                if is_last:
                    break

        except Exception as e:
            writer.close()
            if self.output_path.exists():
                try:
                    self.output_path.unlink()
                except Exception:
                    pass
            raise e

        writer.close()
        t_total = max(0.001, time.time() - t_start)
        video_size = self.output_path.stat().st_size if self.output_path.exists() else 0

        return EncodeResult(
            success=True,
            output_path=self.output_path,
            total_frames=frame_seq,
            total_payload_bytes=total_bytes_streamed,
            video_size_bytes=video_size,
            duration_seconds=t_total,
            average_speed_mb_s=(total_bytes_streamed / (1024 * 1024)) / t_total,
            manifest=manifest,
            global_sha256=stream_hasher.sha256_hex,
            details=f"Successfully encoded {manifest.total_files} files into {frame_seq} 4K frames ({video_size / (1024*1024):.1f} MB video).",
        )
