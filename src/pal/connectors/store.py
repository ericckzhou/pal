"""The connections table, the item-to-document map, and removing everything a connection brought into pal."""

from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .. import db

SERVICES = frozenset({("google", "calendar"), ("google", "drive"), ("google", "gmail")})
COLUMNS = ("id, provider, service, account, scopes, enabled, project_id, config, cursor, last_sync_at, last_ok_at, "
           "last_error, created_at")
ERROR_MAX = 500  # characters of a sync error kept for the Connections page


class ConnectorError(ValueError):
    pass


def create(
    conn: psycopg.Connection, provider: str, service: str, account: str | None, scopes: list[str],
    config: dict | None = None,
) -> dict:
    """Add a connection, or update the account and scopes of one that exists (signing in again keeps its state)."""
    if (provider, service) not in SERVICES:
        raise ConnectorError(f"unknown service {provider}/{service}")
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            f"INSERT INTO connections (provider, service, account, scopes, config) VALUES (%s, %s, %s, %s, %s) "
            f"ON CONFLICT (provider, service) DO UPDATE SET account = EXCLUDED.account, scopes = EXCLUDED.scopes "
            f"RETURNING {COLUMNS}",
            (provider, service, account, scopes, Jsonb(config or {})),
        ).fetchone()
    return db.require(row, "connection")


def get(conn: psycopg.Connection, connection_id: UUID) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(f"SELECT {COLUMNS} FROM connections WHERE id = %s", (connection_id,)).fetchone()


def list_all(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            f"SELECT {', '.join('c.' + c.strip() for c in COLUMNS.split(','))}, "
            "(SELECT count(*) FROM connector_items i WHERE i.connection_id = c.id) "
            "+ (SELECT count(*) FROM calendar_events e WHERE e.connection_id = c.id) AS items "
            "FROM connections c ORDER BY c.provider, c.service"
        ).fetchall()


def set_enabled(conn: psycopg.Connection, connection_id: UUID, enabled: bool) -> bool:
    return conn.execute("UPDATE connections SET enabled = %s WHERE id = %s", (enabled, connection_id)).rowcount == 1


def set_config(conn: psycopg.Connection, connection_id: UUID, config: dict) -> None:
    conn.execute("UPDATE connections SET config = %s WHERE id = %s", (Jsonb(config), connection_id))


def record_success(conn: psycopg.Connection, connection_id: UUID, cursor: str | None) -> None:
    """The one place a cursor moves forward: called only after every item of a sync was stored."""
    conn.execute(
        "UPDATE connections SET cursor = %s, last_sync_at = now(), last_ok_at = now(), last_error = NULL WHERE id = %s",
        (cursor, connection_id),
    )


def record_failure(conn: psycopg.Connection, connection_id: UUID, error: str) -> None:
    """A failed or interrupted sync keeps its old cursor, so the next run repeats the same changes."""
    conn.execute("UPDATE connections SET last_sync_at = now(), last_error = %s WHERE id = %s",
                 (error[:ERROR_MAX], connection_id))


def item_version(conn: psycopg.Connection, connection_id: UUID, external_id: str) -> str | None:
    row = conn.execute("SELECT version FROM connector_items WHERE connection_id = %s AND external_id = %s",
                       (connection_id, external_id)).fetchone()
    return row[0] if row else None


def map_item(conn: psycopg.Connection, connection_id: UUID, external_id: str, document_id: UUID, version: str | None) -> None:
    conn.execute(
        "INSERT INTO connector_items (connection_id, external_id, document_id, version) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (connection_id, external_id) DO UPDATE SET document_id = EXCLUDED.document_id, version = EXCLUDED.version",
        (connection_id, external_id, document_id, version),
    )


def remove_item(conn: psycopg.Connection, connection_id: UUID, external_id: str) -> bool:
    """Delete the pal document behind an outside item (its chunks and vectors go with it)."""
    with conn.transaction():
        row = conn.execute(
            "DELETE FROM connector_items WHERE connection_id = %s AND external_id = %s RETURNING document_id",
            (connection_id, external_id),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM documents WHERE id = %s", (row[0],))
    return row is not None


def remove(conn: psycopg.Connection, connection_id: UUID) -> int:
    """Disconnect: delete the connection and every document it brought in. Returns how many documents went.

    Tokens are the caller's job (they're in the secret store, and revoking them needs the network).
    """
    with conn.transaction():
        docs = conn.execute(
            "DELETE FROM documents WHERE id IN (SELECT document_id FROM connector_items WHERE connection_id = %s)",
            (connection_id,),
        ).rowcount
        conn.execute("DELETE FROM connections WHERE id = %s", (connection_id,))
    return docs


def set_project(conn: psycopg.Connection, connection_id: UUID, project_id: UUID | None) -> int:
    """Put a connection (and every document it already brought in) in a project, or none. Returns documents moved."""
    with conn.transaction():
        conn.execute("UPDATE connections SET project_id = %s WHERE id = %s", (project_id, connection_id))
        return conn.execute(
            "UPDATE documents SET project_id = %s WHERE id IN "
            "(SELECT document_id FROM connector_items WHERE connection_id = %s)",
            (project_id, connection_id),
        ).rowcount
