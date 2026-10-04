# Video Vault 4K (`VAULT4K`) Format Specification

**Version:** 1.0  
**Specification Identifier:** `VAULT4K\x01`  
**Standard Resolution:** 4K UHD ($3840 \times 2160$), 30 FPS  
**Color Space / Pixel Format:** Full 8-bit RGBA (4 bytes/pixel, $33,177,600$ bytes/frame) or RGB24 (3 bytes/pixel, $24,883,200$ bytes/frame)

---

## 1. Overview & Core Philosophy

Standard video compression algorithms (H.264, H.265, AV1, VP9) employ lossy spatial transforms (DCT/wavelets), quantization, and chroma subsampling ($YUV 4:2:0$). These transforms alter pixel values, making ordinary video encoding unusable for arbitrary digital binary storage.

`Video Vault 4K` treats video frames as **physical-looking byte matrices** within standard container formats (`.mkv`, `.avi`). Using mathematically lossless video codecs (**FFV1 Level 3** or **Rawvideo**), every individual pixel channel ($0\text{--}255$) acts as an exact byte storage cell.

To outside viewers, the video appears as continuous, high-entropy, meaningless visual static noise (like television static). Without the `Video Vault 4K` decoder and format specifications, the stream cannot be reconstructed.

---

## 2. Binary Framing Protocol

A Video Vault file is structured as a sequence of discrete 4K video frames indexed $0, 1, 2, \dots, N-1$. Each video frame consists of an **80-Byte Self-Verifying Frame Header**, followed by a **Frame Payload**, followed by **Deterministic Visual Static Padding** if the payload does not completely fill the frame capacity.

### 2.1 80-Byte Frame Header Layout

All integer values are stored in **Network Byte Order (Big-Endian)**.

| Offset (Bytes) | Field Name | Data Type | Size (Bytes) | Description |
|---|---|---|---|---|
| `0x00 - 0x07` | `magic` | `char[8]` | 8 | Fixed magic signature: `b"VAULT4K\x01"` |
| `0x08 - 0x09` | `version` | `uint16` | 2 | Protocol version (`1`) |
| `0x0A - 0x0B` | `flags` | `uint16` | 2 | Bitmask flags (see Section 2.2) |
| `0x0C - 0x0F` | `frame_seq` | `uint32` | 4 | 0-indexed frame sequence number ($0, 1, 2, \dots$) |
| `0x10 - 0x13` | `total_frames` | `uint32` | 4 | Estimated or total frames in video |
| `0x14 - 0x17` | `payload_len` | `uint32` | 4 | Valid payload byte count in this frame |
| `0x18 - 0x1B` | `payload_crc32`| `uint32` | 4 | IEEE 802.3 32-bit CRC of this frame's payload |
| `0x1C - 0x2B` | `salt` | `byte[16]` | 16 | Cryptographic salt for PBKDF2 / IV derivation |
| `0x2C - 0x4B` | `payload_sha256`| `byte[32]` | 32 | Cryptographic SHA-256 digest of this frame's payload |
| `0x4C - 0x4F` | `header_crc32` | `uint32` | 4 | IEEE 802.3 32-bit CRC of the preceding 76 header bytes |

**Total Frame Header Size:** Exactly `80` bytes.

### 2.2 Frame Flags Bitmask

* **Bit 0 (`0x01` - `FLAG_ENCRYPTED`):** When set, the payload is encrypted with military-grade AES-256-GCM AEAD.
* **Bit 1 (`0x02` - `FLAG_FIRST_FRAME`):** Set on Frame 0. Signifies that the payload begins with the `GlobalVaultMeta` block.
* **Bit 2 (`0x04` - `FLAG_LAST_FRAME`):** Set on the final frame. Signifies End-of-Stream (EOS).
* **Bit 3 (`0x08` - `FLAG_RGBA_PIXELS`):** `1` for 4-channel RGBA/BGRA ($3840 \times 2160 \times 4$ bytes), `0` for 3-channel RGB/BGR ($3840 \times 2160 \times 3$ bytes).
* **Bit 4 (`0x10` - `FLAG_YUV_LUMA`):** `1` for Microsoft MP4 mode (YUV420p Luma storage, $3840 \times 2160 \times 1$ byte = $8,294,400$ bytes/frame).

---

## 3. Frame 0: Global Vault Metadata Block (`VLT_META`)

Frame 0 contains a self-describing container metadata block placed at the very beginning of its payload:

```
[MAGIC: "VLT_META" (8B)][JSON_LEN (uint32)][CRC32 (uint32)][JSON_UTF8_PAYLOAD]
```

### 3.1 Metadata Fields (JSON)

