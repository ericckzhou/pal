"""A small read-only Google API client shared by the Calendar, Drive and Gmail connectors.

GET only. A 401 drops the cached access token and retries once; Google's common refusals become messages the
owner can act on (shown on the Connections page and in `pal connections`).
"""

import threading

import httpx

from .google_auth import GoogleAuthError, Tokens

TIMEOUT = httpx.Timeout(20.0, read=60.0)
_shared: Tokens | None = None
_shared_lock = threading.Lock()


class GoogleAPIError(Exception):
    pass


_client: httpx.Client | None = None


def shared_client() -> httpx.Client:
    """One HTTP client per pal process for every Google request (connections are reused, nothing leaks per sync)."""
    global _client
    with _shared_lock:
        if _client is None:
            _client = httpx.Client(timeout=TIMEOUT)
        return _client


def shared_tokens() -> Tokens:
    """One token cache per pal process, so the connectors don't each refresh their own."""
    global _shared
    client = shared_client()
    with _shared_lock:
        if _shared is None:
            _shared = Tokens(client)
        return _shared


def _explain(resp: httpx.Response, what: str) -> str:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    reasons = {e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)} if isinstance(err, dict) else set()
    message = err.get("message", "") if isinstance(err, dict) else ""
    if resp.status_code == 403 and ("accessNotConfigured" in reasons or "SERVICE_DISABLED" in str(err)):
        return f"The {what} API isn't enabled in your Google Cloud project. Enable it, then sync again."
    if resp.status_code == 403 and reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return f"pal wasn't granted {what} access. Run: pal connect google {what.split()[-1].lower()}"
    if resp.status_code in (403, 429) and reasons & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}:
        return f"Google is limiting requests for {what} right now; pal will try again at the next sync."
    if resp.status_code == 404:
        return f"Google couldn't find that in {what} (it may have been deleted or unshared)."
    return f"{what} request failed ({resp.status_code}{': ' + message if message else ''})"


class GoogleAPI:
    def __init__(self, tokens: Tokens, http: httpx.Client, what: str) -> None:
        self._tokens = tokens
        self._http = http
        self._what = what  # "Google Calendar", used in messages

    def get(self, url: str, params: dict | None = None) -> dict:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._tokens.access_token()}"}
            try:
                resp = self._http.get(url, params=params, headers=headers, timeout=TIMEOUT)
            except httpx.HTTPError as exc:
                raise GoogleAPIError(f"Couldn't reach {self._what} ({type(exc).__name__}).") from exc
            if resp.status_code == 401 and attempt == 1:
                self._tokens.forget()
                continue
            if resp.status_code == 401:
                raise GoogleAuthError("Google rejected pal's sign-in. Run: pal connect google <service>")
            if resp.status_code != 200:
                raise GoogleAPIError(_explain(resp, self._what))
            return resp.json()
        raise GoogleAPIError(f"{self._what} request failed")  # not reached
