import json
from datetime import datetime, timedelta

from pal import actions, conversations, reminders, work

from .test_chat import stream_events

NOW = datetime(2026, 11, 24, 10, 0)  # a Tuesday


def new_chat(client) -> str:
    return client.post("/api/conversations").json()["id"]


# ---------- projects, tasks, notes ----------

def test_project_task_note_crud(authed):
    project = authed.post("/api/projects", json={"name": "pal", "summary": "Local assistant"}).json()
    assert authed.post("/api/projects", json={"name": "PAL"}).status_code == 409

    task = authed.post("/api/tasks", json={"title": "Ship M4", "project_id": project["id"], "due_date": "2026-12-01"})
    assert task.status_code == 201
    task = task.json()
    assert (task["project"], task["due_date"], task["status"]) == ("pal", "2026-12-01", "open")

    done = authed.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).json()
    assert done["status"] == "done" and done["done_at"]
    assert authed.get("/api/tasks").json() == []
    assert len(authed.get("/api/tasks?status=done").json()) == 1
    cleared = authed.patch(f"/api/tasks/{task['id']}", json={"due_date": None}).json()
    assert cleared["due_date"] is None and cleared["project"] == "pal"  # untouched fields stay

    note = authed.post("/api/notes", json={"text": "Use PostgreSQL", "kind": "decision", "project_id": project["id"]})
    assert note.json()["kind"] == "decision"
    assert authed.post("/api/notes", json={"text": "   "}).status_code == 422

    listed = authed.get("/api/projects").json()
    assert [(p["name"], p["open_tasks"]) for p in listed] == [("pal", 0)]

    # Deleting a project keeps its tasks and notes, now outside any project.
    assert authed.delete(f"/api/projects/{project['id']}").status_code == 204
    assert authed.get("/api/tasks?status=all").json()[0]["project_id"] is None
    assert authed.get("/api/notes").json()[0]["project_id"] is None


def test_task_board_stage(authed):
    task = authed.post("/api/tasks", json={"title": "Board card"}).json()
    assert task["stage"] == "todo"

    doing = authed.patch(f"/api/tasks/{task['id']}", json={"stage": "doing"}).json()
    assert (doing["status"], doing["stage"]) == ("open", "doing")
    assert [t["id"] for t in authed.get("/api/tasks").json()] == [task["id"]]  # still counts as open

    done = authed.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).json()
    assert done["status"] == "done" and done["done_at"]
    # Reopening straight into a column sets both in one call.
    back = authed.patch(f"/api/tasks/{task['id']}", json={"status": "open", "stage": "todo"}).json()
    assert (back["status"], back["stage"], back["done_at"]) == ("open", "todo", None)

    assert authed.patch(f"/api/tasks/{task['id']}", json={"stage": "review"}).status_code == 422


def test_unknown_project_is_rejected(authed):
    resp = authed.post("/api/tasks", json={"title": "x", "project_id": "0190a5d2-0000-7000-8000-000000000000"})
    assert resp.status_code == 404


def test_project_list_stats(db):
    p = work.create_project(db, "pal")
    quiet = work.create_project(db, "quiet")
    work.create_task(db, "Late", p["id"], NOW.date() - timedelta(days=2))
    work.create_task(db, "Soon", p["id"], NOW.date() + timedelta(days=3))
    work.create_task(db, "Later", p["id"], NOW.date() + timedelta(days=9))
    finished = work.create_task(db, "Was late", p["id"], NOW.date() - timedelta(days=5))
    work.update_task(db, finished["id"], status="done")

    by_name = {row["name"]: row for row in work.list_projects(db, today=NOW.date())}
    assert (by_name["pal"]["open_tasks"], by_name["pal"]["overdue"]) == (3, 1)
    assert by_name["pal"]["next_due"] == NOW.date() + timedelta(days=3)  # overdue and done tasks don't count
    assert by_name["pal"]["last_activity"] >= by_name["pal"]["updated_at"]
    assert (by_name["quiet"]["overdue"], by_name["quiet"]["next_due"]) == (0, None)
    assert by_name["quiet"]["last_activity"] == quiet["updated_at"]


def test_project_card(db):
    p = work.create_project(db, "pal", "Private local assistant")
    work.create_task(db, "Overdue thing", p["id"], NOW.date() - timedelta(days=2))
    work.create_task(db, "Later thing", p["id"])
    work.create_note(db, "Use PostgreSQL", "decision", p["id"])
    work.create_note(db, "a plain note", "note", p["id"])
    card = work.project_card(db, p, NOW.date())
    assert card.splitlines()[0] == "Project pal: Private local assistant"
    assert "Overdue thing (overdue, due 2026-11-22)" in card
    assert "Recent decisions: Use PostgreSQL" in card
    assert "plain note" not in card


