"""Everything that belongs to one project, gathered for its overview page."""

from datetime import date
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from . import db, reminders, work


def _rows(conn: psycopg.Connection, sql: str, params: tuple) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def overview(conn: psycopg.Connection, project_id: UUID, today: date | None = None) -> dict | None:
    project = work.get_project(conn, project_id)
    if project is None:
        return None
    today = today or reminders.local_now().date()
    project["card"] = work.project_card(conn, project, today)
    pid = (project_id,)

    tasks = _rows(conn, """
        SELECT id, title, due_date, created_at FROM tasks WHERE project_id = %s AND status = 'open'
        ORDER BY due_date NULLS LAST, created_at""", pid)
    done_week = db.scalar(conn,
        "SELECT count(*) FROM tasks WHERE project_id = %s AND status = 'done' AND done_at > now() - interval '7 days'",
        pid)
    # A reminder belongs to the project directly, or through the task it reminds about.
    scheduled = [reminders._with_repeat(r) for r in _rows(conn, """
        SELECT r.id, r.title, r.due_at, r.rrule, r.paused, t.title AS task
        FROM reminders r LEFT JOIN tasks t ON t.id = r.task_id
        WHERE r.active AND (r.project_id = %s OR t.project_id = %s)
        ORDER BY r.paused, r.due_at LIMIT 50""", (project_id, project_id))]
    memories = _rows(conn, """
        SELECT id, text, kind, status, pinned, updated_at FROM memories WHERE project_id = %s
        ORDER BY (status = 'proposed') DESC, pinned DESC, updated_at DESC LIMIT 50""", pid)
    documents = _rows(conn, """
        SELECT d.id, d.title, d.filename, d.kind, d.size_bytes, d.updated_at,
               (SELECT count(*) FROM chunks c WHERE c.document_id = d.id AND c.embedding IS NULL) AS pending
        FROM documents d WHERE d.project_id = %s ORDER BY d.updated_at DESC LIMIT 50""", pid)
    notes = _rows(conn, """
        SELECT id, kind, text, created_at FROM notes WHERE project_id = %s ORDER BY created_at DESC LIMIT 10""", pid)
    chats = _rows(conn, """
        SELECT c.id, c.title, c.updated_at, (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
        FROM conversations c WHERE c.project_id = %s ORDER BY c.updated_at DESC LIMIT 10""", pid)

    overdue = sum(1 for t in tasks if t["due_date"] and t["due_date"] < today)
    return {
        "project": project,
        "counts": {
            "open_tasks": len(tasks),
            "overdue": overdue,
            "done_this_week": done_week,
            "scheduled": sum(1 for r in scheduled if not r["paused"]),
            "memories": sum(1 for m in memories if m["status"] == "confirmed"),
            "suggested": sum(1 for m in memories if m["status"] == "proposed"),
            "documents": len(documents),
            "chats": len(chats),
        },
        "tasks": tasks,
        "scheduled": scheduled,
        "memories": memories,
        "documents": documents,
        "notes": notes,
        "chats": chats,
    }
