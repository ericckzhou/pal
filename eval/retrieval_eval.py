"""Retrieval quality: does pal find the right memory, note or document passage, and stay quiet when nothing fits?

Builds a throwaway database (pal_eval_retrieval) from eval/retrieval/corpus.json, made-up data only, embeds it with
the real nomic-embed-text model in local Ollama, then runs every query through pal's retrieval under several
variants and scores them.

Metrics (answerable queries are the ones with an `expect` list; the rest have no answer in the corpus):
- Recall@k   share of answerable queries whose expected passage is among the items pal returns (k = MAX_ITEMS)
- MRR        mean reciprocal rank of the first correct item (0 when it's missing)
- Abstain    share of unanswerable queries for which pal returns nothing at all
- Noise      average number of items returned for unanswerable queries (lower is better)
- p50 ms     median retrieval time per query (embedding the query included)

Usage:  uv run --no-sync python eval/retrieval_eval.py [--write]    (--write updates eval/RETRIEVAL.md)
Needs:  the database container and Ollama running. No network beyond loopback.
"""

import json
import logging
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

from pal import embed, ingest, memory, rerank, retrieval, work  # noqa: E402
from pal.config import Settings, dsn  # noqa: E402
from pal.llm import OllamaGateway  # noqa: E402
from pal.migrate import migrate  # noqa: E402

DB = "pal_eval_retrieval"
CORPUS = ROOT / "retrieval" / "corpus.json"
REPORT = ROOT / "RETRIEVAL.md"


