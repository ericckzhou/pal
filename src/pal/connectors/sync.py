"""Running connectors so that any sync can be repeated safely.

The rule (instead of one long transaction, which would hold locks while Ollama embeds): every write is idempotent
(ingest.store reports "unchanged" for identical bytes, item mappings are upserts), and the cursor is saved in its own
commit only after every item succeeded. A sync that fails or is stopped part way keeps the old cursor, so the next
run sees the same changes again and nothing is lost or duplicated.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg_pool import ConnectionPool

from .. import ingest
from . import store

log = logging.getLogger("pal.connectors")


class Stopped(Exception):
    """pal is shutting down; the sync ends without moving its cursor."""


@dataclass
class SyncContext:
    """What a connector gets: its connection row, a database connection, and the only ways it may write."""

    conn: psycopg.Connection
    connection: dict
    stop: threading.Event | None = None
    summary: dict = field(default_factory=lambda: {"added": 0, "updated": 0, "unchanged": 0, "removed": 0})

    @property
    def cursor(self) -> str | None:
        return self.connection["cursor"]

    @property
    def config(self) -> dict:
        return self.connection["config"]

    def check_stop(self) -> None:
        if self.stop is not None and self.stop.is_set():
            raise Stopped

    def known_version(self, external_id: str) -> str | None:
        return store.item_version(self.conn, self.connection["id"], external_id)

    def put_document(self, external_id: str, filename: str, data: bytes, version: str | None = None) -> str:
        """Store (or refresh) one outside item as a pal document. Returns added|updated|unchanged."""
        self.check_stop()
        doc, outcome = ingest.store(
            self.conn, filename, data, source_path=source_path(self.connection, external_id),
            project_id=self.connection["project_id"],
        )
        store.map_item(self.conn, self.connection["id"], external_id, doc["id"], version)
        self.summary[outcome] += 1
        return outcome

    def remove(self, external_id: str) -> None:
        self.check_stop()
        if store.remove_item(self.conn, self.connection["id"], external_id):
            self.summary["removed"] += 1


class Connector(Protocol):
    def sync(self, ctx: SyncContext) -> str | None:
        """Bring pal up to date and return the cursor the next sync starts from."""
        ...


Factory = Callable[[dict], Connector]
REGISTRY: dict[tuple[str, str], Factory] = {}  # filled in by each service's module


def source_path(connection: dict, external_id: str) -> str:
    """The documents.source_path of an outside item, e.g. "google-drive:1AbC". Never a real path on this PC."""
    return f"{connection['provider']}-{connection['service']}:{external_id}"


_locks: dict[UUID, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(connection_id: UUID) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(connection_id, threading.Lock())


def sync_one(conn: psycopg.Connection, connection_id: UUID, stop: threading.Event | None = None,
             registry: dict[tuple[str, str], Factory] | None = None) -> dict:
    """Sync one connection. Returns its summary plus "status": ok | failed | stopped | busy | skipped."""
    registry = REGISTRY if registry is None else registry
    lock = _lock_for(connection_id)
    if not lock.acquire(blocking=False):  # the loop and "Sync now" never run the same connection twice at once
        return {"status": "busy"}
    try:
        row = store.get(conn, connection_id)
        if row is None or not row["enabled"]:
            return {"status": "skipped"}
        factory = registry.get((row["provider"], row["service"]))
        if factory is None:
            return {"status": "skipped"}
        ctx = SyncContext(conn, row, stop)
        try:
            cursor = factory(row).sync(ctx)
            ctx.check_stop()
        except Stopped:
            store.record_failure(conn, connection_id, "stopped before finishing; the next sync picks up from here")
            return {**ctx.summary, "status": "stopped"}
        except Exception as exc:
            log.warning("%s/%s sync failed: %s", row["provider"], row["service"], exc)
            store.record_failure(conn, connection_id, str(exc) or type(exc).__name__)
            return {**ctx.summary, "status": "failed"}
        store.record_success(conn, connection_id, cursor)
        return {**ctx.summary, "status": "ok"}
    finally:
        lock.release()


def sync_all(pool: ConnectionPool, stop: threading.Event | None = None) -> dict[str, dict]:
    """Sync every enabled connection, one after another. Keyed by "provider/service"."""
    with pool.connection() as conn:
        rows = [r for r in store.list_all(conn) if r["enabled"]]
        out = {}
        for r in rows:
            if stop is not None and stop.is_set():
                break
            out[f"{r['provider']}/{r['service']}"] = sync_one(conn, r["id"], stop)
        return out
