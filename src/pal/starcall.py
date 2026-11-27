"""StarcallOS results as retrieval candidates.

The MCP client (connectors/starcall.py) talks to the server. This module turns what it returns into one ranked list
that retrieval.retrieve() fuses with pal's own lists, and records what happened as a `Lookup`.

- Passages come in StarcallOS's own order. Their `score` is StarcallOS's rank-fusion score, so it is never compared
  with pal's cosine similarities; only the rank is used.
- At most one concept, and only when its whole name is in the question (`concept_matches`). A name match is the
  precision check: `search_concepts` alone matches any word of the name or definition.
- Text from StarcallOS goes into the prompt only. Nothing here stores it (see retrieval.py's trace and
  `stored_source`, added with the chat wiring).
"""

import re
import time
import unicodedata
from dataclasses import dataclass, field

from .connectors.starcall import ConceptHit, ConceptResult, PassageHit, PassageResult, StarcallError

KIND = "starcall"
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
