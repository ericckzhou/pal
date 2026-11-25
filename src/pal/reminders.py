"""Reminders (once or repeating) and the notifications they produce when due.

Times are stored as timestamptz. Repeats are computed in local wall-clock time from `anchor`, so
"every Monday at 9" stays at 9:00 across daylight-saving changes.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from . import when

log = logging.getLogger("pal.reminders")

COLS = (
    "r.id, r.title, r.due_at, r.rrule, r.active, r.paused, r.task_id, r.project_id, p.name AS project, r.created_at"
)
NOTIFY_COLS = "id, reminder_id, title, fired_at, dismissed_at"


def local_now() -> datetime:
    """Naive local wall-clock time (what `when` works in)."""
    return datetime.now().replace(microsecond=0)


def aware(local: datetime) -> datetime:
    return local.astimezone()


def to_local(at: datetime) -> datetime:
    return at.astimezone().replace(tzinfo=None)


def _rows(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def _with_repeat(row: dict) -> dict:
    row["repeat"] = when.describe_rrule(row["rrule"])
    return row


def get(conn: psycopg.Connection, reminder_id: UUID) -> dict | None:
    found = _rows(conn, f"SELECT {COLS} FROM reminders r LEFT JOIN projects p ON p.id = r.project_id WHERE r.id = %s",
                  (reminder_id,))
    return _with_repeat(found[0]) if found else None


def list_reminders(conn: psycopg.Connection, include_done: bool = False) -> list[dict]:
    return [_with_repeat(r) for r in _rows(
        conn,
        f"""SELECT {COLS} FROM reminders r LEFT JOIN projects p ON p.id = r.project_id
            WHERE %s OR r.active ORDER BY r.active DESC, r.due_at LIMIT 500""",
        (include_done,),
    )]


def create(
    conn: psycopg.Connection,
    title: str,
    at: datetime,
    rrule: str | None = None,
    *,
    project_id: UUID | None = None,
    task_id: UUID | None = None,
    now: datetime | None = None,
) -> dict:
    """`at` is local wall-clock. For a repeat, `at` is the anchor and the first firing is the next match."""
    now = now or local_now()
    due = at
    if rrule:
        when.validate_rrule(rrule)
        due = when.first_occurrence(rrule, at, now) or at
    reminder_id = conn.execute(
        """INSERT INTO reminders (title, due_at, rrule, anchor, project_id, task_id)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (" ".join(title.split())[:300], aware(due), rrule, at if rrule else None, project_id, task_id),
    ).fetchone()[0]
    return get(conn, reminder_id)


def update(conn: psycopg.Connection, reminder_id: UUID, *, title=None, at: datetime | None = None,
           rrule: str | None | object = ..., now: datetime | None = None) -> dict | None:
    current = get(conn, reminder_id)
    if current is None:
        return None
    new_rule = current["rrule"] if rrule is ... else rrule
    if new_rule:
        when.validate_rrule(new_rule)
    anchor = at or to_local(current["due_at"])
    due = when.first_occurrence(new_rule, anchor, now or local_now()) if new_rule else anchor
    conn.execute(
        """UPDATE reminders SET title = COALESCE(%s, title), due_at = %s, rrule = %s, anchor = %s,
                  active = true, updated_at = now() WHERE id = %s""",
        (" ".join(title.split())[:300] if title else None, aware(due or anchor), new_rule,
         anchor if new_rule else None, reminder_id),
    )
    return get(conn, reminder_id)


def delete(conn: psycopg.Connection, reminder_id: UUID) -> bool:
    return conn.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,)).rowcount == 1


def set_paused(conn: psycopg.Connection, reminder_id: UUID, paused: bool, now: datetime | None = None) -> dict | None:
    """Pause or resume. A resumed repeat skips the times it missed while paused instead of firing them all."""
    current = get(conn, reminder_id)
    if current is None:
        return None
    due = current["due_at"]
    if not paused and current["paused"] and current["rrule"]:
        now = now or local_now()
        if to_local(due) <= now:
            anchor = to_local(due)
            row = conn.execute("SELECT anchor FROM reminders WHERE id = %s", (reminder_id,)).fetchone()
            nxt = when.next_occurrence(current["rrule"], row[0] or anchor, now)
            due = aware(nxt) if nxt else due
    conn.execute("UPDATE reminders SET paused = %s, due_at = %s, updated_at = now() WHERE id = %s",
                 (paused, due, reminder_id))
    return get(conn, reminder_id)


def trigger(conn: psycopg.Connection, reminder_id: UUID) -> dict | None:
    """Send the reminder's notification now and leave its schedule as it was."""
    current = get(conn, reminder_id)
    if current is None:
        return None
    return _rows(conn, f"INSERT INTO notifications (reminder_id, title) VALUES (%s, %s) RETURNING {NOTIFY_COLS}",
                 (reminder_id, current["title"]))[0]


def fire_due(conn: psycopg.Connection, now: datetime | None = None) -> list[dict]:
    """Turn due reminders into notifications. A repeat moves to its next time; missed repeats fire once."""
    now = now or local_now()
    fired: list[dict] = []
    with conn.transaction():
        due = _rows(
            conn,
            """SELECT id, title, due_at, rrule, anchor FROM reminders
               WHERE active AND NOT paused AND due_at <= %s ORDER BY due_at FOR UPDATE SKIP LOCKED LIMIT 100""",
            (aware(now),),
        )
        for r in due:
            fired += _rows(
                conn,
                f"INSERT INTO notifications (reminder_id, title) VALUES (%s, %s) RETURNING {NOTIFY_COLS}",
                (r["id"], r["title"]),
            )
            nxt = None
            if r["rrule"]:
                try:
                    anchor = r["anchor"] or to_local(r["due_at"])
                    nxt = when.next_occurrence(r["rrule"], anchor, max(now, to_local(r["due_at"])))
                except ValueError:
                    log.warning("reminder %s has a bad repeat rule", r["id"])
            if nxt:
                conn.execute("UPDATE reminders SET due_at = %s, updated_at = now() WHERE id = %s", (aware(nxt), r["id"]))
            else:
                conn.execute("UPDATE reminders SET active = false, updated_at = now() WHERE id = %s", (r["id"],))
    return fired


def tick(pool) -> int:
    with pool.connection() as conn:
        return len(fire_due(conn))


def notifications(conn: psycopg.Connection, include_dismissed: bool = False) -> list[dict]:
    return _rows(
        conn,
        f"""SELECT {NOTIFY_COLS} FROM notifications WHERE %s OR dismissed_at IS NULL
            ORDER BY fired_at DESC LIMIT 100""",
        (include_dismissed,),
    )


def dismiss(conn: psycopg.Connection, notification_id: UUID | None = None) -> int:
    """Dismiss one notification, or all of them when no id is given."""
    return conn.execute(
        "UPDATE notifications SET dismissed_at = now() WHERE dismissed_at IS NULL AND (%s::uuid IS NULL OR id = %s)",
        (notification_id, notification_id),
    ).rowcount


def snooze(conn: psycopg.Connection, notification_id: UUID, minutes: int, now: datetime | None = None) -> dict | None:
    """Dismiss the notification and set a one-off reminder with the same title `minutes` from now."""
    found = _rows(conn, f"SELECT {NOTIFY_COLS} FROM notifications WHERE id = %s", (notification_id,))
    if not found:
        return None
    dismiss(conn, notification_id)
    now = now or local_now()
    return create(conn, found[0]["title"], now + timedelta(minutes=minutes), now=now)
