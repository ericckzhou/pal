"""Projects, tasks and notes (a decision is a note with kind='decision'), and the focus project's card."""

from datetime import date
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

PROJECT_COLS = "id, name, summary, status, created_at, updated_at"
TASK_COLS = "t.id, t.project_id, p.name AS project, t.title, t.status, t.stage, t.due_date, t.created_at, t.done_at"
NOTE_COLS = "n.id, n.project_id, p.name AS project, n.kind, n.text, n.created_at, n.updated_at"
CARD_TASKS = 6
CARD_DECISIONS = 3


def _rows(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def _one(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> dict | None:
    found = _rows(conn, sql, params)
    return found[0] if found else None


def clean(text: str, limit: int) -> str:
    return " ".join(str(text).split())[:limit]


# ---------- projects ----------

def list_projects(conn: psycopg.Connection, include_archived: bool = False, today: date | None = None) -> list[dict]:
    """Projects with the stats their cards show. `today` is the PC's local date (the database may run in UTC)."""
    today = today or date.today()
    return _rows(
        conn,
        f"""SELECT {PROJECT_COLS},
                   (SELECT count(*) FROM tasks WHERE project_id = projects.id AND status = 'open') AS open_tasks,
                   (SELECT count(*) FROM tasks WHERE project_id = projects.id AND status = 'open'
                       AND due_date < %(today)s) AS overdue,
                   (SELECT min(due_date) FROM tasks WHERE project_id = projects.id AND status = 'open'
                       AND due_date >= %(today)s) AS next_due,
                   GREATEST(updated_at,
                       (SELECT max(updated_at) FROM tasks WHERE project_id = projects.id),
                       (SELECT max(updated_at) FROM notes WHERE project_id = projects.id),
                       (SELECT max(updated_at) FROM conversations WHERE project_id = projects.id)) AS last_activity
            FROM projects WHERE %(all)s OR status = 'active' ORDER BY status, lower(name)""",
        {"today": today, "all": include_archived},
    )


def get_project(conn: psycopg.Connection, project_id: UUID) -> dict | None:
    return _one(conn, f"SELECT {PROJECT_COLS} FROM projects WHERE id = %s", (project_id,))


def find_project(conn: psycopg.Connection, name: str) -> dict | None:
    """Exact (case-insensitive) name, else the one active project whose name contains it or vice versa."""
    name = clean(name, 80)
    if not name:
        return None
    exact = _one(conn, f"SELECT {PROJECT_COLS} FROM projects WHERE lower(name) = lower(%s)", (name,))
    if exact:
        return exact
    near = _rows(
        conn,
        f"""SELECT {PROJECT_COLS} FROM projects WHERE status = 'active'
            AND (lower(name) LIKE '%%' || lower(%s) || '%%' OR lower(%s) LIKE '%%' || lower(name) || '%%')""",
        (name, name),
    )
    return near[0] if len(near) == 1 else None


def create_project(conn: psycopg.Connection, name: str, summary: str = "") -> dict:
    return _rows(
        conn,
        f"INSERT INTO projects (name, summary) VALUES (%s, %s) RETURNING {PROJECT_COLS}",
        (clean(name, 80), summary.strip()[:2000]),
    )[0]


def update_project(conn: psycopg.Connection, project_id: UUID, *, name=None, summary=None, status=None) -> dict | None:
    return _one(
        conn,
        f"""UPDATE projects SET name = COALESCE(%s, name), summary = COALESCE(%s, summary),
                   status = COALESCE(%s, status), updated_at = now()
            WHERE id = %s RETURNING {PROJECT_COLS}""",
        (clean(name, 80) if name is not None else None, summary.strip()[:2000] if summary is not None else None,
         status, project_id),
    )


def delete_project(conn: psycopg.Connection, project_id: UUID) -> bool:
    """Tasks and notes stay (moved out of the project); only the project row goes."""
    return conn.execute("DELETE FROM projects WHERE id = %s", (project_id,)).rowcount == 1


def project_names(conn: psycopg.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM projects WHERE status = 'active' ORDER BY lower(name)")]


# ---------- tasks ----------

def list_tasks(conn: psycopg.Connection, project_id: UUID | None = None, status: str = "open",
               no_project: bool = False, limit: int = 500) -> list[dict]:
    return _rows(
        conn,
        f"""SELECT {TASK_COLS} FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
            WHERE (%(status)s = 'all' OR t.status = %(status)s)
              AND (%(pid)s::uuid IS NULL OR t.project_id = %(pid)s)
              AND (NOT %(none)s OR t.project_id IS NULL)
            ORDER BY t.status, t.due_date NULLS LAST, t.created_at DESC LIMIT %(n)s""",
        {"status": status, "pid": project_id, "none": no_project, "n": limit},
    )


def get_task(conn: psycopg.Connection, task_id: UUID) -> dict | None:
    return _one(conn, f"SELECT {TASK_COLS} FROM tasks t LEFT JOIN projects p ON p.id = t.project_id WHERE t.id = %s",
                (task_id,))


def create_task(conn: psycopg.Connection, title: str, project_id: UUID | None = None, due_date: date | None = None,
                conversation_id: UUID | None = None) -> dict:
    task_id = conn.execute(
        "INSERT INTO tasks (title, project_id, due_date, source_conversation_id) VALUES (%s, %s, %s, %s) RETURNING id",
        (clean(title, 300), project_id, due_date, conversation_id),
    ).fetchone()[0]
    return get_task(conn, task_id)


_UNSET = object()


def update_task(conn: psycopg.Connection, task_id: UUID, *, title=None, status=None, stage=None, project_id=_UNSET,
                due_date=_UNSET) -> dict | None:
    sets, params = ["updated_at = now()"], []
    if title is not None:
        sets.append("title = %s")
        params.append(clean(title, 300))
    if status is not None:
        sets.append("status = %s, done_at = CASE WHEN %s = 'done' THEN now() END")
        params += [status, status]
    if stage is not None:
        sets.append("stage = %s")
        params.append(stage)
    if project_id is not _UNSET:
        sets.append("project_id = %s")
        params.append(project_id)
    if due_date is not _UNSET:
        sets.append("due_date = %s")
        params.append(due_date)
    if conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s", (*params, task_id)).rowcount != 1:
        return None
    return get_task(conn, task_id)


def delete_task(conn: psycopg.Connection, task_id: UUID) -> bool:
    return conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,)).rowcount == 1


# ---------- notes and decisions ----------

def list_notes(conn: psycopg.Connection, project_id: UUID | None = None, kind: str | None = None,
               limit: int = 500) -> list[dict]:
    return _rows(
        conn,
        f"""SELECT {NOTE_COLS} FROM notes n LEFT JOIN projects p ON p.id = n.project_id
            WHERE (%(pid)s::uuid IS NULL OR n.project_id = %(pid)s) AND (%(kind)s::text IS NULL OR n.kind = %(kind)s)
            ORDER BY n.created_at DESC LIMIT %(n)s""",
        {"pid": project_id, "kind": kind, "n": limit},
    )


def create_note(conn: psycopg.Connection, text: str, kind: str = "note", project_id: UUID | None = None) -> dict:
    note_id = conn.execute(
        "INSERT INTO notes (text, kind, project_id) VALUES (%s, %s, %s) RETURNING id",
        (text.strip()[:5000], kind, project_id),
    ).fetchone()[0]
    return _one(conn, f"SELECT {NOTE_COLS} FROM notes n LEFT JOIN projects p ON p.id = n.project_id WHERE n.id = %s",
                (note_id,))


def update_note(conn: psycopg.Connection, note_id: UUID, *, text=None, kind=None) -> dict | None:
    """An edit clears the vector so the embedding tick redoes it."""
    if conn.execute(
        """UPDATE notes SET text = COALESCE(%s, text), kind = COALESCE(%s, kind), updated_at = now(),
                  embedding = CASE WHEN %s::text IS NULL THEN embedding END WHERE id = %s""",
        (text.strip()[:5000] if text is not None else None, kind, text, note_id),
    ).rowcount != 1:
        return None
    return _one(conn, f"SELECT {NOTE_COLS} FROM notes n LEFT JOIN projects p ON p.id = n.project_id WHERE n.id = %s",
                (note_id,))


def delete_note(conn: psycopg.Connection, note_id: UUID) -> bool:
    """S10: text and vector go with the row."""
    return conn.execute("DELETE FROM notes WHERE id = %s", (note_id,)).rowcount == 1


# ---------- focus project ----------

def set_focus(conn: psycopg.Connection, conv_id: UUID, project_id: UUID | None) -> bool:
    return conn.execute("UPDATE conversations SET project_id = %s WHERE id = %s", (project_id, conv_id)).rowcount == 1


def focus_project(conn: psycopg.Connection, conv_id: UUID) -> dict | None:
    return _one(
        conn,
        f"SELECT {', '.join('p.' + c for c in PROJECT_COLS.split(', '))} FROM conversations c "
        "JOIN projects p ON p.id = c.project_id WHERE c.id = %s",
        (conv_id,),
    )


def project_card(conn: psycopg.Connection, project: dict, today: date) -> str:
    """A short plain-text summary of one project for the model's context."""
    lines = [f"Project {project['name']}" + (f": {project['summary']}" if project["summary"] else "")]
    tasks = list_tasks(conn, project["id"], "open", limit=CARD_TASKS + 1)
    if tasks:
        shown = []
        for t in tasks[:CARD_TASKS]:
            due = t["due_date"]
            tag = "" if due is None else f" (overdue, due {due})" if due < today else f" (due {due})"
            shown.append(t["title"] + tag)
        more = f"; and {len(tasks) - CARD_TASKS} more" if len(tasks) > CARD_TASKS else ""
        lines.append("Open tasks: " + "; ".join(shown) + more)
    else:
        lines.append("Open tasks: none")
    decisions = list_notes(conn, project["id"], "decision", limit=CARD_DECISIONS)
    if decisions:
        lines.append("Recent decisions: " + "; ".join(clean(d["text"], 200) for d in decisions))
    return "\n".join(lines)
