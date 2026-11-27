"""Phase 6 backend: the Connections page API (client status, Drive folders, project), the per-chat Gmail switch,
calendar events in the weekly digest, and the Docker sign-in port."""

from datetime import date, datetime, timedelta

import pytest

from pal import review
from pal.connectors import google_cli, store

from .test_drive import FOLDER, FakeDrive, api

MONDAY = date(2026, 11, 23)


def _event(db, conn_id, title, start, end, all_day=False):
    cols = "start_date, end_date" if all_day else "start_at, end_at"
    db.execute(
        f"INSERT INTO calendar_events (connection_id, calendar_id, event_id, title, all_day, {cols}, status) "
        "VALUES (%s, 'primary', %s, %s, %s, %s, %s, 'confirmed')", (conn_id, title, title, all_day, start, end))


# --- Connections page ------------------------------------------------------------------------------------------

def test_list_reports_client_and_how_to_connect(authed, monkeypatch):
    from pal.connectors import google_auth

    monkeypatch.setattr(google_auth, "load_client", lambda: None)
    body = authed.get("/api/connections").json()
    assert body["google_client"] is False and body["client_command"] == "pal google-client --set client.json"
    assert [(s["service"], s["command"]) for s in body["services"]] == [
        ("calendar", "pal connect google calendar"), ("drive", "pal connect google drive"),
        ("gmail", "pal connect google gmail")]
    assert all("read-only" in s["grants"] for s in body["services"])


def test_list_shows_docker_commands_in_a_container(authed, monkeypatch):
    monkeypatch.setenv("PAL_CONTAINER", "1")
    body = authed.get("/api/connections").json()
    assert body["client_command"].startswith("bash scripts/pal-docker.sh google-client")
    assert body["services"][0]["command"] == "bash scripts/pal-docker.sh connect calendar"


def test_cursor_never_reaches_the_page(authed, db):
    row = store.create(db, "google", "calendar", "owner@example.com", [])
    store.record_success(db, row["id"], "secret-cursor")
    listed = authed.get("/api/connections").json()["connections"]
    assert "cursor" not in listed[0]


def test_drive_folders_add_list_remove(authed, db):
    fake = FakeDrive()
    fake.add("folderAAAAAA1", "Work", FOLDER)
    fake.add("fileBBBBBBBB2", "a.txt", text="x")
    authed.app.state.drive_api = api(fake)
    row = store.create(db, "google", "drive", "owner@example.com", ["drive.readonly"])
    url = f"/api/connections/{row['id']}/folders"
    assert authed.get(url).json() == {"folders": []}
    added = authed.post(url, json={"link": "https://drive.google.com/drive/folders/folderAAAAAA1?usp=sharing"})
    assert added.status_code == 200 and added.json()["added"] == {"id": "folderAAAAAA1", "name": "Work"}
    bad = authed.post(url, json={"link": "fileBBBBBBBB2"})
    assert bad.status_code == 422 and "not a folder" in bad.json()["detail"]
    assert authed.post(url, json={"link": "folderAAAAAA1"}).json()["detail"] == "That folder is already chosen."
    assert authed.post(url, json={"link": "hello"}).status_code == 422
    assert authed.get(url).json()["folders"] == [{"id": "folderAAAAAA1", "name": "Work"}]
    assert authed.delete(f"{url}/nope000000").status_code == 404
    assert authed.delete(f"{url}/folderAAAAAA1").json() == {"folders": []}


def test_folders_only_for_a_drive_connection(authed, db):
    row = store.create(db, "google", "calendar", "owner@example.com", [])
    assert authed.get(f"/api/connections/{row['id']}/folders").status_code == 404


def test_folder_endpoints_are_pc_only(remote, db):
    row = store.create(db, "google", "drive", "owner@example.com", [])
    assert remote.get(f"/api/connections/{row['id']}/folders").status_code in (401, 403)
    assert remote.post(f"/api/connections/{row['id']}/folders", json={"link": "x"}).status_code in (401, 403)


