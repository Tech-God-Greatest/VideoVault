"""
Command-line interface (CLI) for Video Vault 4K.
Provides subcommands: encode, decode, verify, info, and gui.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure UTF-8 output encoding on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from video_vault.core.config import (
    CODEC_FFV1,
    CODEC_H264,
    CODEC_HUFFYUV,
    CODEC_PNG,
    CODEC_RAW,
    DEFAULT_CONFIG,
    PIX_FMT_BGR24,
    PIX_FMT_BGRA,
    PIX_FMT_RGB24,
    PIX_FMT_RGBA,
    PIX_FMT_YUV420P,
    SUPPORTED_CODECS,
    StreamConfig,
)
from video_vault.core.decoder import DecodeProgress, VaultDecoder
from video_vault.core.encoder import EncodeProgress, VaultEncoder
from video_vault.utils.ffmpeg_finder import verify_ffmpeg


def _format_size(size_bytes: int) -> str:
    """Formats bytes into human readable string (KB, MB, GB)."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def handle_encode(args: argparse.Namespace) -> int:
    inputs = [Path(p) for p in args.inputs]
    output_path = Path(args.output)

    # Determine codec and pixel format (with Microsoft Player support)
    codec = args.codec
    if getattr(args, "ms_player", False) or codec in ("mp4", "h264", "libx264"):
        codec = CODEC_H264
        pix_fmt = PIX_FMT_YUV420P
        if output_path.suffix.lower() != ".mp4":
            output_path = output_path.with_suffix(".mp4")
    elif output_path.suffix.lower() == ".avi":
        if codec == CODEC_HUFFYUV:
            pix_fmt = PIX_FMT_RGB24
        else:
            codec = CODEC_RAW
            pix_fmt = PIX_FMT_BGR24 if args.rgb else PIX_FMT_BGRA
    elif args.rgb:
        pix_fmt = PIX_FMT_RGB24
    else:
        pix_fmt = PIX_FMT_RGBA

    config = StreamConfig(
        pixel_format=pix_fmt,
        codec=codec,
    )

    print("\n" + "=" * 60)
    print("  VIDEO VAULT 4K - ENCODE")
    print("=" * 60)
    print(f"Inputs:        {len(inputs)} item(s)")
    for p in inputs:
        print(f"               - {p}")
    print(f"Output Video:  {output_path}")
    print(f"Codec:         {config.codec}")
    print(f"Pixel Format:  {config.pixel_format} ({config.bytes_per_pixel} bytes/pixel)")
    print(f"Resolution:    {config.width}x{config.height} @ {config.fps} fps")
    print(f"Encrypted:     {'Yes (AES-256-GCM)' if args.password else 'No (Whitened static)'}")
    print("-" * 60)

    last_line_len = 0

    def on_progress(p: EncodeProgress):
        nonlocal last_line_len
        line = (
            f"\r[Frame {p.current_frame}/{p.total_frames_estimate}] "
            f"{_format_size(p.bytes_processed)} | "
            f"{p.mb_per_sec:.1f} MB/s ({p.fps:.1f} fps) | "
            f"ETA: {p.estimated_remaining_seconds:.1f}s | "
            f"{p.current_file[:30]}"
        )
        sys.stdout.write(line.ljust(last_line_len))
        sys.stdout.flush()
        last_line_len = len(line)

    encoder = VaultEncoder(
        inputs=inputs,
        output_path=output_path,
        config=config,
        password=args.password,
        progress_callback=on_progress,
    )

    t0 = time.time()
    try:
        result = encoder.encode()
        print("\n" + "-" * 60)
        print("[OK] ENCODING COMPLETE")
        print(f"Frames Created:     {result.total_frames}")
        print(f"Total Stream Data:  {_format_size(result.total_payload_bytes)}")
        print(f"Video File Size:    {_format_size(result.video_size_bytes)}")
        print(f"Duration:           {result.duration_seconds:.2f} seconds")
        print(f"Average Throughput: {result.average_speed_mb_s:.1f} MB/s")
        print(f"Files Packaged:     {result.manifest.total_files} files, {result.manifest.total_dirs} directories")
        print(f"Stream SHA-256:     {result.global_sha256}")
        print("=" * 60 + "\n")
        return 0
    except Exception as e:
        print(f"\n\n[ERROR] Encoding failed: {e}")
        return 1


