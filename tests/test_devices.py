"""M5 devices: pairing codes, passkeys (real WebAuthn verification against a software authenticator), revocation."""

import hashlib
import json
import os

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url

from pal import tailnet
from pal.config import Settings

from .conftest import PUBLIC_HOST

ORIGIN = f"https://{PUBLIC_HOST}"


class SoftAuthenticator:
    """A minimal platform authenticator: P-256 key, 'none' attestation, counter."""

    def __init__(self, rp_id: str = PUBLIC_HOST, origin: str = ORIGIN) -> None:
        self.rp_id, self.origin = rp_id, origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.count = 0

    def _client_data(self, typ: str, challenge: str) -> bytes:
        return json.dumps({"type": typ, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def _rp_hash(self) -> bytes:
        return hashlib.sha256(self.rp_id.encode()).digest()

    def register(self, options: dict) -> dict:
        nums = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        auth_data = (
            self._rp_hash() + bytes([0x45]) + self.count.to_bytes(4, "big") + b"\0" * 16
            + len(self.cred_id).to_bytes(2, "big") + self.cred_id + cose
        )
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cd = self._client_data("webauthn.create", options["challenge"])
        b = bytes_to_base64url
        return {
            "id": b(self.cred_id), "rawId": b(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b(cd), "attestationObject": b(att)}, "clientExtensionResults": {},
        }

    def login(self, options: dict) -> dict:
        self.count += 1
        auth_data = self._rp_hash() + bytes([0x05]) + self.count.to_bytes(4, "big")
        cd = self._client_data("webauthn.get", options["challenge"])
        sig = self.key.sign(auth_data + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        b = bytes_to_base64url
        return {
            "id": b(self.cred_id), "rawId": b(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b(cd), "authenticatorData": b(auth_data), "signature": b(sig)},
            "clientExtensionResults": {},
        }


def make_code(authed, name="Phone") -> str:
    resp = authed.post("/api/devices/pairing", json={"name": name, "kind": "phone"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["url"] == f"https://{PUBLIC_HOST}/?pair={body['code'].replace('-', '')}"
    return body["code"]


def pair(remote, code: str, authn: SoftAuthenticator):
    start = remote.post("/api/auth/pair/start", json={"code": code})
    assert start.status_code == 200, start.text
    flow = start.json()
    assert flow["options"]["rp"]["id"] == PUBLIC_HOST
    assert flow["options"]["authenticatorSelection"]["residentKey"] == "required"
    return remote.post("/api/auth/pair/finish", json={"flow": flow["flow"], "credential": authn.register(flow["options"])})


def passkey_login(remote, authn: SoftAuthenticator):
    flow = remote.post("/api/auth/passkey/start").json()
    return remote.post("/api/auth/passkey/finish", json={"flow": flow["flow"], "credential": authn.login(flow["options"])})


@pytest.fixture
def paired(authed, remote):
    authn = SoftAuthenticator()
    resp = pair(remote, make_code(authed), authn)
    assert resp.status_code == 200, resp.text
    device_id = authed.get("/api/devices").json()[0]["id"]
    return authn, device_id


# S3: a device pairs with a one-time code + passkey and gets its own session
def test_s3_pair_device_then_use_pal_remotely(authed, remote, paired):
    me = remote.get("/api/auth/me").json()
    assert me == {
        "authenticated": True, "owner": False, "device": "Phone", "password_login": False, "remote_url": ORIGIN,
    }
    assert remote.get("/api/conversations").status_code == 200
    devices = authed.get("/api/devices").json()
    assert [(d["name"], d["kind"], d["passkeys"], d["revoked_at"]) for d in devices] == [("Phone", "phone", 1, None)]


def test_s3_remote_session_cookie_is_secure(authed, remote):
    resp = pair(remote, make_code(authed), SoftAuthenticator())
    cookie = resp.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_s3_pairing_code_is_single_use_and_expires(authed, remote, db, paired):
    code = make_code(authed, "Laptop")
    db.execute("UPDATE pairing_codes SET expires_at = now() - interval '1 second' WHERE device_name = 'Laptop'")
    assert remote.post("/api/auth/pair/start", json={"code": code}).status_code == 400

    code = make_code(authed, "Tablet")
    start = remote.post("/api/auth/pair/start", json={"code": code.lower().replace("-", " ")}).json()  # forgiving input
    remote.post("/api/auth/pair/finish", json={"flow": start["flow"], "credential": SoftAuthenticator().register(start["options"])})
    assert remote.post("/api/auth/pair/start", json={"code": code}).status_code == 400


def test_s3_pairing_code_race_only_one_device_wins(authed, remote):
    code = make_code(authed)
    a = remote.post("/api/auth/pair/start", json={"code": code}).json()
    b = remote.post("/api/auth/pair/start", json={"code": code}).json()
    assert remote.post("/api/auth/pair/finish", json={"flow": a["flow"], "credential": SoftAuthenticator().register(a["options"])}).status_code == 200
    second = remote.post("/api/auth/pair/finish", json={"flow": b["flow"], "credential": SoftAuthenticator().register(b["options"])})
    assert second.status_code == 400 and "already used" in second.json()["detail"]


def test_s3_wrong_codes_lock_pairing(authed, remote):
    code = make_code(authed)
    for _ in range(5):
        assert remote.post("/api/auth/pair/start", json={"code": "AAAA-AAAA"}).status_code == 400
    assert remote.post("/api/auth/pair/start", json={"code": code}).status_code == 429


def test_s3_passkey_sign_in_on_a_fresh_browser(remote, paired, db):
    authn, _ = paired
    remote.cookies.clear()
    assert remote.get("/api/conversations").status_code == 401
    resp = passkey_login(remote, authn)
    assert resp.status_code == 200 and resp.json()["device"] == "Phone"
    assert remote.get("/api/conversations").status_code == 200
    assert db.execute("SELECT sign_count FROM passkeys").fetchone()[0] == 1


def test_s3_unknown_passkey_wrong_origin_and_replayed_challenge_are_refused(remote, paired):
    authn, _ = paired
    remote.cookies.clear()
    assert passkey_login(remote, SoftAuthenticator()).status_code == 400  # never paired

    evil = SoftAuthenticator(origin="https://evil.example")
    evil.key, evil.cred_id = authn.key, authn.cred_id
    assert passkey_login(remote, evil).status_code == 400  # phished origin

    flow = remote.post("/api/auth/passkey/start").json()
    cred = authn.login(flow["options"])
    assert remote.post("/api/auth/passkey/finish", json={"flow": flow["flow"], "credential": cred}).status_code == 200
    again = remote.post("/api/auth/passkey/finish", json={"flow": flow["flow"], "credential": cred})
    assert again.status_code == 400  # each challenge is single-use


def test_s3_revoked_device_gets_401_immediately(authed, remote, paired):
    authn, device_id = paired
    assert remote.get("/api/conversations").status_code == 200
    assert authed.post(f"/api/devices/{device_id}/revoke").status_code == 200
    assert remote.get("/api/conversations").status_code == 401
    remote.cookies.clear()
    assert passkey_login(remote, authn).status_code == 400
    assert authed.get("/api/conversations").status_code == 200  # the PC is unaffected


def test_s3_forget_requires_revoke_first(authed, paired):
    _, device_id = paired
    assert authed.delete(f"/api/devices/{device_id}").status_code == 409
    authed.post(f"/api/devices/{device_id}/revoke")
    assert authed.delete(f"/api/devices/{device_id}").status_code == 204
    assert authed.get("/api/devices").json() == []


def test_s3_idle_device_session_expires(remote, paired, db):
    db.execute("UPDATE sessions SET last_seen_at = now() - interval '15 days' WHERE device_id IS NOT NULL")
    assert remote.get("/api/conversations").status_code == 401


def test_s3_devices_cannot_administer_devices_even_with_forged_tailscale_headers(remote, paired):
    forged = {"Tailscale-User-Login": "owner@example.com", "Tailscale-User-Name": "Owner"}
    assert remote.get("/api/devices", headers=forged).status_code == 403
    assert remote.post("/api/devices/pairing", json={"name": "Evil"}, headers=forged).status_code == 403
    assert remote.post(f"/api/devices/{paired[1]}/revoke").status_code == 403
    assert remote.post("/api/documents/folder", json={"path": "C:\\"}).status_code == 403


def test_s2_password_login_only_at_the_pc(authed, remote):
    resp = remote.post("/api/auth/login", json={"password": "correct horse battery staple"})
    assert resp.status_code == 403
    assert remote.get("/api/auth/me").json()["password_login"] is False
    assert authed.get("/api/auth/me").json()["owner"] is True


def test_s2_public_host_needs_https_origin(remote, paired):
    assert remote.post("/api/conversations", headers={"Origin": "http://evil.example"}).status_code == 403
    assert remote.post("/api/conversations", headers={"Origin": ORIGIN}).status_code == 201


def test_pairing_code_validation(authed):
    assert authed.post("/api/devices/pairing", json={"name": "  ", "kind": "phone"}).status_code == 400
    assert authed.post("/api/devices/pairing", json={"name": "Watch", "kind": "watch"}).status_code == 400


def test_settings_hosts_and_origins():
    s = Settings(public_host="pc.example.ts.net")
    assert "pc.example.ts.net" in s.all_hosts and "pc.example.ts.net:443" in s.all_hosts
    assert "https://pc.example.ts.net" in s.allowed_origins
    assert "http://pc.example.ts.net" not in s.allowed_origins
    assert Settings().public_hosts == ()


def test_tailnet_status_parsing(monkeypatch):
    up = json.dumps({"BackendState": "Running", "Self": {"DNSName": "Desk.Tail1234.ts.net."}})
    assert tailnet.parse_status(up) == "desk.tail1234.ts.net"
    assert tailnet.parse_status(json.dumps({"BackendState": "NeedsLogin", "Self": {"DNSName": "x.ts.net."}})) is None
    assert tailnet.parse_status("not json") is None
    monkeypatch.setenv("PAL_PUBLIC_HOST", "Other.ts.net.")
    assert tailnet.detect_public_host() == "other.ts.net"
    monkeypatch.delenv("PAL_PUBLIC_HOST")
    monkeypatch.setattr(tailnet, "_run", lambda *a: up)
    assert tailnet.detect_public_host() == "desk.tail1234.ts.net"
    monkeypatch.setattr(tailnet, "_run", lambda *a: None)
    assert tailnet.detect_public_host() is None


def test_pwa_manifest_and_icon_are_served(client):
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/manifest+json")
    assert resp.json()["display"] == "standalone"
    assert client.get("/icon.svg").headers["content-type"].startswith("image/svg+xml")
    assert client.get("/index.html").headers["content-type"].startswith("text/html")  # other paths still get the SPA


def test_s2_serve_forwarded_request_with_loopback_host_is_still_remote(client, db):
    from pal.auth import set_password

    set_password(db, "correct horse battery staple")
    for header in ({"X-Forwarded-For": "100.64.0.7"}, {"Tailscale-User-Login": "owner@example.com"}):
        resp = client.post("/api/auth/login", json={"password": "correct horse battery staple"}, headers=header)
        assert resp.status_code == 403
    assert client.post("/api/auth/login", json={"password": "correct horse battery staple"}).status_code == 200
