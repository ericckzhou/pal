"""Hybrid retrieval over memories, document chunks and past conversations.

Per source: a full-text list (any query word) and a vector list (nomic-embed-text, cosine). Lists are fused
with reciprocal-rank fusion. Weak matches are dropped, so retrieval can, and often should, return nothing.
Pinned memories are always included. Every item gets a citation handle: m1.. memories, d1.. documents,
n1.. notes and decisions, c1.. past conversations.

A caller can pass `trace={}` to get a decision trace: each candidate's ranks, similarity and fused score, what the
reranker kept or dropped, and whether it fell back. The trace holds ids and numbers, never item text, so deleting a
memory or document still removes its words everywhere (it is stored with the reply; see trace_for_ui).

StarcallOS (optional, `external=`): its hits arrive as ONE more ranked list (starcall.py), fused by rank like pal's
lists, so a lone StarcallOS hit can't outrank a pal item both of pal's lists found. Its scores are never compared with
pal's cosine cut-offs. The reranker judges StarcallOS candidates like any other; when it didn't run (off, failed), a
StarcallOS hit needs a keyword match. At most STARCALL_CAP of them reach the output, and nothing is reserved for them.
"""

import concurrent.futures
import logging
import re
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from . import rerank, starcall
from .connectors.starcall import CALL_TIMEOUT, SAFETY_MARGIN
from .embed import QUERY_PREFIX, vector_literal

log = logging.getLogger("pal.retrieval")

VEC_MIN = 0.60  # cosine similarity; related text scored 0.61-0.81, unrelated 0.33-0.59 (nomic-embed-text)
_HANDLE = re.compile(r"\s?\[[mdcnps]\d+\]")  # citation handles inside old replies mean nothing out of their turn
FTS_VEC_MIN = 0.55  # a keyword match also needs this much similarity; 0.45 -> 0.55 cut no-answer noise (eval/RETRIEVAL.md)
PER_LIST = 8
MAX_ITEMS = 6
MAX_PINNED = 20
RRF_K = 60
PROJECT_BOOST = 1.5  # fused score multiplier for items in the chat's focus project (ranks higher, never filters)
SNIPPET = 300
ITEM_CHARS = 1500
TRACE_MAX = 20  # fused candidates recorded per reply (the reranker reads at most rerank.POOL of them)
TRACE_VERSION = 2  # 2: StarcallOS candidates (kind "starcall") and the top-level "starcall" outcome
STARCALL_CAP = 2  # StarcallOS items per reply, at most
# How long retrieve() waits for a StarcallOS lookup that is still running. The client enforces its own deadline
# (CALL_TIMEOUT, started before pal's own searches), so this only catches a lookup that never returns.
EXTERNAL_WAIT = CALL_TIMEOUT + SAFETY_MARGIN
_PREFIX = {"memory": "m", "document": "d", "note": "n", "conversation": "c", starcall.KIND: "s"}

