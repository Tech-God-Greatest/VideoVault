"""
Streaming archive unpacker, directory tree reconstructor, and manifest integrity verifier.
Safely extracts files from a streaming tar pipe, restores metadata, and validates per-file SHA-256 hashes.
"""

from __future__ import annotations

import io
import os
import queue
import shutil
import tarfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from video_vault.utils.hashing import sha256_file


class ExtractionError(Exception):
    """Raised when extraction or validation encounters an error."""
    pass


@dataclass
class RestoredItemReport:
    rel_path: str
    size: int
    is_dir: bool
    status: str  # "VERIFIED_OK", "HASH_MISMATCH", "EXTRACTION_FAILED", "MISSING"
    expected_sha256: str = ""
    actual_sha256: str = ""
    error_message: str = ""


@dataclass
class ReconstructionReport:
    success: bool
    total_files: int
    total_dirs: int
    total_bytes: int
    verified_files: int
    corrupted_files: int
    missing_files: int
    items: List[RestoredItemReport] = field(default_factory=list)
    global_sha256_match: bool = False
    details: str = ""


class StreamingTarPipe(io.RawIOBase):
    """
    Streaming reader adapter that receives pushed chunks and provides
    a standard readable file-like interface for `tarfile.open(mode='r|', ...)`.
    """

    def __init__(self, max_queue: int = 32):
        self.queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=max_queue)
        self._current_chunk = bytearray()
        self._eof = False

    def readable(self) -> bool:
        return True

    def push(self, data: bytes) -> None:
        """Pushes data into the pipe from the feeder."""
        if data:
            self.queue.put(data)

    def close_feeder(self) -> None:
        """Signals EOF to the reader."""
        self.queue.put(None)

    def readinto(self, b) -> int:
        if self._eof and not self._current_chunk:
            return 0

        while not self._current_chunk:
            chunk = self.queue.get()
            if chunk is None:
                self._eof = True
                return 0
            self._current_chunk.extend(chunk)

        n = min(len(b), len(self._current_chunk))
        b[:n] = self._current_chunk[:n]
        del self._current_chunk[:n]
        return n


class VaultUnpacker:
    """
    Manages streaming extraction, directory restoration, and manifest hash validation.
    """

    def __init__(
        self,
        dest_dir: Union[str, Path],
        manifest_items: Optional[List[Dict[str, Any]]] = None,
        on_item_extracted: Optional[Callable[[str, int, str], None]] = None,
    ):
        self.dest_dir = Path(dest_dir).resolve()
        self.manifest_items = {item["rel_path"]: item for item in (manifest_items or [])}
        self.on_item_extracted = on_item_extracted
        self.pipe = StreamingTarPipe()
        self.report_items: List[RestoredItemReport] = []
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_exc: Optional[Exception] = None
        self._extracted_paths: set[str] = set()

    def start(self) -> None:
        """Starts background extraction thread listening to pipe."""
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self._worker_thread = threading.Thread(target=self._extraction_worker, daemon=True)
        self._worker_thread.start()

    def feed(self, chunk: bytes) -> None:
        """Feeds a stream chunk into the unpacker pipe."""
        if self._worker_exc:
            raise self._worker_exc
        self.pipe.push(chunk)

    def finish(self, global_sha256_match: bool = True) -> ReconstructionReport:
        """
        Closes stream, waits for extractor to complete, verifies hashes,
        and returns the ReconstructionReport.
        """
        self.pipe.close_feeder()
        if self._worker_thread:
            self._worker_thread.join()

        if self._worker_exc:
            raise ExtractionError(f"Extraction failed: {self._worker_exc}") from self._worker_exc

        # Check for missing items from manifest
        for rel_path, item_meta in self.manifest_items.items():
            if rel_path not in self._extracted_paths:
                self.report_items.append(
                    RestoredItemReport(
                        rel_path=rel_path,
                        size=item_meta.get("size", 0),
                        is_dir=item_meta.get("is_dir", False),
                        status="MISSING",
                        expected_sha256=item_meta.get("sha256", ""),
                        actual_sha256="",
                        error_message="Item was recorded in manifest but not present in extracted archive stream.",
                    )
                )

        # Aggregate counts
        total_files = sum(1 for it in self.report_items if not it.is_dir)
        total_dirs = sum(1 for it in self.report_items if it.is_dir)
        total_bytes = sum(it.size for it in self.report_items if not it.is_dir)
        verified_files = sum(1 for it in self.report_items if it.status == "VERIFIED_OK" and not it.is_dir)
        corrupted_files = sum(1 for it in self.report_items if it.status == "HASH_MISMATCH" and not it.is_dir)
        missing_files = sum(1 for it in self.report_items if it.status == "MISSING")

        success = (corrupted_files == 0) and (missing_files == 0) and global_sha256_match

        return ReconstructionReport(
            success=success,
            total_files=total_files,
            total_dirs=total_dirs,
            total_bytes=total_bytes,
            verified_files=verified_files,
            corrupted_files=corrupted_files,
            missing_files=missing_files,
            items=self.report_items,
            global_sha256_match=global_sha256_match,
            details="All files reconstructed and verified bit-for-bit."
            if success
            else f"Reconstruction failed: {corrupted_files} corrupted files, {missing_files} missing files.",
        )

    def _safe_extract_path(self, member_name: str) -> Path:
        """Sanitizes target path preventing path traversal."""
        target_path = (self.dest_dir / member_name).resolve()
        if not str(target_path).startswith(str(self.dest_dir)):
            raise ExtractionError(f"Attempted directory traversal in archive member: {member_name}")
        return target_path

    def _extraction_worker(self) -> None:
        try:
            with tarfile.open(mode="r|", fileobj=self.pipe) as tar:
                for member in tar:
                    target_path = self._safe_extract_path(member.name)
                    rel_name = member.name.replace("\\", "/").rstrip("/")

                    if member.isdir():
                        target_path.mkdir(parents=True, exist_ok=True)
                        self._extracted_paths.add(rel_name)
                        item_report = RestoredItemReport(
                            rel_path=rel_name,
                            size=0,
                            is_dir=True,
                            status="VERIFIED_OK",
                        )
                        self.report_items.append(item_report)
                        if self.on_item_extracted:
                            self.on_item_extracted(rel_name, 0, "VERIFIED_OK")

                    elif member.isreg():
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        # Extract file
                        f_in = tar.extractfile(member)
                        if f_in is not None:
                            with open(target_path, "wb") as f_out:
                                shutil.copyfileobj(f_in, f_out)

                        # Restore mtime
                        try:
                            os.utime(target_path, (member.mtime, member.mtime))
                        except Exception:
                            pass

                        # Verify SHA-256 against manifest
                        actual_hash = sha256_file(target_path)
                        expected_item = self.manifest_items.get(rel_name)
                        expected_hash = expected_item.get("sha256", "") if expected_item else ""

                        if expected_hash and actual_hash.lower() != expected_hash.lower():
                            status = "HASH_MISMATCH"
                            err = f"Hash mismatch: expected {expected_hash}, got {actual_hash}"
                        else:
                            status = "VERIFIED_OK"
                            err = ""

                        self._extracted_paths.add(rel_name)
                        item_report = RestoredItemReport(
                            rel_path=rel_name,
                            size=member.size,
                            is_dir=False,
                            status=status,
                            expected_sha256=expected_hash,
                            actual_sha256=actual_hash,
                            error_message=err,
                        )
                        self.report_items.append(item_report)
                        if self.on_item_extracted:
                            self.on_item_extracted(rel_name, member.size, status)

        except Exception as e:
            self._worker_exc = e
