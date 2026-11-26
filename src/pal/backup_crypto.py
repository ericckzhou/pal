"""Hardening: backups are encrypted at rest (AES-256-GCM) with a key kept in Windows Credential Manager.

File format: MAGIC (6 bytes) | nonce (12 bytes) | ciphertext+tag. MAGIC is also the associated data, so a
file whose header was tampered with fails to decrypt.
"""

import base64
import binascii
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"PALBK1"
NONCE_BYTES = 12
KEY_BYTES = 32
KEYRING_ACCOUNT = "backup-key"
ENC_SUFFIX = ".enc"


class BackupKeyError(Exception):
    pass


def encrypt(data: bytes, key: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def decrypt(blob: bytes, key: bytes) -> bytes:
    if not is_encrypted(blob):
        raise BackupKeyError("Not a pal encrypted backup.")
    nonce, body = blob[len(MAGIC) : len(MAGIC) + NONCE_BYTES], blob[len(MAGIC) + NONCE_BYTES :]
    try:
        return AESGCM(key).decrypt(nonce, body, MAGIC)
    except InvalidTag as exc:
        raise BackupKeyError("Backup could not be decrypted: wrong key or the file is damaged.") from exc


def read_dump(path: Path, key: bytes | None) -> bytes:
    """Plain pg_dump bytes from a backup file, decrypting when needed."""
    blob = path.read_bytes()
    if not is_encrypted(blob):
        return blob
    if key is None:
        raise BackupKeyError(f"{path.name} is encrypted and no backup key was given.")
    return decrypt(blob, key)


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def encode_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode()


def decode_key(text: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(text.strip().encode())
    except (binascii.Error, ValueError) as exc:
        raise BackupKeyError("That isn't a pal backup key.") from exc
    if len(key) != KEY_BYTES:
        raise BackupKeyError("That isn't a pal backup key.")
    return key


def load_key(create: bool = True) -> bytes:
    """The backup key from the secret store; made on first use when `create` is set."""
    from . import secret_store

    stored = secret_store.get(KEYRING_ACCOUNT)
    if stored:
        return decode_key(stored)
    if not create:
        raise BackupKeyError(f"No backup key in {secret_store.where()}.")
    key = secrets.token_bytes(KEY_BYTES)
    secret_store.put(KEYRING_ACCOUNT, encode_key(key))
    return key


def store_key(key: bytes) -> None:
    from . import secret_store

    secret_store.put(KEYRING_ACCOUNT, encode_key(key))