* `version` (`int`): Protocol version (`1`).
* `created_at` (`string`): ISO 8601 UTC creation timestamp.
* `codec` (`string`): Codec used (`"ffv1"`, `"rawvideo"`, `"png"`, or `"huffyuv"`).
* `pixel_format` (`string`): `"rgba"` or `"rgb24"`.
* `width` / `height` (`int`): Frame dimensions (`3840`, `2160`).
* `fps` (`int`): Video framerate (`30`).
* `total_payload_bytes` (`int`): Total uncompressed payload byte count.
* `total_frames` (`int`): Total frames in the stream.
* `total_files` (`int`): Total individual files packaged.
* `total_dirs` (`int`): Total directories packaged.
* `encrypted` (`bool`): `true` if encrypted with AES-256-GCM.
* `kdf_salt` (`string`): Hex-encoded 16-byte salt for key derivation.
* `global_sha256` (`string`): SHA-256 hex digest of the entire unencrypted archive stream.
* `manifest` (`array`): Comprehensive manifest of all items:
  ```json
  [
    {
      "rel_path": "photos/image.png",
      "size": 2450821,
      "mtime": 1727532000.0,
      "is_dir": false,
      "sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a"
    },
    {
      "rel_path": "photos/empty_album",
      "size": 0,
      "mtime": 1727532000.0,
      "is_dir": true,
      "sha256": ""
    }
  ]
  ```

---

## 4. Packaging & Filesystem Encapsulation

Arbitrary files, multiple files, and deeply nested directory trees are encapsulated inside a streaming POSIX PAX `tar` stream:
* **Preserves:** Relative directory paths, modification timestamps (`mtime`), POSIX permissions, and empty directories.
* **Arbitrary Character Support:** Handles UTF-8 Unicode, emojis, spaces, accents, and special symbols safely.
* **Streamability:** Chunked on-the-fly without buffering the full archive in memory. Memory consumption remains $<100$ MB even for 100+ GB datasets.

---

## 5. Security & Visual Noise Transformation

To guarantee that the video always looks like physical visual static and reveals no file signatures (e.g. `PK\x03\x04`, `\x89PNG`, `%PDF`):

### 5.1 AES-256-GCM Encryption (Password Mode)
1. **Key Derivation:** PBKDF2-HMAC-SHA256 (100,000 iterations) with a random 16-byte salt derives a 256-bit AES key.
2. **Per-Frame Nonce (IV):** Deterministic 12-byte nonce derived via `BLAKE2b(salt, key=salt, digest_size=12)` updated with `frame_seq`.
3. **Associated Authenticated Data (AAD):** `frame_seq` is bound to the GCM tag to prevent frame-reordering attacks.
4. **Authentication:** 16-byte GCM authentication tag verifies payload integrity and detects any tampering.

### 5.2 Stream Whitening (Unencrypted Mode)
When no password is provided, data is processed through an OpenSSL hardware-accelerated **AES-CTR Whitening Mask** seeded by the frame sequence index and container salt.
* Guarantees maximal Shannon entropy ($\sim 7.95\text{--}8.0$ bits/byte).
* Completely eliminates repetitive patterns, solid color blocks, and discernible visual file headers.
* The output renders as uniform, colorful static television noise.

---

## 6. Corruption Detection & Error Pinpointing

`VAULT4K` provides a 3-tier hierarchical error detection scheme:

1. **Header Level:** 32-bit CRC on the 76-byte header detects corrupted frame sequence numbers, damaged payload lengths, or bad flags.
2. **Frame Payload Level:** Dual CRC32 and SHA-256 check per frame detects any bit flips, damaged pixels, or missing frames, pinpointing the exact timestamp (`HH:MM:SS.mmm`) and frame index.
3. **Archive Manifest Level:** Per-file SHA-256 checksums verify every extracted file against its pre-flight hash, reporting precisely which files were affected by any damaged video segments.

---

## 7. Microsoft Video Player Native Compatibility

To ensure videos can be natively opened and played in **Microsoft's Windows Media Player**, **Movies & TV**, and **Windows 11 Media Player** without external codec packs, `Video Vault 4K` provides two dedicated Microsoft-compatible output profiles:

### 7.1 Profile 1: Microsoft MP4 Universal (`.mp4`)
* **Container:** Standard MPEG-4 Part 14 (`.mp4`).
* **Codec:** Lossless H.264 (`-c:v libx264 -crf 0 -preset ultrafast -tune fastdecode`).
* **Pixel Encoding:** `yuv420p` with **Luma Data Mapping**.
* **Mechanism:** Data payload is stored strictly within the full-resolution Luma ($Y$) channel ($3840 \times 2160$ bytes = **$8,294,400$ bytes ($\sim 7.91$ MB) per frame**). The chroma channels ($U$ and $V$) are populated with neutral constant gray ($128$).
* **Playback:** Natively hardware-decoded by Microsoft Media Foundation and Windows Media Player, displaying silky smooth grayscale visual static noise.
* **Lossless Guarantee:** H.264 in `-crf 0` mode bypasses DCT quantization and transform rounding on the Luma plane, ensuring $100\%$ bit-for-bit exact byte restoration.

### 7.2 Profile 2: Microsoft AVI Lossless (`.avi`)
* **Container:** Microsoft Audio Video Interleave (`.avi`).
* **Codec:** Uncompressed raw video (`rawvideo`).
* **FourCC Tag:** `DIB ` (Device Independent Bitmap).
* **Pixel Formats:** BGR24 ($24.88$ MB/frame) or BGRA ($33.18$ MB/frame).
* **Playback:** Opens directly in Windows Media Player using Microsoft's native DirectShow / VFW uncompressed video renderer.
* **Lossless Guarantee:** Uncompressed direct bitmap storage with zero encoding transformations.