def handle_decode(args: argparse.Namespace) -> int:
    config = StreamConfig(
        pixel_format=PIX_FMT_RGB24 if args.rgb else PIX_FMT_RGBA,
    )

    video_path = Path(args.video)
    dest_dir = Path(args.output)

    print("\n" + "=" * 60)
    print("  VIDEO VAULT 4K - DECODE & RECONSTRUCT")
    print("=" * 60)
    print(f"Video File:        {video_path}")
    print(f"Destination Dir:   {dest_dir}")
    print("-" * 60)

    last_line_len = 0

    def on_progress(p: DecodeProgress):
        nonlocal last_line_len
        line = (
            f"\r[Frame {p.current_frame}/{p.total_frames}] "
            f"{_format_size(p.bytes_processed)} | "
            f"{p.mb_per_sec:.1f} MB/s ({p.fps:.1f} fps) | "
            f"Extracting: {p.current_file[:30]}"
        )
        sys.stdout.write(line.ljust(last_line_len))
        sys.stdout.flush()
        last_line_len = len(line)

    decoder = VaultDecoder(
        video_path=video_path,
        dest_dir=dest_dir,
        password=args.password,
        config=config,
        progress_callback=on_progress,
    )

    try:
        result = decoder.decode(verify_only=False)
        print("\n" + "-" * 60)
        if result.success:
            print("[OK] DECODING & VERIFICATION SUCCESSFUL")
        else:
            print("[ERROR] DECODING ENCOUNTERED ERRORS")

        print(f"Frames Read:        {result.total_frames_read}")
        print(f"Bytes Decoded:      {_format_size(result.total_bytes_decoded)}")
        print(f"Duration:           {result.duration_seconds:.2f} seconds")
        print(f"Average Throughput: {result.average_speed_mb_s:.1f} MB/s")

        if result.reconstruction_report:
            rep = result.reconstruction_report
            print(f"Files Restored:     {rep.verified_files} / {rep.total_files} verified OK")
            print(f"Directories Built:  {rep.total_dirs}")
            print(f"Global Stream Hash: {'MATCH (OK)' if rep.global_sha256_match else 'MISMATCH (Corrupted)'}")

            if rep.corrupted_files > 0:
                print(f"\n[!] WARNING: {rep.corrupted_files} file(s) failed hash verification:")
                for item in rep.items:
                    if item.status == "HASH_MISMATCH":
                        print(f"    - {item.rel_path} (Expected: {item.expected_sha256[:12]}..., Got: {item.actual_sha256[:12]}...)")

            if rep.missing_files > 0:
                print(f"\n[!] WARNING: {rep.missing_files} file(s) missing from stream:")
                for item in rep.items:
                    if item.status == "MISSING":
                        print(f"    - {item.rel_path}")

        if result.corruption_errors:
            print("\n[!] Frame-Level Corruption Log:")
            for c in result.corruption_errors:
                print(f"    - Frame {c.frame_seq} (@ {c.timestamp_seconds:.2f}s): {c.error_type} - {c.details}")

        print("=" * 60 + "\n")
        return 0 if result.success else 2
    except Exception as e:
        print(f"\n\n[ERROR] Decoding failed: {e}")
        return 1


def handle_verify(args: argparse.Namespace) -> int:
    config = StreamConfig(
        pixel_format=PIX_FMT_RGB24 if args.rgb else PIX_FMT_RGBA,
    )
    video_path = Path(args.video)

    print("\n" + "=" * 60)
    print("  VIDEO VAULT 4K - INTEGRITY VERIFIER")
    print("=" * 60)
    print(f"Video File: {video_path}")
    print("Scanning video stream and verifying frame checksums...")
    print("-" * 60)

    decoder = VaultDecoder(
        video_path=video_path,
        dest_dir=None,
        password=args.password,
        config=config,
    )

    try:
        result = decoder.decode(verify_only=True)
        print("-" * 60)
        if result.success:
            print("[OK] ALL FRAMES AND CHECKSUMS VERIFIED SUCCESSFULLY")
            print(f"Total Frames:       {result.total_frames_read}")
            print(f"Total Data:         {_format_size(result.total_bytes_decoded)}")
            print(f"Verification Speed: {result.average_speed_mb_s:.1f} MB/s")
            print("Status:             100% Bit-for-bit Lossless Stream Intact")
            print("=" * 60 + "\n")
            return 0
        else:
            print("[ERROR] INTEGRITY VERIFICATION FAILED")
            print(f"Corruptions Detected: {len(result.corruption_errors)}")
            for c in result.corruption_errors:
                print(f"  - Frame {c.frame_seq} (Time {c.timestamp_seconds:.2f}s): {c.error_type}")
                print(f"    Details: {c.details}")
            print("=" * 60 + "\n")
            return 2
    except Exception as e:
        print(f"\n[ERROR] Verification failed: {e}")
        return 1


