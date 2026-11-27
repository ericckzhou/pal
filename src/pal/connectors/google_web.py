"""Signing in to Google from the Connections page, on the PC only.

The browser goes to Google and comes back to pal's own loopback address (a Desktop OAuth client accepts any
loopback redirect), so no extra port or terminal is needed. Google's return trip is a cross-site navigation, so the
SameSite=strict session cookie isn't sent with it: the callback is trusted because it reaches loopback directly and
carries a state value pal handed out moments earlier (single use, 10 minutes), with PKCE on top.
"""

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import google_auth

PENDING_TTL_S = 600
PENDING_MAX = 8  # a handful of open sign-in tabs at most; the oldest is dropped beyond this
CALLBACK_PATH = "/api/connections/google/callback"


@dataclass(frozen=True)
class Pending:
    service: str
    verifier: str
    redirect_uri: str
    created: float


class SignIns:
    """Sign-ins started from the page and waiting for Google, plus the outcome of the last one for the page."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pending: dict[str, Pending] = {}
        self._outcome: dict | None = None
        self._lock = threading.Lock()

    def start(self, client: google_auth.Client, service: str, origin: str) -> str:
        """Return the Google sign-in URL for `service`; Google sends the browser back to `origin` + CALLBACK_PATH."""
        if service not in google_auth.SCOPES:
            raise google_auth.GoogleAuthError(f"Unknown Google service {service!r}.")
        verifier, challenge = google_auth.pkce_pair()
        state = secrets.token_urlsafe(24)
        redirect_uri = origin + CALLBACK_PATH
        with self._lock:
            self._expire()
            while len(self._pending) >= PENDING_MAX:
                self._pending.pop(next(iter(self._pending)))
            self._pending[state] = Pending(service, verifier, redirect_uri, self._clock())
        return google_auth.auth_url(client, service, redirect_uri, state, challenge)

    def take(self, state: str | None) -> Pending | None:
        """The sign-in this state belongs to, removed so it can't be used twice. None if unknown or expired."""
        with self._lock:
            self._expire()
            return self._pending.pop(state, None) if state else None

    def record(self, ok: bool, message: str) -> None:
        with self._lock:
            self._outcome = {"ok": ok, "message": message}

    def pop_outcome(self) -> dict | None:
        """The last sign-in's result, shown once on the Connections page."""
        with self._lock:
            outcome, self._outcome = self._outcome, None
            return outcome

    def _expire(self) -> None:
        now = self._clock()
        for state in [s for s, p in self._pending.items() if now - p.created > PENDING_TTL_S]:
            del self._pending[state]
