import json

from pal import embed, memory, memory_replace, retrieval

from .test_memory import chat

ACME = "Owner works at Acme Corp"
INITECH = "Owner works at Initech Corp"


def saved(db, text, **kw):
    return memory.create(db, text, origin="stated", status="confirmed", **kw)


def status(db, memory_id):
    return db.execute("SELECT status FROM memories WHERE id = %s", (memory_id,)).fetchone()[0]


# ---------- replacing by hand ----------

def test_rewriting_a_memory_replaces_it_and_retrieval_stops_using_it(authed, db, llm):
    project = db.execute("INSERT INTO projects (name) VALUES ('Job') RETURNING id").fetchone()[0]
    old = saved(db, ACME, pinned=True, project_id=project)
    r = authed.post(f"/api/memories/{old['id']}/replace-with", json={"text": INITECH})
    assert r.status_code == 201
    new = r.json()
    assert new["replaces_id"] == str(old["id"]) and new["replace_state"] == "applied"
    assert new["pinned"] is True and new["project_id"] == str(project)  # takes the old one's place
    assert status(db, old["id"]) == "replaced"

    in_use = authed.get("/api/memories").json()
    assert [(m["text"], m["replaces_text"]) for m in in_use] == [(INITECH, ACME)]
    replaced = authed.get("/api/memories?status=replaced").json()
    assert [(m["text"], m["replaced_by_text"]) for m in replaced] == [(ACME, INITECH)]

    embed.embed_pending(db, llm)
    texts = [i["text"] for i in retrieval.retrieve(db, llm, "where does the owner work")]
    assert INITECH in texts and ACME not in texts


def test_restore_puts_a_replaced_memory_back_and_drops_the_link(authed, db):
    old = saved(db, ACME)
    new = authed.post(f"/api/memories/{old['id']}/replace-with", json={"text": INITECH}).json()
    r = authed.post(f"/api/memories/{old['id']}/restore")
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert memory.get(db, new["id"])["replace_state"] is None
    assert {m["text"] for m in authed.get("/api/memories").json()} == {ACME, INITECH}
    assert authed.post(f"/api/memories/{old['id']}/restore").status_code == 404  # not replaced any more


def test_replace_refuses_what_it_should(authed, db):
    a, b = saved(db, ACME), saved(db, INITECH)
    proposal = memory.create(db, "Owner likes tea", origin="inferred", status="proposed")
    assert authed.post(f"/api/memories/{a['id']}/replace", json={"old_id": str(a["id"])}).status_code == 409
    assert authed.post(f"/api/memories/{a['id']}/replace", json={"old_id": str(proposal["id"])}).status_code == 409
    missing = "00000000-0000-7000-8000-000000000000"
    assert authed.post(f"/api/memories/{a['id']}/replace", json={"old_id": missing}).status_code == 404
    assert authed.post(f"/api/memories/{b['id']}/replace", json={"old_id": str(a["id"])}).status_code == 200
    # a replaced memory can't turn around and replace its replacement
    assert authed.post(f"/api/memories/{a['id']}/replace", json={"old_id": str(b["id"])}).status_code == 409
    assert authed.post(f"/api/memories/{a['id']}/replace-with", json={"text": "x"}).status_code == 409


def test_replace_routes_require_login(client):
    some = "00000000-0000-7000-8000-000000000000"
    assert client.post(f"/api/memories/{some}/replace", json={"old_id": some}).status_code == 401
    assert client.post(f"/api/memories/{some}/replace-with", json={"text": "x"}).status_code == 401
    assert client.post(f"/api/memories/{some}/restore").status_code == 401
    assert client.post(f"/api/memories/{some}/keep-both").status_code == 401


# ---------- suggestions ----------

def test_suggestion_is_only_a_suggestion_until_the_owner_applies_it(authed, db, llm):
    old = saved(db, ACME)
    new = memory.create(db, INITECH, origin="inferred", status="proposed")
    embed.embed_pending(db, llm)
    llm.json_reply = json.dumps({"replaces": "o1"})
    assert memory_replace.check(db, llm, new["id"]) == old["id"]
    shown = json.loads(llm.json_calls[-1][1]["content"])
    assert shown == {"new": INITECH, "old": [{"id": "o1", "text": ACME}]}

    # S6: the model's answer changed nothing that is in use
    assert status(db, old["id"]) == "confirmed" and status(db, new["id"]) == "proposed"
    inbox = authed.get("/api/memories?status=proposed").json()
    assert inbox[0]["replace_state"] == "suggested" and inbox[0]["replaces_text"] == ACME

    r = authed.post(f"/api/memories/{new['id']}/replace", json={"old_id": str(old["id"])})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert status(db, old["id"]) == "replaced"


