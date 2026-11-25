import json

import pytest

from pal import memory

from .test_chat import stream_events


def chat(client, text, conv_id=None):
    conv_id = conv_id or client.post("/api/conversations").json()["id"]
    return conv_id, stream_events(client, conv_id, text)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Remember that my sister's birthday is March 3.", "my sister's birthday is March 3"),
        ("please remember: I take my coffee black", "I take my coffee black"),
        ("remember I'm allergic to cats!", "I'm allergic to cats"),
        ("Do you remember what I said?", None),
        ("I can't remember that song", None),
        ("remember", None),
    ],
)
def test_parse_remember(message, expected):
    assert memory.parse_remember(message) == expected


def test_remember_that_saves_a_confirmed_stated_memory(authed):
    conv_id, events = chat(authed, "Remember that I'm vegetarian")
    assert events[0] == {"type": "step", "step": {"kind": "memory", "label": "Saved to memory", "detail": events[1]["memory"]["text"]}}
    assert events[1]["type"] == "memory_saved"
    saved = events[1]["memory"]
    assert (saved["text"], saved["origin"], saved["status"]) == ("I'm vegetarian", "stated", "confirmed")
    assert saved["source_conversation_id"] == conv_id
    assert events[-1]["type"] == "done"
    assert [m["text"] for m in authed.get("/api/memories").json()] == ["I'm vegetarian"]


def test_confirmed_memories_reach_the_model_but_proposals_do_not(authed, db, llm):
    memory.create(db, "Owner is vegetarian", "preference", origin="stated", status="confirmed")
    memory.create(db, "Owner likes jazz", origin="inferred", status="proposed")
    memory.create(db, "Owner's dog is Biscuit", "person", origin="stated", status="confirmed", pinned=True)
    chat(authed, "any vegetarian dinner ideas?")
    sent = llm.calls[-1][-1]["content"]
    assert "Owner is vegetarian" in sent  # keyword match
    assert "[m1] (memory, pinned) Owner's dog is Biscuit" in sent  # pinned: always included
    assert "jazz" not in str(llm.calls[-1])  # never confirmed


def test_unrelated_unpinned_memory_stays_out_of_context(authed, db, llm):
    memory.create(db, "Owner's dentist is Dr. Kim", origin="stated", status="confirmed")
    chat(authed, "explain recursion")
    assert "dentist" not in str(llm.calls[-1])


def test_suggest_memories_creates_proposals_only(authed, llm):
    conv_id, _ = chat(authed, "I just moved to Toronto and I work as a nurse")
    # S6: even if the model claims a status or pin, the row is a plain proposal.
    llm.json_reply = json.dumps({"memories": [
        {"text": "Lives in Toronto", "kind": "fact", "status": "confirmed", "pinned": True},
        {"text": "Works as a nurse", "kind": "bogus"},
        {"text": "   ", "kind": "fact"},
        "not an object",
    ]})
    resp = authed.post(f"/api/conversations/{conv_id}/suggest-memories")
    assert resp.status_code == 200
    proposed = resp.json()["proposed"]
    assert [(p["text"], p["kind"], p["status"], p["pinned"], p["origin"]) for p in proposed] == [
        ("Lives in Toronto", "fact", "proposed", False, "inferred"),
        ("Works as a nurse", "fact", "proposed", False, "inferred"),
    ]
    assert authed.get("/api/memories").json() == []  # nothing confirmed
    assert authed.get("/api/memories/inbox-count").json() == {"count": 2}

    transcript = json.loads(llm.json_calls[-1][1]["content"])
    assert transcript[0] == {"role": "user", "content": "I just moved to Toronto and I work as a nurse"}


def test_scan_only_reads_new_messages_and_skips_duplicates(authed, llm):
    conv_id, _ = chat(authed, "I'm learning Japanese")
    llm.json_reply = '{"memories": [{"text": "Learning Japanese", "kind": "project"}]}'
    authed.post(f"/api/conversations/{conv_id}/suggest-memories")
    calls = len(llm.json_calls)

    assert authed.post(f"/api/conversations/{conv_id}/suggest-memories").json() == {"proposed": []}
    assert len(llm.json_calls) == calls  # nothing new, no model call

    chat(authed, "My teacher is Aiko", conv_id)
    llm.json_reply = '{"memories": [{"text": "learning japanese!", "kind": "project"}, {"text": "Teacher is Aiko", "kind": "person"}]}'
    proposed = authed.post(f"/api/conversations/{conv_id}/suggest-memories").json()["proposed"]
    assert [p["text"] for p in proposed] == ["Teacher is Aiko"]
    sent = json.loads(llm.json_calls[-1][1]["content"])
    assert sent[-2]["content"] == "My teacher is Aiko"
    assert len(sent) <= memory.CONTEXT_BEFORE + 2


