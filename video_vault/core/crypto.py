"""
Cryptographic and Stream Whitening Layer.
Provides military-grade AES-256-GCM AEAD encryption and high-throughput
AES-CTR stream whitening to ensure all video frames exhibit uniform high-entropy visual static.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend


class CryptoError(Exception):
    """Base exception for cryptographic failures."""
    pass


class AuthenticationFailedError(CryptoError):
    """Raised when AES-GCM authentication fails (data tampered or corrupted)."""
    pass


class InvalidPasswordError(CryptoError):
    """Raised when password provided is incorrect."""
    pass


# Fixed constant key for stream whitening (when no user password is provided)
# Ensures plain streams also appear as uniform high-entropy visual static noise.
_WHITENING_MASTER_KEY = bytes.fromhex(
    "9f83a4c2847a9e102938475610293847a1b2c3d4e5f60718293a4b5c6d7e8f90"
)


def derive_key(password: str, salt: bytes, iterations: int = 100_000) -> bytes:
    """
    Derives a 256-bit AES key from a user password using PBKDF2-HMAC-SHA256.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
        backend=default_backend(),
    )
    return kdf.derive(password.encode("utf-8"))


def generate_salt(length: int = 16) -> bytes:
    """Generates cryptographically secure random salt."""
    return os.urandom(length)


def _make_frame_iv(salt: bytes, frame_seq: int) -> bytes:
    """
    Generates a unique, deterministic 12-byte nonce for AES-GCM
    based on salt and frame sequence number.
    """
    h = hashlib.blake2b(key=salt, digest_size=12)
    h.update(frame_seq.to_bytes(4, byteorder="big"))
    return h.digest()


def encrypt_payload_gcm(
    payload: bytes,
    key: bytes,
    salt: bytes,
    frame_seq: int,
    associated_data: bytes = b"",
) -> bytes:
    """
    Encrypts frame payload with AES-256-GCM.
    Returns: ciphertext + 16-byte GCM authentication tag.
    """
    iv = _make_frame_iv(salt, frame_seq)
    aesgcm = AESGCM(key)
    return aesgcm.encrypt(iv, payload, associated_data)


def decrypt_payload_gcm(
    ciphertext_and_tag: bytes,
    key: bytes,
    salt: bytes,
    frame_seq: int,
    associated_data: bytes = b"",
) -> bytes:
    """
    Decrypts frame payload with AES-256-GCM and verifies authentication tag.
    Raises AuthenticationFailedError if data was corrupted or password is wrong.
    """
    iv = _make_frame_iv(salt, frame_seq)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(iv, ciphertext_and_tag, associated_data)
    except Exception as e:
        raise AuthenticationFailedError(
            f"AES-GCM authentication failed for frame {frame_seq}. Data is corrupted or password is incorrect: {e}"
        ) from e


def whiten_stream(data: bytes, frame_seq: int, salt: bytes = b"\x00" * 16) -> bytes:
    """
    Fast AES-CTR stream whitening mask.
    Transforms low-entropy plaintext (text files, repetitive bytes, tar headers)
    into high-entropy pseudo-random noise, eliminating recognizable pixel patterns.
    Because CTR mode is symmetric, calling whiten_stream twice with the same
    parameters perfectly restores the original data.
    """
    # 16-byte IV from frame_seq and salt
    iv_hash = hashlib.sha256(salt + frame_seq.to_bytes(4, "big")).digest()[:16]
    cipher = Cipher(algorithms.AES(_WHITENING_MASTER_KEY), modes.CTR(iv_hash), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(data) + encryptor.finalize()


# Unwhitening is identical to whitening in CTR mode
unwhiten_stream = whiten_stream
