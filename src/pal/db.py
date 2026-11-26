"""Small helpers for queries that always return a row (INSERT ... RETURNING, count(*), a row checked just before)."""

from typing import Any

import psycopg


def one(conn: psycopg.Connection, sql: str, params: Any = ()) -> tuple:
    """The single row `sql` returns; a missing row is a bug, so it raises instead of returning None."""
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise LookupError(f"expected a row from: {sql.split()[0]} ...")
    return row


def scalar(conn: psycopg.Connection, sql: str, params: Any = ()) -> Any:
    """The first column of the single row `sql` returns."""
    return one(conn, sql, params)[0]



def require[T](value: T | None, what: str = "row") -> T:
    """A value that must exist (e.g. a row read back right after inserting it)."""
    if value is None:
        raise LookupError(f"expected a {what}")
    return value
