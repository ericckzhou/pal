"""S12: where pal's secrets live. Windows Credential Manager by default; a secrets folder in containers and CI.

Secrets are the database password ("postgres"), the backup key ("backup-key") and the optional Anthropic API key
("anthropic-api-key"). On Windows they live in Credential Manager under the service "pal". When PAL_SECRETS_DIR is
set (Docker secrets mounted at /run/secrets, or a temporary folder in CI), each secret is a file named after its
account in that folder instead. Either way, nothing secret is ever in the repo, the database or the web UI.
"""

import os
from contextlib import suppress
from pathlib import Path

SERVICE = "pal"
ENV_DIR = "PAL_SECRETS_DIR"


def _folder() -> Path | None:
    value = os.environ.get(ENV_DIR)
    return Path(value) if value else None


def get(account: str) -> str | None:
    folder = _folder()
    if folder is not None:
        path = folder / account
        return (path.read_text(encoding="utf-8").strip() or None) if path.is_file() else None
    import keyring

    return keyring.get_password(SERVICE, account) or None


def put(account: str, value: str) -> None:
    folder = _folder()
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / account
        path.write_text(value, encoding="utf-8")
        with suppress(OSError):  # read-only mounts and Windows ACLs: the folder's own permissions apply
            path.chmod(0o600)
        return
    import keyring

    keyring.set_password(SERVICE, account, value)


def delete(account: str) -> bool:
    folder = _folder()
    if folder is not None:
        path = folder / account
        if not path.is_file():
            return False
        path.unlink()
        return True
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(SERVICE, account)
        return True
    except PasswordDeleteError:
        return False


def where() -> str:
    """Where secrets are kept, for messages to the owner."""
    folder = _folder()
    return f"the secrets folder {folder}" if folder is not None else "Windows Credential Manager"
