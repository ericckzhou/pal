"""Memories and documents can belong to a project; the chat's focus project ranks its items higher."""

import json
from uuid import UUID

from pal import embed, memory, retrieval, work

from .test_memory import chat


def _project(db, name="Kitchen remodel"):
    return work.create_project(db, name)["id"]


# ---------- retrieval ----------

def test_focus_project_ranks_its_items_higher_but_keeps_the_rest(db, llm):
    pid = _project(db)
    general = memory.create(db, "dentist appointment friday", origin="stated", status="confirmed")
    tagged = memory.create(db, "dentist appointment", origin="stated", status="confirmed", project_id=pid)
    embed.embed_pending(db, llm)
    query = "dentist appointment friday"

    plain = [i["id"] for i in retrieval.retrieve(db, llm, query)]
    assert plain[:2] == [str(general["id"]), str(tagged["id"])]

    boosted = [i["id"] for i in retrieval.retrieve(db, llm, query, project_id=pid)]
    assert boosted[:2] == [str(tagged["id"]), str(general["id"])]  # reordered, not filtered


def test_chat_passes_its_focus_project_to_retrieval(authed, db, llm, monkeypatch):
    pid = _project(db)
    conv_id = authed.post("/api/conversations").json()["id"]
    work.set_focus(db, UUID(conv_id), pid)
    seen = {}
    real = retrieval.retrieve

    def spy(conn, llm_, query, conv=None, project_id=None, **kwargs):
        seen["project_id"] = project_id
        return real(conn, llm_, query, conv, project_id, **kwargs)

    monkeypatch.setattr(retrieval, "retrieve", spy)
    chat(authed, "what is left to do?", conv_id)
    assert seen["project_id"] == pid


# ---------- memories ----------

def test_memory_project_can_be_set_kept_and_cleared(authed, db):
    pid = str(_project(db))
    m = authed.post("/api/memories", json={"text": "Cabinets are oak", "project_id": pid}).json()
    assert m["project_id"] == pid

    kept = authed.patch(f"/api/memories/{m['id']}", json={"pinned": True}).json()
    assert kept["project_id"] == pid and kept["pinned"]

    cleared = authed.patch(f"/api/memories/{m['id']}", json={"project_id": None}).json()
    assert cleared["project_id"] is None


def test_unknown_project_is_refused(authed):
    missing = "00000000-0000-7000-8000-000000000000"
    assert authed.post("/api/memories", json={"text": "x", "project_id": missing}).status_code == 404


def test_deleting_a_project_makes_its_memories_global(db):
    pid = _project(db)
    m = memory.create(db, "Tiles ordered", origin="stated", status="confirmed", project_id=pid)
    work.delete_project(db, pid)
    assert memory.get(db, m["id"])["project_id"] is None


def test_inferred_project_memories_take_the_chat_focus_but_facts_stay_global(authed, db, llm):
    pid = _project(db)
    conv_id, _ = chat(authed, "We picked oak cabinets and I love oak in general")
    work.set_focus(db, UUID(conv_id), pid)
    llm.json_reply = json.dumps({"memories": [
        {"text": "Kitchen remodel uses oak cabinets", "kind": "decision"},
        {"text": "Likes oak wood", "kind": "preference"},
    ]})
    created = {m["kind"]: m["project_id"] for m in memory.scan_conversation(db, llm, UUID(conv_id))}
    assert created == {"decision": pid, "preference": None}


# ---------- documents ----------

def test_folder_project_applies_to_its_files_and_new_ones(authed, db, tmp_path):
    pid = str(_project(db))
    folder = tmp_path / "remodel"
    folder.mkdir()
    (folder / "quote.md").write_text("# Quote\n\ncabinet quote")
    authed.post("/api/documents/folder", json={"path": str(folder)})
    root = str(folder.resolve())

    assert authed.post("/api/documents/folder/project", json={"path": root, "project_id": pid}).json() == {"updated": 1}
    (folder / "plan.md").write_text("# Plan\n\nfloor plan")
    authed.post("/api/documents/folder", json={"path": str(folder)})
    docs = {d["filename"]: d["project_id"] for d in authed.get("/api/documents").json()}
    assert docs == {"quote.md": pid, "plan.md": pid}

    missing = authed.post("/api/documents/folder/project", json={"path": "C:\\nope", "project_id": None})
    assert missing.status_code == 404


def test_single_document_project_can_be_set_and_cleared(authed, db):
    pid = str(_project(db))
    doc = authed.post("/api/documents/upload?filename=n.txt", content=b"some notes",
                      headers={"Content-Type": "application/octet-stream"}).json()["document"]
    assert authed.put(f"/api/documents/{doc['id']}/project", json={"project_id": pid}).json()["project_id"] == pid
    assert authed.put(f"/api/documents/{doc['id']}/project", json={"project_id": None}).json()["project_id"] is None
    gone = "00000000-0000-7000-8000-000000000000"
    assert authed.put(f"/api/documents/{gone}/project", json={"project_id": None}).status_code == 404


def test_uploads_and_added_folders_can_go_straight_into_a_project(authed, db, tmp_path):
    pid = str(_project(db))
    r = authed.post(f"/api/documents/upload?filename=spec.txt&project_id={pid}", content=b"spec text",
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 201 and r.json()["document"]["project_id"] == pid

    # The same bytes uploaded again with no project keep the tag; with a project they move.
    again = authed.post("/api/documents/upload?filename=spec.txt", content=b"spec text",
                        headers={"Content-Type": "application/octet-stream"}).json()
    assert again["outcome"] == "unchanged" and again["document"]["project_id"] == pid

    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "a.md").write_text("# A\n\nalpha")
    authed.post("/api/documents/folder", json={"path": str(folder), "project_id": pid})
    docs = {d["filename"]: d["project_id"] for d in authed.get("/api/documents").json()}
    assert docs["a.md"] == pid

    missing = "00000000-0000-7000-8000-000000000000"
    bad = authed.post(f"/api/documents/upload?filename=x.txt&project_id={missing}", content=b"x",
                      headers={"Content-Type": "application/octet-stream"})
    assert bad.status_code == 404
