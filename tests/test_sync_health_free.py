"""Google sync health on Status, and "Am I free?" on Schedule."""

from datetime import UTC, date, datetime, timedelta

from pal.connectors import store
from pal.dashboard_api import sync_health

from .test_connections_ui import _event

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
DAY = date(2026, 12, 2)


def _row(service="calendar", enabled=True, ok_ago=1, error=None, created_ago=48):
    return {"service": service, "enabled": enabled, "last_error": error,
            "last_ok_at": NOW - timedelta(hours=ok_ago) if ok_ago is not None else None,
            "created_at": NOW - timedelta(hours=created_ago)}


def test_healthy_failed_stale_and_paused():
    rows = [_row(), _row("drive", error="Google rejected pal's sign-in."), _row("gmail", ok_ago=None),
            _row("calendar", ok_ago=7), _row("drive", enabled=False, ok_ago=100)]
    out, warnings = sync_health(rows, NOW)
    assert [o["state"] for o in out] == ["ok", "error", "ok", "stale", "paused"]
    assert [w["kind"] for w in warnings] == ["sync-drive", "sync-calendar"]
    assert "Google Drive sync failed: Google rejected" in warnings[0]["message"]
    assert "hasn't synced in over 6 hours" in warnings[1]["message"]


def test_new_connection_isnt_stale_before_its_first_sync():
    out, warnings = sync_health([_row(ok_ago=None, created_ago=1)], NOW)
    assert out[0]["state"] == "ok" and warnings == []
    out, _ = sync_health([_row(ok_ago=None, created_ago=8)], NOW)
    assert out[0]["state"] == "stale"


def test_status_reports_sync_health(authed, db):
    row = store.create(db, "google", "calendar", "owner@example.com", [])
    db.execute("UPDATE connections SET last_error = 'boom' WHERE id = %s", (row["id"],))
    body = authed.get("/api/status").json()
    assert body["connections"][0]["state"] == "error"
    assert any(p["kind"] == "sync-calendar" for p in body["pressure"])


def _local(h, m=0):
    return datetime(DAY.year, DAY.month, DAY.day, h, m).astimezone()


def test_free_and_busy(authed, db):
    row = store.create(db, "google", "calendar", "owner@example.com", [])
    _event(db, row["id"], "Dentist", _local(14, 30), _local(15, 30))
    _event(db, row["id"], "Holiday", DAY, DAY + timedelta(days=1), all_day=True)
    q = {"day": DAY.isoformat()}
    busy = authed.get("/api/calendar/free", params={**q, "start": "14:00", "end": "15:00"}).json()
    assert busy["connected"] and not busy["free"] and [e["title"] for e in busy["busy"]] == ["Dentist"]
    assert [e["title"] for e in busy["all_day"]] == ["Holiday"]
    free = authed.get("/api/calendar/free", params={**q, "start": "15:30", "end": "16:00"}).json()
    assert free["free"] and free["coverage"] == "connected calendars only"


def test_free_without_a_calendar_and_bad_range(authed):
    q = {"day": DAY.isoformat(), "start": "10:00"}
    assert authed.get("/api/calendar/free", params={**q, "end": "11:00"}).json() == {"connected": False}
    assert authed.get("/api/calendar/free", params={**q, "end": "09:00"}).status_code == 422
