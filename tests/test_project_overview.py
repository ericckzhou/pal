"""A project's overview gathers its open tasks, schedule, memories, documents, notes and chats."""

from datetime import timedelta

from pal import ingest, memory, reminders


def test_overview_collects_everything_in_the_project(authed, db):
    garden = authed.post("/api/projects", json={"name": "Garden"}).json()
    other = authed.post("/api/projects", json={"name": "Taxes"}).json()
    pid = garden["id"]

    late = authed.post("/api/tasks", json={"title": "Order seeds", "project_id": pid, "due_date": "2020-01-01"}).json()
    authed.post("/api/tasks", json={"title": "Build beds", "project_id": pid})
    done = authed.post("/api/tasks", json={"title": "Buy soil", "project_id": pid}).json()
    authed.patch(f"/api/tasks/{done['id']}", json={"status": "done"})
    authed.post("/api/tasks", json={"title": "File return", "project_id": other["id"]})

    now = reminders.local_now()
    reminders.create(db, "Water the beds", now + timedelta(hours=2), "FREQ=DAILY", project_id=pid, now=now)
    paused = reminders.create(db, "Check frost", now + timedelta(days=1), project_id=pid, now=now)
    reminders.set_paused(db, paused["id"], True)
    reminders.create(db, "Seed order arrives", now + timedelta(days=3), task_id=late["id"], now=now)  # via its task
    reminders.create(db, "Pay taxes", now + timedelta(days=3), project_id=other["id"], now=now)

    memory.create(db, "Beds face south", origin="stated", status="confirmed", project_id=pid)
    memory.create(db, "Tomatoes did well", origin="inferred", status="proposed", project_id=pid)
    memory.create(db, "I like tea", origin="stated", status="confirmed")
    doc, _ = ingest.store(db, "plan.md", b"# Garden plan\nRaised beds.", project_id=pid)
    authed.post("/api/notes", json={"text": "Use cedar", "kind": "decision", "project_id": pid})
    conv = authed.post("/api/conversations").json()["id"]
    authed.put(f"/api/conversations/{conv}/project", json={"project_id": pid})

    body = authed.get(f"/api/projects/{pid}/overview").json()
    assert body["project"]["name"] == "Garden" and "card" in body["project"]
    assert [t["title"] for t in body["tasks"]] == ["Order seeds", "Build beds"]  # due first, then undated
    assert body["counts"] == {"open_tasks": 2, "overdue": 1, "done_this_week": 1, "scheduled": 2, "memories": 1,
                              "suggested": 1, "documents": 1, "chats": 1}
    titles = [r["title"] for r in body["scheduled"]]
    assert set(titles) == {"Water the beds", "Seed order arrives", "Check frost"} and titles[-1] == "Check frost"
    assert next(r for r in body["scheduled"] if r["title"] == "Seed order arrives")["task"] == "Order seeds"
    assert body["memories"][0]["status"] == "proposed"  # suggestions waiting come first
    assert [d["id"] for d in body["documents"]] == [str(doc["id"])]
    assert [n["text"] for n in body["notes"]] == ["Use cedar"]
    assert [c["id"] for c in body["chats"]] == [conv]


def test_overview_of_unknown_project_is_404(authed):
    assert authed.get("/api/projects/00000000-0000-0000-0000-000000000000/overview").status_code == 404
