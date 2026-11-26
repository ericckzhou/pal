"""Runtime settings. Secrets are never stored here: the DB password comes from the OS credential store."""

import os
from dataclasses import dataclass
from pathlib import Path

import keyring

KEYRING_SERVICE = "pal"


def _default_backup_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "pal" / "backups"


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "pal"
    db_user: str = "pal"
    db_container: str = "pal-db-1"
    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen3.5:4b"
    extra_models: tuple[str, ...] = ()  # owner-added local models (Settings page), on top of llm.ALLOWED_MODELS
    num_ctx: int = 8192
    answer_reserve: int = 1536
    thinking: str = "off"  # default thinking level for chats that don't pick one (thinking.LEVELS)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:8765", "localhost:8765")  # this PC (plain http on loopback)
    public_host: str | None = None  # tailnet name served by Tailscale Serve over https; `pal serve` detects it
    session_days: int = 30
    device_idle_days: int = 14  # a paired device unused this long must sign in again (DEC-16)
    backup_dir: Path = _default_backup_dir()
    backup_keep: int = 14
    memory_idle_minutes: int = 15  # a conversation this quiet counts as ended; pal then proposes memories
    brief_hour: int = 8  # local hour the morning brief goes out; 0 = off
    rescan_minutes: int = 30  # how often added folders are checked for new or changed files; 0 = off

    @property
    def public_hosts(self) -> tuple[str, ...]:
        return (self.public_host, f"{self.public_host}:443") if self.public_host else ()

    @property
    def all_hosts(self) -> tuple[str, ...]:
        return self.allowed_hosts + self.public_hosts

    @property
    def allowed_origins(self) -> frozenset[str]:
        local = {f"{scheme}://{h}" for h in self.allowed_hosts for scheme in ("http", "https")}
        return frozenset(local | ({f"https://{self.public_host}"} if self.public_host else set()))


def db_password() -> str:
    password = keyring.get_password(KEYRING_SERVICE, "postgres")
    if not password:
        raise SystemExit("Database password not found in Credential Manager. Run: uv run scripts/db.py up")
    return password


def dsn(settings: Settings, db_name: str | None = None) -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(
        host=settings.db_host,
        port=settings.db_port,
        dbname=db_name or settings.db_name,
        user=settings.db_user,
        password=db_password(),
    )
