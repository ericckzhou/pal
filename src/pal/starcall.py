"""StarcallOS results as retrieval candidates.

The MCP client (connectors/starcall.py) talks to the server. This module turns what it returns into one ranked list
that retrieval.retrieve() fuses with pal's own lists, and records what happened as a `Lookup`.

- Passages come in StarcallOS's own order. Their `score` is StarcallOS's rank-fusion score, so it is never compared
  with pal's cosine similarities; only the rank is used.
- At most one concept, and only when its whole name is in the question (`concept_matches`). A name match is the
  precision check: `search_concepts` alone matches any word of the name or definition.
- Text from StarcallOS goes into the prompt only. Nothing here stores it: the trace keeps ids and "title p.N",
  `stored_source` blanks the snippet before a reply is saved.

Permission (`allowed`): the chat's own `starcall` switch (off by default) AND the StarcallOS connection configured and
on AND native Windows, and in a Claude chat also that chat's existing `claude_context` sharing permission. Neither
switch stands in for the other.
"""

import concurrent.futures
import contextlib
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from . import claude
from .config import in_container
from .connectors.starcall import (
    SCOPES,
    ConceptHit,
    ConceptResult,
    PassageHit,
    PassageResult,
    StarcallClient,
    StarcallConfig,
    StarcallError,
)

log = logging.getLogger("pal.starcall")

KIND = "starcall"
PROVIDER, SERVICE = "starcall", "library"  # its row in `connections`; config holds server_path and scopes only
DOCKER_REASON = "Docker build: StarcallOS runs on native Windows only"
ERROR_MAX = 500
PASSAGE_LIMIT = 6  # passages asked for per turn; the cap in retrieval keeps at most 2 of anything StarcallOS sends
CONCEPT_LIMIT = 8
MIN_SINGLE_WORD = 4  # a one-word concept name shorter than this ("Set", "Map") would match far too many questions

_STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "into", "is", "it", "of", "on", "or", "the", "to", "what", "when", "where", "which", "who", "why", "with"]
)
_PUNCT = re.compile(r"[^\w\s]+")


@dataclass
class Lookup:
    """One turn's StarcallOS outcome. `candidates` hold text for the prompt; the trace keeps only the other fields."""

    status: str  # "ok" or "failed"
    candidates: list[dict] = field(default_factory=list)
    reason: str | None = None  # a StarcallError kind when failed
    message: str | None = None
    ms: int | None = None
    mode_used: str | None = None
    returned: int = 0

    def trace(self) -> dict:
        out: dict = {"status": self.status, "ms": self.ms, "returned": self.returned, "mode_used": self.mode_used}
        if self.reason:
            out["reason"] = self.reason
        return out


def failed(err: BaseException, ms: int | None = None) -> Lookup:
    if isinstance(err, StarcallError):
        return Lookup("failed", reason=err.kind, message=err.message, ms=ms)
    return Lookup("failed", reason="failed", message=f"{type(err).__name__}: {err}"[:300], ms=ms)


# ── concept name check ─────────────────────────────────────────────────────────────────────────────────────────────


def _fold(word: str) -> str:
    """Light plural fold, so "eigenvalues" in a question matches the concept "Eigenvalue"."""
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower().replace("_", " ")
    return [_fold(w) for w in _PUNCT.sub(" ", text).split()]


def concept_matches(name: str, query: str) -> bool:
    """True when the concept's name is in the question: as a whole phrase, or every meaningful name word appears.

    A one-word name needs at least MIN_SINGLE_WORD characters.
    """
    name_words = normalize(name)
    query_words = normalize(query)
    if not name_words or not query_words:
        return False
    if len(name_words) == 1 and len(name_words[0]) < MIN_SINGLE_WORD:
        return False
    n = len(name_words)
    if any(query_words[i : i + n] == name_words for i in range(len(query_words) - n + 1)):
        return True
    meaningful = [w for w in name_words if w not in _STOPWORDS]
    return bool(meaningful) and set(meaningful) <= set(query_words)


# ── hits to candidates ─────────────────────────────────────────────────────────────────────────────────────────────


