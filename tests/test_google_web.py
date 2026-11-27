"""Signing in to Google from the Connections page: PC only, state single-use, outcome shown once."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from pal.connectors import google_auth, google_web, store

from .test_google_auth import CAL, CLIENT, FakeGoogle


@pytest.fixture
def google_client():
    google_auth.store_client(json.dumps({"installed": {"client_id": CLIENT.client_id,
                                                       "client_secret": CLIENT.client_secret}}))


def _start(c, service="calendar") -> dict:
    resp = c.post("/api/connections/google/start", json={"service": service})
    assert resp.status_code == 200, resp.text
    return {k: v[0] for k, v in parse_qs(urlparse(resp.json()["url"]).query).items()}


def _fake(c, fake: FakeGoogle) -> FakeGoogle:
    c.app.state.google_http = fake.client
    return fake


def test_start_points_google_back_at_pals_own_address(authed, google_client):
    q = _start(authed)
    assert q["redirect_uri"] == "http://testserver" + google_web.CALLBACK_PATH
    assert CAL in q["scope"].split() and q["code_challenge_method"] == "S256" and q["state"]


def test_callback_connects_and_reports_once(authed, db, google_client):
    fake = _fake(authed, FakeGoogle())
    q = _start(authed)
    resp = authed.get(google_web.CALLBACK_PATH, params={"code": "c1", "state": q["state"]}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/?view=connections"
    assert fake.forms[0]["redirect_uri"] == q["redirect_uri"] and fake.forms[0]["code_verifier"]
    assert [(r["service"], r["account"]) for r in store.list_all(db)] == [("calendar", "owner@example.com")]
    first = authed.get("/api/connections").json()["signin"]
    assert first["ok"] and "Connected Google Calendar for owner@example.com" in first["message"]
    assert authed.get("/api/connections").json()["signin"] is None


def test_state_is_single_use(authed, db, google_client):
    _fake(authed, FakeGoogle())
    q = _start(authed)
    authed.get(google_web.CALLBACK_PATH, params={"code": "c1", "state": q["state"]}, follow_redirects=False)
    authed.get("/api/connections")  # read the first outcome
    authed.get(google_web.CALLBACK_PATH, params={"code": "c2", "state": q["state"]}, follow_redirects=False)
    out = authed.get("/api/connections").json()["signin"]
    assert not out["ok"] and "expired or was already used" in out["message"]


def test_forged_state_connects_nothing(client, db, google_client):
    fake = _fake(client, FakeGoogle())
    client.get(google_web.CALLBACK_PATH, params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert store.list_all(db) == [] and fake.forms == []


def test_cancel_and_unticked_box_connect_nothing(authed, db, google_client):
    _fake(authed, FakeGoogle(scopes="openid email"))
    q = _start(authed)
    authed.get(google_web.CALLBACK_PATH, params={"error": "access_denied", "state": q["state"]}, follow_redirects=False)
    assert authed.get("/api/connections").json()["signin"]["message"] == "Sign-in was cancelled."
    q = _start(authed)
    authed.get(google_web.CALLBACK_PATH, params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert "didn't grant calendar" in authed.get("/api/connections").json()["signin"]["message"]
    assert store.list_all(db) == []


def test_start_needs_a_stored_client(authed):
    resp = authed.post("/api/connections/google/start", json={"service": "calendar"})
    assert resp.status_code == 409


def test_start_rejects_unknown_service(authed, google_client):
    assert authed.post("/api/connections/google/start", json={"service": "photos"}).status_code == 422


def test_phone_cannot_start_or_finish(remote, db, google_client):
    assert remote.post("/api/connections/google/start", json={"service": "calendar"}).status_code in (401, 403)
    resp = remote.get(google_web.CALLBACK_PATH, params={"code": "c", "state": "x"}, follow_redirects=False)
    assert resp.headers["location"] == "/" and store.list_all(db) == []


def test_proxied_request_at_the_pc_cannot_start(authed, google_client):
    resp = authed.post("/api/connections/google/start", json={"service": "calendar"},
                       headers={"X-Forwarded-For": "100.64.0.9"})
    assert resp.status_code == 403


def test_pending_sign_ins_expire_and_are_bounded():
    now = [0.0]
    s = google_web.SignIns(clock=lambda: now[0])
    states = [parse_qs(urlparse(s.start(CLIENT, "calendar", "http://x")).query)["state"][0]
              for _ in range(google_web.PENDING_MAX + 1)]
    assert s.take(states[0]) is None  # the oldest was dropped
    now[0] = google_web.PENDING_TTL_S + 1
    assert s.take(states[-1]) is None  # expired
    assert s.take(None) is None
