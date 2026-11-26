"""Re-reading PDFs that an older extractor read, as a job the owner starts, watches and can stop.

Re-reading is slow (Docling runs ~1-5 s a page on the CPU), so pal never does it on its own: the Documents page
offers it, shows progress, and the owner can stop it; a stopped job leaves every document readable (each file is
swapped in its own transaction). Files added from this PC are re-read from disk; uploaded PDFs from the encrypted
copy pal keeps (kept_uploads.py). Uploads from before pal kept copies can't be re-read.
"""

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import backup_crypto, ingest, kept_uploads, pdf

log = logging.getLogger("pal.reread")

_lock = threading.Lock()
_state: dict = {"running": False}
_stop = threading.Event()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stale_rows(conn: psycopg.Connection) -> tuple[list[dict], list[dict]]:
    """Stale PDFs split into (re-readable, not): from disk or a kept upload copy, versus older uploads."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id, source_path, sha256, filename, size_bytes FROM documents "
            "WHERE kind = 'pdf' AND parser IS DISTINCT FROM %s ORDER BY size_bytes",
            (pdf.current_parser(),),
        ).fetchall()
    ok = [r for r in rows if r["source_path"] is not None or kept_uploads.has(bytes(r["sha256"]))]
    return ok, [r for r in rows if r not in ok]


def stale(conn: psycopg.Connection) -> dict:
    """PDFs read by something other than the current extractor: those pal can re-read, and uploads it can't."""
    ok, lost = _stale_rows(conn)
    return {"files": len(ok), "bytes": sum(r["size_bytes"] for r in ok), "uploads": len(lost),
            "parser": pdf.current_parser()}


def snapshot() -> dict:
    with _lock:
        return {**_state, "failed": list(_state.get("failed", []))}


def start(pool) -> dict:
    """Start re-reading every stale PDF from this PC. Returns the job snapshot; raises RuntimeError if one runs."""
    with pool.connection() as conn:
        rows, _ = _stale_rows(conn)
    with _lock:
        if _state.get("running"):
            raise RuntimeError("A re-read is already running.")
        if not rows:
            raise LookupError("Nothing to re-read.")
        _stop.clear()
        _state.clear()
        _state.update(running=True, total=len(rows), done=0, current=None, failed=[], stopped=False,
                      started_at=_now(), finished_at=None)
    threading.Thread(target=_run, args=(pool, rows), name="pal-reread", daemon=True).start()
    return snapshot()


def stop() -> dict:
    _stop.set()
    return snapshot()


def _update(**changes) -> None:
    with _lock:
        _state.update(changes)


def _run(pool, rows: list[dict]) -> None:
    try:
        for i, row in enumerate(rows):
            if _stop.is_set():
                _update(stopped=True)
                break
            name = row["source_path"] or row["filename"]
            _update(current=Path(name).name)
            reason = _reread_one(pool, row)
            with _lock:
                if reason:
                    _state["failed"] = [*_state["failed"], {"path": name, "reason": reason}]
                _state["done"] = i + 1
    except Exception:
        log.exception("PDF re-read stopped by an error")
    finally:
        _update(running=False, current=None, finished_at=_now())
        log.info("PDF re-read finished: %s", {k: v for k, v in snapshot().items() if k != "failed"})


def _reread_one(pool, row: dict) -> str | None:
    """Re-read one file (from disk, or from its kept upload copy); returns why it was skipped, or None."""
    path = row["source_path"]
    try:
        if path is None:
            data, name = kept_uploads.load(bytes(row["sha256"])), row["filename"]
        else:
            data, name = Path(path).read_bytes(), Path(path).name
        with pool.connection() as conn:
            ingest.store(conn, name, data, path, reread=True)
        return None
    except FileNotFoundError:
        return "file no longer on this PC"
    except backup_crypto.BackupKeyError:
        return "pal's kept copy couldn't be decrypted (backup key missing or changed)"
    except (ingest.IngestError, OSError) as exc:
        return str(exc)
    except psycopg.Error as exc:
        log.warning("could not re-read %s: %s", path, exc)
        return "pal couldn't save this file's text"
