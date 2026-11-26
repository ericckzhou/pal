"""S11: nightly pg_dump into a folder outside the repo, rotation, and a restore check.

Hardening: every dump is encrypted with the backup key (see backup_crypto) before it touches the disk."""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import backup_crypto

log = logging.getLogger("pal.backup")
CONTAINER = "pal-db-1"
RESTORE_DB = "pal_restore_check"


def _command(args: tuple[str, ...]) -> tuple[list[str], dict | None]:
    """How to run a Postgres tool: `docker exec` into the DB container on Windows, or directly inside pal's image."""
    from .config import Settings, db_password, in_container

    if not in_container():
        return ["docker", "exec", "-i", CONTAINER, *args], None
    settings = Settings.from_env()
    env = {**os.environ, "PGHOST": settings.db_host, "PGPORT": str(settings.db_port), "PGPASSWORD": db_password()}
    return list(args), env


def _docker(*args: str, stdin: bytes | None = None) -> bytes:
    command, env = _command(args)
    result = subprocess.run(command, input=stdin, capture_output=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _psql(db: str, sql: str) -> str:
    return _docker("psql", "-U", "pal", "-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql).decode().strip()


def backup_files(backup_dir: Path) -> list[Path]:
    """All backups, oldest first (names start with a sortable timestamp)."""
    if not backup_dir.is_dir():
        return []
    return sorted([*backup_dir.glob("pal-*.dump"), *backup_dir.glob("pal-*.dump.enc")], key=lambda p: p.name)


def encrypt_legacy(backup_dir: Path, key: bytes) -> int:
    """Encrypt plaintext backups left from before encryption, then delete the plaintext. Returns how many."""
    done = 0
    for plain in sorted(backup_dir.glob("pal-*.dump")):
        blob = plain.read_bytes()
        target = plain.with_name(plain.name + backup_crypto.ENC_SUFFIX)
        data = blob if backup_crypto.is_encrypted(blob) else backup_crypto.encrypt(blob, key)
        backup_crypto.write_atomic(target, data)
        stat = plain.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))
        plain.unlink()
        done += 1
    return done


def run_backup(backup_dir: Path, key: bytes, db_name: str = "pal", keep: int = 14) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dump = _docker("pg_dump", "-U", "pal", "-Fc", db_name)
    path = backup_dir / f"pal-{datetime.now():%Y%m%d-%H%M%S}.dump{backup_crypto.ENC_SUFFIX}"
    backup_crypto.write_atomic(path, backup_crypto.encrypt(dump, key))
    if moved := encrypt_legacy(backup_dir, key):
        log.info("encrypted %d older plaintext backup(s)", moved)
    for old in backup_files(backup_dir)[:-keep]:
        old.unlink()
    return path


def _table_counts(db: str) -> dict[str, int]:
    tables = _psql(db, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1").splitlines()
    return {t: int(_psql(db, f'SELECT count(*) FROM "{t}"')) for t in tables if t}


def restore_check(dump_path: Path, key: bytes | None, source_db: str = "pal") -> dict:
    """Restore the dump into a scratch DB and compare row counts with the source."""
    _psql("postgres", f"DROP DATABASE IF EXISTS {RESTORE_DB} WITH (FORCE)")
    _psql("postgres", f"CREATE DATABASE {RESTORE_DB}")
    try:
        _docker("pg_restore", "-U", "pal", "-d", RESTORE_DB, "--no-owner", stdin=backup_crypto.read_dump(dump_path, key))
        source, restored = _table_counts(source_db), _table_counts(RESTORE_DB)
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {RESTORE_DB} WITH (FORCE)")
    tables = {t: (source.get(t, -1), restored.get(t, -1)) for t in source.keys() | restored.keys()}
    return {"ok": all(a == b for a, b in tables.values()), "tables": tables}


def start_nightly(backup_dir: Path, db_name: str, keep: int, hour: int = 3) -> threading.Thread:
    """The key is read from the secret store on each run, so a key restored later is picked up."""
    def loop() -> None:
        while True:
            now = datetime.now()
            target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            time.sleep((target - now).total_seconds())
            try:
                path = run_backup(backup_dir, backup_crypto.load_key(), db_name, keep)
                log.info("nightly backup written: %s", path)
            except Exception:
                log.exception("nightly backup failed")

    thread = threading.Thread(target=loop, name="pal-nightly-backup", daemon=True)
    thread.start()
    return thread
