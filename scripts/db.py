# /// script
# requires-python = ">=3.13"
# dependencies = ["keyring>=25"]
# ///
"""Start or stop pal's PostgreSQL container.

The database password lives in the Windows Credential Manager (S12), never in a file.
On first run a random password is generated and stored there.

Usage:  uv run scripts/db.py up | down | status
"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import keyring

SERVICE = "pal"
ACCOUNT = "postgres"
REPO = Path(__file__).resolve().parent.parent


def get_or_create_password() -> str:
    password = keyring.get_password(SERVICE, ACCOUNT)
    if password:
        return password
    password = secrets.token_urlsafe(32)
    keyring.set_password(SERVICE, ACCOUNT, password)
    print("Generated a new database password and stored it in Windows Credential Manager.")
    return password


def compose(*args: str) -> int:
    env = {**os.environ, "PAL_DB_PASSWORD": get_or_create_password()}
    return subprocess.call(["docker", "compose", *args], cwd=REPO, env=env)


def main() -> int:
    commands = {
        "up": ("up", "-d", "--wait"),
        "down": ("down",),  # keeps the data volume; never passes -v
        "status": ("ps",),
    }
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print(__doc__)
        return 2
    return compose(*commands[sys.argv[1]])


if __name__ == "__main__":
    sys.exit(main())
