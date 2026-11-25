"""Background embedding of chunks, memories and messages that don't have a vector yet."""

import logging

import psycopg

log = logging.getLogger("pal.embed")

BATCH = 32
MAX_CHARS = 6000
MIN_MESSAGE_CHARS = 15  # shorter messages ("hi", "thanks") aren't worth searching by meaning
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

# (table, text expression, extra filter). Text is re-checked on write so an edit made mid-batch isn't overwritten.
TARGETS = (
    # A chunk is embedded with its document's title and section path, so a passage that never names its subject
    # ("Revenue grew 4%") still lands near questions about that document.
    ("chunks", "(SELECT d.title FROM documents d WHERE d.id = document_id) || E'\\n' "
               "|| coalesce(heading || E'\\n', '') || content", "true"),
    ("memories", "text", "true"),
    ("notes", "text", "true"),
    ("messages", "content", f"length(content) >= {MIN_MESSAGE_CHARS}"),
)


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def embed_pending(conn: psycopg.Connection, llm, batch: int = BATCH) -> int:
    """Embed up to `batch` rows per table. Returns how many rows got a vector."""
    done = 0
    for table, expr, extra in TARGETS:
        rows = conn.execute(
            f"SELECT id, {expr} FROM {table} WHERE embedding IS NULL AND {extra} ORDER BY id LIMIT %s", (batch,)
        ).fetchall()
        if not rows:
            continue
        vectors = llm.embed([DOC_PREFIX + text[:MAX_CHARS] for _, text in rows])
        with conn.transaction():
            for (row_id, text), vec in zip(rows, vectors, strict=True):
                done += conn.execute(
                    f"UPDATE {table} SET embedding = %s::vector WHERE id = %s AND {expr} = %s",
                    (vector_literal(vec), row_id, text),
                ).rowcount
    return done


def embed_all(pool, llm) -> int:
    total = 0
    while True:
        with pool.connection() as conn:
            n = embed_pending(conn, llm)
        total += n
        if n == 0:
            return total
