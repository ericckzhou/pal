"""v1.1: neglected-task scan and weekly digest; S13 one-time Device Encryption check."""

from datetime import date, datetime, timedelta

import pytest

from pal import dashboard_api, encryption, review, work

MONDAY = date(2026, 11, 23)  # a Monday


def _age(db, table: str, row_id, days: int, column: str = "updated_at") -> None:
    db.execute(f"UPDATE {table} SET {column} = now() - make_interval(days => %s) WHERE id = %s", (days, row_id))


# ---------- neglected-task scan ----------

def test_neglected_finds_long_overdue_and_untouched_tasks(db):
    today = date.today()
    overdue = work.create_task(db, "File taxes", None, today - timedelta(days=10))
    work.create_task(db, "Just overdue", None, today - timedelta(days=2))  # overdue, but not neglected yet
    stale = work.create_task(db, "Fix the fence", None, None)
    _age(db, "tasks", stale["id"], 20)
    fresh = work.create_task(db, "Call mum", None, None)
    future = work.create_task(db, "Renew passport", None, today + timedelta(days=30))
    _age(db, "tasks", future["id"], 60)  # a future due date is a plan, not neglect
    done = work.create_task(db, "Old done thing", None, None)
    work.update_task(db, done["id"], status="done")
    _age(db, "tasks", done["id"], 90)

    found = review.neglected(db, today)
    by_title = {t["title"]: t["reason"] for t in found["tasks"]}
    assert by_title == {"File taxes": "overdue", "Fix the fence": "stale"}
    assert found["tasks"][0]["title"] == "File taxes"  # overdue first
    assert fresh["id"] not in {t["id"] for t in found["tasks"]}
    assert overdue["id"] in {t["id"] for t in found["tasks"]}


def test_neglected_skips_archived_projects_and_flags_quiet_ones(db):
    quiet = work.create_project(db, "Garden")
    task = work.create_task(db, "Plant bulbs", quiet["id"], None)
    _age(db, "tasks", task["id"], 30)
    _age(db, "projects", quiet["id"], 30)
    archived = work.create_project(db, "Old job")
    old = work.create_task(db, "Hand over laptop", archived["id"], None)
    _age(db, "tasks", old["id"], 30)
    work.update_project(db, archived["id"], status="archived")
    busy = work.create_project(db, "House move")
    work.create_task(db, "Book van", busy["id"], None)
    _age(db, "projects", busy["id"], 30)  # its task was touched today, so it isn't quiet

    found = review.neglected(db, date.today())
    assert [t["title"] for t in found["tasks"]] == ["Plant bulbs"]
    assert [(p["name"], p["open_tasks"]) for p in found["projects"]] == [("Garden", 1)]


def test_still_on_it_clears_a_stale_task(authed, db):
    task = work.create_task(db, "Sort photos", None, None)
    _age(db, "tasks", task["id"], 20)
    assert [t["title"] for t in authed.get("/api/attention").json()["tasks"]] == ["Sort photos"]
    assert authed.post(f"/api/tasks/{task['id']}/still-on-it").status_code == 200
    assert authed.get("/api/attention").json()["tasks"] == []
    assert authed.post("/api/tasks/00000000-0000-0000-0000-000000000000/still-on-it").status_code == 404


# ---------- weekly digest ----------

def test_week_start_is_monday():
    assert review.week_start(date(2026, 11, 29)) == MONDAY  # Sunday
    assert review.week_start(MONDAY) == MONDAY


