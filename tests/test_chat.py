import json

from pal.context import SYSTEM_PROMPT, build_messages


def stream_events(client, conv_id, text):
    with client.stream("POST", f"/api/conversations/{conv_id}/messages", json={"content": text}) as resp:
        assert resp.status_code == 200
        return [json.loads(line) for line in resp.iter_lines() if line]


def test_chat_streams_and_stores_both_messages(authed, llm):
    conv = authed.post("/api/conversations").json()
    events = stream_events(authed, conv["id"], "Tell me about tea brewing")
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Hello there"
    assert events[-1]["type"] == "done"

    full = authed.get(f"/api/conversations/{conv['id']}").json()
    assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
    assert full["messages"][1]["content"] == "Hello there"
    assert full["title"] == "Tell me about tea brewing"
    assert llm.calls[0][0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_model_failure_reports_error_and_keeps_partial_reply_out(authed, llm):
    llm.fail = True
    conv = authed.post("/api/conversations").json()
    events = stream_events(authed, conv["id"], "hi")
    assert events[-1]["type"] == "error" and events[-1]["unsent"] is True
    # The unanswered message is removed, so history never has two user turns in a row; the UI restores the text.
    after = authed.get(f"/api/conversations/{conv['id']}").json()
    assert after["messages"] == [] and after["title"] == "New chat"


def test_failed_reply_keeps_earlier_turns_and_title(authed, llm):
    conv = authed.post("/api/conversations").json()
    stream_events(authed, conv["id"], "first question")
    llm.fail = True
    events = stream_events(authed, conv["id"], "second question")
    assert events[-1]["type"] == "error"
    after = authed.get(f"/api/conversations/{conv['id']}").json()
    assert [m["content"] for m in after["messages"]] == ["first question", "Hello there"]
    assert after["title"] == "first question"


def test_empty_reply_also_drops_the_unanswered_message(authed, llm):
    llm.reply = ["   "]
    conv = authed.post("/api/conversations").json()
    events = stream_events(authed, conv["id"], "hi")
    assert events[-1] == {"type": "error", "message": "The model returned an empty reply.", "unsent": True}
    assert authed.get(f"/api/conversations/{conv['id']}").json()["messages"] == []


def test_unknown_conversation_is_404(authed):
    resp = authed.post("/api/conversations/00000000-0000-0000-0000-000000000000/messages", json={"content": "x"})
    assert resp.status_code == 404


def test_empty_message_rejected(authed):
    conv = authed.post("/api/conversations").json()
    assert authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "  "}).status_code == 422


def test_search_finds_past_conversation(authed):
    conv = authed.post("/api/conversations").json()
    stream_events(authed, conv["id"], "My favourite mountain is Kilimanjaro")
    hits = authed.get("/api/search", params={"q": "kilimanjaro"}).json()
    assert hits and hits[0]["conversation_id"] == conv["id"]
    assert authed.get("/api/search", params={"q": "zebra"}).json() == []


# S10: deletion purges the text
def test_s10_delete_conversation_purges_messages(authed, db):
    conv = authed.post("/api/conversations").json()
    stream_events(authed, conv["id"], "secret plans about Zanzibar")
    assert authed.delete(f"/api/conversations/{conv['id']}").status_code == 204
    left = db.execute("SELECT count(*) FROM messages WHERE content ILIKE '%zanzibar%'").fetchone()[0]
    assert left == 0
    assert authed.get("/api/search", params={"q": "zanzibar"}).json() == []
    assert authed.get(f"/api/conversations/{conv['id']}").status_code == 404


def test_conversation_list_newest_first(authed):
    a = authed.post("/api/conversations").json()
    b = authed.post("/api/conversations").json()
    ids = [c["id"] for c in authed.get("/api/conversations").json()]
    assert ids[:2] == [b["id"], a["id"]]


def test_context_keeps_recent_messages_within_budget():
    history = [{"role": "user", "content": "x" * 3000} for _ in range(20)]
    history.append({"role": "user", "content": "latest question"})
    msgs, _ = build_messages(history, num_ctx=4096, reserve=1024)
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "latest question"
    assert len(msgs) < len(history)


def test_context_truncates_a_single_oversized_message():
    msgs, _ = build_messages([{"role": "user", "content": "y" * 100_000}], num_ctx=4096, reserve=1024)
    assert len(msgs) == 2 and len(msgs[1]["content"]) < 100_000
