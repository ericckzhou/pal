"""Google sign-in: the OAuth client file, the PKCE browser flow, partial consent, refresh, one account, disconnect."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from pal import egress
from pal.connectors import google, google_auth, hosts, store

CLIENT = google_auth.Client("cid.apps.googleusercontent.com", "csecret")
CAL = google_auth.SCOPES["calendar"]


def _id_token(email: str) -> str:
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{part({'alg': 'RS256'})}.{part({'email': email})}.sig"


class FakeGoogle:
    """Google's token and revoke endpoints. Records each form it receives."""

    def __init__(self, scopes: str = f"openid email {CAL}", email: str = "owner@example.com",
                 refresh: str | None = "r-1", refuse_refresh: bool = False) -> None:
        self.scopes, self.email, self.refresh, self.refuse_refresh = scopes, email, refresh, refuse_refresh
        self.forms: list[dict] = []
        self.revoked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if str(request.url).startswith(google_auth.REVOKE_URL):
            self.revoked.append(form["token"])
            return httpx.Response(200)
        self.forms.append(form)
        if form["grant_type"] == "refresh_token" and self.refuse_refresh:
            return httpx.Response(400, json={"error": "invalid_grant"})
        body = {"access_token": f"a-{len(self.forms)}", "expires_in": 3600, "scope": self.scopes,
                "id_token": _id_token(self.email)}
        if form["grant_type"] == "authorization_code" and self.refresh:
            body["refresh_token"] = self.refresh
        return httpx.Response(200, json=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def browser(answer: dict | None = None, wrong_state: bool = False):
    """Stands in for the owner's browser: reads the sign-in URL and sends Google's redirect to pal's callback."""
    seen: dict = {}

    def open_browser(url: str) -> None:
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)
        params = answer if answer is not None else {"code": "auth-code"}
        params = {**params, "state": "forged" if wrong_state else q["state"]}
        with httpx.Client() as c:
            c.get(q["redirect_uri"], params=params)

    open_browser.seen = seen  # type: ignore[attr-defined]
    return open_browser


# --- the client file -------------------------------------------------------------------------------------------

def test_desktop_client_file_is_accepted_and_stored(google_secrets):
    raw = json.dumps({"installed": {"client_id": "x.apps", "client_secret": "s", "redirect_uris": ["http://localhost"]}})
    assert google_auth.store_client(raw) == google_auth.Client("x.apps", "s")
    assert google_auth.load_client() == google_auth.Client("x.apps", "s")
    assert "google-oauth-client" in google_secrets


@pytest.mark.parametrize("raw, words", [
    ("not json", "isn't JSON"),
    (json.dumps({"web": {"client_id": "a", "client_secret": "b"}}), "Desktop app"),
    (json.dumps({"installed": {"client_id": "a"}}), "doesn't look like"),
])
def test_wrong_client_files_are_refused_with_a_reason(raw, words):
    with pytest.raises(google_auth.GoogleAuthError, match=words):
        google_auth.parse_client(raw)


# --- the browser flow ------------------------------------------------------------------------------------------

