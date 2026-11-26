"""Retrieval quality: does pal find the right memory, note, document passage or past chat, and stay quiet when nothing fits?

Two made-up sets, each loaded into a throwaway database (pal_eval_retrieval) and embedded with the real
nomic-embed-text model in local Ollama:
- tuning    eval/retrieval/corpus.json. pal's cut-offs (VEC_MIN, FTS_VEC_MIN) were calibrated on it, so its scores
            are in-sample. Lenient scoring: a hit is any returned item containing an expected string.
- held-out  eval/retrieval/heldout.json. Written after tuning and never used to tune anything. Strict scoring: a hit
            is the expected stored item (by corpus id), and when the query gives an `answer`, the returned passage
            itself must contain it (the right chunk of a document, not just the right file). It has longer documents
            that are split into chunks, past conversations, look-alike distractors, and two kinds of no-answer
            questions: off-topic, and near misses (the topic is stored, the asked-for fact is not).

Metrics (answerable queries have an `expect` list; the rest have no answer stored):
- Recall@k   share of answerable queries with a correct item among those returned (k = MAX_ITEMS), with a 95% Wilson
             interval
- MRR        mean reciprocal rank of the first correct item (0 when it's missing)
- Abstain    share of no-answer queries for which pal returns nothing at all, with a 95% Wilson interval
- Noise      average number of items returned for no-answer queries (lower is better)
- p50 ms     median retrieval time per query (embedding the query included)

The reranker asks the local model, so its variant runs --repeat times (default 3) and reports the range.

Usage:  uv run --no-sync python eval/retrieval_eval.py [--write] [--repeat N] [--set tuning|held-out] [variant ...]
        (--write updates eval/RETRIEVAL.md)
Needs:  the database container and Ollama running. No network beyond loopback.
"""

import json
import logging
import math
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

from pal import conversations, embed, ingest, memory, rerank, retrieval, work  # noqa: E402
from pal.config import Settings, dsn  # noqa: E402
from pal.llm import OllamaGateway  # noqa: E402
from pal.migrate import migrate  # noqa: E402

DB = "pal_eval_retrieval"
SETS = {"tuning": ROOT / "retrieval" / "corpus.json", "held-out": ROOT / "retrieval" / "heldout.json"}
REPORT = ROOT / "RETRIEVAL.md"


def _entry(item, n: int, prefix: str) -> tuple[str, dict]:
    """Corpus entries are plain strings (tuning set) or dicts with an `id` (held-out set)."""
    item = {"text": item} if isinstance(item, str) else item
    return item.get("id", f"{prefix}{n}"), item


