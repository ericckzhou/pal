"""Paired devices (S3): one-time pairing codes made at this PC, a passkey per device, instant revocation.

Flow: the owner makes a pairing code at the PC; the phone opens pal over Tailscale, enters the code and creates a
passkey, which becomes a device with its own session. Later sign-ins use that passkey. Revoking a device deletes its
passkeys and ends its sessions; its next request gets 401.
"""

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from uuid import UUID

import psycopg
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
CODE_LEN = 8
CODE_MINUTES = 10
KINDS = ("phone", "laptop", "tablet", "other")
CEREMONY_TTL_S = 300


class DeviceError(Exception):
    """A pairing or passkey step failed; the message is safe to show."""


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def _code_hash(code: str) -> bytes:
    return hashlib.sha256(normalize_code(code).encode()).digest()


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def create_pairing_code(conn: psycopg.Connection, name: str, kind: str) -> dict:
    name = name.strip()
    if not 1 <= len(name) <= 60:
        raise DeviceError("Give the device a name (up to 60 characters).")
    if kind not in KINDS:
        raise DeviceError(f"Kind must be one of: {', '.join(KINDS)}.")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    expires = conn.execute(
        "INSERT INTO pairing_codes (code_hash, device_name, kind, expires_at) "
        "VALUES (%s, %s, %s, now() + make_interval(mins => %s)) RETURNING expires_at",
        (_code_hash(code), name, kind, CODE_MINUTES),
    ).fetchone()[0]
    conn.execute("DELETE FROM pairing_codes WHERE expires_at < now() - interval '1 day'")
    return {"code": format_code(code), "raw": code, "name": name, "kind": kind, "expires_at": expires.isoformat()}


def check_pairing_code(conn: psycopg.Connection, code: str) -> tuple[bytes, str] | None:
    """(code hash, device name) when the code is live and unused."""
    if len(normalize_code(code)) != CODE_LEN:
        return None
    h = _code_hash(code)
    row = conn.execute(
        "SELECT device_name FROM pairing_codes WHERE code_hash = %s AND used_at IS NULL AND expires_at > now()", (h,)
    ).fetchone()
    return (h, row[0]) if row else None


def list_devices(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT d.id, d.name, d.kind, d.created_at, d.last_seen_at, d.revoked_at, "
        "       (SELECT count(*) FROM passkeys p WHERE p.device_id = d.id) "
        "FROM devices d ORDER BY d.revoked_at IS NOT NULL, d.created_at DESC"
    ).fetchall()
    keys = ("id", "name", "kind", "created_at", "last_seen_at", "revoked_at", "passkeys")
    out = []
    for row in rows:
        d = dict(zip(keys, row))
        for k in ("created_at", "last_seen_at", "revoked_at"):
            d[k] = d[k].isoformat() if d[k] else None
        d["id"] = str(d["id"])
        out.append(d)
    return out


def device_name(conn: psycopg.Connection, device_id: UUID) -> str | None:
    row = conn.execute("SELECT name FROM devices WHERE id = %s", (device_id,)).fetchone()
    return row[0] if row else None


