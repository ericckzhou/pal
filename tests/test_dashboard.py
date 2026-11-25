"""Status, sessions, analytics, logs, settings, per-chat model, step cards and schedule controls."""

import logging
from datetime import datetime, timedelta

from pal import dashboard_api, reminders

from .test_chat import stream_events
from .test_devices import paired  # noqa: F401  (fixture)

GB = 1024**3


def new_chat(client) -> str:
    return client.post("/api/conversations").json()["id"]


# ---------- status ----------

def test_status_reports_health_counts_and_recent_sessions(authed):
    conv = new_chat(authed)
    stream_events(authed, conv, "Plan a trip to Lisbon")
    status = authed.get("/api/status").json()
    assert status["ollama"]["up"] is True and "fake-model" in status["ollama"]["installed"]
    assert status["counts"]["conversations"] == 1 and status["counts"]["messages"] == 2
    assert status["counts"]["active_chats"] == 1
    assert status["counts"]["tokens_24h"] == 103  # fake usage: 100 prompt + 3 reply parts
    recent = status["recent"][0]
    assert (recent["title"], recent["message_count"], recent["tokens"], recent["last_model"]) == (
        "Plan a trip to Lisbon", 2, 103, "fake-model")
    assert recent["preview"] == "Plan a trip to Lisbon"
    assert "dir" in status["backups"]  # the PC sees where backups live


def test_backups_summary_counts_plaintext(settings, tmp_path):
    from dataclasses import replace

    from pal.dashboard_api import _backups

    (tmp_path / "pal-20260101-000000.dump").write_bytes(b"PGDMP")
    (tmp_path / "pal-20260102-000000.dump.enc").write_bytes(b"PALBK1...")
    summary = _backups(replace(settings, backup_dir=tmp_path))
    assert (summary["count"], summary["plaintext"], summary["latest_bytes"]) == (2, 1, 9)


def test_status_flags_ollama_down(authed, llm):
    llm.ollama_down = True
    status = authed.get("/api/status").json()
    assert status["ollama"]["up"] is False
    assert status["pressure"][0]["kind"] == "model"


def test_status_needs_sign_in(client):
    assert client.get("/api/status").status_code == 401


def test_pressure_orders_by_severity():
    warn = dashboard_api.pressure(disk_free=3 * GB, memory=(0.01, 300 * 1024**2), ollama_up=True)
    assert [(w["level"], w["kind"]) for w in warn] == [("critical", "memory"), ("elevated", "disk")]
    assert dashboard_api.pressure(disk_free=100 * GB, memory=(0.5, 8 * GB), ollama_up=True) == []
    assert dashboard_api.pressure(None, None, True) == []
    low = dashboard_api.pressure(disk_free=100 * GB, memory=(0.036, int(1.2 * GB)), ollama_up=True)
    assert [(w["level"], w["kind"]) for w in low] == [("elevated", "memory")]


# ---------- per-chat model, steps, usage ----------

def test_chat_uses_its_chosen_model_and_records_usage(authed, llm):
    conv = new_chat(authed)
    assert authed.patch(f"/api/conversations/{conv}", json={"model": "not-allowed"}).status_code == 422
    changed = authed.patch(f"/api/conversations/{conv}", json={"model": "fake-large"}).json()
    assert changed["model"] == "fake-large"
    events = stream_events(authed, conv, "hello")
    assert events[-1]["model"] == "fake-large" and events[-1]["usage"]["prompt_tokens"] == 100
    assert llm.models_used == ["fake-large"]
    reply = authed.get(f"/api/conversations/{conv}").json()["messages"][1]
    assert (reply["model"], reply["prompt_tokens"], reply["completion_tokens"]) == ("fake-large", 100, 3)

    authed.patch(f"/api/conversations/{conv}", json={"model": None})  # back to the default
    stream_events(authed, conv, "again")
    assert llm.models_used[-1] == "fake-model"


def test_models_lists_allowlist_with_install_state(authed):
    body = authed.get("/api/models").json()
    assert body["default"] == "fake-model"
    local = {m["name"]: m["installed"] for m in body["models"] if m["provider"] == "local"}
    assert local == {"fake-large": True, "fake-model": True}


def test_retrieval_step_is_streamed_and_stored(authed):
    authed.post("/api/memories", json={"text": "My locker code is 4417", "kind": "fact", "pinned": True})
    conv = new_chat(authed)
    events = stream_events(authed, conv, "What is my locker code?")
    steps = [e["step"] for e in events if e["type"] == "step"]
    assert steps and steps[0]["kind"] == "retrieval" and "memory" in steps[0]["detail"]
    stored = authed.get(f"/api/conversations/{conv}").json()["messages"][1]["steps"]
    assert stored == steps


# ---------- sessions ----------