def build(settings: Settings, llm, data: dict) -> tuple[psycopg.Connection, dict[tuple[str, str], str]]:
    """Load a corpus into a fresh database. Returns the connection and a map (kind, db id) -> corpus id."""
    with psycopg.connect(dsn(settings, "pal"), autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {DB}")
    conn_info = dsn(replace(settings, db_name=DB))
    migrate(conn_info)
    conn = psycopg.connect(conn_info, autocommit=True)
    ids: dict[tuple[str, str], str] = {}
    for n, raw in enumerate(data["memories"]):
        cid, m = _entry(raw, n, "m")
        ids[("memory", str(memory.create(conn, m["text"], origin="stated", status="confirmed")["id"]))] = cid
    for n, raw in enumerate(data["notes"]):
        cid, note = _entry(raw, n, "n")
        ids[("note", str(work.create_note(conn, note["text"], kind=note["kind"])["id"]))] = cid
    for n, raw in enumerate(data["documents"]):
        cid, doc = _entry(raw, n, "d")
        stored, _ = ingest.store(conn, doc["filename"], doc["text"].encode("utf-8"))
        ids[("document", str(stored["id"]))] = cid
    for n, raw in enumerate(data.get("conversations", [])):
        cid, conv = _entry(raw, n, "c")
        conv_id = conversations.create_conversation(conn)["id"]
        for msg in conv["messages"]:
            conversations.add_message(conn, conv_id, msg["role"], msg["content"])
        ids[("conversation", str(conv_id))] = cid
    while embed.embed_pending(conn, llm):
        pass
    return conn, ids


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


def corpus_id(item: dict, ids: dict[tuple[str, str], str]) -> str | None:
    key = {"document": item.get("document_id"), "conversation": item.get("conversation_id")}.get(item["kind"], item["id"])
    return ids.get((item["kind"], str(key)))


def is_hit(item: dict, q: dict, ids: dict[tuple[str, str], str] | None) -> bool:
    text = f"{item.get('title') or ''} {item['text']}".lower()
    if ids is None:  # tuning set: lenient substring match
        return any(e.lower() in text for e in q["expect"])
    return corpus_id(item, ids) in q["expect"] and q.get("answer", "").lower() in text


def found_rank(items: list[dict], q: dict, ids: dict[tuple[str, str], str] | None) -> int | None:
    return next((rank for rank, item in enumerate(items, start=1) if is_hit(item, q, ids)), None)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class Score:
    hits: int = 0
    rr: float = 0.0
    answerable: int = 0
    abstained: dict[str, int] = field(default_factory=dict)  # by no-answer type
    unanswerable: dict[str, int] = field(default_factory=dict)
    noise: int = 0
    ms: list[float] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    false_alarms: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.hits / self.answerable

    @property
    def mrr(self) -> float:
        return self.rr / self.answerable

    def abstain(self, kind: str | None = None) -> tuple[int, int]:
        kinds = [kind] if kind else list(self.unanswerable)
        return sum(self.abstained.get(k, 0) for k in kinds), sum(self.unanswerable.get(k, 0) for k in kinds)

    @property
    def mean_noise(self) -> float:
        return self.noise / max(1, sum(self.unanswerable.values()))


def score(conn, llm, queries: list[dict], ids: dict[tuple[str, str], str] | None) -> Score:
    s = Score()
    for q in queries:
        start = time.perf_counter()
        items = retrieval.retrieve(conn, llm, q["q"])
        s.ms.append((time.perf_counter() - start) * 1000)
        if q["expect"]:
            s.answerable += 1
            rank = found_rank(items, q, ids)
            if rank:
                s.hits += 1
                s.rr += 1 / rank
            else:
                s.misses.append(q["q"])
        else:
            kind = q.get("type", "no answer")
            s.unanswerable[kind] = s.unanswerable.get(kind, 0) + 1
            s.abstained[kind] = s.abstained.get(kind, 0) + (not items)
            s.noise += len(items)
            if items:
                s.false_alarms.append(f"{q['q']} → {' '.join(items[0]['text'].split())[:80]}")
    return s


def pct(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k / n:.0%} ({lo:.0%}–{hi:.0%})" if n else "–"


def spread(values: list[float], fmt: str) -> str:
    """One value, or the median with the min–max range when a variant ran more than once."""
    if len(values) == 1 or max(values) == min(values):
        return format(values[0], fmt)
    return f"{format(statistics.median(values), fmt)} [{format(min(values), fmt)}–{format(max(values), fmt)}]"


def table(runs: dict[str, list[Score]]) -> list[str]:
    first = next(iter(runs.values()))[0]
    kinds = list(first.unanswerable)
    head = "| Variant | Recall@6 | MRR | " + " | ".join(f"Abstains: {k}" for k in kinds) + " | Noise | p50 ms |"
    lines = [head, "|" + "---|" * (head.count("|") - 1)]
    for name, scores in runs.items():
        s = sorted(scores, key=lambda x: (x.recall, x.mrr))[len(scores) // 2]  # median run by recall, then MRR
        cells = [pct(s.hits, s.answerable), spread([x.mrr for x in scores], ".2f")]
        cells += [pct(*s.abstain(k)) for k in kinds]
        cells += [spread([x.mean_noise for x in scores], ".1f"), f"{statistics.median(s.ms):.0f}"]
        label = f"{name} (×{len(scores)})" if len(scores) > 1 else name
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return lines


# Written by hand from the 2026-11-26 run; revisit when the numbers move.
FINDINGS = [
    "## What this shows",
    "",
    "- **The tuning set overstated things, mostly on abstention.** Plain hybrid search abstained on 70% of the tuning "
    "set's no-answer questions but 47% of the held-out ones. The cut-offs fit the tuning data better than new data, "
    "which is what in-sample calibration predicts.",
    "- **The reranker does most of the work.** On the held-out set it lifts MRR from 0.85 to 0.96, and abstention on "
    "near misses from 13% to 80%, for about 0.35 s per message. Without it, a question like \"What's the wifi "
    "password?\" brings back the memory naming the network, and the model has to work out that the password isn't there.",
    "- **Reranking can only remove items, not find missing ones.** Both held-out misses are gaps in the embedding "
    "model: \"Which herb should I leave out?\" doesn't reach \"I can't stand cilantro\", and \"my nephew and niece in "
    "Calgary\" needs two steps (Dev lives in Calgary; Dev's kids are Ravi and Leela).",
    "- **Keyword search adds noise on this set, not recall.** Vector-only matched hybrid's recall with better "
    "abstention (60% vs 47%). The keyword list is kept for exact names, codes and numbers (licence plates, ticket "
    "numbers) that embed poorly, but this set doesn't show it earning its place. That is a question for the next set: "
    "changing pal based on this one would turn it into a second tuning set.",
    "- **Remaining near-miss failures are defensible.** The three no-answer questions the default still answers return "
    "the closest stored fact (\"What's Dr. Haddad's phone number?\" returns the memory naming Dr. Haddad), which a "
    "careful model can use to say what it doesn't know.",
    "",
    "## Limits",
    "",
    "- Both sets are made up, and were written alongside the retrieval code by people who know how it works, which "
    "biases them toward questions it handles. Questions from real use would be a better test.",
    "- The held-out set is small (71 + 30 questions), so the intervals above are wide: the reranker's 80% near-miss "
    "abstention is anywhere from 55% to 93%.",
    "- Not covered: adversarial text inside documents, contradicting memories (handled separately by memory "
    "replacement), large corpora where many items compete, and pinned memories.",
    "",
]


def report(results: dict[str, dict[str, list[Score]]]) -> str:
    lines = [
        "# Retrieval evaluation",
        "",
        f"Run {date.today().isoformat()} by `eval/retrieval_eval.py` with the real embedding model. "
        f"pal returns at most {retrieval.MAX_ITEMS} items per question. Percentages show a 95% confidence interval "
        "in brackets; with a few dozen questions per set, those intervals are wide, so differences of a few points "
        "between variants are not meaningful.",
        "",
    ]
    if "held-out" in results:
        first = next(iter(results["held-out"].values()))[0]
        lines += [
            "## Held-out set (the honest numbers)",
            "",
            f"[Made-up data](retrieval/heldout.json) written after the cut-offs were tuned and never used to tune "
            f"anything: {first.answerable} questions with a stored answer and {sum(first.unanswerable.values())} without. "
            "Strict scoring: a hit must be the expected item, and for documents the returned chunk must contain the answer. "
            "**Near miss** questions ask for a fact that isn't stored about a topic that is (\"What's Dr. Haddad's phone "
            "number?\" when only the name is stored); returning the related item there is defensible, since the model can "
            "then say what it does and doesn't know, so that column is a harder bar than off-topic.",
            "",
            *table(results["held-out"]),
            "",
        ]
    if "tuning" in results:
        first = next(iter(results["tuning"].values()))[0]
        lines += [
            "## Tuning set (in-sample)",
            "",
            f"[Made-up data](retrieval/corpus.json) that pal's cut-offs were calibrated on: {first.answerable} questions "
            f"with a stored answer and {sum(first.unanswerable.values())} without. Lenient scoring: a hit is any returned "
            "item containing an expected string. These numbers are optimistic by construction; they are kept to show "
            "what tuning bought, not how pal does on new data.",
            "",
            *table(results["tuning"]),
            "",
        ]
    lines += [
        "**Recall@6**: a correct item is among those returned. **MRR**: how high it ranks (1.0 = always first). "
        "**Abstains**: for questions with no stored answer, pal sends the model nothing. **Noise**: items returned for "
        "those questions. **p50 ms**: median time per question on the development PC. **(×3)**: the variant ran three "
        "times because the reranker asks the local model; brackets after MRR and noise give the range across runs "
        "(none means the runs agreed).",
        "",
        *FINDINGS,
    ]
    for set_name, runs in results.items():
        shown = DEFAULT if DEFAULT in runs else next(iter(runs))
        s = runs[shown][0]
        lines += [f"## {set_name.capitalize()} set: what {shown} got wrong (first run)", ""]
        lines += ["Missed (no correct item returned):", ""] + ([f"- {m}" for m in s.misses] or ["- none"]) + [""]
        lines += ["Returned something for a no-answer question (first item shown):", ""]
        lines += [f"- {m}" for m in s.false_alarms] or ["- none"]
        lines.append("")
    return "\n".join(lines)


def run_set(settings: Settings, llm, name: str, only: list[str], repeat: int) -> dict[str, list[Score]]:
    data = json.loads(SETS[name].read_text(encoding="utf-8"))
    strict = name == "held-out"
    conn, ids = build(settings, llm, data)
    runs: dict[str, list[Score]] = {}
    with conn:
        for variant, (values, wrap) in VARIANTS.items():
            if only and variant not in only:
                continue
            times = repeat if values.get("rerank_ENABLED") else 1
            with patched(**values):
                runs[variant] = [score(conn, wrap(llm), data["queries"], ids if strict else None) for _ in range(times)]
            s = runs[variant][0]
            print(f"[{name}] {variant:32} recall {s.recall:.0%}  mrr {s.mrr:.2f}  "
                  f"abstain {pct(*s.abstain())}  noise {s.mean_noise:.1f}  p50 {statistics.median(s.ms):.0f} ms")
    return runs


def main() -> None:
    logging.disable(logging.WARNING)  # the keyword-only variant makes every query log "embedding failed"
    args = sys.argv[1:]
    repeat = int(args[args.index("--repeat") + 1]) if "--repeat" in args else 3
    chosen = [args[args.index("--set") + 1]] if "--set" in args else list(SETS)
    flags_with_values = {i + 1 for i, a in enumerate(args) if a in ("--repeat", "--set")}
    only = [a for i, a in enumerate(args) if not a.startswith("--") and i not in flags_with_values]
    settings = Settings.from_env()
    llm = OllamaGateway(settings.ollama_url, settings.chat_model, settings.num_ctx)
    results = {name: run_set(settings, llm, name, only, repeat) for name in ["held-out", "tuning"] if name in chosen}
    text = report(results)
    if "--write" in args:
        REPORT.write_text(text, encoding="utf-8")
        print(f"wrote {REPORT}")
    else:
        print(text)


if __name__ == "__main__":
    main()
