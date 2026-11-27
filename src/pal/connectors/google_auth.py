"""Signing in to Google: the desktop-app OAuth flow with PKCE, token refresh, and revoking access.

- The owner brings their own OAuth client ("Desktop app" in Google Cloud Console); its JSON is kept in the secret
  store under CLIENT_ACCOUNT. pal ships no client of its own (the repo is public).
- Permissions are asked one service at a time (include_granted_scopes): connecting Calendar asks only for
  calendar.readonly, and Gmail's restricted scope is asked for only if the owner turns Gmail on.
- Google may grant less than was asked (people can untick a box). A service is connected only if its scope was
  actually granted.
- The refresh token covers everything granted so far and is kept in the secret store under TOKEN_ACCOUNT. Google
  has no per-scope revoke: revoking ends the whole grant, so that happens when the last Google service is removed.
- The id_token comes straight from Google's token endpoint over TLS, so its payload is read without re-verifying
  the signature (Google's guidance for tokens received this way); pal only uses it to show which account it is.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .. import secret_store

CLIENT_ACCOUNT = "google-oauth-client"
TOKEN_ACCOUNT = "google-refresh-token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
IDENTITY_SCOPES = ("openid", "email")
SCOPES = {
    "calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "drive": "https://www.googleapis.com/auth/drive.readonly",
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
}
CALLBACK_TIMEOUT_S = 300
REFRESH_MARGIN_S = 60  # refresh an access token this long before Google says it expires


class GoogleAuthError(Exception):
    """Something the owner can act on; the message says what."""


class SignInExpired(GoogleAuthError):
    """Google refused the refresh token (revoked, expired after 7 days in Testing, or password changed)."""


@dataclass(frozen=True)
class Client:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class Grant:
    refresh_token: str | None
    access_token: str
    expires_in: int
    scopes: frozenset[str]
    account: str | None


# --- the owner's OAuth client ----------------------------------------------------------------------------------

def parse_client(raw: str) -> Client:
    """Accept the JSON Google Cloud Console downloads for a Desktop app client."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise GoogleAuthError("That file isn't JSON. Download the client from Google Cloud Console again.") from exc
    if "web" in data:
        raise GoogleAuthError("That's a Web application client. Create one of type \"Desktop app\" instead.")
    inner = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(inner, dict) or not inner.get("client_id") or not inner.get("client_secret"):
        raise GoogleAuthError("That doesn't look like a Google OAuth client file (no \"installed\" client id and secret).")
    return Client(str(inner["client_id"]), str(inner["client_secret"]))


def store_client(raw: str) -> Client:
    client = parse_client(raw)
    secret_store.put(CLIENT_ACCOUNT, json.dumps({"installed": {"client_id": client.client_id,
                                                               "client_secret": client.client_secret}}))
    return client


def load_client() -> Client | None:
    raw = secret_store.get(CLIENT_ACCOUNT)
    return parse_client(raw) if raw else None


# --- the sign-in flow ------------------------------------------------------------------------------------------

def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client: Client, service: str, redirect_uri: str, state: str, challenge: str) -> str:
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join((*IDENTITY_SCOPES, SCOPES[service])),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",  # a refresh token, so pal can sync without the owner present
        "prompt": "consent",  # Google only returns a refresh token on consent
        "include_granted_scopes": "true",  # keep what was granted for other services
    }
    return f"{AUTH_URL}?{urlencode(params)}"


