"""StarcallOS on the Connections page: save the server path and scopes (PC browser only), Test, pause, remove, and
the card the page shows. The client is a fake; nothing here starts Node."""

import json

import pytest

from pal import starcall
from pal.connectors.starcall import StarcallConfig

from .test_starcall_chat import FakeStarcall

PATH = r"C:\sc\packages\mcp-server\dist\index.js"
URL = "/api/connections/starcall"


class CheckableStarcall(FakeStarcall):
    """FakeStarcall plus the Connections page's check() and status()."""

    outcome: dict = {"state": "running", "error": None, "last_ok_at": None}

    def __init__(self, cfg: StarcallConfig) -> None:
        super().__init__(cfg)
        self.checks = 0

    def check(self) -> dict:
        self.checks += 1
        return self.status()

    def status(self) -> dict:
        return dict(self.outcome)


@pytest.fixture
def made(monkeypatch):
    out: list[CheckableStarcall] = []

    def make(cfg: StarcallConfig) -> CheckableStarcall:
        out.append(CheckableStarcall(cfg))
        return out[-1]

    monkeypatch.setattr(starcall, "make_client", make)
    monkeypatch.setattr(CheckableStarcall, "outcome", {"state": "running", "error": None, "last_ok_at": None})
    return out


def _row(db):
    return db.execute("SELECT enabled, config, last_ok_at, last_error FROM connections WHERE provider = 'starcall'"
                      ).fetchone()


def test_the_card_before_setup(authed):
    card = authed.get("/api/connections").json()["starcall"]
    assert card["native"] is True and card["reason"] is None and card["connection"] is None
    assert [s["name"] for s in card["scopes"]] == ["passages", "concepts"]
    assert card["meaning_search"] is True  # pal's Ollama is on loopback, so the server gets --embed-url


def test_save_strips_quotes_keeps_passages_on_and_makes_chats_able_to_use_it(authed, db):
    card = authed.put(URL, json={"server_path": f'  "{PATH}" ', "scopes": ["concepts"]}).json()
    assert card["connection"]["server_path"] == PATH and card["connection"]["enabled"] is True
    assert card["connection"]["scopes"] == ["passages", "concepts"]
    assert _row(db)[1] == {"server_path": PATH, "scopes": ["passages", "concepts"]}
    conv = authed.post("/api/conversations").json()["id"]
    assert authed.get(f"/api/conversations/{conv}").json()["starcall_available"] is True


@pytest.mark.parametrize("path", ["", "packages/mcp-server/dist/index.js", r"C:\sc\packages\mcp-server\index.js",
                                  r"C:\sc\packages\mcp-server\dist\server.js", "C:" + "x" * 1001])
def test_save_refuses_a_path_that_cannot_be_the_server(authed, db, path):
    resp = authed.put(URL, json={"server_path": path or " "})
    assert resp.status_code == 422 and _row(db) is None


def test_save_refuses_unknown_scopes(authed, db):
    resp = authed.put(URL, json={"server_path": PATH, "scopes": ["passages", "notes"]})
    assert resp.status_code == 422 and "notes" in resp.text and _row(db) is None


def test_save_is_only_from_a_browser_on_the_pc(authed, db):
    resp = authed.put(URL, json={"server_path": PATH}, headers={"X-Forwarded-For": "100.64.0.9"})
    assert resp.status_code == 403 and _row(db) is None


def test_the_routes_need_the_owner(remote):
    assert remote.put(URL, json={"server_path": PATH}).status_code in (401, 403)
    assert remote.post(f"{URL}/test").status_code in (401, 403)


def test_resaving_the_same_settings_keeps_the_last_outcome_and_a_change_clears_it(authed, db):
    authed.put(URL, json={"server_path": PATH})
    db.execute("UPDATE connections SET last_ok_at = now(), last_error = 'timeout: slow' WHERE provider = 'starcall'")
    authed.put(URL, json={"server_path": PATH, "scopes": ["passages"]})
    assert _row(db)[2] is not None and _row(db)[3] == "timeout: slow"
    authed.put(URL, json={"server_path": PATH, "scopes": ["passages", "concepts"]})
    assert _row(db)[2] is None and _row(db)[3] is None