def handle_info(args: argparse.Namespace) -> int:
    config = StreamConfig(
        pixel_format=PIX_FMT_RGB24 if args.rgb else PIX_FMT_RGBA,
    )
    video_path = Path(args.video)

    decoder = VaultDecoder(video_path=video_path, password=args.password, config=config)
    try:
        meta = decoder.probe()
        print("\n" + "=" * 60)
        print("  VIDEO VAULT 4K - CONTAINER METADATA")
        print("=" * 60)
        print(f"Format Version:     {meta.version}")
        print(f"Created At:         {meta.created_at}")
        print(f"Resolution:         {meta.width}x{meta.height} @ {meta.fps} fps")
        print(f"Codec:              {meta.codec}")
        print(f"Pixel Format:       {meta.pixel_format}")
        print(f"Encrypted:          {'Yes (AES-256-GCM)' if meta.encrypted else 'No (Whitened)'}")
        print(f"Total Payload Size: {_format_size(meta.total_payload_bytes)}")
        print(f"Files Recorded:     {meta.total_files} files, {meta.total_dirs} directories")
        print(f"Global SHA-256:     {meta.global_sha256 or 'N/A'}")
        print("-" * 60)
        print("Manifest Sample (up to 15 items):")
        for idx, item in enumerate(meta.manifest[:15]):
            kind = "[DIR] " if item.get("is_dir") else "[FILE]"
            sz = _format_size(item.get("size", 0)) if not item.get("is_dir") else ""
            print(f"  {kind} {item.get('rel_path')} {sz}")
        if len(meta.manifest) > 15:
            print(f"  ... and {len(meta.manifest) - 15} more items.")
        print("=" * 60 + "\n")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Failed to probe video metadata: {e}")
        return 1


def handle_gui(args: argparse.Namespace) -> int:
    try:
        from video_vault.gui.app import run_gui
        return run_gui()
    except Exception as e:
        print(f"Failed to launch GUI: {e}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_vault",
        description="Video Vault 4K: Lossless Arbitrary File -> 4K Video Container",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Encode
    p_enc = subparsers.add_parser("encode", help="Encode files/folders into a 4K video container")
    p_enc.add_argument("inputs", nargs="+", help="Files or directories to encode")
    p_enc.add_argument("-o", "--output", required=True, help="Output video file path (.mkv, .avi, .mp4)")
    p_enc.add_argument("--ms-player", action="store_true", help="Generate video format supported natively by Microsoft's Windows Media Player (.mp4)")
    p_enc.add_argument("--codec", default=CODEC_FFV1, help="Lossless video codec (ffv1, rawvideo, libx264, mp4, png, huffyuv)")
    p_enc.add_argument("--password", help="Optional encryption password (AES-256-GCM)")
    p_enc.add_argument("--rgb", action="store_true", help="Use RGB24 (3 bytes/px) instead of RGBA (4 bytes/px)")

    # Decode
    p_dec = subparsers.add_parser("decode", help="Decode and reconstruct files from a 4K video container")
    p_dec.add_argument("video", help="Input video file path")
    p_dec.add_argument("-o", "--output", required=True, help="Destination directory for reconstructed files")
    p_dec.add_argument("--password", help="Decryption password if container was encrypted")
    p_dec.add_argument("--rgb", action="store_true", help="Use RGB24 pixel format")

    # Verify
    p_ver = subparsers.add_parser("verify", help="Verify stream and frame checksum integrity without extracting")
    p_ver.add_argument("video", help="Input video file path")
    p_ver.add_argument("--password", help="Decryption password if container was encrypted")
    p_ver.add_argument("--rgb", action="store_true", help="Use RGB24 pixel format")

    # Info
    p_inf = subparsers.add_parser("info", help="Display metadata and manifest from Frame 0 of video")
    p_inf.add_argument("video", help="Input video file path")
    p_inf.add_argument("--password", help="Decryption password if container was encrypted")
    p_inf.add_argument("--rgb", action="store_true", help="Use RGB24 pixel format")

    # GUI
    subparsers.add_parser("gui", help="Launch the Desktop Graphical Interface (PyQt6)")

    return parser


def main() -> int:
    parser = build_parser()
    if len(sys.argv) == 1:
        # Default to GUI if launched with no arguments
        try:
            from video_vault.gui.app import run_gui
            return run_gui()
        except Exception:
            parser.print_help()
            return 0

    args = parser.parse_args()

    if args.command == "encode":
        return handle_encode(args)
    elif args.command == "decode":
        return handle_decode(args)
    elif args.command == "verify":
        return handle_verify(args)
    elif args.command == "info":
        return handle_info(args)
    elif args.command == "gui":
        return handle_gui(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
