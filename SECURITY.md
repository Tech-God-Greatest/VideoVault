# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

---

## Security Architecture Overview

**Video Vault 4K** incorporates multi-layered cryptographic and integrity controls designed to protect stored data from unauthorized inspection, manipulation, and physical corruption:

1. **Authenticated Encryption (AES-256-GCM)**:
   - When a password is supplied, data payloads are encrypted using AES-256 in Galois/Counter Mode (GCM), providing both confidentiality and AEAD authentication tags.
   - Key derivation utilizes **PBKDF2-HMAC-SHA256** with 100,000 iterations and a unique 16-byte cryptographically random salt per container.
   - Frame nonces are deterministically derived per frame (`nonce = base_nonce XOR frame_index`) to prevent nonce reuse across frame sequences.
   - The salt is stored in the 80-byte frame header to allow decoders to derive the key before decrypting Frame 0.

2. **Stream Whitening (Maximal Entropy Transformation)**:
   - In unencrypted mode, data is processed through an AES-CTR stream whitening layer.
   - Eliminates structured headers, zero-fill blocks, and human-readable patterns from appearing as visible bands or artifacts in the video.
   - Ensures uniform Shannon entropy ($\sim 8.0$ bits/byte) across all output frames, rendering the video indistinguishable from television static.

3. **Integrity & Tamper Detection**:
   - Every 80-byte frame header includes a dedicated **CRC32** checksum of the header itself.
   - Every frame envelope includes an independent payload **CRC32** and **SHA-256** digest.
   - The global manifest (Frame 0) stores individual cryptographic SHA-256 checksums for every packaged file, validated at file reconstruction.

---

## Reporting a Vulnerability

If you discover a security vulnerability in Video Vault 4K, please report it responsibly:

1. **Do not disclose the vulnerability in public issues or pull requests.**
2. Send a detailed report via GitHub Security Advisories or contact the repository maintainers directly.
3. Include:
   - A description of the vulnerability and its potential impact.
   - Proof-of-concept steps or code to reproduce the issue.
   - Any suggested remediations or mitigations.

We will acknowledge receipt within 48 hours and work on a coordinated disclosure and patch release.
