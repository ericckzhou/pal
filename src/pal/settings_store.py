"""Settings the owner can change from the UI, stored in app_settings and laid over the built-in defaults.

Only the fields listed in FIELDS can be changed. Hosts, ports, paths and secrets are deliberately not editable
here: they decide who can reach pal, so they stay in code and on the command line.
"""

from dataclasses import dataclass, replace

import psycopg
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from .config import Settings
from .llm import OllamaGateway


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str  # "model" | "int" | "models"
    help: str
    minimum: int | None = None
    maximum: int | None = None
    restart: bool = False


FIELDS: tuple[Field, ...] = (
    Field("chat_model", "Default model", "model", "model", "Used by new chats unless a chat picks its own."),
    Field("extra_models", "Extra allowed models", "model", "models",
          "Models installed in Ollama on this PC that chats may use, besides the built-in allowlist."),
    Field("num_ctx", "Context size (tokens)", "model", "int", "How much text the model reads per reply.", 2048, 131072),
    Field("answer_reserve", "Reply budget (tokens)", "model", "int",
          "Room kept free in the context for the reply itself.", 256, 8192),
    Field("memory_idle_minutes", "Suggest memories after (minutes idle)", "memory", "int",
          "A chat this quiet counts as ended, and pal then suggests memories from it.", 5, 240, restart=True),
    Field("session_days", "Sign-in lasts (days)", "security", "int", "How long a sign-in on this PC stays valid.", 1, 365),
    Field("device_idle_days", "Paired device idle limit (days)", "security", "int",
          "A phone or laptop unused this long must sign in again.", 1, 90),
    Field("backup_keep", "Backups to keep", "backup", "int", "Nightly backups older than this many are deleted.",
          1, 365, restart=True),
    Field("brief_hour", "Morning brief at (hour, 0 = off)", "automation", "int",
          "One notification with what's overdue, due and scheduled today, sent at this hour (24-hour clock) "
          "or as soon as pal is running after it. Days with nothing due send nothing.", 0, 23),
    Field("rescan_minutes", "Check added folders every (minutes, 0 = off)", "automation", "int",
          "New and changed files in folders you added on the Documents page are picked up on their own. "
          "Files deleted from disk stay in pal until you remove them.", 0, 1440),
)
BY_KEY = {f.key: f for f in FIELDS}


class SettingsError(ValueError):
    pass


def load(conn: psycopg.Connection) -> dict:
    return {k: v for k, v in conn.execute("SELECT key, value FROM app_settings").fetchall() if k in BY_KEY}


def apply(base: Settings, overrides: dict) -> Settings:
    values = {k: tuple(v) if BY_KEY[k].kind == "models" else v for k, v in overrides.items() if k in BY_KEY}
    return replace(base, **values)


def validate(changes: dict, allowed: frozenset[str], installed: list[str] | None) -> dict:
    """Check types and ranges. `installed` is None when Ollama can't be reached."""
    clean: dict = {}
    extra = changes.get("extra_models")
    for key, value in changes.items():
        field = BY_KEY.get(key)
        if field is None:
            raise SettingsError(f"{key} can't be changed here")
        if field.kind == "int":
            if not isinstance(value, int) or isinstance(value, bool) or not field.minimum <= value <= field.maximum:
                raise SettingsError(f"{field.label} must be a whole number from {field.minimum} to {field.maximum}")
        elif field.kind == "models":
            if not isinstance(value, list) or not all(isinstance(m, str) and 0 < len(m) <= 100 for m in value):
                raise SettingsError(f"{field.label} must be a list of model names")
            value = sorted({m.strip() for m in value if m.strip()})
            if value and installed is None:
                raise SettingsError("Ollama isn't reachable, so new models can't be checked. Start Ollama and retry.")
            missing = [m for m in value if installed is not None and m not in installed]
            if missing:
                raise SettingsError(f"Not installed in Ollama: {', '.join(missing)}")
        elif field.kind == "model":
            if not isinstance(value, str):
                raise SettingsError("Pick a model")
            usable = allowed | frozenset(extra if isinstance(extra, list) else [])
            if value not in usable:
                raise SettingsError(f"{value} is not an allowed model")
        clean[key] = value
    return clean


def save(conn: psycopg.Connection, changes: dict) -> None:
    with conn.transaction():
        for key, value in changes.items():
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (key, Jsonb(value)),
            )


def install(app: FastAPI, settings: Settings) -> None:
    """Make `settings` live: request handlers read app.state.settings; the model gateway is rebuilt to match."""
    app.state.settings = settings
    llm = app.state.llm
    if isinstance(llm, OllamaGateway):  # tests inject a fake model and keep it
        app.state.llm = OllamaGateway(
            settings.ollama_url, settings.chat_model, settings.num_ctx, extra_models=settings.extra_models
        )


def describe(settings: Settings, defaults: Settings, overrides: dict) -> list[dict]:
    out = []
    for f in FIELDS:
        value = getattr(settings, f.key)
        out.append({
            "key": f.key, "label": f.label, "group": f.group, "kind": f.kind, "help": f.help,
            "minimum": f.minimum, "maximum": f.maximum, "restart": f.restart,
            "value": list(value) if f.kind == "models" else value,
            "default": list(getattr(defaults, f.key)) if f.kind == "models" else getattr(defaults, f.key),
            "changed": f.key in overrides,
        })
    return out
