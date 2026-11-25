"""v1.1: the neglected-task scan and the weekly digest.

Both are plain queries over tasks, projects and activity; no model is involved, so the digest is exact and
never invents anything. A digest is made once per week (Monday from 08:00 local time) and stored as a snapshot.
"""

import json
from datetime import date, datetime, time, timedelta
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .reminders import aware, local_now

STALE_DAYS = 14          # an open task with no due date, untouched this long, is neglected
OVERDUE_DAYS = 7         # an open task this many days past its due date is neglected
QUIET_PROJECT_DAYS = 21  # an active project with open tasks and no activity this long is quiet
DIGEST_HOUR = 8          # Monday, local time
LIST_LIMIT = 50
DIGEST_COLS = "id, week_start, created_at, read_at"
NOTIFY_TITLE = "Your weekly digest is ready (Digest page)"


def _rows(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def _midnight(day: date) -> datetime:
    return aware(datetime.combine(day, time()))


# ---------- neglected-task scan ----------

def neglected(conn: psycopg.Connection, today: date | None = None) -> dict:
    """Open tasks that are long overdue or untouched, and active projects that have gone quiet."""
    today = today or local_now().date()
    params = {
        "overdue_before": today - timedelta(days=OVERDUE_DAYS - 1),
        "stale_before": _midnight(today) - timedelta(days=STALE_DAYS),
        "quiet_before": _midnight(today) - timedelta(days=QUIET_PROJECT_DAYS),
        "limit": LIST_LIMIT,
    }
    tasks = _rows(conn, """
        SELECT t.id, t.title, t.due_date, t.updated_at, t.project_id, p.name AS project,
               CASE WHEN t.due_date IS NOT NULL THEN 'overdue' ELSE 'stale' END AS reason
        FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.status = 'open' AND (p.id IS NULL OR p.status = 'active')
          AND ((t.due_date IS NOT NULL AND t.due_date < %(overdue_before)s)
               OR (t.due_date IS NULL AND t.updated_at < %(stale_before)s))
        ORDER BY (t.due_date IS NULL), t.due_date, t.updated_at
        LIMIT %(limit)s""", params)
    projects = _rows(conn, """
        SELECT * FROM (
            SELECT p.id, p.name,
                   (SELECT count(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'open') AS open_tasks,
                   GREATEST(p.updated_at,
                            (SELECT max(updated_at) FROM tasks WHERE project_id = p.id),
                            (SELECT max(updated_at) FROM notes WHERE project_id = p.id),
                            (SELECT max(updated_at) FROM conversations WHERE project_id = p.id)) AS last_activity
            FROM projects p WHERE p.status = 'active') q
        WHERE open_tasks > 0 AND last_activity < %(quiet_before)s
        ORDER BY last_activity LIMIT %(limit)s""", params)
    return {"tasks": tasks, "projects": projects,
            "rules": {"stale_days": STALE_DAYS, "overdue_days": OVERDUE_DAYS, "quiet_project_days": QUIET_PROJECT_DAYS}}


def still_on_it(conn: psycopg.Connection, task_id: UUID) -> bool:
    """The owner says an open task still matters: restart its staleness clock."""
    return conn.execute("UPDATE tasks SET updated_at = now() WHERE id = %s AND status = 'open'",
                        (task_id,)).rowcount == 1


# ---------- weekly digest ----------

def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_digest(conn: psycopg.Connection, monday: date) -> dict:
    """What happened in the 7 days before `monday`, and what's due in the 7 days from it."""
    start, end = monday - timedelta(days=7), monday
    p = {"start": _midnight(start), "end": _midnight(end), "ahead": _midnight(end + timedelta(days=7)),
         "monday": monday, "sunday": end + timedelta(days=6), "limit": LIST_LIMIT}
    task_cols = "t.id, t.title, t.due_date, p.name AS project"
    join = "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id"
    done = _rows(conn, f"""SELECT {task_cols}, t.done_at {join}
        WHERE t.status = 'done' AND t.done_at >= %(start)s AND t.done_at < %(end)s
        ORDER BY t.done_at LIMIT %(limit)s""", p)
    due = _rows(conn, f"""SELECT {task_cols} {join}
        WHERE t.status = 'open' AND t.due_date BETWEEN %(monday)s AND %(sunday)s
        ORDER BY t.due_date, t.created_at LIMIT %(limit)s""", p)
    overdue = _rows(conn, f"""SELECT {task_cols} {join}
        WHERE t.status = 'open' AND t.due_date < %(monday)s
        ORDER BY t.due_date LIMIT %(limit)s""", p)
    upcoming = _rows(conn, """SELECT r.id, r.title, r.due_at, p.name AS project
        FROM reminders r LEFT JOIN projects p ON p.id = r.project_id
        WHERE r.active AND NOT r.paused AND r.due_at >= %(end)s AND r.due_at < %(ahead)s
        ORDER BY r.due_at LIMIT %(limit)s""", p)
    projects = _rows(conn, """SELECT p.name,
            (SELECT count(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'done'
               AND t.done_at >= %(start)s AND t.done_at < %(end)s) AS done,
            (SELECT count(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'open') AS open
        FROM projects p WHERE p.status = 'active' ORDER BY 2 DESC, 3 DESC, p.name LIMIT 10""", p)
    counts = _rows(conn, """SELECT
        (SELECT count(*) FROM tasks WHERE done_at >= %(start)s AND done_at < %(end)s AND status = 'done') AS done,
        (SELECT count(*) FROM tasks WHERE created_at >= %(start)s AND created_at < %(end)s) AS added,
        (SELECT count(*) FROM notifications WHERE reminder_id IS NOT NULL
           AND fired_at >= %(start)s AND fired_at < %(end)s) AS reminders_fired,
        (SELECT count(*) FROM memories WHERE status = 'confirmed'
           AND COALESCE(confirmed_at, created_at) >= %(start)s AND COALESCE(confirmed_at, created_at) < %(end)s)
           AS memories_added,
        (SELECT count(*) FROM documents WHERE created_at >= %(start)s AND created_at < %(end)s) AS documents_added,
        (SELECT count(DISTINCT conversation_id) FROM messages
           WHERE created_at >= %(start)s AND created_at < %(end)s) AS chats,
        (SELECT count(*) FROM messages WHERE role = 'user'
           AND created_at >= %(start)s AND created_at < %(end)s) AS messages_sent""", p)[0]
    attention = neglected(conn, monday)
    body = {
        "period": {"start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat()},
        "counts": counts,
        "done": done,
        "due_this_week": due,
        "overdue": overdue,
        "upcoming_reminders": upcoming,
        "projects": [r for r in projects if r["done"] or r["open"]],
        "neglected": {"tasks": attention["tasks"][:10], "projects": attention["projects"][:10]},
    }
    return json.loads(json.dumps(body, default=str))  # dates and UUIDs as strings, as the API returns them


def _save(conn: psycopg.Connection, monday: date, replace: bool) -> dict | None:
    body = build_digest(conn, monday)
    conflict = "DO UPDATE SET body = EXCLUDED.body, created_at = now(), read_at = NULL" if replace else "DO NOTHING"
    found = _rows(conn, f"""INSERT INTO digests (week_start, body) VALUES (%s, %s)
                            ON CONFLICT (week_start) {conflict} RETURNING {DIGEST_COLS}""", (monday, Jsonb(body)))
    return found[0] if found else None


def weekly_tick(conn: psycopg.Connection, now: datetime | None = None) -> dict | None:
    """Make this week's digest once it's Monday 08:00 or later, and notify. Returns it when one was made."""
    now = now or local_now()
    monday = week_start(now.date())
    if now < datetime.combine(monday, time(DIGEST_HOUR)):
        return None
    if conn.execute("SELECT 1 FROM digests WHERE week_start = %s", (monday,)).fetchone():
        return None
    with conn.transaction():
        made = _save(conn, monday, replace=False)
        if made:
            conn.execute("INSERT INTO notifications (title) VALUES (%s)", (NOTIFY_TITLE,))
    return made


def tick(pool) -> dict | None:
    with pool.connection() as conn:
        return weekly_tick(conn)


def generate(conn: psycopg.Connection, today: date | None = None) -> dict:
    """Rebuild this week's digest now (no notification)."""
    return _save(conn, week_start(today or local_now().date()), replace=True)


def list_digests(conn: psycopg.Connection) -> list[dict]:
    return _rows(conn, f"SELECT {DIGEST_COLS} FROM digests ORDER BY week_start DESC LIMIT 104")


def get_digest(conn: psycopg.Connection, digest_id: UUID) -> dict | None:
    found = _rows(conn, f"SELECT {DIGEST_COLS}, body FROM digests WHERE id = %s", (digest_id,))
    return found[0] if found else None


def mark_read(conn: psycopg.Connection, digest_id: UUID) -> dict | None:
    found = _rows(conn, f"""UPDATE digests SET read_at = COALESCE(read_at, now()) WHERE id = %s
                            RETURNING {DIGEST_COLS}""", (digest_id,))
    return found[0] if found else None
