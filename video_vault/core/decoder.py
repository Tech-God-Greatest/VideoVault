"""
High-level streaming VaultDecoder and Integrity Verifier.
Reads frames, verifies headers and payload checksums (CRC32 & SHA-256), pinpoints corruptions,
decrypts/unwhitens stream, drives streaming extraction, and generates comprehensive reconstruction reports.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from video_vault.core.config import (
    DEFAULT_CONFIG,
    FLAG_ENCRYPTED,
    FLAG_FIRST_FRAME,
    FLAG_LAST_FRAME,
    PIX_FMT_RGB24,
    PIX_FMT_RGBA,
    StreamConfig,
)
from video_vault.core.crypto import (
    AuthenticationFailedError,
    CryptoError,
    derive_key,
    decrypt_payload_gcm,
    unwhiten_stream,
)
from video_vault.core.framing import (
    FrameCorruptionError,
    FrameHeader,
    GlobalVaultMeta,
    InvalidMagicError,
    disect_frame,
    unpack_frame_header,
)
from video_vault.core.unpacker import ReconstructionReport, VaultUnpacker
from video_vault.core.video_backend import LosslessVideoReader, probe_video_container
from video_vault.utils.hashing import StreamHashTracker


class PasswordRequiredError(CryptoError):
    """Raised when decoding an encrypted vault without providing a password."""
    def __init__(self, salt_hex: Optional[str] = None):
        super().__init__("This Video Vault is encrypted with AES-256-GCM. A password is required.")
        self.salt_hex = salt_hex


@dataclass
class DecodeProgress:
    current_frame: int
    total_frames: int
    bytes_processed: int
    mb_per_sec: float
    fps: float
    elapsed_seconds: float
    current_file: str


@dataclass
class FrameCorruptionInfo:
    frame_seq: int
    timestamp_seconds: float
    error_type: str
    details: str


@dataclass
class DecodeResult:
    success: bool
    total_frames_read: int
    total_bytes_decoded: int
    duration_seconds: float
    average_speed_mb_s: float
    reconstruction_report: Optional[ReconstructionReport]
    corruption_errors: List[FrameCorruptionInfo]
    global_meta: Optional[GlobalVaultMeta]
    details: str = ""


class VaultDecoder:
    """
    Decodes and verifies a Video Vault 4K container.
    """

    def __init__(
        self,
        video_path: Union[str, Path],
        dest_dir: Optional[Union[str, Path]] = None,
        password: Optional[str] = None,
        config: StreamConfig = DEFAULT_CONFIG,
        progress_callback: Optional[Callable[[DecodeProgress], None]] = None,
    ):
        self.video_path = Path(video_path).resolve()
        self.dest_dir = Path(dest_dir).resolve() if dest_dir else None
        self.password = password

        # Automatically probe container to detect codec, resolution, and pixel format
        probed_cfg = probe_video_container(self.video_path) if self.video_path.is_file() else None
        self.config = probed_cfg if probed_cfg else config
        self.progress_callback = progress_callback

        self._is_cancelled = False
        self._current_file = ""

    def cancel(self) -> None:
        self._is_cancelled = True

    def _on_item_extracted(self, rel_path: str, size: int, status: str) -> None:
        self._current_file = rel_path

    def probe(self) -> GlobalVaultMeta:
        """
        Quickly inspects Frame 0 of the video to extract container metadata
        (version, file count, total size, manifest) without extracting files.
        """
        reader = LosslessVideoReader(self.video_path, self.config)
        try:
            frame_0 = reader.read_frame()
            if not frame_0:
                raise ValueError("Video is empty or cannot be read.")

            header, payload = disect_frame(frame_0)

            if header.is_encrypted:
                if not self.password:
                    raise PasswordRequiredError(header.salt.hex())
                salt = header.salt
                aes_key = derive_key(self.password, salt)
                raw_payload = decrypt_payload_gcm(
                    payload, aes_key, salt, frame_seq=0, associated_data=(0).to_bytes(4, "big")
                )
            else:
                raw_payload = unwhiten_stream(payload, frame_seq=0, salt=header.salt)

            meta, _ = GlobalVaultMeta.deserialize(raw_payload)
            return meta
        finally:
            reader.close()

    def decode(self, verify_only: bool = False) -> DecodeResult:
        """
        Executes full decoding or verification of the video stream.
        If verify_only is True, tests all frame checksums and crypto without extracting to disk.
        """
        t_start = time.time()
        corruptions: List[FrameCorruptionInfo] = []
        global_meta: Optional[GlobalVaultMeta] = None
        unpacker: Optional[VaultUnpacker] = None

        reader = LosslessVideoReader(self.video_path, self.config)
        stream_hasher = StreamHashTracker()

        frame_seq = 0
        expected_seq = 0
        total_frames_expected = 0
        eos_reached = False
        total_payload_bytes = 0
        last_progress_time = time.time()

        aes_key = None
        salt = None

        try:
            while True:
                if self._is_cancelled:
                    raise InterruptedError("Decoding was cancelled by user.")

                frame_data = reader.read_frame()
                if frame_data is None:
                    # Clean EOF from reader
                    break

                timestamp_sec = frame_seq / max(1, self.config.fps)

                # Validate frame checksums and envelope
                try:
                    header, payload = disect_frame(frame_data)
                except (FrameCorruptionError, InvalidMagicError) as e:
                    corruptions.append(
                        FrameCorruptionInfo(
                            frame_seq=frame_seq,
                            timestamp_seconds=timestamp_sec,
                            error_type="CHECKSUM_OR_HEADER_FAILURE",
                            details=str(e),
                        )
                    )
                    break

                # Sequence continuity check
                if header.frame_seq != expected_seq:
                    corruptions.append(
                        FrameCorruptionInfo(
                            frame_seq=header.frame_seq,
                            timestamp_seconds=timestamp_sec,
                            error_type="SEQUENCE_GAP",
                            details=f"Expected frame sequence {expected_seq}, but received {header.frame_seq}. Dropped/missing frames detected!",
                        )
                    )
                    break

                if frame_seq == 0:
                    total_frames_expected = header.total_frames
                    salt = header.salt
                    if header.is_encrypted:
                        if not self.password:
                            raise PasswordRequiredError(header.salt.hex())
                        aes_key = derive_key(self.password, salt)

                # Decrypt or unwhiten
                try:
                    if header.is_encrypted:
                        if not aes_key or salt is None:
                            raise PasswordRequiredError(header.salt.hex())
                        raw_payload = decrypt_payload_gcm(
                            payload,
                            aes_key,
                            salt,
                            frame_seq=header.frame_seq,
                            associated_data=header.frame_seq.to_bytes(4, "big"),
                        )
                    else:
                        raw_payload = unwhiten_stream(payload, frame_seq=header.frame_seq, salt=header.salt)
                except AuthenticationFailedError as e:
                    corruptions.append(
                        FrameCorruptionInfo(
                            frame_seq=header.frame_seq,
                            timestamp_seconds=timestamp_sec,
                            error_type="DECRYPTION_AUTH_FAILED",
                            details=str(e),
                        )
                    )
                    break

                # Frame 0 setup: extract GlobalVaultMeta
                data_to_feed = raw_payload
                if frame_seq == 0:
                    stream_hasher.update(raw_payload)
                    global_meta, meta_bytes_consumed = GlobalVaultMeta.deserialize(raw_payload)
                    data_to_feed = raw_payload[meta_bytes_consumed:]
                    total_frames_expected = max(total_frames_expected, global_meta.total_frames)

                    if not verify_only and self.dest_dir:
                        unpacker = VaultUnpacker(
                            dest_dir=self.dest_dir,
                            manifest_items=global_meta.manifest,
                            on_item_extracted=self._on_item_extracted,
                        )
                        unpacker.start()
                else:
                    stream_hasher.update(raw_payload)

                total_payload_bytes += len(data_to_feed)

                # Feed unpacker
                if unpacker and data_to_feed:
                    unpacker.feed(data_to_feed)

                frame_seq += 1
                expected_seq += 1

                # Progress callback
                now = time.time()
                elapsed = max(0.001, now - t_start)
                speed_mb_s = (total_payload_bytes / (1024 * 1024)) / elapsed
                fps = frame_seq / elapsed

                if self.progress_callback and (now - last_progress_time >= 0.1 or header.is_last_frame):
                    last_progress_time = now
                    self.progress_callback(
                        DecodeProgress(
                            current_frame=frame_seq,
                            total_frames=max(frame_seq, total_frames_expected),
                            bytes_processed=total_payload_bytes,
                            mb_per_sec=speed_mb_s,
                            fps=fps,
                            elapsed_seconds=elapsed,
                            current_file=self._current_file,
                        )
                    )

                if header.is_last_frame:
                    eos_reached = True
                    break

        finally:
            reader.close()

        # Check for truncated video
        if not eos_reached and not corruptions:
            corruptions.append(
                FrameCorruptionInfo(
                    frame_seq=frame_seq,
                    timestamp_seconds=frame_seq / max(1, self.config.fps),
                    error_type="INCOMPLETE_STREAM",
                    details=f"Stream ended unexpectedly at frame {frame_seq} before encountering End-of-Stream flag (expected at least {total_frames_expected} frames). Video is truncated!",
                )
            )

        # Finalize unpacker
        reconstruction_report: Optional[ReconstructionReport] = None
        global_sha256_match = False
        if global_meta and global_meta.global_sha256:
            global_sha256_match = (stream_hasher.sha256_hex.lower() == global_meta.global_sha256.lower())
        else:
            global_sha256_match = True

        if unpacker:
            reconstruction_report = unpacker.finish(global_sha256_match=global_sha256_match)

        t_total = max(0.001, time.time() - t_start)
        success = (len(corruptions) == 0) and (reconstruction_report.success if reconstruction_report else True)

        return DecodeResult(
            success=success,
            total_frames_read=frame_seq,
            total_bytes_decoded=total_payload_bytes,
            duration_seconds=t_total,
            average_speed_mb_s=(total_payload_bytes / (1024 * 1024)) / t_total,
            reconstruction_report=reconstruction_report,
            corruption_errors=corruptions,
            global_meta=global_meta,
            details="Decoded and verified successfully without errors."
            if success
            else f"Decode finished with {len(corruptions)} corruption errors.",
        )
