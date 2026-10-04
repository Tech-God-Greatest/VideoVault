# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-09-28

### Added
- **Universal Packaging Engine**:
  - Streaming POSIX PAX tar packaging and unpacking with manifest generator.
  - Recursive folder hierarchy preservation with empty folders, file permissions, and modification timestamps (`mtime`).
  - Support for special characters, spaces, and multi-byte UTF-8 / emoji filenames.
- **4K Pixel Storage Pipeline**:
  - Full 4K UHD ($3840 \times 2160$) frame mapping in RGBA ($33.18$ MB/frame) and RGB24 ($24.88$ MB/frame).
  - High-performance `numpy` byte-to-pixel and pixel-to-byte vectorized reshapes.
- **Microsoft Video Player Compatibility**:
  - `--ms-player` CLI flag and dedicated GUI checkbox.
  - **Universal MP4 container (`.mp4`)**: Lossless H.264 video (`libx264 -crf 0`) playable in Windows Media Player, Windows 11 Movies & TV, QuickTime, iOS, and Android.
  - **AVI Lossless container (`.avi`)**: Native Windows bitmap container with FourCC `DIB ` playable in legacy and modern Windows Media Player.
- **`VAULT4K` Binary Envelope Protocol**:
  - 80-byte binary frame header with magic bytes, protocol version, stream flags, sequence index, payload size, header CRC32, payload CRC32, payload SHA-256, and 16-byte salt.
  - Frame 0 Global Vault Header (`VLT_META`) with container metadata and per-file verification manifest.
- **Security & Entropy**:
  - AES-256-GCM AEAD encryption with PBKDF2-HMAC-SHA256 key derivation.
  - AES-CTR stream whitening producing maximal Shannon entropy ($\sim 8.0$ bits/byte) resembling pure television static.
- **Desktop Graphical User Interface (PyQt6)**:
  - Polished modern dark-theme interface with drag-and-drop file ingestion.
  - Encode tab: input list, container selector, codec picker, encryption toggle, live FPS/speed gauge, pause/resume/cancel buttons, and live 4K visual static thumbnail rendering.
  - Decode tab: automatic container probe, metadata reader, password prompt, extraction progress, and bit-for-bit file verification table.
  - Verify tab: standalone frame integrity checker.
- **Command-Line Interface**:
  - Subcommands: `encode`, `decode`, `verify`, `info`, and `gui`.
- **Comprehensive Automated Test Suite**:
  - 22 tests covering roundtrip losslessness, small files, multi-frame files, nested directories, unicode names, tampering detection, and Microsoft player formats.