def page_label(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    if end is None or end == start:
        return f"p.{start}"
    return f"pp.{start}–{end}"


def passage_candidate(hit: PassageHit, rank: int) -> dict:
    pages = page_label(hit.page_start, hit.page_end)
    title = hit.source_title + (f" › {hit.heading}" if hit.heading else "")
    return {
        "id": f"{hit.source_id}:{hit.chunk_id}",
        "kind": KIND,
        "starcall_kind": "passage",
        "title": title,
        "text": hit.text,
        "page": hit.page_start,
        "page_end": hit.page_end,
        "source_id": hit.source_id,
        "chunk_id": hit.chunk_id,
        "concept_id": None,
        "starcall_rank": rank,
        "keyword_rank": hit.keyword_rank,
        "vector_rank": hit.vector_rank,
        "keyword_match": hit.keyword_rank is not None,
        # Document metadata, kept in the trace so "Why these sources?" reads well without the server. Never passage text.
        "label": " ".join(f"{hit.source_title} {pages}".split()),
        "conversation_id": None, "document_id": None, "at": None, "project_id": None, "sim": None,
    }


def concept_candidate(hit: ConceptHit, rank: int) -> dict:
    text = hit.name + (f": {hit.definition}" if hit.definition else "")
    return {
        "id": f"concept:{hit.concept_id}",
        "kind": KIND,
        "starcall_kind": "concept",
        "title": f"concept “{hit.name}”" + (f", {hit.source_title}" if hit.source_title else ""),
        "text": text,
        "page": None,
        "page_end": None,
        "source_id": hit.source_id,
        "chunk_id": None,
        "concept_id": hit.concept_id,
        "starcall_rank": rank,
        "keyword_rank": None,
        "vector_rank": None,
        "keyword_match": True,  # its whole name is in the question, a stricter test than a shared word
        "label": f"Concept: {hit.name}",
        "conversation_id": None, "document_id": None, "at": None, "project_id": None, "sim": None,
    }


def to_candidates(passages: PassageResult | None, concepts: ConceptResult | None, query: str) -> list[dict]:
    """One ranked list. A matching concept goes first (its name is in the question), then passages in order."""
    out: list[dict] = []
    if concepts is not None:
        match = next(
            (c for c in concepts.results if "name" in c.matched_in and concept_matches(c.name, query)), None
        )
        if match is not None:
            out.append(concept_candidate(match, 1))
    seen: set[str] = set()
    for hit in passages.results if passages is not None else []:
        cand = passage_candidate(hit, len(out) + 1)
        if cand["id"] not in seen:
            seen.add(cand["id"])
            out.append(cand)
    return out


def build_lookup(
    query: str,
    passages: PassageResult | BaseException,
    concepts: ConceptResult | BaseException | None = None,
    started: float | None = None,
) -> Lookup:
    """Combine one turn's results. A failed passage search fails the lookup; a failed concept search is ignored."""
    ms = round((time.monotonic() - started) * 1000) if started is not None else None
    if isinstance(passages, BaseException):
        return failed(passages, ms)
    concept_result = concepts if isinstance(concepts, ConceptResult) else None
    candidates = to_candidates(passages, concept_result, query)
    return Lookup("ok", candidates=candidates, ms=ms, mode_used=passages.mode_used, returned=len(candidates))


# ── connection, permission, client ─────────────────────────────────────────────────────────────────────────────────


def connection(conn: psycopg.Connection) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT id, enabled, config, last_ok_at, last_error FROM connections WHERE provider = %s AND service = %s",
            (PROVIDER, SERVICE),
        ).fetchone()


def config_from(row: dict | None, ollama_url: str | None = None) -> StarcallConfig | None:
    """The client config a connection row describes, or None when it has no usable server path."""
    if row is None:
        return None
    cfg = row.get("config") or {}
    path = cfg.get("server_path")
    if not isinstance(path, str) or not path.strip():
        return None
    wanted = cfg.get("scopes") or ["passages"]
    scopes = tuple(s for s in SCOPES if s in wanted) or ("passages",)
    embed = f"{ollama_url.rstrip('/')}/v1" if ollama_url else None  # the server takes an OpenAI-style base URL
    return StarcallConfig(server_path=path.strip(), scopes=scopes, embed_url=embed)


def available(row: dict | None) -> bool:
    """The connection could be used by a chat that allows it: native Windows, configured, switched on."""
    return not in_container() and row is not None and bool(row["enabled"]) and config_from(row) is not None