def build(settings: Settings, llm) -> psycopg.Connection:
    with psycopg.connect(dsn(settings, "pal"), autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {DB}")
    conn_info = dsn(replace(settings, db_name=DB))
    migrate(conn_info)
    conn = psycopg.connect(conn_info, autocommit=True)
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    for text in data["memories"]:
        memory.create(conn, text, origin="stated", status="confirmed")
    for note in data["notes"]:
        work.create_note(conn, note["text"], kind=note["kind"])
    for doc in data["documents"]:
        ingest.store(conn, doc["filename"], doc["text"].encode("utf-8"))
    while embed.embed_pending(conn, llm):
        pass
    return conn


@contextmanager
def patched(**values) -> Iterator[None]:
    """Temporarily change retrieval's module settings (cut-offs, list sizes, the reranker switch)."""
    old = {}
    for name, value in values.items():
        module, attr = (rerank, name[7:]) if name.startswith("rerank_") else (retrieval, name)
        old[(module, attr)] = getattr(module, attr)
        setattr(module, attr, value)
    try:
        yield
    finally:
        for (module, attr), value in old.items():
            setattr(module, attr, value)


def keyword_only(llm):
    class NoVectors:
        def embed(self, texts):
            raise RuntimeError("vectors switched off for this variant")

        def __getattr__(self, name):
            return getattr(llm, name)

    return NoVectors()


DEFAULT = "Hybrid + rerank (pal's default)"
VARIANTS: dict[str, tuple[dict, Callable]] = {
    "Keyword only": ({}, keyword_only),
    "Vector only": ({"FTS_VEC_MIN": 2.0}, lambda llm: llm),  # every keyword hit fails the similarity floor
    "Hybrid, no cut-off": ({"VEC_MIN": -1.0, "FTS_VEC_MIN": -1.0}, lambda llm: llm),
    "Hybrid": ({}, lambda llm: llm),
    DEFAULT: ({"rerank_ENABLED": True}, lambda llm: llm),
}


def found_rank(items: list[dict], expect: list[str]) -> int | None:
    for rank, item in enumerate(items, start=1):
        text = f"{item.get('title') or ''} {item['text']}".lower()
        if any(e.lower() in text for e in expect):
            return rank
    return None


def score(conn, llm, queries: list[dict]) -> dict:
    hits, rr, abstain, noise, ms = 0, 0.0, 0, 0, []
    answerable = [q for q in queries if q["expect"]]
    unanswerable = [q for q in queries if not q["expect"]]
    misses = []
    for q in queries:
        start = time.perf_counter()
        items = retrieval.retrieve(conn, llm, q["q"])
        ms.append((time.perf_counter() - start) * 1000)
        if q["expect"]:
            rank = found_rank(items, q["expect"])
            if rank:
                hits += 1
                rr += 1 / rank
            else:
                misses.append(q["q"])
        else:
            abstain += not items
            noise += len(items)
    return {
        "recall": hits / len(answerable),
        "mrr": rr / len(answerable),
        "abstain": abstain / len(unanswerable),
        "noise": noise / len(unanswerable),
        "p50_ms": statistics.median(ms),
        "misses": misses,
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
    }


def report(results: dict[str, dict]) -> str:
    first = next(iter(results.values()))
    lines = [
        "# Retrieval evaluation",
        "",
        f"Run {date.today().isoformat()} by `eval/retrieval_eval.py` on [made-up data](retrieval/corpus.json): "
        f"{first['n_answerable']} questions with a known answer and {first['n_unanswerable']} with none "
        "(general-knowledge questions and personal facts that were never stored). "
        f"pal returns at most {retrieval.MAX_ITEMS} items per question.",
        "",
        "| Variant | Recall@6 | MRR | Abstains when nothing fits | Noise (items on no-answer questions) | p50 ms |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        lines.append(f"| {name} | {r['recall']:.0%} | {r['mrr']:.2f} | {r['abstain']:.0%} | {r['noise']:.1f} | {r['p50_ms']:.0f} |")
    lines += ["", "**Recall@6**: the right passage is among the items returned. **MRR**: how high it ranks (1.0 = always first).",
              "**Abstains**: for questions with no stored answer, pal sends the model nothing, so it can't cite something unrelated.",
              "**p50 ms**: median time per question on the development PC, embedding the question included.", ""]
    lines.append("## What this shows, and its limits")
    lines.append("")
    lines += [
        "- Keyword search alone misses paraphrases (\"Who is my boss?\" vs \"My manager is...\"). Vectors catch them.",
        "- Without a similarity cut-off, every question returns six items, so the model always gets something to cite, "
        "related or not.",
        "- The keyword floor (`FTS_VEC_MIN`) was raised from 0.45 to 0.55 after this evaluation: abstention went from 50% "
        "to 70% with no loss of recall. 0.60 scored slightly better still, but was not chosen, to leave margin for exact "
        "names that embed poorly; tuning hard against 60 questions would overfit.",
        "- Reranking with the local model removes the remaining noise for about 0.3 s per message.",
        "- The set is small, made up and fairly easy: most variants are near the ceiling on recall, so the useful "
        "differences are in ranking (MRR) and abstention. A larger set built from real use would separate them better.",
        "",
        f"## Questions missed by {DEFAULT}",
        "",
    ]
    misses = results.get(DEFAULT, first)["misses"]
    lines += [f"- {m}" for m in misses] or ["- none"]
    return "\n".join(lines) + "\n"


def main() -> None:
    logging.disable(logging.WARNING)  # the keyword-only variant makes every query log "embedding failed"
    settings = Settings.from_env()
    llm = OllamaGateway(settings.ollama_url, settings.chat_model, settings.num_ctx)
    queries = json.loads(CORPUS.read_text(encoding="utf-8"))["queries"]
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    with build(settings, llm) as conn:
        results = {}
        for name, (values, wrap) in VARIANTS.items():
            if only and name not in only:
                continue
            with patched(**values):
                results[name] = score(conn, wrap(llm), queries)
            r = results[name]
            print(f"{name:22} recall {r['recall']:.0%}  mrr {r['mrr']:.2f}  abstain {r['abstain']:.0%}  "
                  f"noise {r['noise']:.1f}  p50 {r['p50_ms']:.0f} ms")
    text = report(results)
    if "--write" in sys.argv:
        REPORT.write_text(text, encoding="utf-8")
        print(f"wrote {REPORT}")
    else:
        print(text)


if __name__ == "__main__":
    main()