class _Callback(BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    done = threading.Event()

    def do_GET(self) -> None:  # noqa: N802 (http.server's name)
        query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = query
        ok = "code" in query
        body = ("<h2>pal is signed in.</h2><p>You can close this tab and go back to the terminal.</p>" if ok else
                "<h2>Sign-in was cancelled.</h2><p>Go back to the terminal for details.</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())
        type(self).done.set()

    def log_message(self, *args) -> None:  # keep the code out of the terminal
        pass


def _account_from_id_token(id_token: str | None) -> str | None:
    if not id_token or id_token.count(".") != 2:
        return None
    payload = id_token.split(".")[1]
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    return data.get("email") if isinstance(data, dict) else None


def _grant(data: dict, refresh_token: str | None = None) -> Grant:
    return Grant(
        refresh_token=data.get("refresh_token") or refresh_token,
        access_token=data["access_token"],
        expires_in=int(data.get("expires_in", 3600)),
        scopes=frozenset(str(data.get("scope", "")).split()),
        account=_account_from_id_token(data.get("id_token")),
    )


def _token_request(http: httpx.Client, form: dict) -> dict:
    try:
        resp = http.post(TOKEN_URL, data=form, timeout=20.0)
    except httpx.HTTPError as exc:
        raise GoogleAuthError(f"Couldn't reach Google ({type(exc).__name__}). Check the internet connection.") from exc
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code == 400 and data.get("error") == "invalid_grant":
        raise SignInExpired("Google no longer accepts pal's sign-in. Sign in again on the Connections page, or run: pal connect google <service>")
    if resp.status_code != 200 or "access_token" not in data:
        raise GoogleAuthError(f"Google refused the request: {data.get('error_description') or data.get('error') or resp.status_code}")
    return data


def sign_in(
    client: Client, service: str, http: httpx.Client, open_browser: Callable[[str], object],
    host: str = "127.0.0.1", port: int = 0, timeout: float = CALLBACK_TIMEOUT_S,
) -> Grant:
    """Run the browser sign-in for one service and return what Google granted.

    `open_browser` gets the URL (webbrowser.open, or print it with --no-browser). Raises GoogleAuthError when the
    owner cancels, the state doesn't match, time runs out, or the service's scope wasn't granted.
    """
    if service not in SCOPES:
        raise GoogleAuthError(f"Unknown Google service {service!r}; pick one of {', '.join(SCOPES)}.")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    class handler(_Callback):  # noqa: N801 (a fresh class per sign-in, so its result and event are its own)
        result: dict[str, str] = {}
        done = threading.Event()

    server = HTTPServer((host, port), handler)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        open_browser(auth_url(client, service, redirect_uri, state, challenge))
        if not handler.done.wait(timeout):
            raise GoogleAuthError("No answer from Google within 5 minutes. Run the command again.")
    finally:
        server.shutdown()
        server.server_close()
    result = handler.result
    if result.get("state") != state:
        raise GoogleAuthError("The sign-in answer didn't match this request, so pal ignored it. Try again.")
    return finish(client, service, result, verifier, redirect_uri, http)


def finish(client: Client, service: str, result: dict[str, str], verifier: str, redirect_uri: str,
           http: httpx.Client) -> Grant:
    """Turn Google's answer (state already checked) into a grant, refusing a cancel or a missing scope."""
    if "error" in result:
        raise GoogleAuthError("Sign-in was cancelled." if result["error"] == "access_denied"
                              else f"Google returned an error: {result['error']}")
    if not result.get("code"):
        raise GoogleAuthError("Google's answer had no sign-in code. Try again.")
    data = _token_request(http, {
        "code": result["code"], "client_id": client.client_id, "client_secret": client.client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
    })
    grant = _grant(data)
    if SCOPES[service] not in grant.scopes:
        raise GoogleAuthError(f"Google didn't grant {service} access (the box was left unticked). Nothing was connected.")
    if not grant.refresh_token:
        raise GoogleAuthError("Google didn't return a refresh token. Remove pal at myaccount.google.com/permissions and try again.")
    return grant


# --- tokens while pal runs -------------------------------------------------------------------------------------

class Tokens:
    """Hands out a fresh access token, refreshing it from the stored refresh token when it is about to expire."""

    def __init__(self, http: httpx.Client, client: Client | None = None, refresh_token: str | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._http = http
        self._client = client
        self._refresh_token = refresh_token
        self._clock = clock
        self._access: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def access_token(self) -> str:
        with self._lock:
            if self._access and self._clock() < self._expires_at - REFRESH_MARGIN_S:
                return self._access
            client = self._client or load_client()
            refresh = self._refresh_token or secret_store.get(TOKEN_ACCOUNT)
            if client is None or refresh is None:
                raise SignInExpired("pal isn't signed in to Google. Sign in again on the Connections page, or run: pal connect google <service>")
            grant = _grant(_token_request(self._http, {
                "client_id": client.client_id, "client_secret": client.client_secret,
                "refresh_token": refresh, "grant_type": "refresh_token",
            }), refresh)
            self._access, self._expires_at = grant.access_token, self._clock() + grant.expires_in
            return self._access

    def forget(self) -> None:
        """Drop the cached access token (after a 401, or once the grant is revoked)."""
        with self._lock:
            self._access, self._expires_at = None, 0.0


def revoke(http: httpx.Client, token: str) -> bool:
    """Ask Google to end pal's access. False if Google couldn't be reached or refused (the token is deleted anyway)."""
    try:
        resp = http.post(REVOKE_URL, data={"token": token}, timeout=20.0)
    except httpx.HTTPError:
        return False
    return resp.status_code == 200 or (resp.status_code == 400 and "invalid_token" in resp.text)
