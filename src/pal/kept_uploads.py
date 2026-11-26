"""Uploaded PDFs, kept encrypted so a better PDF reader can re-read them later (reread.py).

Files added from a folder are re-read from disk. An upload has no file on this PC, so pal keeps its bytes in
<data dir>/uploads/<sha256>.pdf.enc, encrypted with AES-256-GCM under the backup key (backup_crypto). They live
outside the repo and the database, and are deleted with their document (S10).

Only PDFs are kept: Markdown and text are read exactly the first time, so there is nothing to re-read.
"""

import logging
from pathlib import Path

from . import backup_crypto
from .config import data_dir

log = logging.getLogger("pal.uploads")


def _key(create: bool = True) -> bytes:
    return backup_crypto.load_key(create=create)


def folder() -> Path:
    return data_dir() / "uploads"


def path_for(sha256: bytes) -> Path:
    return folder() / f"{sha256.hex()}.pdf.enc"


def has(sha256: bytes) -> bool:
    return path_for(sha256).is_file()


def keep(sha256: bytes, data: bytes) -> bool:
    """Store an uploaded PDF. Never raises: a failed copy only means this upload can't be re-read later."""
    try:
        target = path_for(sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_crypto.write_atomic(target, backup_crypto.encrypt(data, _key()))
        return True
    except Exception:
        log.warning("could not keep an encrypted copy of an uploaded PDF", exc_info=True)
        return False


def load(sha256: bytes) -> bytes:
    return backup_crypto.decrypt(path_for(sha256).read_bytes(), _key(create=False))


def discard(sha256: bytes) -> None:
    path_for(sha256).unlink(missing_ok=True)
