"""Conversation and message storage + full-text search."""

from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import db

TITLE_LEN = 60
COMMAND_MODEL = "command"  # marks pal's replies to slash commands


def _rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def list_conversations(conn: psycopg.Connection, limit: int = 200) -> list[dict]:
    return _rows(conn, "SELECT c.id, c.title, c.updated_at, c.model, "
        "(SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
        "FROM conversations c ORDER BY c.updated_at DESC, c.id DESC LIMIT %s", (limit,))


def create_conversation(conn: psycopg.Connection) -> dict:
    return _rows(conn, "INSERT INTO conversations DEFAULT VALUES RETURNING id, title, updated_at")[0]


def get_conversation(conn: psycopg.Connection, conv_id: UUID) -> dict | None:
    found = _rows(conn, "SELECT id, title, updated_at, project_id, model, thinking, claude_context FROM conversations WHERE id = %s", (conv_id,))
    if not found:
        return None
    conv = found[0]
    conv["messages"] = _rows(
        conn,
        "SELECT id, role, content, model, sources, steps, prompt_tokens, completion_tokens, created_at, "
        "retrieval_trace IS NOT NULL AS has_trace FROM messages WHERE conversation_id = %s "
        "ORDER BY created_at, id",
        (conv_id,),
    )
    return conv


def delete_conversation(conn: psycopg.Connection, conv_id: UUID) -> bool:
    """Unconfirmed proposals drawn from the conversation go with it; confirmed memories stay (source cleared)."""
    with conn.transaction():
        conn.execute("DELETE FROM memories WHERE source_conversation_id = %s AND status = 'proposed'", (conv_id,))
        return conn.execute("DELETE FROM conversations WHERE id = %s", (conv_id,)).rowcount == 1


def add_message(
    conn: psycopg.Connection,
    conv_id: UUID,
    role: str,
    content: str,
    model: str | None = None,
    sources: list[dict] | None = None,
    *,
    steps: list[dict] | None = None,
    usage: dict | None = None,
    trace: dict | None = None,
) -> UUID:
    usage = usage or {}
    msg_id = db.scalar(conn,
        """INSERT INTO messages (conversation_id, role, content, model, sources, steps, prompt_tokens, completion_tokens,
                                 retrieval_trace)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (conv_id, role, content, model, Jsonb(sources) if sources else None, Jsonb(steps) if steps else None,
         usage.get("prompt_tokens"), usage.get("completion_tokens"), Jsonb(trace) if trace else None),
    )
    title = " ".join(content.split())[:TITLE_LEN]
    if title.startswith("/"):  # a slash command makes a poor chat title; wait for a real message
        role = "command"
    conn.execute(
        "UPDATE conversations SET updated_at = now(), "
        "title = CASE WHEN title = 'New chat' AND %s = 'user' THEN %s ELSE title END WHERE id = %s",
        (role, title, conv_id),
    )
    return msg_id


def drop_unanswered(conn: psycopg.Connection, conv_id: UUID, msg_id: UUID) -> None:
    """Remove a user message whose reply failed, so history never holds two user turns in a row.

    Anything made from it (a stated memory, a drafted action) stays; their link to the message is cleared. If it
    was the chat's only message, the chat goes back to "New chat" so the resent text can title it again.
    """
    with conn.transaction():
        conn.execute("DELETE FROM messages WHERE id = %s AND conversation_id = %s AND role = 'user'", (msg_id, conv_id))
        conn.execute(
            "UPDATE conversations SET title = 'New chat' WHERE id = %s "
            "AND NOT EXISTS (SELECT 1 FROM messages WHERE conversation_id = %s)",
            (conv_id, conv_id),
        )


def history(conn: psycopg.Connection, conv_id: UUID, with_model: bool = False) -> list[dict]:
    """What the model sees. Slash commands and their replies are left out: the model would imitate them."""
    cols = "role, content, model" if with_model else "role, content"
    return _rows(
        conn,
        rf"""SELECT {cols} FROM messages WHERE conversation_id = %s
              AND model IS DISTINCT FROM %s AND NOT (role = 'user' AND content ~ '^\s*/[A-Za-z]+(\s|$)')
            ORDER BY created_at, id""",
        (conv_id, COMMAND_MODEL),
    )


def search(conn: psycopg.Connection, query: str, limit: int = 20) -> list[dict]:
    return _rows(
        conn,
        """
        SELECT m.conversation_id, c.title, m.id AS message_id, m.role, m.created_at,
               ts_headline('english', m.content, q, 'MaxWords=25, MinWords=8, StartSel=«, StopSel=»') AS snippet
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id,
             websearch_to_tsquery('english', %s) q
        WHERE m.search @@ q
        ORDER BY ts_rank(m.search, q) DESC, m.created_at DESC
        LIMIT %s
        """,
        (query, limit),
    )
