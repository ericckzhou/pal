"""Apply numbered SQL files from migrations/ in order, once each."""

from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def migrate(conninfo: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(conninfo) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        conn.commit()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            applied.append(path.stem)
    return applied