def test_digest_summarises_last_week_and_looks_ahead(db):
    last_week = datetime(2026, 11, 18, 12, 0).astimezone()
    done = work.create_task(db, "Send invoice", None, None)
    work.update_task(db, done["id"], status="done")
    db.execute("UPDATE tasks SET done_at = %s, created_at = %s WHERE id = %s", (last_week, last_week, done["id"]))
    old_done = work.create_task(db, "Ancient", None, None)
    work.update_task(db, old_done["id"], status="done")
    db.execute("UPDATE tasks SET done_at = %s WHERE id = %s", (last_week - timedelta(days=30), old_done["id"]))
    work.create_task(db, "Dentist", None, MONDAY + timedelta(days=2))
    work.create_task(db, "Late thing", None, MONDAY - timedelta(days=3))
    work.create_task(db, "Next month", None, MONDAY + timedelta(days=40))

    body = review.build_digest(db, MONDAY)
    assert body["period"] == {"start": "2026-11-16", "end": "2026-11-22"}
    assert [t["title"] for t in body["done"]] == ["Send invoice"]
    assert body["counts"]["done"] == 1
    assert [t["title"] for t in body["due_this_week"]] == ["Dentist"]
    assert [t["title"] for t in body["overdue"]] == ["Late thing"]


def test_weekly_tick_makes_one_digest_after_monday_morning_and_notifies(db):
    assert review.weekly_tick(db, datetime(2026, 11, 23, 7, 59)) is None  # before 08:00
    made = review.weekly_tick(db, datetime(2026, 11, 23, 8, 0))
    assert made is not None and made["week_start"] == MONDAY
    assert review.weekly_tick(db, datetime(2026, 11, 25, 9, 0)) is None  # already made this week
    titles = [r[0] for r in db.execute("SELECT title FROM notifications").fetchall()]
    assert titles == ["Your weekly digest is ready (Digest page)"]


def test_digest_api_lists_reads_and_rebuilds(authed, db):
    review.weekly_tick(db, datetime(2026, 11, 23, 9, 0))
    listed = authed.get("/api/digests").json()
    assert len(listed) == 1 and listed[0]["read_at"] is None
    one = authed.get(f"/api/digests/{listed[0]['id']}").json()
    assert one["body"]["period"]["start"] == "2026-11-16"
    assert authed.post(f"/api/digests/{listed[0]['id']}/read").json()["read_at"] is not None
    rebuilt = authed.post("/api/digests/generate").json()
    assert rebuilt["week_start"] == review.week_start(date.today()).isoformat()
    assert authed.get("/api/digests/00000000-0000-0000-0000-000000000000").status_code == 404


# ---------- S13: Device Encryption ----------

@pytest.mark.parametrize("raw, state", [("1", "on"), ("2", "off"), ("3", "encrypting"), ("5", "suspended"),
                                        ("8", "waiting"), ("0", "unavailable"), ("", "unknown"), ("x", "unknown")])
def test_s13_encryption_codes_map_to_states(raw, state):
    assert encryption.parse(raw) == state


def test_s13_unprotected_disk_warns_until_acknowledged(authed, monkeypatch):
    monkeypatch.setattr(encryption, "_result", {"state": "off", "drive": "C:"})
    warn = [p for p in authed.get("/api/status").json()["pressure"] if p["kind"] == "encryption"]
    assert warn and warn[0]["level"] == "elevated" and "C:" in warn[0]["message"]
    assert authed.post("/api/status/encryption/ack").status_code == 200
    status = authed.get("/api/status").json()
    assert not [p for p in status["pressure"] if p["kind"] == "encryption"]
    assert status["encryption"] == {"state": "off", "drive": "C:", "acknowledged": True}


def test_s13_encrypted_or_unknown_disk_never_warns():
    assert dashboard_api.encryption_warning({"state": "on", "drive": "C:"}, False) is None
    assert dashboard_api.encryption_warning({"state": "unknown", "drive": "C:"}, False) is None
    assert dashboard_api.encryption_warning(None, False) is None
    assert dashboard_api.encryption_warning({"state": "unavailable", "drive": "C:"}, False)["kind"] == "encryption"


def test_s13_check_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("no powershell")

    monkeypatch.setattr(encryption.subprocess, "run", boom)
    assert encryption.check()["state"] == "unknown"
