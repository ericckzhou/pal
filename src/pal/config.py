"""Runtime settings. Secrets are never stored here: the DB password comes from the OS credential store."""

import os
from dataclasses import dataclass
from pathlib import Path

KEYRING_SERVICE = "pal"  # service name for every secret (see secret_store)


def data_dir() -> Path:
    """pal's own files outside the repo: backups, Docling models, kept uploads.

    PAL_DATA_DIR (containers) wins; otherwise %LOCALAPPDATA%/pal on Windows, ~/.local/share/pal elsewhere.
    """
    if os.environ.get("PAL_DATA_DIR"):
        return Path(os.environ["PAL_DATA_DIR"])
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "pal"


def _default_backup_dir() -> Path:
    return data_dir() / "backups"


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
    rerank: str = "on"  # "on": the local model checks search results before they reach the prompt (rerank.py)
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
    sync_minutes: int = 15  # how often connected services (Google Calendar, Drive) are synced; 0 = off

    @classmethod
    def from_env(cls) -> "Settings":
        """Built-in defaults, with the few values a container needs taken from PAL_* environment variables.

        Only addresses and paths can come from the environment; secrets never do (see secret_store).
        """
        env = os.environ
        values: dict = {}
        if env.get("PAL_HOST"):
            values["host"] = env["PAL_HOST"]
        if env.get("PAL_DB_HOST"):
            values["db_host"] = env["PAL_DB_HOST"]
        if env.get("PAL_DB_PORT"):
            values["db_port"] = int(env["PAL_DB_PORT"])
        if env.get("PAL_OLLAMA_URL"):
            values["ollama_url"] = env["PAL_OLLAMA_URL"].rstrip("/")
        if env.get("PAL_BACKUP_DIR"):
            values["backup_dir"] = Path(env["PAL_BACKUP_DIR"])
        if env.get("PAL_PUBLIC_PORT"):
            # The port the browser uses (Docker may publish pal on another host port than it listens on).
            port = env["PAL_PUBLIC_PORT"]
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                raise SystemExit(f"PAL_PUBLIC_PORT must be a port number (1-65535), not {port!r}.")
            values["allowed_hosts"] = (f"127.0.0.1:{port}", f"localhost:{port}")
        return cls(**values)

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


def in_container() -> bool:
    """Running in pal's Docker image (PAL_CONTAINER=1): bind inside the container, reach the DB by its service name."""
    return os.environ.get("PAL_CONTAINER") == "1"


def db_password() -> str:
    from . import secret_store

    password = secret_store.get("postgres")
    if not password:
        raise SystemExit(f"Database password not found in {secret_store.where()}. Run: uv run scripts/db.py up")
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
