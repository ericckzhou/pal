"""StarcallOS results inside pal's retrieval: one ranked list, the keyword check, the cap of 2, and the trace."""

import concurrent.futures
import json
import random

import pytest

from pal import embed, memory, retrieval, starcall
from pal.connectors.starcall import ConceptResult, PassageResult, StarcallError

MARKER = "ZQX-PASSAGE-TEXT"  # must never reach the trace


def passages(*specs, mode="hybrid") -> PassageResult:
    """specs: (chunk_id, keyword_rank or None). Hits come back in the given order."""
    return PassageResult.model_validate({
        "mode_used": mode,
        "results": [
            {"chunk_id": cid, "source_id": 7, "source_title": "Linear Algebra Notes", "page_start": 10 + i,
             "page_end": 10 + i, "heading": None, "text": f"{MARKER} passage {cid} about eigenvalues",
             "score": 0.03 - i * 0.001, "keyword_rank": kw, "vector_rank": i + 1}
            for i, (cid, kw) in enumerate(specs)
        ],
    })


def lookup(*specs, query="eigenvalues") -> starcall.Lookup:
    return starcall.build_lookup(query, passages(*specs))


def ext_items(items):
    return [i for i in items if i["kind"] == "starcall"]


@pytest.fixture
def garden(db, llm):
    memory.create(db, "Owner plants tomatoes in mid May after the last frost", origin="stated", status="confirmed")
    memory.create(db, "Owner turns the compost pile every two weeks", origin="stated", status="confirmed")
    embed.embed_pending(db, llm)


# ── no change without StarcallOS ──────────────────────────────────────────────────────────────────────────────────


def test_output_is_unchanged_without_external_or_with_no_hits(db, llm, garden):
    plain_trace: dict = {}
    plain = retrieval.retrieve(db, llm, "when do I plant tomatoes", use_rerank=False, trace=plain_trace)
    empty_trace: dict = {}
    empty = retrieval.retrieve(db, llm, "when do I plant tomatoes", use_rerank=False, trace=empty_trace,
                               external=starcall.Lookup("ok"))
    assert plain and plain == empty
    assert "starcall" not in plain_trace
    assert empty_trace["starcall"] == {"status": "ok", "ms": None, "returned": 0, "mode_used": None}
    assert {k: v for k, v in empty_trace.items() if k != "starcall"} == plain_trace


def test_failed_lookup_leaves_pal_results_alone(db, llm, garden):
    plain = retrieval.retrieve(db, llm, "compost pile", use_rerank=False)
    trace: dict = {}
    err = StarcallError("schema_mismatch", "StarcallOS refused its database: 2 migration(s) behind")
    items = retrieval.retrieve(db, llm, "compost pile", use_rerank=False, trace=trace,
                               external=starcall.failed(err, ms=12))
    assert items == plain
    assert trace["starcall"] == {"status": "failed", "reason": "schema_mismatch", "ms": 12, "returned": 0,
                                 "mode_used": None}


# ── the cap and the keyword check ─────────────────────────────────────────────────────────────────────────────────


def test_more_than_two_strong_hits_are_capped_at_two(db, llm):
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "eigenvalues", use_rerank=False, trace=trace,
                               external=lookup((1, 1), (2, 2), (3, 3), (4, 4), (5, 5)))
    got = ext_items(items)
    assert [i["handle"] for i in got] == ["s1", "s2"]
    assert [i["chunk_id"] for i in got] == [1, 2]
    decisions = {c["chunk_id"]: c["decision"] for c in trace["candidates"] if c["kind"] == "starcall"}
    assert decisions == {1: "used", 2: "used", 3: "capped", 4: "capped", 5: "capped"}


def test_hit_without_a_keyword_match_is_left_out_when_nothing_judged_it(db, llm):
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "eigenvalues", use_rerank=False, trace=trace,
                               external=lookup((1, None), (2, 1), (3, None)))
    assert [i["chunk_id"] for i in ext_items(items)] == [2]
    decisions = {c["chunk_id"]: c["decision"] for c in trace["candidates"] if c["kind"] == "starcall"}
    assert decisions == {1: "no_keyword_match", 2: "used", 3: "no_keyword_match"}


def test_reranker_fallback_also_needs_the_keyword_match(db, llm):
    llm.json_reply = "not json"
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "eigenvalues", use_rerank=True, trace=trace,
                               external=lookup((1, None), (2, 1)))
    assert trace["rerank"]["status"] == "fell_back"
    assert [i["chunk_id"] for i in ext_items(items)] == [2]


def test_reranker_that_ran_may_keep_a_semantic_only_hit(db, llm):
    llm.json_reply = json.dumps({"relevant": [1]})
    items = retrieval.retrieve(db, llm, "eigenvalues", use_rerank=True, external=lookup((1, None), (2, 1)))
    assert [i["chunk_id"] for i in ext_items(items)] == [1]