def test_test_starts_the_client_and_records_success(authed, db, made):
    authed.put(URL, json={"server_path": PATH, "scopes": ["passages", "concepts"]})
    body = authed.post(f"{URL}/test").json()
    assert body["ok"] is True and body["status"]["state"] == "running"
    assert made[0].cfg.scopes == ("passages", "concepts") and made[0].checks == 1
    assert made[0].cfg.embed_url == "http://127.0.0.1:11434/v1"
    assert _row(db)[2] is not None and _row(db)[3] is None
    assert body["card"]["runtime"]["state"] == "running"
    assert authed.get("/api/connections").json()["starcall"]["runtime"]["state"] == "running"


def test_test_records_why_it_failed(authed, db, made, monkeypatch):
    monkeypatch.setattr(CheckableStarcall, "outcome", {
        "state": "cooldown", "retry_in": 10.0, "last_ok_at": None,
        "error": {"kind": "server_missing", "message": f"No StarcallOS MCP server at {PATH}."}})
    authed.put(URL, json={"server_path": PATH})
    body = authed.post(f"{URL}/test").json()
    assert body["ok"] is False and body["status"]["error"]["kind"] == "server_missing"
    assert _row(db)[3] == f"server_missing: No StarcallOS MCP server at {PATH}."
    assert body["card"]["connection"]["last_error"].startswith("server_missing")


def test_test_without_a_saved_path(authed, made):
    resp = authed.post(f"{URL}/test")
    assert resp.status_code == 409 and "Save the server path" in resp.json()["detail"] and made == []


def test_testing_a_paused_connection_closes_the_client_again(authed, db, made):
    authed.put(URL, json={"server_path": PATH})
    authed.patch(URL, json={"enabled": False})
    assert authed.post(f"{URL}/test").json()["ok"] is True
    assert made[0].closed and getattr(authed.app.state, "starcall", None) is None


def test_docker_shows_why_and_never_starts_anything(authed, db, made, monkeypatch):
    db.execute("INSERT INTO connections (provider, service, config) VALUES ('starcall', 'library', %s)",
               (json.dumps({"server_path": PATH}),))
    monkeypatch.setenv("PAL_CONTAINER", "1")
    card = authed.get("/api/connections").json()["starcall"]
    assert card["native"] is False and card["reason"] == starcall.DOCKER_REASON
    resp = authed.post(f"{URL}/test")
    assert resp.status_code == 409 and starcall.DOCKER_REASON in resp.json()["detail"] and made == []


def test_pause_stops_the_server_and_chats_stop_using_it(authed, db, made):
    authed.put(URL, json={"server_path": PATH})
    authed.post(f"{URL}/test")
    conv = authed.post("/api/conversations").json()["id"]
    card = authed.patch(URL, json={"enabled": False}).json()
    assert card["connection"]["enabled"] is False and made[0].closed
    assert authed.get(f"/api/conversations/{conv}").json()["starcall_available"] is False
    assert authed.patch(URL, json={"enabled": True}).json()["connection"]["enabled"] is True


def test_remove_forgets_it_stops_the_server_and_keeps_each_chats_switch(authed, db, made):
    authed.put(URL, json={"server_path": PATH})
    authed.post(f"{URL}/test")
    conv = authed.post("/api/conversations").json()["id"]
    authed.patch(f"/api/conversations/{conv}", json={"starcall": True})
    card = authed.delete(URL).json()
    assert card["connection"] is None and _row(db) is None and made[0].closed
    assert authed.get(f"/api/conversations/{conv}").json()["starcall"] is True
    assert authed.delete(URL).status_code == 404 and authed.patch(URL, json={"enabled": True}).status_code == 404


def test_google_routes_leave_the_starcall_row_alone(authed, db):
    authed.put(URL, json={"server_path": PATH})
    row_id = db.execute("SELECT id FROM connections WHERE provider = 'starcall'").fetchone()[0]
    assert authed.delete(f"/api/connections/{row_id}").status_code == 404
    assert authed.patch(f"/api/connections/{row_id}", json={"enabled": False}).status_code == 404
    assert authed.post(f"/api/connections/{row_id}/sync").status_code == 404
    assert _row(db)[0] is True
