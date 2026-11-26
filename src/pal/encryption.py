"""S13: a one-time, non-blocking check that the system drive is encrypted (BitLocker / Device Encryption).

It reads the Windows Shell property System.Volume.BitLockerProtection, which needs no admin rights and changes
nothing. It runs once in a background thread at startup; pal never waits for it or refuses to run because of it.
The Status page shows a warning when the drive is unprotected, until the owner acknowledges it.
"""

import logging
import os
import subprocess
import sys
import threading

log = logging.getLogger("pal.encryption")
ACK_KEY = "encryption_notice_ack"  # stored in app_settings; not an editable setting
CODES = {"0": "unavailable", "1": "on", "2": "off", "3": "encrypting", "4": "decrypting", "5": "suspended",
         "6": "on", "7": "on", "8": "waiting"}
UNPROTECTED = {"off", "decrypting", "suspended", "waiting", "unavailable"}

_result: dict | None = None  # set once by check(); None until it finishes


def parse(raw: str) -> str:
    return CODES.get(raw.strip(), "unknown")


def _drive() -> str:
    return os.environ.get("SystemDrive", "C:")  # noqa: SIM112 (Windows spells it this way)


def check() -> dict:
    """Read the system drive's state. Never raises: any failure is reported as 'unknown'."""
    global _result
    drive = _drive()
    state = "unknown"
    if sys.platform == "win32":
        script = (f"(New-Object -ComObject Shell.Application).NameSpace('{drive}\\').Self"
                  ".ExtendedProperty('System.Volume.BitLockerProtection')")
        try:
            out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            state = parse(out.stdout) if out.returncode == 0 else "unknown"
        except Exception as exc:  # noqa: BLE001 — a failed check must never affect pal
            log.warning("device encryption check failed: %s", exc)
    _result = {"state": state, "drive": drive}
    log.info("device encryption on %s: %s", drive, state)
    return _result


def current() -> dict | None:
    return _result


def start_check() -> threading.Thread:
    thread = threading.Thread(target=check, name="pal-encryption-check", daemon=True)
    thread.start()
    return thread
