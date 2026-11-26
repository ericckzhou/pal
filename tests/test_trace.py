"""Retrieval decision traces: per-reply scores and reranker outcomes, stored without item text."""

import json

from pal import embed, memory, retrieval
from pal.context import build_messages

from .test_chat import stream_events


def _seed(db, llm) -> None:
    memory.create(db, "Pinned: owner prefers metric units", origin="stated", status="confirmed", pinned=True)
    memory.create(db, "My dentist is Dr. Okafor at Riverside Dental.", origin="stated", status="confirmed")
    memory.create(db, "The dentist parking lot is behind the bakery.", origin="stated", status="confirmed")
    embed.embed_pending(db, llm)


def test_trace_records_scores_and_used_items(db, llm):
    _seed(db, llm)
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "dentist", use_rerank=False, trace=trace)
    assert trace["v"] == retrieval.TRACE_VERSION and trace["query"] == "dentist"
    assert trace["rerank"] == {"status": "off"} and trace["fused"] == 2
    cands = trace["candidates"]
    assert cands[0]["pinned"] and cands[0]["decision"] == "used" and cands[0]["handle"] == "m1"
    scored = cands[1:]
    assert [c["decision"] for c in scored] == ["used", "used"]
    assert all(c["score"] > 0 and c["keyword_rank"] for c in scored)
    assert scored[0]["score"] >= scored[1]["score"]
    assert {c["handle"] for c in cands} == {i["handle"] for i in items}


def test_trace_never_stores_item_text(db, llm):
    _seed(db, llm)
    trace: dict = {}
    retrieval.retrieve(db, llm, "dentist", use_rerank=False, trace=trace)
    dumped = json.dumps(trace)
    assert "Okafor" not in dumped and "bakery" not in dumped and "metric" not in dumped


def test_trace_shows_what_the_reranker_dropped(db, llm):
    _seed(db, llm)
    llm.json_reply = '{"relevant": [2]}'
    trace: dict = {}
    retrieval.retrieve(db, llm, "dentist", use_rerank=True, trace=trace)
    assert trace["rerank"] == {"status": "ran", "pool": 2, "kept": 1}
    assert sorted(c["decision"] for c in trace["candidates"][1:]) == ["reranker_dropped", "used"]


def test_trace_shows_reranker_fallback(db, llm):
    _seed(db, llm)
    llm.json_reply = "not json"
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "dentist", use_rerank=True, trace=trace)
    assert trace["rerank"]["status"] == "fell_back" and len(items) == 3
    assert all(c["decision"] == "used" for c in trace["candidates"])


def test_trace_counts_keyword_only_search_when_embedding_fails(db, llm):
    _seed(db, llm)
    llm.embed_fail = True
    trace: dict = {}
    retrieval.retrieve(db, llm, "dentist", use_rerank=False, trace=trace)
    assert trace["vector_search"] is False
    assert all(c["vector_rank"] is None for c in trace["candidates"][1:])


def test_ranked_lower_beyond_the_item_limit(db, llm, monkeypatch):
    _seed(db, llm)
    monkeypatch.setattr(retrieval, "MAX_ITEMS", 1)
    trace: dict = {}
    retrieval.retrieve(db, llm, "dentist", use_rerank=False, trace=trace)
    assert [c["decision"] for c in trace["candidates"][1:]] == ["used", "ranked_lower"]


def test_mark_trimmed_flags_items_cut_for_space(db, llm):
    _seed(db, llm)
    trace: dict = {}
    items = retrieval.retrieve(db, llm, "dentist", use_rerank=False, trace=trace)
    items[-1] = dict(items[-1], text="x" * 20000)  # too big for the context budget
    _, used = build_messages([{"role": "user", "content": "dentist"}], 8192, 1024, items)
    marked = retrieval.mark_trimmed(trace, used)
    trimmed = [c["handle"] for c in marked["candidates"] if c["decision"] == "trimmed"]
    assert trimmed == [items[-1]["handle"]]
    assert trace["candidates"] != marked["candidates"]  # the original is left as it was


def test_trace_for_ui_labels_live_items_and_blanks_deleted_ones(db, llm):
    _seed(db, llm)
    trace: dict = {}
    retrieval.retrieve(db, llm, "dentist", use_rerank=False, trace=trace)
    gone = trace["candidates"][1]["id"]
    db.execute("DELETE FROM memories WHERE id = %s", (gone,))
    labels = {c["id"]: c["label"] for c in retrieval.trace_for_ui(db, trace)["candidates"]}
    assert labels[gone] is None
    assert any(label and "metric" in label for label in labels.values())


def test_reply_stores_trace_and_endpoint_returns_it(authed, db, llm):
    _seed(db, llm)
    conv = authed.post("/api/conversations").json()
    events = stream_events(authed, conv["id"], "who is my dentist?")
    done = events[-1]
    assert done["type"] == "done" and done["has_trace"] is True
    msgs = authed.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert [m["has_trace"] for m in msgs] == [False, True]
    trace = authed.get(f"/api/messages/{done['message_id']}/trace").json()
    assert trace["query"] and any(c.get("label") for c in trace["candidates"])
    assert authed.get(f"/api/messages/{msgs[0]['id']}/trace").status_code == 404


def test_trace_endpoint_needs_a_session(client):
    resp = client.get("/api/messages/00000000-0000-0000-0000-000000000000/trace")
    assert resp.status_code == 401