def allowed(conv: dict, row: dict | None) -> bool:
    """May this turn search StarcallOS? Every condition must hold; none can stand in for another."""
    if not conv.get("starcall") or not available(row):
        return False
    # A Claude chat sends what matches to Anthropic, so it also needs that chat's sharing permission.
    return not claude.is_claude(conv.get("model")) or bool(conv.get("claude_context"))


make_client = StarcallClient  # tests swap in a fake
_client_lock = threading.Lock()


def client(state, cfg: StarcallConfig) -> StarcallClient | None:
    """The process's one client, made on first use. A changed config replaces it; the Docker build never makes one."""
    if in_container():
        return None
    with _client_lock:
        current = getattr(state, "starcall", None)
        if current is not None and current.cfg == cfg:
            return current
        state.starcall = make_client(cfg)
    if current is not None:  # closing waits for the child to exit; don't hold up this chat turn for it
        threading.Thread(target=current.close, name="starcall-close", daemon=True).start()
    return state.starcall


def close(state) -> None:
    current = getattr(state, "starcall", None)
    if current is not None:
        state.starcall = None
        current.close()


# ── one turn's lookup ──────────────────────────────────────────────────────────────────────────────────────────────

# Each lookup makes at most two client calls, and the client itself refuses more than MAX_IN_FLIGHT at once (`busy`),
# so this pool never holds more than a few blocked threads.
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="starcall-lookup")


def _outcome(fut: concurrent.futures.Future | None):
    if fut is None:
        return None
    if fut.cancelled():
        return StarcallError("timeout", "StarcallOS lookup was cancelled.")
    return fut.exception() or fut.result()


def lookup_future(sc: StarcallClient, query: str) -> concurrent.futures.Future[Lookup]:
    """Start this turn's StarcallOS search now, so it runs while pal searches its own data.

    Passages and (when that scope is on) concepts are asked for at the same time. retrieve() waits for the result.
    """
    query = query.strip()
    started = time.monotonic()
    out: concurrent.futures.Future[Lookup] = concurrent.futures.Future()
    parts: list[concurrent.futures.Future] = [_POOL.submit(sc.search_passages, query, PASSAGE_LIMIT)]
    if "concepts" in sc.cfg.scopes:
        parts.append(_POOL.submit(sc.search_concepts, query, CONCEPT_LIMIT))
    lock = threading.Lock()
    left = [len(parts)]

    def one_done(_: concurrent.futures.Future) -> None:
        with lock:
            left[0] -= 1
            if left[0]:
                return
        try:
            result = build_lookup(query, _outcome(parts[0]), _outcome(parts[1]) if len(parts) > 1 else None, started)
        except Exception as e:  # noqa: BLE001 - a bad result must never break the reply
            log.warning("StarcallOS results could not be read", exc_info=True)
            result = failed(e)
        with contextlib.suppress(concurrent.futures.InvalidStateError):  # retrieve() stopped waiting and cancelled it
            out.set_result(result)

    for part in parts:
        part.add_done_callback(one_done)
    return out


def finished(fut: concurrent.futures.Future[Lookup] | None) -> Lookup | None:
    """The lookup's result once retrieve() is done with it (None if it never finished)."""
    if fut is None or not fut.done() or fut.cancelled():
        return None
    try:
        return fut.result(timeout=0)
    except Exception:  # noqa: BLE001
        return None


def record(conn: psycopg.Connection, lookup: Lookup | None, trace: dict | None) -> None:
    """Keep the connection's last success or error up to date for the Connections page. Never passage text."""
    status = (trace or {}).get("starcall") or {}
    if not status:
        return
    if status.get("status") == "ok":
        conn.execute("UPDATE connections SET last_ok_at = now(), last_error = NULL WHERE provider = %s AND service = %s",
                     (PROVIDER, SERVICE))
        return
    reason = status.get("reason") or "failed"
    if reason == "busy":
        return  # other chats were using it; nothing is wrong with the setup
    message = lookup.message if lookup is not None and lookup.message else REASONS.get(reason, "")
    conn.execute("UPDATE connections SET last_error = %s WHERE provider = %s AND service = %s",
                 (f"{reason}: {message}"[:ERROR_MAX], PROVIDER, SERVICE))