def test_focus_project_card_and_notes_reach_the_model(authed, db, llm):
    p = work.create_project(db, "Garden", "Backyard vegetable beds")
    work.create_task(db, "Order tomato seeds", p["id"])
    work.create_note(db, "We chose raised cedar beds", "decision")
    conv = new_chat(authed)
    assert authed.put(f"/api/conversations/{conv}/project", json={"project_id": str(p["id"])}).status_code == 200
    assert authed.get(f"/api/conversations/{conv}").json()["project_id"] == str(p["id"])

    events = stream_events(authed, conv, "which beds did we choose for the garden?")
    sent = llm.calls[-1][-1]["content"]
    assert "[p1] (project card) Project Garden: Backyard vegetable beds" in sent
    assert "Order tomato seeds" in sent
    assert "(decision) We chose raised cedar beds" in sent  # notes are retrieved too, with an n handle
    handles = {s["handle"] for s in next(e for e in events if e["type"] == "sources")["sources"]}
    assert {"p1", "n1"} <= handles


def test_invented_citations_are_stripped_before_saving(authed, llm):
    conv = new_chat(authed)
    llm.reply = ["Paris [m7] is the capital."]
    stream_events(authed, conv, "capital of France?")
    saved = authed.get(f"/api/conversations/{conv}").json()["messages"][-1]["content"]
    assert saved == "Paris is the capital."


# ---------- model-proposed actions (S6) ----------

def test_action_proposal_needs_owner_confirmation(authed, db, llm):
    work.create_project(db, "StarcallOS")
    conv = new_chat(authed)
    llm.json_reply = json.dumps({
        "action": "create_task", "title": "write StarcallOS release notes", "due_text": "next Wednesday",
        "recurrence_text": None, "project": "starcallos",
    })
    events = stream_events(authed, conv, "Add 'write StarcallOS release notes' to the StarcallOS project, due next Wednesday.")
    assert events[-2]["type"] == "done"
    proposal = events[-1]["proposal"]
    assert proposal["action"] == "create_task" and proposal["status"] == "proposed"
    assert proposal["payload"]["project"] == "StarcallOS"
    assert proposal["payload"]["due_date"]  # parsed in code from the copied phrase
    assert "Known projects: StarcallOS" in llm.json_calls[-1][0]["content"]
    # The reply is told exactly what was drafted (date parsed in code) and that it isn't saved yet.
    system = llm.calls[-1][0]["content"]
    assert 'pal has drafted a task "Write StarcallOS release notes" in project StarcallOS' in system
    assert "Do not mention any date" in system  # the card shows the code-parsed date instead
    assert "NOT saved yet" in system
    assert authed.get("/api/tasks").json() == []  # nothing created yet

    assert [p["id"] for p in authed.get(f"/api/conversations/{conv}/proposals").json()] == [proposal["id"]]
    result = authed.post(f"/api/proposals/{proposal['id']}/confirm", json={"title": "Release notes v2"}).json()
    assert result["task"]["title"] == "Release notes v2" and result["task"]["project"] == "StarcallOS"
    assert authed.get(f"/api/conversations/{conv}/proposals").json() == []
    assert authed.post(f"/api/proposals/{proposal['id']}/confirm", json={}).status_code == 404  # only once


def test_reminder_proposal_and_reject(authed, llm):
    conv = new_chat(authed)
    llm.json_reply = json.dumps({"action": "set_reminder", "title": "review my week", "due_text": "at 9",
                                 "recurrence_text": "every Monday", "project": None})
    events = stream_events(authed, conv, "Remind me every Monday at 9 to review my week.")
    payload = events[-1]["proposal"]["payload"]
    assert payload["rrule"] == "FREQ=WEEKLY;BYDAY=MO" and payload["repeat"] == "every Monday"
    assert datetime.fromisoformat(payload["at"]).weekday() == 0 and payload["at"].endswith("09:00")
    assert authed.delete(f"/api/proposals/{events[-1]['proposal']['id']}").status_code == 204
    assert authed.get("/api/reminders").json() == []


def test_model_output_is_validated(db, llm):
    c = conversations.create_conversation(db)
    msg = conversations.add_message(db, c["id"], "user", "remind me")
    for reply in ['{"action": "delete_all"}', "not json", '{"action": "create_task", "title": "  "}',
                  '{"action": "set_reminder", "title": "x", "due_text": null, "recurrence_text": null}']:
        llm.json_reply = reply
        assert actions.propose(db, llm, c["id"], msg, "Can you remind me?", NOW) is None
    # A date phrase the model made up (not in the message) is ignored.
    llm.json_reply = json.dumps({"action": "create_task", "title": "buy ink", "due_text": "December 25"})
    p = actions.propose(db, llm, c["id"], msg, "todo: buy printer ink", NOW)
    assert p["payload"]["due_date"] is None


