"""Re-reading PDFs that an older extractor read, as a job the owner starts, watches and can stop.

Re-reading is slow (Docling runs ~1-5 s a page on the CPU), so pal never does it on its own: the Documents page
offers it, shows progress, and the owner can stop it; a stopped job leaves every document readable (each file is
swapped in its own transaction). Only files added from this PC can be re-read; pal doesn't keep uploaded bytes.
"""

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import ingest, pdf

log = logging.getLogger("pal.reread")

_lock = threading.Lock()
_state: dict = {"running": False}
_stop = threading.Event()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def stale(conn: psycopg.Connection) -> dict:
    """PDFs read by something other than the current extractor: those pal can re-read, and uploads it can't."""
    current = pdf.current_parser()
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            """SELECT count(*) FILTER (WHERE source_path IS NOT NULL) AS files,
                      coalesce(sum(size_bytes) FILTER (WHERE source_path IS NOT NULL), 0) AS bytes,
                      count(*) FILTER (WHERE source_path IS NULL) AS uploads
               FROM documents WHERE kind = 'pdf' AND parser IS DISTINCT FROM %s""",
            (current,),
        ).fetchone()
    return {**row, "parser": current}


def snapshot() -> dict:
    with _lock:
        return {**_state, "failed": list(_state.get("failed", []))}


def start(pool) -> dict:
    """Start re-reading every stale PDF from this PC. Returns the job snapshot; raises RuntimeError if one runs."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, source_path FROM documents WHERE kind = 'pdf' AND source_path IS NOT NULL "
            "AND parser IS DISTINCT FROM %s ORDER BY size_bytes",
            (pdf.current_parser(),),
        ).fetchall()
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


def _run(pool, rows: list[tuple]) -> None:
    try:
        for i, (_doc_id, path) in enumerate(rows):
            if _stop.is_set():
                _update(stopped=True)
                break
            _update(current=Path(path).name)
            reason = _reread_one(pool, path)
            with _lock:
                if reason:
                    _state["failed"] = [*_state["failed"], {"path": path, "reason": reason}]
                _state["done"] = i + 1
    except Exception:
        log.exception("PDF re-read stopped by an error")
    finally:
        _update(running=False, current=None, finished_at=_now())
        log.info("PDF re-read finished: %s", {k: v for k, v in snapshot().items() if k != "failed"})


def _reread_one(pool, path: str) -> str | None:
    """Re-read one file; returns why it was skipped, or None."""
    try:
        data = Path(path).read_bytes()
        with pool.connection() as conn:
            ingest.store(conn, Path(path).name, data, path, reread=True)
        return None
    except FileNotFoundError:
        return "file no longer on this PC"
    except (ingest.IngestError, OSError) as exc:
        return str(exc)
    except psycopg.Error as exc:
        log.warning("could not re-read %s: %s", path, exc)
        return "pal couldn't save this file's text"
