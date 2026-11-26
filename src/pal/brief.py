"""The morning brief: one notification a day with what's overdue, due, scheduled and in progress today.

Plain queries, no model, like the weekly digest. It goes out once per local day, the first time pal is running
at or after the chosen hour (so a PC switched on at 10:00 still gets it). A day with nothing to say sends nothing.
"""

from datetime import date, datetime, time, timedelta

import psycopg
from psycopg.types.json import Jsonb

from . import db
from .reminders import aware, local_now

SENT_KEY = "brief_last_sent"  # app_settings; the local date of the last brief (not an editable setting)


def build(conn: psycopg.Connection, today: date) -> dict:
    start = aware(datetime.combine(today, time()))
    row = db.one(
        conn,
        """WITH open AS (  -- open tasks, leaving out archived projects (as the neglected-task scan does)
             SELECT t.* FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
             WHERE t.status = 'open' AND (p.id IS NULL OR p.status = 'active'))
           SELECT
             (SELECT count(*) FROM open WHERE due_date < %(today)s),
             (SELECT count(*) FROM open WHERE due_date = %(today)s),
             (SELECT count(*) FROM reminders WHERE active AND NOT paused AND due_at >= %(start)s AND due_at < %(end)s),
             (SELECT count(*) FROM open WHERE stage = 'doing'),
             (SELECT title FROM open WHERE due_date <= %(today)s ORDER BY due_date, created_at LIMIT 1)""",
        {"today": today, "start": start, "end": start + timedelta(days=1)},
    )
    return dict(zip(("overdue", "due_today", "reminders_today", "in_progress", "first"), row, strict=True))


def _part(n: int, one: str, many: str | None = None) -> str | None:
    return None if n == 0 else f"{n} {one if n == 1 else many or one}"


def text(b: dict) -> str | None:
    """The notification line, or None when there's nothing to report."""
    parts = [p for p in (
        _part(b["overdue"], "overdue"),
        _part(b["due_today"], "due today"),
        _part(b["reminders_today"], "reminder today", "reminders today"),
        _part(b["in_progress"], "in progress"),
    ) if p]
    if not parts:
        return None
    line = "Morning brief: " + " · ".join(parts) + "."
    return f"{line} Start with: {b['first']}" if b["first"] else line


def daily_tick(conn: psycopg.Connection, hour: int, now: datetime | None = None) -> str | None:
    """Send today's brief if it's due. `hour` 0 turns the brief off. Returns the text when a notification was made."""
    now = now or local_now()
    if hour == 0 or now.hour < hour:
        return None
    today = now.date().isoformat()
    last = conn.execute("SELECT value FROM app_settings WHERE key = %s", (SENT_KEY,)).fetchone()
    if last and last[0] == today:
        return None
    line = text(build(conn, now.date()))
    with conn.transaction():
        conn.execute(
            """INSERT INTO app_settings (key, value) VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
            (SENT_KEY, Jsonb(today)),
        )
        if line:
            conn.execute("INSERT INTO notifications (title) VALUES (%s)", (line,))
    return line


def tick(pool, hour: int) -> str | None:
    with pool.connection() as conn:
        return daily_tick(conn, hour)