def test_no_action_call_for_ordinary_chat(authed, llm):
    conv = new_chat(authed)
    events = stream_events(authed, conv, "What's the capital of Peru?")
    assert events[-1]["type"] == "done"
    assert llm.json_calls == []


# ---------- slash commands ----------

def test_slash_commands(authed, llm):
    conv = new_chat(authed)
    events = stream_events(authed, conv, "/project Garden")
    assert events[0]["step"] == {"kind": "command", "label": "Ran /project", "detail": None}
    assert events[1]["type"] == "work_changed"
    assert "Created project **Garden**" in events[2]["text"]
    assert authed.get(f"/api/conversations/{conv}").json()["title"] == "New chat"  # commands aren't titles
    stream_events(authed, conv, "/task order seeds by friday")
    stream_events(authed, conv, "/decision raised cedar beds")
    events = stream_events(authed, conv, "/remind tomorrow 6pm to water the beds")
    assert "Reminder set: **Water the beds**" in events[2]["text"]
    assert llm.calls == [] and llm.json_calls == []  # the model is never involved
    msgs = authed.get(f"/api/conversations/{conv}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] * 4

    task = authed.get("/api/tasks").json()[0]
    assert (task["title"], task["project"]) == ("Order seeds", "Garden")
    assert datetime.fromisoformat(task["due_date"]).weekday() == 4
    assert authed.get("/api/notes?kind=decision").json()[0]["project"] == "Garden"
    assert authed.get("/api/reminders").json()[0]["project"] == "Garden"

    # The model never sees slash commands or pal's replies to them (it would imitate "Added task ...").
    stream_events(authed, conv, "hello there")
    seen = " ".join(m["content"] for m in llm.calls[-1])
    assert "/task" not in seen and "Added task" not in seen and "hello there" in seen

    events = stream_events(authed, conv, "/remind sometime to relax")
    assert "When, and what?" in events[-2]["text"]
    assert "Commands:" in stream_events(authed, conv, "/help")[-2]["text"]


# ---------- reminders ----------

def test_reminders_fire_once_and_repeat(db):
    once = reminders.create(db, "Call mom", NOW + timedelta(minutes=5), now=NOW)
    weekly = reminders.create(db, "Review week", datetime(2026, 11, 24, 9), "FREQ=WEEKLY;BYDAY=MO", now=NOW)
    assert reminders.to_local(weekly["due_at"]) == datetime(2026, 11, 30, 9)

    assert reminders.fire_due(db, NOW) == []
    fired = reminders.fire_due(db, NOW + timedelta(minutes=6))
    assert [n["title"] for n in fired] == ["Call mom"]
    assert reminders.get(db, once["id"])["active"] is False

    # A week late: the repeat fires once and moves to the next Monday after now.
    fired = reminders.fire_due(db, datetime(2026, 12, 9, 12))
    assert [n["title"] for n in fired] == ["Review week"]
    assert reminders.to_local(reminders.get(db, weekly["id"])["due_at"]) == datetime(2026, 12, 14, 9)
    assert len(reminders.notifications(db)) == 2


def test_notifications_api(authed, db):
    reminders.create(db, "Stretch", NOW, now=NOW - timedelta(hours=1))
    reminders.fire_due(db, NOW)
    [n] = authed.get("/api/notifications").json()
    snoozed = authed.post(f"/api/notifications/{n['id']}/snooze", json={"minutes": 10}).json()
    assert snoozed["title"] == "Stretch" and snoozed["active"]
    assert authed.get("/api/notifications").json() == []


def test_reminder_api_validation(authed):
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="minutes")
    assert authed.post("/api/reminders", json={"title": "x", "at": past}).status_code == 422
    assert authed.post("/api/reminders", json={"title": "x", "at": past, "rrule": "FREQ=SECONDLY"}).status_code == 422
    ok = authed.post("/api/reminders", json={"title": "Stand up", "at": past, "rrule": "FREQ=DAILY"})
    assert ok.status_code == 201 and ok.json()["repeat"] == "every day"
    rid = ok.json()["id"]
    assert authed.patch(f"/api/reminders/{rid}", json={"rrule": None}).json()["rrule"] is None
    assert authed.delete(f"/api/reminders/{rid}").status_code == 204


def test_work_routes_need_a_session(client):
    for path in ("/api/projects", "/api/tasks", "/api/notes", "/api/reminders", "/api/notifications"):
        assert client.get(path).status_code == 401
