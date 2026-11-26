"""The optional reranker: keeps only what the model picks, in its order, and never fails retrieval."""

from pal import rerank, retrieval

CANDIDATES = [
    {"title": None, "text": "My dentist is Dr. Okafor."},
    {"title": "lease", "text": "Monthly rent is 2,150 dollars."},
    {"title": None, "text": "I'm allergic to penicillin."},
]


def test_keeps_picked_candidates_in_model_order(llm):
    llm.json_reply = '{"relevant": [3, 1, 3, 9, 0]}'  # duplicates and out-of-range numbers are ignored
    out = rerank.rerank(llm, "dentist or allergy?", CANDIDATES)
    assert [c["text"] for c in out] == ["I'm allergic to penicillin.", "My dentist is Dr. Okafor."]
    prompt = llm.json_calls[-1][1]["content"]
    assert "[2] lease: Monthly rent" in prompt and prompt.startswith("Question: dentist or allergy?")


def test_empty_pick_drops_everything(llm):
    llm.json_reply = '{"relevant": []}'
    assert rerank.rerank(llm, "capital of Australia?", CANDIDATES) == []


def test_bad_output_keeps_the_fused_order(llm):
    for raw in ("not json", '{"memories": []}', '{"relevant": "1"}'):
        llm.json_reply = raw
        assert rerank.rerank(llm, "q", CANDIDATES) == CANDIDATES


def test_retrieval_uses_the_reranker_only_when_asked(db, llm):
    from pal import memory

    memory.create(db, "My dentist is Dr. Okafor at Riverside Dental.", origin="stated", status="confirmed")
    memory.create(db, "The dentist parking lot is behind the bakery.", origin="stated", status="confirmed")
    calls = len(llm.json_calls)
    assert len(retrieval.retrieve(db, llm, "dentist", use_rerank=False)) == 2
    assert len(llm.json_calls) == calls
    llm.json_reply = '{"relevant": [2]}'
    items = retrieval.retrieve(db, llm, "dentist", use_rerank=True)
    assert len(items) == 1 and len(llm.json_calls) == calls + 1
