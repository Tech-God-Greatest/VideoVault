"""
Streaming file & folder packager and manifest generator.
Recursively packages arbitrary files and directories into a streaming POSIX PAX tar container,
computing per-file SHA-256 checksums, timestamps, and structure.
"""

from __future__ import annotations

import io
import os
import queue
import tarfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Generator, List, Optional, Sequence, Union

from video_vault.utils.hashing import sha256_file


@dataclass
class FileManifestItem:
    rel_path: str
    size: int
    mtime: float
    is_dir: bool
    sha256: str = ""


@dataclass
class ArchiveManifest:
    total_files: int
    total_dirs: int
    total_uncompressed_bytes: int
    items: List[FileManifestItem]


class PackagingError(Exception):
    """Raised when file scanning or packaging fails."""
    pass


def scan_inputs(
    inputs: Sequence[Union[str, Path]],
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> ArchiveManifest:
    """
    Scans input files and directories, generating a comprehensive manifest
    with per-file SHA-256 checksums and hierarchy before packaging.
    """
    items: List[FileManifestItem] = []
    total_files = 0
    total_dirs = 0
    total_bytes = 0

    seen_rel_paths = set()

    for item_input in inputs:
        p = Path(item_input).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Input path does not exist: {p}")

        if p.is_file():
            rel_path = p.name
            if rel_path in seen_rel_paths:
                # Disambiguate if multiple top-level files have same name
                rel_path = f"{p.parent.name}_{p.name}"
            seen_rel_paths.add(rel_path)

            stat = p.stat()
            file_hash = sha256_file(p)
            items.append(
                FileManifestItem(
                    rel_path=rel_path,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    is_dir=False,
                    sha256=file_hash,
                )
            )
            total_files += 1
            total_bytes += stat.st_size
            if progress_callback:
                progress_callback(rel_path, stat.st_size)

        elif p.is_dir():
            base_dir_name = p.name
            # Add root directory entry
            root_rel = base_dir_name
            seen_rel_paths.add(root_rel)
            items.append(
                FileManifestItem(
                    rel_path=root_rel,
                    size=0,
                    mtime=p.stat().st_mtime,
                    is_dir=True,
                    sha256="",
                )
            )
            total_dirs += 1

            for root, dirs, files in os.walk(p):
                rel_root = os.path.relpath(root, p)

                # Record empty or sub directories
                for d in dirs:
                    dir_full_path = Path(root) / d
                    d_rel = (Path(base_dir_name) / rel_root / d).as_posix().lstrip("./")
                    if d_rel not in seen_rel_paths:
                        seen_rel_paths.add(d_rel)
                        items.append(
                            FileManifestItem(
                                rel_path=d_rel,
                                size=0,
                                mtime=dir_full_path.stat().st_mtime,
                                is_dir=True,
                                sha256="",
                            )
                        )
                        total_dirs += 1

                for f in files:
                    file_full_path = Path(root) / f
                    f_rel = (Path(base_dir_name) / rel_root / f).as_posix().lstrip("./")
                    if f_rel in seen_rel_paths:
                        continue
                    seen_rel_paths.add(f_rel)

                    stat = file_full_path.stat()
                    file_hash = sha256_file(file_full_path)
                    items.append(
                        FileManifestItem(
                            rel_path=f_rel,
                            size=stat.st_size,
                            mtime=stat.st_mtime,
                            is_dir=False,
                            sha256=file_hash,
                        )
                    )
                    total_files += 1
                    total_bytes += stat.st_size
                    if progress_callback:
                        progress_callback(f_rel, stat.st_size)

    return ArchiveManifest(
        total_files=total_files,
        total_dirs=total_dirs,
        total_uncompressed_bytes=total_bytes,
        items=items,
    )


class ChunkedStreamBuffer(io.RawIOBase):
    """
    A streaming raw I/O adapter that collects bytes written by tarfile
    and emits them in fixed-size chunks through a thread-safe queue.
    """

    def __init__(self, chunk_size: int, max_queue: int = 32):
        self.chunk_size = chunk_size
        self._buf = bytearray()
        self.queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=max_queue)
        self.exception: Optional[Exception] = None

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        if self.exception:
            raise self.exception
        self._buf.extend(b)
        while len(self._buf) >= self.chunk_size:
            chunk = bytes(self._buf[: self.chunk_size])
            del self._buf[: self.chunk_size]
            self.queue.put(chunk)
        return len(b)

    def close(self):
        if self._buf:
            self.queue.put(bytes(self._buf))
            self._buf.clear()
        self.queue.put(None)  # Sentinel for EOF
        super().close()


def stream_package_archive(
    inputs: Sequence[Union[str, Path]],
    chunk_size: int,
    manifest: ArchiveManifest,
    on_file_start: Optional[Callable[[str], None]] = None,
) -> Generator[bytes, None, None]:
    """
    Streams a POSIX PAX tar archive in fixed `chunk_size` chunks.
    Runs tar building in a worker thread and yields chunks as they are generated,
    maintaining a constant tiny memory footprint.
    """
    stream_buf = ChunkedStreamBuffer(chunk_size=chunk_size)

    def worker():
        try:
            with tarfile.open(mode="w|", fileobj=stream_buf, format=tarfile.PAX_FORMAT) as tar:
                # Add all items based on inputs
                for item_input in inputs:
                    p = Path(item_input).resolve()
                    if p.is_file():
                        arcname = p.name
                        if on_file_start:
                            on_file_start(arcname)
                        tar.add(p, arcname=arcname, recursive=False)
                    elif p.is_dir():
                        base_name = p.name
                        # Add the root directory itself to tar
                        tar.add(p, arcname=base_name, recursive=False)
                        # Recursively add dir contents
                        for root, dirs, files in os.walk(p):
                            rel_root = os.path.relpath(root, p)
                            dir_arcname = (Path(base_name) / rel_root).as_posix().lstrip("./")
                            if rel_root != ".":
                                tar.add(root, arcname=dir_arcname, recursive=False)

                            for f in files:
                                f_path = Path(root) / f
                                f_arcname = (Path(base_name) / rel_root / f).as_posix().lstrip("./")
                                if on_file_start:
                                    on_file_start(f_arcname)
                                tar.add(f_path, arcname=f_arcname, recursive=False)
        except Exception as e:
            stream_buf.exception = e
        finally:
            stream_buf.close()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    while True:
        chunk = stream_buf.queue.get()
        if chunk is None:
            break
        yield chunk

    t.join()
    if stream_buf.exception:
        raise PackagingError(f"Failed while packaging archive: {stream_buf.exception}") from stream_buf.exception