def revoke_device(conn: psycopg.Connection, device_id: UUID) -> bool:
    with conn.transaction():
        row = conn.execute(
            "UPDATE devices SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL RETURNING id", (device_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM passkeys WHERE device_id = %s", (device_id,))
        conn.execute("UPDATE sessions SET revoked_at = now() WHERE device_id = %s AND revoked_at IS NULL", (device_id,))
    return True


def forget_device(conn: psycopg.Connection, device_id: UUID) -> bool:
    """Remove a revoked device from the list (live devices must be revoked first)."""
    return conn.execute("DELETE FROM devices WHERE id = %s AND revoked_at IS NOT NULL", (device_id,)).rowcount > 0


# ---- WebAuthn ceremonies -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ceremony:
    kind: str  # "register" | "login"
    challenge: bytes
    rp_id: str
    origin: str
    code_hash: bytes | None
    expires: float


class Ceremonies:
    """Pending challenges, in memory (one process). Each is single-use and lives 5 minutes."""

    def __init__(self, max_pending: int = 200) -> None:
        self._items: dict[str, Ceremony] = {}
        self._lock = threading.Lock()
        self.max_pending = max_pending

    def put(self, ceremony: Ceremony) -> str:
        flow = secrets.token_urlsafe(18)
        now = time.monotonic()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v.expires > now}
            if len(self._items) >= self.max_pending:
                self._items.pop(next(iter(self._items)))
            self._items[flow] = ceremony
        return flow

    def take(self, flow: str, kind: str) -> Ceremony:
        with self._lock:
            c = self._items.pop(flow, None)
        if c is None or c.kind != kind or c.expires < time.monotonic():
            raise DeviceError("That sign-in attempt expired. Try again.")
        return c


def start_registration(ceremonies: Ceremonies, rp_id: str, origin: str, code_hash: bytes, name: str) -> dict:
    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name="pal",
        user_id=secrets.token_bytes(16),  # a separate account per device, so passkeys never overwrite each other
        user_name=f"pal ({name})",
        user_display_name=f"pal ({name})",
        timeout=120_000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    flow = ceremonies.put(Ceremony("register", opts.challenge, rp_id, origin, code_hash, time.monotonic() + CEREMONY_TTL_S))
    return {"flow": flow, "options": json.loads(options_to_json(opts))}


def finish_registration(conn: psycopg.Connection, ceremony: Ceremony, credential: dict) -> UUID:
    try:
        v = verify_registration_response(
            credential=credential,
            expected_challenge=ceremony.challenge,
            expected_rp_id=ceremony.rp_id,
            expected_origin=ceremony.origin,
        )
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as exc:
        raise DeviceError("The passkey couldn't be verified. Try again.") from exc
    with conn.transaction():
        row = conn.execute(
            "UPDATE pairing_codes SET used_at = now() WHERE code_hash = %s AND used_at IS NULL AND expires_at > now() "
            "RETURNING device_name, kind",
            (ceremony.code_hash,),
        ).fetchone()
        if row is None:
            raise DeviceError("That pairing code has expired or was already used. Make a new one at the PC.")
        device_id = conn.execute(
            "INSERT INTO devices (name, kind, last_seen_at) VALUES (%s, %s, now()) RETURNING id", row
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO passkeys (credential_id, device_id, public_key, sign_count, rp_id) VALUES (%s, %s, %s, %s, %s)",
            (v.credential_id, device_id, v.credential_public_key, v.sign_count, ceremony.rp_id),
        )
    return device_id


def start_login(ceremonies: Ceremonies, rp_id: str, origin: str) -> dict:
    opts = generate_authentication_options(
        rp_id=rp_id, timeout=120_000, user_verification=UserVerificationRequirement.PREFERRED
    )
    flow = ceremonies.put(Ceremony("login", opts.challenge, rp_id, origin, None, time.monotonic() + CEREMONY_TTL_S))
    return {"flow": flow, "options": json.loads(options_to_json(opts))}


def finish_login(conn: psycopg.Connection, ceremony: Ceremony, credential: dict) -> UUID:
    try:
        cred_id = base64url_to_bytes(str(credential.get("rawId") or credential.get("id") or ""))
    except (ValueError, TypeError) as exc:
        raise DeviceError("The passkey couldn't be read.") from exc
    row = conn.execute(
        "SELECT p.public_key, p.sign_count, p.device_id FROM passkeys p JOIN devices d ON d.id = p.device_id "
        "WHERE p.credential_id = %s AND p.rp_id = %s AND d.revoked_at IS NULL",
        (cred_id, ceremony.rp_id),
    ).fetchone()
    if row is None:
        raise DeviceError("This passkey isn't paired with pal, or its device was revoked. Pair it again from the PC.")
    public_key, sign_count, device_id = row
    try:
        v = verify_authentication_response(
            credential=credential,
            expected_challenge=ceremony.challenge,
            expected_rp_id=ceremony.rp_id,
            expected_origin=ceremony.origin,
            credential_public_key=public_key,
            credential_current_sign_count=sign_count,
        )
    except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError) as exc:
        raise DeviceError("The passkey couldn't be verified. Try again.") from exc
    conn.execute(
        "UPDATE passkeys SET sign_count = %s, last_used_at = now() WHERE credential_id = %s",
        (v.new_sign_count, cred_id),
    )
    return device_id