def test_project_moves_synced_documents_too(authed, db):
    from pal import ingest, work

    project = work.create_project(db, "Home")
    row = store.create(db, "google", "drive", "owner@example.com", [])
    doc, _ = ingest.store(db, "a.txt", b"hello there", source_path="google-drive:f1")
    store.map_item(db, row["id"], "f1", doc["id"], "v1")

    def doc_project():
        found = db.execute("SELECT project_id FROM documents WHERE id = %s", (doc["id"],)).fetchone()[0]
        return str(found) if found else None

    patched = authed.patch(f"/api/connections/{row['id']}", json={"project_id": str(project["id"])})
    assert patched.status_code == 200 and str(patched.json()["project_id"]) == str(project["id"])
    assert doc_project() == str(project["id"])
    assert authed.patch(f"/api/connections/{row['id']}", json={"project_id": None}).json()["project_id"] is None
    assert doc_project() is None
    missing = authed.patch(f"/api/connections/{row['id']}", json={"project_id": "00000000-0000-7000-8000-000000000000"})
    assert missing.status_code == 422
    assert authed.patch(f"/api/connections/{row['id']}", json={"enabled": False}).json()["enabled"] is False


# --- per-chat Gmail switch -------------------------------------------------------------------------------------

def test_chat_gmail_switch(authed, db):
    conv = authed.post("/api/conversations").json()["id"]
    got = authed.get(f"/api/conversations/{conv}").json()
    assert got["gmail"] is True and got["gmail_connected"] is False
    assert authed.patch(f"/api/conversations/{conv}", json={"gmail": False}).json()["gmail"] is False
    store.create(db, "google", "gmail", "owner@example.com", [])
    got = authed.get(f"/api/conversations/{conv}").json()
    assert got["gmail"] is False and got["gmail_connected"] is True


# --- digest ----------------------------------------------------------------------------------------------------

def test_digest_lists_next_weeks_events(db):
    row = store.create(db, "google", "calendar", "owner@example.com", [])
    at = datetime(2026, 11, 25, 10, 0).astimezone()
    _event(db, row["id"], "Dentist", at, at + timedelta(hours=1))
    _event(db, row["id"], "Trip", date(2026, 11, 27), date(2026, 11, 29), all_day=True)
    _event(db, row["id"], "Before", at - timedelta(days=7), at - timedelta(days=7) + timedelta(hours=1))
    _event(db, row["id"], "Too far", at + timedelta(days=10), at + timedelta(days=10, hours=1))
    body = review.build_digest(db, MONDAY)
    assert [e["title"] for e in body["calendar"]] == ["Dentist", "Trip"]
    assert body["calendar"][1]["when"] == "Fri 27 Nov – Sat 28 Nov, all day"


def test_digest_without_a_calendar_says_nothing_about_it(db):
    assert review.build_digest(db, MONDAY)["calendar"] is None
    row = store.create(db, "google", "calendar", "owner@example.com", [])
    store.set_enabled(db, row["id"], False)
    assert review.build_digest(db, MONDAY)["calendar"] is None


# --- Docker sign-in --------------------------------------------------------------------------------------------

def test_oauth_port_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("PAL_OAUTH_PORT", raising=False)
    assert google_cli.oauth_port() == 8766
    monkeypatch.setenv("PAL_OAUTH_PORT", "9001")
    assert google_cli.oauth_port() == 9001
    monkeypatch.setenv("PAL_OAUTH_PORT", "nine")
    with pytest.raises(SystemExit):
        google_cli.oauth_port()


def test_connect_in_a_container_waits_on_the_published_port(settings, monkeypatch):
    from pal.connectors import google_auth

    seen = {}

    def fake_sign_in(client, service, http, open_browser, host, port):
        seen.update(host=host, port=port)
        raise google_auth.GoogleAuthError("stop here")

    monkeypatch.setenv("PAL_CONTAINER", "1")
    monkeypatch.setenv("PAL_OAUTH_PORT", "8766")
    monkeypatch.setattr(google_auth, "load_client", lambda: google_auth.Client("c", "s"))
    monkeypatch.setattr(google_auth, "sign_in", fake_sign_in)
    with pytest.raises(SystemExit, match="stop here"):
        google_cli.cmd_connect(settings, "calendar", True, 0)
    assert seen == {"host": "0.0.0.0", "port": 8766}
