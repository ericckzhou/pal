"""Server-side sessions. This PC signs in with the owner password; paired devices use passkeys (devices.py)."""

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from uuid import UUID

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

SESSION_COOKIE = "pal_session"
_hasher = PasswordHasher()


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def set_password(conn: psycopg.Connection, password: str) -> None:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    conn.execute(
        "INSERT INTO owner (id, password_hash) VALUES (1, %s) "
        "ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash, updated_at = now()",
        (_hasher.hash(password),),
    )
    conn.execute("UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL")


def verify_password(conn: psycopg.Connection, password: str) -> bool:
    row = conn.execute("SELECT password_hash FROM owner WHERE id = 1").fetchone()
    if row is None:
        _hasher.hash(password)  # equalize timing
        return False
    try:
        return _hasher.verify(row[0], password)
    except (VerifyMismatchError, InvalidHashError):
        return False


@dataclass(frozen=True)
class Session:
    id: UUID
    device_id: UUID | None  # None = this PC, signed in with the owner password

    @property
    def is_owner_pc(self) -> bool:
        return self.device_id is None


def create_session(
    conn: psycopg.Connection, days: int, label: str | None = None, device_id: UUID | None = None
) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, label, expires_at, device_id) "
        "VALUES (%s, %s, now() + make_interval(days => %s), %s)",
        (_hash_token(token), label, days, device_id),
    )
    return token


def lookup_session(conn: psycopg.Connection, token: str | None, device_idle_days: int = 14) -> Session | None:
    """One query: the session is live, not idle too long (devices), and its device isn't revoked (S3)."""
    if not token:
        return None
    row = conn.execute(
        "UPDATE sessions s SET last_seen_at = now() WHERE token_hash = %s "
        "AND revoked_at IS NULL AND expires_at > now() "
        "AND (device_id IS NULL OR (last_seen_at > now() - make_interval(days => %s) "
        "     AND EXISTS (SELECT 1 FROM devices d WHERE d.id = s.device_id AND d.revoked_at IS NULL))) "
        "RETURNING id, device_id",
        (_hash_token(token), device_idle_days),
    ).fetchone()
    if row is None:
        return None
    if row[1] is not None:
        conn.execute("UPDATE devices SET last_seen_at = now() WHERE id = %s", (row[1],))
    return Session(row[0], row[1])


def revoke_session(conn: psycopg.Connection, session_id: UUID) -> None:
    conn.execute("UPDATE sessions SET revoked_at = now() WHERE id = %s", (session_id,))


def revoke_all(conn: psycopg.Connection) -> int:
    return conn.execute("UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL").rowcount


class LoginLimiter:
    """Lock login for `lockout_s` after `max_failures` failures within `window_s`."""

    def __init__(self, max_failures: int = 5, window_s: float = 300, lockout_s: float = 300) -> None:
        self.max_failures, self.window_s, self.lockout_s = max_failures, window_s, lockout_s
        self._failures: list[float] = []
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def locked(self) -> bool:
        return time.monotonic() < self._locked_until

    def record_failure(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures = [t for t in self._failures if now - t < self.window_s] + [now]
            if len(self._failures) >= self.max_failures:
                self._locked_until = now + self.lockout_s
                self._failures.clear()

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
