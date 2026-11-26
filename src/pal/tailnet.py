"""Find this PC's tailnet name so pal accepts requests that Tailscale Serve forwards (S2).

Serve terminates HTTPS on <pc>.<tailnet>.ts.net and proxies to 127.0.0.1:8765. pal still binds loopback only and
never trusts Tailscale's identity headers: every request needs a pal session.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("pal.tailnet")
WINDOWS_EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"  # noqa: SIM112


def tailscale_exe() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return str(WINDOWS_EXE) if WINDOWS_EXE.exists() else None


def _run(*args: str) -> str | None:
    exe = tailscale_exe()
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def parse_status(raw: str) -> str | None:
    """The MagicDNS name from `tailscale status --json`, when the node is up."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if data.get("BackendState") != "Running":
        return None
    name = ((data.get("Self") or {}).get("DNSName") or "").rstrip(".").lower()
    return name or None


def detect_public_host() -> str | None:
    override = os.environ.get("PAL_PUBLIC_HOST", "").strip().rstrip(".").lower()
    if override:
        return override
    raw = _run("status", "--json")
    host = parse_status(raw) if raw else None
    if host is None:
        log.info("Tailscale not running or not installed; pal is reachable from this PC only")
    return host


def log_serve_state() -> None:
    """Remind the owner if Serve isn't set up, and warn loudly if Funnel (public internet) is on."""
    serve = _run("serve", "status")
    if serve is not None and "8765" not in serve:
        log.warning("Tailscale Serve isn't forwarding to pal. Run: tailscale serve --bg http://127.0.0.1:8765")
    funnel = _run("funnel", "status")
    if funnel and "Funnel on" in funnel:
        log.error("Tailscale Funnel is ON: pal may be reachable from the public internet. Run: tailscale funnel reset")