# Each source: (kind, handle prefix, SQL returning id/title/text/extra columns + sim, with {where} for filters)
_SOURCES = {
    "memory": (
        "m",
        """SELECT id, NULL::text AS title, text, NULL::uuid AS conversation_id, NULL::uuid AS document_id,
                  NULL::int AS page, updated_at AS at, project_id, {sim} AS sim
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
    conn: psycopg.Connection, kind: str, query: str, qvec: str | None, conv_id: UUID | None,
    cut: dict[str, int] | None = None,
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
        vec: list[dict] = []
        found: list[dict] = []
        if qvec:
            # HNSW + a WHERE filter (confirmed memories, other chats) could otherwise return fewer than `n` rows:
            # iterative scan keeps walking the index until enough rows pass the filter, in exact distance order.
            cur.execute("SET hnsw.iterative_scan = strict_order")
            vec_sql = (
                sql.format(sim=sim, where=f"{a}embedding IS NOT NULL")
                + f" ORDER BY {a}embedding <=> %(qvec)s::vector LIMIT %(n)s"
            )
            found = cur.execute(vec_sql, params).fetchall()
            vec = [r for r in found if r["sim"] >= VEC_MIN]
    # A keyword-only hit must still be somewhat related in meaning (when we can tell).
    kept_fts = [r for r in fts if r["sim"] is None or r["sim"] >= FTS_VEC_MIN]
    if cut is not None:
        cut["keyword"] = cut.get("keyword", 0) + len(fts) - len(kept_fts)
        cut["vector"] = cut.get("vector", 0) + len(found) - len(vec)
    fts = kept_fts
    for r in fts + vec:
        r["kind"] = kind
    return [fts, vec]


def _await_external(external: "concurrent.futures.Future[starcall.Lookup] | starcall.Lookup") -> starcall.Lookup:
    """The StarcallOS outcome for this turn. Any failure becomes a failed Lookup: StarcallOS never breaks a reply."""
    if isinstance(external, starcall.Lookup):
        return external
    try:
        result = external.result(timeout=EXTERNAL_WAIT)
    except concurrent.futures.TimeoutError:
        external.cancel()
        return starcall.Lookup("failed", reason="timeout", message="StarcallOS did not answer in time.")
    except Exception as e:  # the lookup's own errors are already Lookups; this is a bug or a cancelled future
        log.warning("StarcallOS lookup raised", exc_info=True)
        return starcall.failed(e)
    if not isinstance(result, starcall.Lookup):
        return starcall.Lookup("failed", reason="failed", message="StarcallOS lookup returned no result.")
    return result


def _pick(ordered: list, rows: dict, needs_keyword: bool) -> tuple[list, dict]:
    """Up to MAX_ITEMS keys in order, at most STARCALL_CAP of them StarcallOS. Returns the picks and why others lost."""
    best: list = []
    why: dict = {}
    n_ext = 0
    for key in ordered:
        if len(best) >= MAX_ITEMS:
            break
        if key[0] == starcall.KIND:
            if needs_keyword and not rows[key]["keyword_match"]:
                why[key] = "no_keyword_match"
                continue
            if n_ext >= STARCALL_CAP:
                why[key] = "capped"
                continue
            n_ext += 1
        best.append(key)
    return best, why


def retrieve(
    conn: psycopg.Connection, llm, query: str, conv_id: UUID | None = None, project_id: UUID | None = None,
    use_rerank: bool | None = None, trace: dict | None = None,
    external: "concurrent.futures.Future[starcall.Lookup] | starcall.Lookup | None" = None,
) -> list[dict]:
    """Return context items: pinned memories first, then the best fused matches. Each has a `handle`.

    Items tagged with `project_id` (the chat's focus project) get a score boost; untagged items still compete.
    With `use_rerank` (Settings > Model, on by default), the local model reads the top fused candidates and drops the ones that
    don't answer the question (rerank.py). Pass `trace={}` to have it filled with the decision trace.

    `external` is this turn's StarcallOS lookup (a Lookup, or a future started before pal's own searches so both run
    at once). Without it the result is exactly what it was before StarcallOS existed.
    """
    use_rerank = rerank.ENABLED if use_rerank is None else use_rerank
    query = query.strip()
    qvec = _query_vector(llm, query) if query else None
    scores: dict[tuple[str, UUID | str], float] = {}
    rows: dict[tuple[str, UUID | str], dict] = {}
    ranks: dict[tuple[str, UUID | str], dict[str, int]] = {}
    cut: dict[str, int] = {"keyword": 0, "vector": 0}
    if query:
        for kind in _SOURCES:
            for list_name, ranked in zip(("keyword", "vector"), _search(conn, kind, query, qvec, conv_id, cut), strict=True):
                for rank, r in enumerate(ranked):
                    key = (kind, r["id"])
                    scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
                    rows[key] = r
                    ranks.setdefault(key, {})[list_name] = rank + 1
    lookup = _await_external(external) if external is not None else None
    if lookup is not None and query:
        # One list: the hit at rank r adds 1/(RRF_K + r), the weight of a single pal list.
        for cand in lookup.candidates:
            key = (starcall.KIND, cand["id"])
            if key not in scores:
                scores[key] = 1.0 / (RRF_K + cand["starcall_rank"])
                rows[key] = cand
    boosted = set()
    if project_id is not None:
        for key, r in rows.items():
            if r.get("project_id") == project_id:
                scores[key] *= PROJECT_BOOST
                boosted.add(key)
    order = sorted(scores, key=lambda k: scores[k], reverse=True)
    rerank_info: dict = {"status": "off"}
    dropped: set = set()
    if use_rerank and order:
        pool = order[: rerank.POOL]
        kept, fell_back = rerank.rerank_checked(llm, query, [dict(rows[k], key=k) for k in pool])
        # A fallback returns the pool unjudged, so StarcallOS hits then need the keyword check too.
        best, why_not = _pick([c["key"] for c in kept], rows, needs_keyword=fell_back)
        dropped = set(pool) - {c["key"] for c in kept}
        rerank_info = {"status": "fell_back" if fell_back else "ran", "pool": len(pool), "kept": len(kept)}
    else:
        best, why_not = _pick(order, rows, needs_keyword=True)
        if use_rerank:
            rerank_info = {"status": "no_candidates"}

    with conn.cursor(row_factory=dict_row) as cur:
        pinned = cur.execute(
            """SELECT id, NULL::text AS title, text, NULL::uuid AS conversation_id, NULL::uuid AS document_id,
                      NULL::int AS page, updated_at AS at, 'memory' AS kind
               FROM memories WHERE status = 'confirmed' AND pinned ORDER BY updated_at DESC LIMIT %s""",
            (MAX_PINNED,),
        ).fetchall()

    items = [dict(r, pinned=True) for r in pinned] + [dict(rows[k], pinned=False) for k in best]
    counters = dict.fromkeys(_PREFIX, 0)
    out = []
    for item in items:
        if item["kind"] == "conversation":
            item["text"] = _HANDLE.sub("", item["text"])
        counters[item["kind"]] += 1
        entry = {
            "handle": f"{_PREFIX[item['kind']]}{counters[item['kind']]}",
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
        }
        if item["kind"] == starcall.KIND:
            entry.update({k: item[k] for k in ("starcall_kind", "page_end", "source_id", "chunk_id", "concept_id")})
        out.append(entry)
    if trace is not None:
        handles = {(o["kind"], o["id"]): o["handle"] for o in out}
        candidates: list[dict] = [
            {"kind": "memory", "id": str(r["id"]), "pinned": True, "handle": handles[("memory", str(r["id"]))],
             "decision": "used"}
            for r in pinned
        ]
        chosen = set(best)
        traced = order[:TRACE_MAX]
        # Every StarcallOS candidate is recorded, even below TRACE_MAX, so a capped hit is always explained.
        traced += [k for k in order[TRACE_MAX:] if k[0] == starcall.KIND]
        for key in traced:
            sim = rows[key]["sim"]
            if key in chosen:
                decision = "used"
            elif key in dropped:
                decision = "reranker_dropped"
            else:
                decision = why_not.get(key, "ranked_lower")
            entry = {
                "kind": key[0], "id": str(key[1]), "score": round(scores[key], 5),
                "keyword_rank": ranks.get(key, {}).get("keyword"), "vector_rank": ranks.get(key, {}).get("vector"),
                "sim": round(sim, 3) if sim is not None else None, "boosted": key in boosted,
                "decision": decision,
            }
            if key[0] == starcall.KIND:
                r = rows[key]
                # StarcallOS's own ranks; ids and "title p.N" metadata only, never passage text.
                entry.update({k: r[k] for k in ("starcall_kind", "starcall_rank", "keyword_rank", "vector_rank",
                                                "source_id", "chunk_id", "concept_id", "label")})
            if key in chosen:
                entry["handle"] = handles[(key[0], str(key[1]))]
            candidates.append(entry)
        trace.update({
            "v": TRACE_VERSION, "query": query, "vector_search": qvec is not None,
            "cutoffs": {"vec_min": VEC_MIN, "fts_vec_min": FTS_VEC_MIN}, "below_cutoff": cut,
            "fused": len(order), "rerank": rerank_info, "candidates": candidates,
        })
        if lookup is not None:
            trace["starcall"] = lookup.trace()
    return out


def mark_trimmed(trace: dict, used: list[dict]) -> dict:
    """Items retrieve() picked that build_messages left out for space: 'used' becomes 'trimmed'."""
    sent = {u["handle"] for u in used}
    candidates = [
        dict(c, decision="trimmed") if c["decision"] == "used" and c.get("handle") not in sent else c
        for c in trace.get("candidates", [])
    ]
    return dict(trace, candidates=candidates)


_LABEL_SQL = {
    "memory": "SELECT id, left(text, 120) AS label FROM memories WHERE id = ANY(%s)",
    "document": """SELECT c.id, d.title || coalesce(' › ' || c.heading, '') || coalesce(', p.' || c.page, '') AS label
                   FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = ANY(%s)""",
    "note": "SELECT id, left(text, 120) AS label FROM notes WHERE id = ANY(%s)",
    "conversation": """SELECT m.id, cv.title AS label FROM messages m JOIN conversations cv ON cv.id = m.conversation_id
                       WHERE m.id = ANY(%s)""",
}


def trace_for_ui(conn: psycopg.Connection, trace: dict) -> dict:
    """Add a current label to each candidate (None once the item is deleted); the stored trace has no text.

    StarcallOS entries keep the "title p.N" label stored with them: looking it up again would need the server.
    """
    by_kind: dict[str, list[UUID]] = {}
    for c in trace.get("candidates", []):
        if c["kind"] in _LABEL_SQL:
            by_kind.setdefault(c["kind"], []).append(UUID(c["id"]))
    labels: dict[tuple[str, str], str] = {}
    with conn.cursor(row_factory=dict_row) as cur:
        for kind, ids in by_kind.items():
            for r in cur.execute(_LABEL_SQL[kind], (ids,)).fetchall():
                labels[(kind, str(r["id"]))] = " ".join((r["label"] or "").split())
    candidates = [
        dict(c, label=labels.get((c["kind"], c["id"])) if c["kind"] in _LABEL_SQL else c.get("label"))
        for c in trace.get("candidates", [])
    ]
    return dict(trace, candidates=candidates)


def public_sources(items: list[dict]) -> list[dict]:
    """What the UI gets (and what is stored with the reply): everything but the full text."""
    return [{k: v for k, v in item.items() if k != "text"} for item in items]