def test_sessions_rename_search_export_and_bulk_delete(authed):
    a, b = new_chat(authed), new_chat(authed)
    stream_events(authed, a, "Notes about sourdough starters")
    stream_events(authed, b, "Bike repair checklist")
    assert authed.patch(f"/api/conversations/{a}", json={"title": "  "}).status_code == 422
    assert authed.patch(f"/api/conversations/{a}", json={"title": "Bread"}).json()["title"] == "Bread"

    assert [s["title"] for s in authed.get("/api/sessions", params={"q": "sourdough"}).json()] == ["Bread"]
    assert len(authed.get("/api/sessions").json()) == 2

    md = authed.get(f"/api/conversations/{a}/export")
    assert md.headers["content-disposition"] == 'attachment; filename="Bread.md"'
    assert "# Bread" in md.text and "Notes about sourdough starters" in md.text
    js = authed.get(f"/api/conversations/{a}/export", params={"format": "json"}).json()
    assert [m["role"] for m in js["messages"]] == ["user", "assistant"]

    assert authed.post("/api/conversations/bulk-delete", json={"ids": [a, b]}).json() == {"removed": 2}
    assert authed.get("/api/sessions").json() == []
    assert authed.get(f"/api/conversations/{a}/export").status_code == 404


# ---------- analytics ----------

def test_usage_by_day_and_model(authed, db):
    conv = new_chat(authed)
    stream_events(authed, conv, "one")
    stream_events(authed, conv, "/project Garden")  # commands don't count
    db.execute("""INSERT INTO messages (conversation_id, role, content, model) VALUES (%s, 'assistant', 'old', 'fake-model')""",
               (conv,))  # a reply from before usage was tracked
    body = authed.get("/api/analytics/usage", params={"days": 7}).json()
    assert len(body["daily"]) == 7
    assert body["daily"][-1]["replies"] == 2 and body["daily"][-1]["prompt_tokens"] == 100
    assert body["by_model"] == [{"model": "fake-model", "replies": 2, "prompt_tokens": 100, "completion_tokens": 3}]
    assert body["totals"]["untracked_replies"] == 1


# ---------- logs and settings: PC only ----------

def test_logs_are_filtered_and_pc_only(authed, remote, paired):  # noqa: F811
    logging.getLogger("pal.test").warning("disk is grumpy")
    logging.getLogger("pal.test").info("routine tick")
    warn = authed.get("/api/logs", params={"level": "warning", "q": "grumpy"}).json()
    assert [line["message"] for line in warn] == ["disk is grumpy"]
    assert all(line["level"] in {"warning", "error"} for line in authed.get("/api/logs?level=warning").json())
    assert remote.get("/api/logs").status_code == 403
    assert remote.get("/api/settings").status_code == 403


def test_settings_validate_save_apply_and_reset(authed, client):
    fields = {f["key"]: f for f in authed.get("/api/settings").json()["fields"]}
    assert fields["num_ctx"]["value"] == 8192 and not fields["num_ctx"]["changed"]

    assert authed.put("/api/settings", json={"num_ctx": 100}).status_code == 422
    assert authed.put("/api/settings", json={"hosts": ["evil"]}).status_code == 422
    assert authed.put("/api/settings", json={"answer_reserve": 6000}).status_code == 422  # over half of 8192
    assert authed.put("/api/settings", json={"extra_models": ["missing:1b"]}).status_code == 422

    saved = {f["key"]: f for f in authed.put("/api/settings", json={"num_ctx": 16384}).json()["fields"]}
    assert saved["num_ctx"]["value"] == 16384 and saved["num_ctx"]["changed"]
    assert client.app.state.settings.num_ctx == 16384  # live without a restart

    ok = authed.put("/api/settings", json={"extra_models": ["other:7b"], "chat_model": "other:7b"})
    assert ok.status_code == 200, ok.text
    assert client.app.state.settings.chat_model == "other:7b"

    reset = {f["key"]: f for f in authed.delete("/api/settings/num_ctx").json()["fields"]}
    assert reset["num_ctx"]["value"] == 8192 and not reset["num_ctx"]["changed"]


def test_extra_models_need_ollama(authed, llm):
    llm.ollama_down = True
    resp = authed.put("/api/settings", json={"extra_models": ["other:7b"]})
    assert resp.status_code == 422 and "Ollama" in resp.json()["detail"]


# ---------- schedule ----------

def test_pause_resume_and_trigger(authed, db):
    now = reminders.local_now()
    r = reminders.create(db, "Stretch", now - timedelta(days=3), "FREQ=DAILY", now=now - timedelta(days=3))
    assert authed.post(f"/api/reminders/{r['id']}/pause").json()["paused"] is True
    db.execute("UPDATE reminders SET due_at = now() - interval '2 days' WHERE id = %s", (r["id"],))
    assert reminders.fire_due(db) == []  # paused reminders never fire

    resumed = authed.post(f"/api/reminders/{r['id']}/resume").json()
    assert resumed["paused"] is False
    assert datetime.fromisoformat(resumed["due_at"]).replace(tzinfo=None) > datetime.now()  # missed times skipped

    due_before = resumed["due_at"]
    note = authed.post(f"/api/reminders/{r['id']}/trigger").json()
    assert note["title"] == "Stretch"
    assert authed.get("/api/reminders").json()[0]["due_at"] == due_before  # schedule untouched
    assert authed.post("/api/reminders/00000000-0000-0000-0000-000000000000/pause").status_code == 404