def test_sign_in_asks_for_one_service_with_pkce_and_returns_the_grant():
    fake = FakeGoogle()
    open_browser = browser()
    grant = google_auth.sign_in(CLIENT, "calendar", fake.client(), open_browser, timeout=10)
    q = open_browser.seen
    assert q["scope"].split() == ["openid", "email", CAL]  # no Drive or Gmail
    assert q["access_type"] == "offline" and q["include_granted_scopes"] == "true"
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"].startswith("http://127.0.0.1:")
    form = fake.forms[0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert challenge == q["code_challenge"] and form["code"] == "auth-code"
    assert grant.account == "owner@example.com" and grant.refresh_token == "r-1" and CAL in grant.scopes


def test_unticked_permission_connects_nothing():
    fake = FakeGoogle(scopes="openid email")
    with pytest.raises(google_auth.GoogleAuthError, match="didn't grant calendar"):
        google_auth.sign_in(CLIENT, "calendar", fake.client(), browser(), timeout=10)


def test_cancelled_sign_in_is_reported():
    with pytest.raises(google_auth.GoogleAuthError, match="cancelled"):
        google_auth.sign_in(CLIENT, "drive", FakeGoogle().client(), browser({"error": "access_denied"}), timeout=10)


def test_answer_with_the_wrong_state_is_ignored():
    fake = FakeGoogle()
    with pytest.raises(google_auth.GoogleAuthError, match="didn't match"):
        google_auth.sign_in(CLIENT, "calendar", fake.client(), browser(wrong_state=True), timeout=10)
    assert fake.forms == []  # the code was never exchanged


def test_no_answer_times_out():
    with pytest.raises(google_auth.GoogleAuthError, match="No answer"):
        google_auth.sign_in(CLIENT, "calendar", FakeGoogle().client(), lambda url: None, timeout=0.2)


def test_missing_refresh_token_is_an_error():
    with pytest.raises(google_auth.GoogleAuthError, match="refresh token"):
        google_auth.sign_in(CLIENT, "calendar", FakeGoogle(refresh=None).client(), browser(), timeout=10)


# --- tokens while pal runs -------------------------------------------------------------------------------------

def test_access_token_is_cached_and_refreshed_near_expiry():
    fake = FakeGoogle()
    now = [0.0]
    tokens = google_auth.Tokens(fake.client(), CLIENT, "r-1", clock=lambda: now[0])
    assert tokens.access_token() == "a-1"
    now[0] = 3000
    assert tokens.access_token() == "a-1"  # still well inside the hour
    now[0] = 3550
    assert tokens.access_token() == "a-2"  # within a minute of expiry
    assert fake.forms[-1] == {"client_id": CLIENT.client_id, "client_secret": "csecret", "refresh_token": "r-1",
                              "grant_type": "refresh_token"}


def test_refused_refresh_token_says_sign_in_again():
    tokens = google_auth.Tokens(FakeGoogle(refuse_refresh=True).client(), CLIENT, "r-1")
    with pytest.raises(google_auth.SignInExpired, match="pal connect google"):
        tokens.access_token()


def test_tokens_read_the_stored_client_and_refresh_token(google_secrets):
    google_secrets["google-oauth-client"] = json.dumps({"installed": {"client_id": "c", "client_secret": "s"}})
    google_secrets["google-refresh-token"] = "stored"
    fake = FakeGoogle()
    assert google_auth.Tokens(fake.client()).access_token() == "a-1"
    assert fake.forms[0]["refresh_token"] == "stored"


def test_not_signed_in_at_all():
    with pytest.raises(google_auth.SignInExpired, match="isn't signed in"):
        google_auth.Tokens(FakeGoogle().client()).access_token()


# --- saving a grant, one account, disconnect -------------------------------------------------------------------

def _grant(email="owner@example.com", scopes=("openid", "email", CAL), refresh="r-1"):
    return google_auth.Grant(refresh, "a", 3600, frozenset(scopes), email)


def test_saving_a_grant_connects_the_service_and_stores_the_token(db, google_secrets):
    with FakeGoogle().client() as http:
        row = google.save_grant(db, "calendar", _grant(), http)
    assert row["service"] == "calendar" and row["account"] == "owner@example.com" and CAL in row["scopes"]
    assert google_secrets["google-refresh-token"] == "r-1"


def test_a_second_service_updates_every_row_with_the_full_grant(db, google_secrets):
    drive = google_auth.SCOPES["drive"]
    with FakeGoogle().client() as http:
        google.save_grant(db, "calendar", _grant(), http)
        google.save_grant(db, "drive", _grant(scopes=("openid", "email", CAL, drive), refresh="r-2"), http)
    scopes = {r["service"]: set(r["scopes"]) for r in store.list_all(db)}
    assert scopes["calendar"] == scopes["drive"] == {"openid", "email", CAL, drive}
    assert google_secrets["google-refresh-token"] == "r-2"


def test_a_different_account_is_refused_and_its_grant_revoked(db, google_secrets):
    fake = FakeGoogle()
    with fake.client() as http:
        google.save_grant(db, "calendar", _grant(), http)
        with pytest.raises(google_auth.GoogleAuthError, match="one Google account"):
            google.save_grant(db, "drive", _grant(email="other@example.com", refresh="stray"), http)
    assert fake.revoked == ["stray"]
    assert google_secrets["google-refresh-token"] == "r-1"
    assert [r["service"] for r in store.list_all(db)] == ["calendar"]


def test_disconnecting_one_of_two_keeps_the_token_and_says_why(db, google_secrets):
    fake = FakeGoogle()
    with fake.client() as http:
        cal = google.save_grant(db, "calendar", _grant(), http)
        google.save_grant(db, "drive", _grant(), http)
        result = google.disconnect(db, cal["id"], http)
    assert result["revoked"] is False and "last Google service" in result["note"]
    assert fake.revoked == [] and google_secrets["google-refresh-token"] == "r-1"


def test_disconnecting_the_last_one_revokes_and_deletes_the_token(db, google_secrets):
    fake = FakeGoogle()
    with fake.client() as http:
        cal = google.save_grant(db, "calendar", _grant(), http)
        result = google.disconnect(db, cal["id"], http)
    assert result == {"documents_removed": 0, "revoked": True, "note": None}
    assert fake.revoked == ["r-1"] and "google-refresh-token" not in google_secrets


def test_revoke_failure_still_deletes_the_token_and_points_to_google(db, google_secrets):
    def down(request):
        raise httpx.ConnectError("offline")

    with FakeGoogle().client() as ok_http:
        cal = google.save_grant(db, "calendar", _grant(), ok_http)
    with httpx.Client(transport=httpx.MockTransport(down)) as http:
        result = google.disconnect(db, cal["id"], http)
    assert result["revoked"] is False and "myaccount.google.com" in result["note"]
    assert "google-refresh-token" not in google_secrets


def test_sync_loop_lets_newly_connected_hosts_through(db, settings, monkeypatch):
    from psycopg_pool import ConnectionPool

    from pal.config import dsn
    from pal.connectors import sync

    allowed: list[tuple] = []
    monkeypatch.setattr(egress, "allow", lambda h: allowed.append(h))
    store.create(db, "google", "calendar", None, [CAL])
    with ConnectionPool(dsn(settings), min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        sync.sync_all(pool)
    assert allowed == [hosts.for_services([("google", "calendar")])]


# --- command line ----------------------------------------------------------------------------------------------

def test_cli_connect_needs_a_client_first(settings, db):
    from pal.connectors import google_cli

    with pytest.raises(SystemExit, match="google-client --set"):
        google_cli.cmd_connect(settings, "calendar", True, 0)


def test_cli_client_connect_list_and_disconnect(settings, db, tmp_path, monkeypatch, capsys, google_secrets):
    from pal.connectors import google_cli

    path = tmp_path / "client.json"
    path.write_text(json.dumps({"installed": {"client_id": "abc.apps", "client_secret": "s"}}))
    google_cli.cmd_google_client(str(path), False)
    monkeypatch.setattr(google_auth, "sign_in", lambda *a, **k: _grant())
    monkeypatch.setattr(google_auth, "revoke", lambda http, token: True)
    google_cli.cmd_connect(settings, "calendar", True, 0)
    google_cli.cmd_connections(settings)
    google_cli.cmd_disconnect(settings, "all")
    out = capsys.readouterr().out
    assert "read your Google Calendar events (read-only)" in out
    assert "Connected calendar for owner@example.com. Granted: calendar.readonly." in out
    assert "google/calendar" in out and "never" in out
    assert "Google access ended" in out
    assert store.list_all(db) == [] and "google-refresh-token" not in google_secrets