def test_weak_hits_dropped_by_the_reranker_never_appear(db, llm):
    llm.json_reply = json.dumps({"relevant": []})
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "eigenvalues", use_rerank=True, trace=trace,
                               external=lookup((1, 1), (2, 2)))
    assert ext_items(items) == []
    assert {c["decision"] for c in trace["candidates"] if c["kind"] == "starcall"} == {"reranker_dropped"}
    # The reranker read them as ordinary candidates, text included (that's the prompt, not storage).
    assert "Linear Algebra Notes" in llm.json_calls[-1][1]["content"]


def test_cap_frees_the_slot_for_the_next_pal_item(db, llm, garden):
    llm.json_reply = json.dumps({"relevant": [1, 2, 3, 4, 5]})
    query = "tomatoes compost eigenvalues"
    items = retrieval.retrieve(db, llm, query, use_rerank=True,
                               external=lookup((1, 1), (2, 2), (3, 3), query=query))
    assert len(ext_items(items)) == 2
    assert {i["kind"] for i in items} == {"starcall", "memory"}


def test_pal_items_found_by_both_lists_stay_above_a_starcall_hit(db, llm, garden):
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "plant tomatoes", use_rerank=False, trace=trace,
                               external=lookup((1, 1), query="plant tomatoes"))
    both = [c for c in trace["candidates"] if c["kind"] == "memory" and c["keyword_rank"] and c["vector_rank"]]
    ext = next(c for c in trace["candidates"] if c["kind"] == "starcall")
    assert both and all(c["score"] > ext["score"] for c in both)
    assert items[0]["kind"] == "memory" and items[1]["kind"] == "starcall"


@pytest.mark.parametrize("seed", range(40))
def test_random_mixes_never_exceed_the_cap(db, llm, seed):
    rng = random.Random(seed)
    for i in range(rng.randint(0, 8)):  # pal items to compete with
        memory.create(db, f"Owner fact {i} about eigenvalues and matrices", origin="stated", status="confirmed")
    embed.embed_pending(db, llm)
    specs = [(cid, rng.choice([None, cid])) for cid in rng.sample(range(1, 50), rng.randint(0, 10))]
    mode = rng.choice(["off", "ran", "fell_back"])
    if mode == "ran":
        llm.json_reply = json.dumps({"relevant": rng.sample(range(1, 15), rng.randint(0, 12))})
    elif mode == "fell_back":
        llm.json_reply = "{"
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "eigenvalues matrices", use_rerank=mode != "off", trace=trace,
                               external=lookup(*specs, query="eigenvalues matrices"))
    picked = [i for i in items if not i["pinned"]]
    assert len(ext_items(items)) <= retrieval.STARCALL_CAP
    assert len(picked) <= retrieval.MAX_ITEMS
    used = [c for c in trace["candidates"] if c["kind"] == "starcall" and c["decision"] == "used"]
    assert len(used) == len(ext_items(items))
    if mode != "ran":
        assert all(i["chunk_id"] in {cid for cid, kw in specs if kw} for i in ext_items(items))


# ── output, trace, waiting ────────────────────────────────────────────────────────────────────────────────────────


def test_items_carry_ids_and_pages_and_the_trace_carries_no_text(db, llm):
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "eigenvalues", use_rerank=False, trace=trace, external=lookup((9, 1)))
    s1 = ext_items(items)[0]
    assert (s1["handle"], s1["id"], s1["source_id"], s1["chunk_id"], s1["page"]) == ("s1", "7:9", 7, 9, 10)
    assert s1["starcall_kind"] == "passage" and s1["title"] == "Linear Algebra Notes"
    assert MARKER in s1["text"]  # for the prompt
    assert MARKER not in json.dumps(trace)
    entry = next(c for c in trace["candidates"] if c["kind"] == "starcall")
    assert entry["label"] == "Linear Algebra Notes p.10"
    assert (entry["starcall_rank"], entry["keyword_rank"], entry["handle"]) == (1, 1, "s1")
    assert trace["v"] == 2


def test_trace_for_ui_keeps_the_stored_starcall_label(db, llm):
    trace: dict = {}
    retrieval.retrieve(db, llm, "eigenvalues", use_rerank=False, trace=trace, external=lookup((9, 1)))
    ui = retrieval.trace_for_ui(db, trace)
    assert next(c for c in ui["candidates"] if c["kind"] == "starcall")["label"] == "Linear Algebra Notes p.10"