def test_keep_both_dismisses_the_suggestion(authed, db, llm):
    old = saved(db, ACME)
    new = saved(db, INITECH)
    embed.embed_pending(db, llm)
    llm.json_reply = json.dumps({"replaces": "o1"})
    memory_replace.check(db, llm, new["id"])
    r = authed.post(f"/api/memories/{new['id']}/keep-both")
    assert r.status_code == 200 and r.json()["replace_state"] is None and r.json()["replaces_id"] is None
    assert status(db, old["id"]) == "confirmed"


def test_model_answers_outside_the_candidates_are_ignored(db, llm):
    old = saved(db, ACME)
    new = saved(db, INITECH)
    embed.embed_pending(db, llm)
    for reply in ['{"replaces": "o9"}', json.dumps({"replaces": str(old["id"])}), "not json", '["o1"]',
                  '{"replaces": null}']:
        db.execute("UPDATE memories SET replace_checked_at = NULL WHERE id = %s", (new["id"],))
        llm.json_reply = reply
        assert memory_replace.check(db, llm, new["id"]) is None
    assert memory.get(db, new["id"])["replace_state"] is None


def test_unrelated_memories_are_not_sent_to_the_model(db, llm):
    saved(db, "Owner likes jazz music")
    new = saved(db, "Owner drives a blue bicycle")
    embed.embed_pending(db, llm)
    assert memory_replace.candidates(db, memory_replace._get(db, new["id"])) == []
    assert memory_replace.check(db, llm, new["id"]) is None
    assert llm.json_calls == []
    assert new["id"] not in memory_replace.unchecked(db)  # checked, so not looked at again


def test_newer_memories_are_never_candidates(db, llm):
    new = saved(db, INITECH)
    saved(db, ACME)  # created after `new`
    embed.embed_pending(db, llm)
    assert memory_replace.candidates(db, memory_replace._get(db, new["id"])) == []


def test_without_vectors_shared_words_still_find_candidates(db, llm):
    old = saved(db, ACME)
    new = saved(db, INITECH)
    cands = memory_replace.candidates(db, memory_replace._get(db, new["id"]))
    assert [c["id"] for c in cands] == [old["id"]]


def test_background_tick_checks_new_memories_once_and_survives_a_down_model(authed, db, llm):
    pool = authed.app.state.pool
    old = saved(db, ACME)
    new = saved(db, INITECH)
    assert memory_replace.unchecked(db) == []  # waits for a vector first
    embed.embed_pending(db, llm)
    assert set(memory_replace.unchecked(db)) == {old["id"], new["id"]}

    def down(*_a, **_k):
        from pal.llm import LLMError

        raise LLMError("down")

    llm.complete_json = down
    assert memory_replace.check_pending(pool, llm) == 0
    assert new["id"] in memory_replace.unchecked(db)  # retried later

    del llm.complete_json
    llm.json_reply = json.dumps({"replaces": "o1"})
    assert memory_replace.check_pending(pool, llm) == 1
    assert memory_replace.unchecked(db) == []
    assert memory.get(db, new["id"])["replaces_id"] == old["id"]


def test_suggest_memories_checks_new_proposals_right_away(authed, db, llm):
    old = saved(db, ACME)
    embed.embed_pending(db, llm)
    conv_id, _ = chat(authed, "I just started a new job at Initech Corp")
    # The fake model answers every JSON call with this; extraction reads "memories", the check reads "replaces".
    llm.json_reply = json.dumps({"memories": [{"text": INITECH, "kind": "fact"}], "replaces": "o1"})
    proposed = authed.post(f"/api/conversations/{conv_id}/suggest-memories").json()["proposed"]
    assert [(p["text"], p["replaces_id"], p["replace_state"]) for p in proposed] == [
        (INITECH, str(old["id"]), "suggested")
    ]