# ── what the chat shows and keeps ──────────────────────────────────────────────────────────────────────────────────

REASONS = {
    "schema_mismatch": "its database needs the StarcallOS app opened once after an update (schema mismatch)",
    "db_missing": "no StarcallOS database was found on this PC",
    "node_missing": "Node.js wasn't found; StarcallOS needs Node 22.13 or newer",
    "node_too_old": "StarcallOS needs Node 22.13 or newer",
    "server_missing": "the StarcallOS server path is wrong; check it on the Connections page",
    "incompatible_server": "this StarcallOS server doesn't match what pal expects; update it, then press Test",
    "timeout": "StarcallOS didn't answer in time",
    "busy": "StarcallOS was busy with other chats' lookups",
    "cooldown": "StarcallOS is waiting to restart after an error",
    "starting": "StarcallOS is still starting",
}


def notice(trace: dict | None) -> dict | None:
    """A step card when this turn's StarcallOS search didn't run. The reply goes ahead with pal's own sources."""
    status = (trace or {}).get("starcall") or {}
    if status.get("status") != "failed":
        return None
    why = REASONS.get(status.get("reason") or "", "StarcallOS couldn't be searched")
    return {"kind": "notice", "label": "StarcallOS skipped",
            "detail": f"{why[0].upper()}{why[1:]}. This reply uses pal's own sources only."}


def stored_source(source: dict) -> dict:
    """What is kept with a reply for a StarcallOS item it used: ids, title and page, never what the passage said."""
    if source.get("kind") != KIND:
        return source
    return {"handle": source["handle"], "kind": KIND, "starcall_kind": source.get("starcall_kind"),
            "id": source.get("id"), "title": source.get("title"), "snippet": "", "page": source.get("page"),
            "page_end": source.get("page_end"), "source_id": source.get("source_id"),
            "chunk_id": source.get("chunk_id"), "concept_id": source.get("concept_id"),
            "conversation_id": None, "document_id": None, "at": None, "pinned": False}


def chat_allows(conn: psycopg.Connection, conv_id: UUID) -> bool:
    row = conn.execute("SELECT starcall FROM conversations WHERE id = %s", (conv_id,)).fetchone()
    return bool(row and row[0])


def _not_ready_note(conn: psycopg.Connection, conv_id: UUID) -> str:
    if in_container():
        return f" It won't run here: {DOCKER_REASON}."
    row = connection(conn)
    if config_from(row) is None:
        return " StarcallOS isn't set up yet: add it on the Connections page (on the PC)."
    if row is not None and not row["enabled"]:
        return " The StarcallOS connection is paused on the Connections page."
    found = conn.execute("SELECT model, claude_context FROM conversations WHERE id = %s", (conv_id,)).fetchone()
    if found and claude.is_claude(found[0]):
        if not found[1]:
            return (" This is a Claude chat, so it also needs sharing with Claude turned on. With both on, matching "
                    "library passages are sent to Anthropic.")
        return " This is a Claude chat with sharing on, so matching library passages are sent to Anthropic."
    return ""


def run_command(conn: psycopg.Connection, conv_id: UUID, arg: str) -> dict:
    """`/starcall`, `/starcall on`, `/starcall off`: whether pal may search the StarcallOS library in this chat."""
    arg = arg.strip().lower()
    if arg in ("on", "off"):
        conn.execute("UPDATE conversations SET starcall = %s WHERE id = %s", (arg == "on", conv_id))
        if arg == "off":
            return {"reply": "pal won't search your StarcallOS library in this chat. Turn it back on with "
                             "`/starcall on`.", "changed": False, "starcall_changed": True, "starcall": False}
        return {"reply": "pal will search your StarcallOS library in this chat, and use at most 2 of its items per "
                         f"reply.{_not_ready_note(conn, conv_id)}", "changed": False, "starcall_changed": True,
                "starcall": True}
    if arg:
        return {"reply": "Use `/starcall on` or `/starcall off`.", "changed": False}
    state = "on" if chat_allows(conn, conv_id) else "off"
    note = _not_ready_note(conn, conv_id) if state == "on" else ""
    return {"reply": f"StarcallOS library search is **{state}** for this chat. Change it with `/starcall on` or "
                     f"`/starcall off`.{note}", "changed": False}