def test_lookup_future_is_waited_for(db, llm):
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        fut = pool.submit(lambda: lookup((1, 1)))
        items = retrieval.retrieve(db, llm, "eigenvalues", use_rerank=False, external=fut)
    assert [i["chunk_id"] for i in ext_items(items)] == [1]


def test_lookup_that_never_returns_is_abandoned(db, llm, monkeypatch):
    monkeypatch.setattr(retrieval, "EXTERNAL_WAIT", 0.05)
    never: concurrent.futures.Future = concurrent.futures.Future()
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "eigenvalues", use_rerank=False, trace=trace, external=never)
    assert ext_items(items) == []
    assert trace["starcall"]["status"] == "failed" and trace["starcall"]["reason"] == "timeout"
    assert never.cancelled()


def test_lookup_future_that_raises_becomes_a_failure(db, llm):
    fut: concurrent.futures.Future = concurrent.futures.Future()
    fut.set_exception(RuntimeError("boom"))
    trace: dict = {}
    retrieval.retrieve(db, llm, "eigenvalues", use_rerank=False, trace=trace, external=fut)
    assert trace["starcall"]["reason"] == "failed"


def test_old_s_handles_are_stripped_from_past_replies():
    assert retrieval._HANDLE.sub("", "See the notes [s1] and [d2].") == "See the notes and."


# ── concepts ──────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "query", "ok"), [
    ("Eigenvalue", "what is an eigenvalue?", True),
    ("Eigenvalue", "list the eigenvalues of A", True),  # plural fold
    ("Gradient Descent", "explain gradient descent please", True),  # phrase
    ("Gradient Descent", "why does descent along the gradient work", True),  # every word, any order
    ("Gradient Descent", "what is a gradient", False),  # only part of the name
    ("Theory of Computation", "computation theory basics", True),  # stopword "of" not needed
    ("Set", "set up my notes", False),  # one short word
    ("Map", "show me the map", False),
    ("Rank", "matrix rank", True),  # four characters is enough
    ("Bayes’ Theorem", "state bayes theorem", True),  # punctuation folded
    ("ＬＵ Decomposition", "lu decomposition steps", True),  # NFKC
    ("Matrix", "matrices", False),  # irregular plural isn't folded
    ("", "anything", False),
])
def test_concept_name_must_be_in_the_question(name, query, ok):
    assert starcall.concept_matches(name, query) is ok


def concepts(*rows) -> ConceptResult:
    return ConceptResult.model_validate({"results": [
        {"concept_id": cid, "name": name, "source_id": 7, "source_title": "Linear Algebra Notes",
         "definition": f"{MARKER} definition of {name}", "matched_in": matched}
        for cid, name, matched in rows
    ]})


def test_at_most_one_concept_and_only_on_a_name_match():
    q = "how do eigenvalue and eigenvector relate"
    found = concepts((1, "Matrix", ["definition"]), (2, "Eigenvalue", ["name"]), (3, "Eigenvector", ["name"]))
    cands = starcall.to_candidates(passages((5, 1)), found, q)
    assert [c["id"] for c in cands] == ["concept:2", "7:5"]
    assert [c["starcall_rank"] for c in cands] == [1, 2]
    assert cands[0]["label"] == "Concept: Eigenvalue" and cands[0]["keyword_match"]
    # matched_in "definition" alone never qualifies, even if the words are there
    only_def = concepts((1, "Eigenvalue", ["definition"]))
    assert [c["id"] for c in starcall.to_candidates(passages(), only_def, q)] == []


def test_a_concept_counts_toward_the_cap(db, llm):
    q = "what is an eigenvalue"
    lk = starcall.build_lookup(q, passages((1, 1), (2, 2)), concepts((4, "Eigenvalue", ["name"])))
    items = retrieval.retrieve(db, llm, q, use_rerank=False, external=lk)
    assert [i["starcall_kind"] for i in ext_items(items)] == ["concept", "passage"]
    assert ext_items(items)[0]["concept_id"] == 4


def test_failed_passage_search_fails_the_lookup_but_failed_concepts_dont():
    err = StarcallError("timeout", "StarcallOS took longer than 4 s.")
    bad = starcall.build_lookup("eigenvalue", err, concepts((4, "Eigenvalue", ["name"])))
    assert (bad.status, bad.reason, bad.candidates) == ("failed", "timeout", [])
    ok = starcall.build_lookup("eigenvalue", passages((1, 1)), err, started=0.0)
    assert ok.status == "ok" and [c["id"] for c in ok.candidates] == ["7:1"] and ok.mode_used == "hybrid"


def test_duplicate_passages_are_listed_once():
    cands = starcall.to_candidates(passages((1, 1), (1, 1), (2, None)), None, "x")
    assert [c["id"] for c in cands] == ["7:1", "7:2"] and [c["starcall_rank"] for c in cands] == [1, 2]
