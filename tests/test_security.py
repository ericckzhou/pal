"""One test (or a small group) per foundational security promise covered in M1."""

import socket
from importlib.metadata import distributions
from pathlib import Path

import pytest

from pal import egress
from pal.cli import check_loopback
from pal.config import Settings

from .conftest import PASSWORD

REPO = Path(__file__).resolve().parent.parent


# S1: loopback only
def test_s1_defaults_bind_loopback_and_refuse_public_hosts():
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.ollama_url.startswith("http://127.0.0.1:")
    check_loopback("127.0.0.1")
    with pytest.raises(SystemExit):
        check_loopback("0.0.0.0")
    compose = (REPO / "compose.yaml").read_text()
    assert '"127.0.0.1:5432:5432"' in compose


# S2: every API request needs an app session
def test_s2_api_requires_session(client):
    for method, path in [("get", "/api/conversations"), ("post", "/api/conversations"), ("get", "/api/search?q=x")]:
        assert getattr(client, method)(path).status_code == 401


def test_s2_tailscale_identity_headers_are_not_trusted(client):
    resp = client.get(
        "/api/conversations",
        headers={"Tailscale-User-Login": "owner@example.com", "Tailscale-User-Name": "Owner"},
    )
    assert resp.status_code == 401


# S3: server-side sessions with instant revocation
def test_s3_login_cookie_flags_and_revocation(client, db):
    from pal.auth import set_password

    set_password(db, PASSWORD)
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/conversations").status_code == 200

    token = client.cookies.get("pal_session")
    client.post("/api/auth/logout")
    client.cookies.set("pal_session", token)
    assert client.get("/api/conversations").status_code == 401


def test_s3_login_lockout_after_repeated_failures(client, db):
    from pal.auth import set_password

    set_password(db, PASSWORD)
    for _ in range(5):
        client.post("/api/auth/login", json={"password": "nope"})
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429


# S4: Host/Origin allowlist + CSRF header
def test_s4_rejects_unknown_host(authed):
    assert authed.get("/api/conversations", headers={"Host": "evil.example"}).status_code == 400


def test_s4_rejects_foreign_origin_and_missing_csrf_header(authed):
    assert authed.post("/api/conversations", headers={"Origin": "https://evil.example"}).status_code == 403
    assert authed.post("/api/conversations", headers={"X-Pal-Request": ""}).status_code == 403
    assert authed.post("/api/conversations", headers={"Origin": "http://testserver"}).status_code == 201


# S5: strict CSP, nothing loads remotely
def test_s5_strict_csp_on_every_response(client):
    csp = client.get("/api/auth/me").headers["content-security-policy"]
    for directive in ["default-src 'self'", "script-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "connect-src 'self'"]:
        assert directive in csp
    assert "http" not in csp.replace("'self'", "")


# S8: no outbound traffic from the app
def test_s8_egress_guard_blocks_non_loopback():
    egress.install()
    with pytest.raises(egress.EgressBlocked):
        socket.getaddrinfo("example.com", 443)
    sock = socket.socket()
    try:
        with pytest.raises(egress.EgressBlocked):
            sock.connect(("93.184.215.14", 443))
    finally:
        sock.close()
    socket.getaddrinfo("127.0.0.1", 5432)  # loopback still allowed


TELEMETRY_DENYLIST = {
    "sentry-sdk", "posthog", "mixpanel", "segment-analytics-python", "analytics-python",
    "amplitude-analytics", "datadog", "ddtrace", "newrelic", "bugsnag", "rollbar", "logfire",
    "opentelemetry-exporter-otlp", "opentelemetry-exporter-otlp-proto-http", "scarf",
}


def test_s8_no_telemetry_sdks_installed():
    installed = {d.metadata["Name"].lower() for d in distributions()}
    assert not installed & TELEMETRY_DENYLIST


# S12: secrets in the OS credential store, not in files
def test_s12_no_secret_files_and_password_from_keyring():
    assert not (REPO / ".env").exists()
    assert "${PAL_DB_PASSWORD:?" in (REPO / "compose.yaml").read_text()
    assert "password" not in Settings.__dataclass_fields__
