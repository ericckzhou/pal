"""Hybrid retrieval over memories, document chunks and past conversations.

Per source: a full-text list (any query word) and a vector list (nomic-embed-text, cosine). Lists are fused
with reciprocal-rank fusion. Weak matches are dropped, so retrieval can, and often should, return nothing.
Pinned memories are always included. Every item gets a citation handle: m1.. memories, d1.. documents,
n1.. notes and decisions, c1.. past conversations.
"""

import logging
import re
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .embed import QUERY_PREFIX, vector_literal

log = logging.getLogger("pal.retrieval")

VEC_MIN = 0.60  # cosine similarity; related text scored 0.61-0.81, unrelated 0.33-0.59 (nomic-embed-text)
_HANDLE = re.compile(r"\s?\[[mdcnp]\d+\]")  # citation handles inside old replies mean nothing out of their turn
FTS_VEC_MIN = 0.45  # a keyword match also needs at least this much similarity once vectors exist
PER_LIST = 8
MAX_ITEMS = 6
MAX_PINNED = 20
RRF_K = 60
PROJECT_BOOST = 1.5  # fused score multiplier for items in the chat's focus project (ranks higher, never filters)
SNIPPET = 300
ITEM_CHARS = 1500

# Each source: (kind, handle prefix, SQL returning id/title/text/extra columns + sim, with {where} for filters)
_SOURCES = {
    "memory": (
        "m",
        """SELECT id, NULL::text AS title, text, NULL::uuid AS conversation_id, NULL::uuid AS document_id,
                  NULL::int AS page, NULL::timestamptz AS at, project_id, {sim} AS sim
           FROM memories WHERE status = 'confirmed' AND NOT pinned AND {where}""",
    ),
    "document": (
        "d",
        """SELECT c.id, d.title || coalesce(' › ' || c.heading, '') AS title, c.content AS text,
                  NULL::uuid AS conversation_id, d.id AS document_id, c.page, d.updated_at AS at, d.project_id, {sim} AS sim
           FROM chunks c JOIN documents d ON d.id = c.document_id WHERE {where}""",
    ),
    "note": (
        "n",
        """SELECT n.id, CASE WHEN n.kind = 'decision' THEN 'decision' ELSE 'note' END
                         || coalesce(', project ' || p.name, '') AS title,
                  n.text, NULL::uuid AS conversation_id, NULL::uuid AS document_id, NULL::int AS page,
                  n.created_at AS at, n.project_id, {sim} AS sim
           FROM notes n LEFT JOIN projects p ON p.id = n.project_id WHERE {where}""",
    ),
    "conversation": (
        "c",
        """SELECT m.id, cv.title, m.content AS text, m.conversation_id, NULL::uuid AS document_id,
                  NULL::int AS page, m.created_at AS at, cv.project_id, {sim} AS sim
           FROM messages m JOIN conversations cv ON cv.id = m.conversation_id
           WHERE m.conversation_id IS DISTINCT FROM %(conv)s AND length(m.content) >= 15 AND {where}""",
    ),
}
_ALIAS = {"memory": "", "document": "c.", "note": "n.", "conversation": "m."}


def _query_vector(llm, text: str) -> str | None:
    try:
        return vector_literal(llm.embed([QUERY_PREFIX + text[:2000]])[0])
    except Exception:
        log.warning("query embedding failed; using keyword search only", exc_info=True)
        return None


def _search(
    conn: psycopg.Connection, kind: str, query: str, qvec: str | None, conv_id: UUID | None
) -> list[list[dict]]:
    _, sql = _SOURCES[kind]
    a = _ALIAS[kind]
    sim = f"CASE WHEN %(qvec)s::vector IS NULL OR {a}embedding IS NULL THEN NULL ELSE 1 - ({a}embedding <=> %(qvec)s::vector) END"
    params = {"q": query, "qvec": qvec, "conv": conv_id, "n": PER_LIST}
    any_word = "to_tsquery('english', NULLIF(replace(plainto_tsquery('english', %(q)s)::text, '&', '|'), ''))"
    fts_sql = (
        sql.format(sim=sim, where=f"{a}search @@ {any_word}")
        + f" ORDER BY ts_rank({a}search, {any_word}) DESC LIMIT %(n)s"
    )
    with conn.cursor(row_factory=dict_row) as cur:
        fts = cur.execute(fts_sql, params).fetchall()
        vec = []
        if qvec:
            vec_sql = (
                sql.format(sim=sim, where=f"{a}embedding IS NOT NULL")
                + f" ORDER BY {a}embedding <=> %(qvec)s::vector LIMIT %(n)s"
            )
            vec = [r for r in cur.execute(vec_sql, params).fetchall() if r["sim"] >= VEC_MIN]
    # A keyword-only hit must still be somewhat related in meaning (when we can tell).
    fts = [r for r in fts if r["sim"] is None or r["sim"] >= FTS_VEC_MIN]
    for r in fts + vec:
        r["kind"] = kind
    return [fts, vec]


def retrieve(
    conn: psycopg.Connection, llm, query: str, conv_id: UUID | None = None, project_id: UUID | None = None
) -> list[dict]:
    """Return context items: pinned memories first, then the best fused matches. Each has a `handle`.

    Items tagged with `project_id` (the chat's focus project) get a score boost; untagged items still compete.
    """
    query = query.strip()
    qvec = _query_vector(llm, query) if query else None
    scores: dict[tuple[str, UUID], float] = {}
    rows: dict[tuple[str, UUID], dict] = {}
    if query:
        for kind in _SOURCES:
            for ranked in _search(conn, kind, query, qvec, conv_id):
                for rank, r in enumerate(ranked):
                    key = (kind, r["id"])
                    scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
                    rows[key] = r
    if project_id is not None:
        for key, r in rows.items():
            if r.get("project_id") == project_id:
                scores[key] *= PROJECT_BOOST
    best = sorted(scores, key=lambda k: scores[k], reverse=True)[:MAX_ITEMS]

    with conn.cursor(row_factory=dict_row) as cur:
        pinned = cur.execute(
            """SELECT id, NULL::text AS title, text, NULL::uuid AS conversation_id, NULL::uuid AS document_id,
                      NULL::int AS page, NULL::timestamptz AS at, 'memory' AS kind
               FROM memories WHERE status = 'confirmed' AND pinned ORDER BY updated_at DESC LIMIT %s""",
            (MAX_PINNED,),
        ).fetchall()

    items = [dict(r, pinned=True) for r in pinned] + [dict(rows[k], pinned=False) for k in best]
    counters = {"memory": 0, "document": 0, "note": 0, "conversation": 0}
    out = []
    for item in items:
        if item["kind"] == "conversation":
            item["text"] = _HANDLE.sub("", item["text"])
        counters[item["kind"]] += 1
        prefix = _SOURCES[item["kind"]][0]
        out.append({
            "handle": f"{prefix}{counters[item['kind']]}",
            "kind": item["kind"],
            "id": str(item["id"]),
            "title": item["title"],
            "text": item["text"][:ITEM_CHARS],
            "snippet": " ".join(item["text"].split())[:SNIPPET],
            "page": item["page"],
            "conversation_id": str(item["conversation_id"]) if item["conversation_id"] else None,
            "document_id": str(item["document_id"]) if item["document_id"] else None,
            "at": item["at"].date().isoformat() if item["at"] else None,
            "pinned": item["pinned"],
        })
    return out


def public_sources(items: list[dict]) -> list[dict]:
    """What the UI gets (and what is stored with the reply): everything but the full text."""
    return [{k: v for k, v in item.items() if k != "text"} for item in items]