def test_garbage_model_output_proposes_nothing(authed, llm):
    conv_id, _ = chat(authed, "hello")
    llm.json_reply = "sorry, I can't do JSON"
    assert authed.post(f"/api/conversations/{conv_id}/suggest-memories").json() == {"proposed": []}


def test_accept_edit_pin_and_delete(authed, db):
    m = memory.create(db, "Likes tea", origin="inferred", status="proposed")
    mid = str(m["id"])
    edited = authed.patch(f"/api/memories/{mid}", json={"text": "Likes green tea", "kind": "preference"}).json()
    assert (edited["text"], edited["kind"], edited["status"]) == ("Likes green tea", "preference", "proposed")

    confirmed = authed.post(f"/api/memories/{mid}/confirm").json()
    assert confirmed["status"] == "confirmed" and confirmed["confirmed_at"]
    assert authed.post(f"/api/memories/{mid}/confirm").status_code == 404  # only proposals can be confirmed

    assert authed.patch(f"/api/memories/{mid}", json={"pinned": True}).json()["pinned"] is True
    assert authed.delete(f"/api/memories/{mid}").status_code == 204
    assert authed.delete(f"/api/memories/{mid}").status_code == 404


def test_memory_delete_purges_the_row(authed, db):
    """S10: text and vector are gone from the table, not hidden."""
    m = memory.create(db, "Secret plan alpha", origin="stated", status="confirmed")
    db.execute("UPDATE memories SET embedding = array_fill(0.1, ARRAY[768])::vector WHERE id = %s", (m["id"],))
    authed.delete(f"/api/memories/{m['id']}")
    assert db.execute("SELECT count(*) FROM memories WHERE text LIKE '%%alpha%%'").fetchone()[0] == 0


def test_deleting_conversation_drops_its_proposals_but_keeps_confirmed(authed, db):
    conv_id, _ = chat(authed, "Remember that I run on Sundays")
    memory.create(db, "Maybe likes running", origin="inferred", status="proposed", conversation_id=conv_id)
    authed.delete(f"/api/conversations/{conv_id}")
    rows = db.execute("SELECT text, status, source_conversation_id FROM memories").fetchall()
    assert rows == [("I run on Sundays", "confirmed", None)]


def test_add_and_search_memories(authed):
    created = authed.post("/api/memories", json={"text": "Partner is Sam", "kind": "person", "pinned": True}).json()
    assert (created["origin"], created["status"], created["pinned"]) == ("stated", "confirmed", True)
    authed.post("/api/memories", json={"text": "Prefers window seats"})
    assert [m["text"] for m in authed.get("/api/memories?q=sam").json()] == ["Partner is Sam"]
    assert authed.post("/api/memories", json={"text": "  "}).status_code == 422
    assert authed.post("/api/memories", json={"text": "x", "kind": "secret"}).status_code == 422


def test_memory_routes_require_login(client):
    assert client.get("/api/memories").status_code == 401
    assert client.post("/api/memories", json={"text": "x"}).status_code == 401


def test_idle_scan_proposes_for_quiet_conversations(authed, db, llm):
    conv_id, _ = chat(authed, "I'm training for a marathon")
    llm.json_reply = '{"memories": [{"text": "Training for a marathon", "kind": "project"}]}'
    pool = authed.app.state.pool
    assert memory.scan_idle(pool, llm, idle_minutes=15) == 0  # still active
    db.execute("UPDATE messages SET created_at = created_at - interval '1 hour' WHERE conversation_id = %s", (conv_id,))
    assert memory.scan_idle(pool, llm, idle_minutes=15) == 1
    assert memory.scan_idle(pool, llm, idle_minutes=15) == 0  # already scanned


def test_near_duplicates_of_known_memories_are_not_proposed(authed, llm):
    conv_id, _ = chat(authed, "Remember that my sister Mei's birthday is March 3")
    chat(authed, "I'm vegetarian btw", conv_id)
    llm.json_reply = json.dumps({"memories": [
        {"text": "Mei's birthday is March 3.", "kind": "person"},
        {"text": "The user's sister is named Mei.", "kind": "person"},
        {"text": "The user is vegetarian.", "kind": "preference"},
        {"text": "User is vegetarian", "kind": "preference"},
    ]})
    proposed = authed.post(f"/api/conversations/{conv_id}/suggest-memories").json()["proposed"]
    # "sister is named Mei" adds nothing to "my sister Mei's birthday ..."; only the new fact survives, once.
    assert [p["text"] for p in proposed] == ["The user is vegetarian."]
    sent = [m["content"] for m in json.loads(llm.json_calls[-1][1]["content"]) if m["role"] == "user"]
    assert sent == ["I'm vegetarian btw"]  # the remember-that message is not re-extracted


def test_only_remember_messages_skip_the_model(authed, llm):
    conv_id, _ = chat(authed, "remember that I like hiking")
    assert authed.post(f"/api/conversations/{conv_id}/suggest-memories").json() == {"proposed": []}
    assert llm.json_calls == []
