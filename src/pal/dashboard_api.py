"""Status, sessions, analytics, logs, settings and schedule controls for the dashboard pages."""

import ctypes
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from importlib import metadata
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator

from . import auth, claude, encryption, logbuffer, reminders, reread, settings_store
from .api import require_owner, require_session
from .backup import backup_files
from .config import Settings
from .llm import LLMError

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
owner = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])
STARTED = time.time()
GB = 1024**3


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def _version() -> str:
    try:
        return metadata.version("pal")
    except metadata.PackageNotFoundError:
        return "dev"


# ---------- status ----------

def _memory_available() -> tuple[float, int] | None:
    """(fraction free, bytes free) of physical memory, or None when unknown."""
    if sys.platform == "win32":
        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        stat = MemStatus()
        stat.dwLength = ctypes.sizeof(MemStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return stat.ullAvailPhys / stat.ullTotalPhys, stat.ullAvailPhys
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            info = dict(line.split(":", 1) for line in f)
        total, avail = int(info["MemTotal"].split()[0]) * 1024, int(info["MemAvailable"].split()[0]) * 1024
        return avail / total, avail
    except (OSError, KeyError, ValueError):
        return None


def _disk_free(path) -> int | None:
    probe = path
    while not os.path.exists(probe) and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _memory_critical(memory: tuple[float, int]) -> bool:
    return memory[0] < 0.02 or memory[1] < 512 * 1024**2


def pressure(disk_free: int | None, memory: tuple[float, int] | None, ollama_up: bool) -> list[dict]:
    """Warnings, most severe first. Thresholds suit a desktop PC, not a small server."""
    out = []
    if disk_free is not None and disk_free < 1 * GB:
        out.append({"level": "critical", "kind": "disk", "message": "The disk holding pal's backups is almost full. "
                    "Backups will start failing."})
    if not ollama_up:
        out.append({"level": "critical", "kind": "model", "message": "Ollama isn't responding, so pal can't reply. "
                    "Start Ollama, or run start-pal.ps1 again."})
    if memory is not None and _memory_critical(memory):
        out.append({"level": "critical", "kind": "memory", "message": "This PC is almost out of memory. "
                    "Replies may stall or Ollama may restart."})
    if disk_free is not None and 1 * GB <= disk_free < 5 * GB:
        out.append({"level": "elevated", "kind": "disk", "message": "The disk holding pal's backups has under 5 GB free."})
    if memory is not None and not _memory_critical(memory) and (memory[0] < 0.05 or memory[1] < 1536 * 1024**2):
        out.append({"level": "elevated", "kind": "memory", "message": "This PC is low on memory; replies may be slow."})
    return out


ENCRYPTION_MESSAGES = {
    "unavailable": "{drive} isn't encrypted, and Device Encryption isn't available for it on this PC. If the PC is "
                   "stolen, pal's chats, memories and backups can be read straight off the disk.",
    "suspended": "BitLocker is suspended on {drive}, so pal's data isn't protected right now. "
                 "Resume it in Control Panel > BitLocker Drive Encryption.",
}
ENCRYPTION_DEFAULT = ("Device Encryption is off on {drive}. Turn it on in Settings > Privacy & security > "
                      "Device encryption, so pal's data can't be read if this PC is stolen.")


def encryption_warning(result: dict | None, acknowledged: bool) -> dict | None:
    """S13: one non-blocking warning when the system drive is unprotected, until the owner acknowledges it."""
    if result is None or acknowledged or result["state"] not in encryption.UNPROTECTED:
        return None
    text = ENCRYPTION_MESSAGES.get(result["state"], ENCRYPTION_DEFAULT)
    return {"level": "elevated", "kind": "encryption", "message": text.format(drive=result["drive"])}


def _backups(settings: Settings) -> dict:
    files = backup_files(settings.backup_dir)
    latest = files[-1] if files else None
    return {
        "count": len(files),
        "plaintext": sum(1 for f in files if f.suffix == ".dump"),
        "latest_at": datetime.fromtimestamp(latest.stat().st_mtime).astimezone().isoformat() if latest else None,
        "latest_bytes": latest.stat().st_size if latest else None,
    }


SESSION_COLS = """
    c.id, c.title, c.created_at, c.updated_at, c.model AS chosen_model, p.name AS project,
    (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count,
    (SELECT COALESCE(sum(COALESCE(m.prompt_tokens, 0) + COALESCE(m.completion_tokens, 0)), 0)
       FROM messages m WHERE m.conversation_id = c.id) AS tokens,
    (SELECT m.model FROM messages m WHERE m.conversation_id = c.id AND m.role = 'assistant'
       AND m.model IS DISTINCT FROM 'command' ORDER BY m.created_at DESC LIMIT 1) AS last_model,
    (SELECT left(m.content, 160) FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user'
       ORDER BY m.created_at LIMIT 1) AS preview
"""


def list_sessions(conn, query: str = "", limit: int = 200) -> list[dict]:
    q = query.strip()
    return _rows(
        conn,
        f"""SELECT {SESSION_COLS} FROM conversations c LEFT JOIN projects p ON p.id = c.project_id
            WHERE %s = '' OR c.title ILIKE '%%' || %s || '%%'
               OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id
                          AND m.search @@ websearch_to_tsquery('english', %s))
            ORDER BY c.updated_at DESC, c.id DESC LIMIT %s""",
        (q, q, q, limit),
    )


@router.get("/status")
def status(request: Request, session: auth.Session = Depends(require_session)) -> dict:
    state = request.app.state
    settings: Settings = state.settings
    try:
        installed = state.llm.installed_models()
        ollama = {"up": True, "installed": installed, "error": None}
    except (LLMError, AttributeError) as exc:
        ollama = {"up": False, "installed": [], "error": str(exc)[:200]}
    with state.pool.connection() as conn:
        counts = _rows(conn, """SELECT
            (SELECT count(*) FROM conversations) AS conversations,
            (SELECT count(*) FROM messages) AS messages,
            (SELECT count(*) FROM conversations WHERE updated_at > now() - interval '5 minutes') AS active_chats,
            (SELECT count(*) FROM memories WHERE status = 'confirmed') AS memories,
            (SELECT count(*) FROM memories WHERE status = 'proposed') AS inbox,
            (SELECT count(*) FROM documents) AS documents,
            (SELECT count(*) FROM chunks WHERE embedding IS NULL) AS embed_backlog,
            (SELECT count(*) FROM tasks WHERE status = 'open') AS open_tasks,
            (SELECT count(*) FROM reminders WHERE active AND NOT paused) AS reminders,
            (SELECT min(due_at) FROM reminders WHERE active AND NOT paused) AS next_reminder,
            (SELECT count(*) FROM devices WHERE revoked_at IS NULL) AS devices,
            (SELECT COALESCE(sum(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)), 0)
               FROM messages WHERE created_at > now() - interval '24 hours') AS tokens_24h""")[0]
        recent = list_sessions(conn, limit=20)
        counts["stale_pdfs"] = reread.stale(conn)["files"]
        counts["reread"] = reread.snapshot()
        acked = conn.execute("SELECT 1 FROM app_settings WHERE key = %s", (encryption.ACK_KEY,)).fetchone() is not None
    enc = encryption.current()
    backups = _backups(settings)
    disk = _disk_free(settings.backup_dir)
    if session.is_owner_pc:
        backups["dir"] = str(settings.backup_dir)
    return {
        "version": _version(),
        "started_at": datetime.fromtimestamp(STARTED).astimezone().isoformat(),
        "uptime_s": int(time.time() - STARTED),
        "model": settings.chat_model,
        "num_ctx": settings.num_ctx,
        "ollama": ollama,
        "database": {"up": True},
        "counts": counts,
        "backups": backups,
        "disk_free": disk,
        "pressure": pressure(disk, _memory_available(), ollama["up"])
                    + [w for w in [encryption_warning(enc, acked)] if w],
        "encryption": {**enc, "acknowledged": acked} if enc else None,
        "recent": recent,
    }


@router.get("/models")
def models(request: Request) -> dict:
    llm = request.app.state.llm
    allowed = sorted(getattr(llm, "allowed", {llm.model}))
    try:
        installed = set(llm.installed_models())
        up = True
    except (LLMError, AttributeError):
        installed, up = set(), False
    has_claude = getattr(request.app.state, "claude", None) is not None
    return {
        "default": request.app.state.settings.chat_model,
        "ollama_up": up,
        "claude_key": has_claude,
        "models": [{"name": m, "installed": (m in installed) if up else None, "provider": "local"} for m in allowed]
        + [{"name": m, "installed": has_claude, "provider": "claude"} for m in claude.MODELS],
    }


# ---------- sessions (conversations with stats) ----------

class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=100)
    thinking: Literal["off", "low", "medium", "high"] | None = None
    claude_context: bool | None = None  # a Claude chat also sends matching memories, documents and notes

    @field_validator("title")
    @classmethod
    def not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("must not be empty")
        return " ".join(v.split()) if v else v


class BulkDelete(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=500)


@router.get("/sessions")
def sessions(request: Request, q: str = "") -> list[dict]:
    with request.app.state.pool.connection() as conn:
        return list_sessions(conn, q[:200])


@router.patch("/conversations/{conv_id}")
def patch_conversation(conv_id: UUID, body: ConversationPatch, request: Request) -> dict:
    state = request.app.state
    fields = body.model_fields_set
    if "model" in fields and body.model is not None:
        allowed = set(getattr(state.llm, "allowed", {state.llm.model}))
        if getattr(state, "claude", None) is not None:
            allowed |= set(claude.MODELS)  # choosing one is this chat's opt-in to sending its text to Anthropic
        if body.model not in allowed:
            raise HTTPException(status_code=422, detail=f"{body.model} is not an allowed model")
    with state.pool.connection() as conn:
        found = conn.execute("SELECT 1 FROM conversations WHERE id = %s", (conv_id,)).fetchone()
        if not found:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if "title" in fields and body.title:
            conn.execute("UPDATE conversations SET title = %s WHERE id = %s", (body.title, conv_id))
        if "model" in fields:
            conn.execute("UPDATE conversations SET model = %s WHERE id = %s", (body.model, conv_id))
        if "thinking" in fields:
            conn.execute("UPDATE conversations SET thinking = %s WHERE id = %s", (body.thinking, conv_id))
        if "claude_context" in fields and body.claude_context is not None:
            conn.execute("UPDATE conversations SET claude_context = %s WHERE id = %s", (body.claude_context, conv_id))
        return _rows(conn, "SELECT id, title, updated_at, project_id, model, thinking, claude_context FROM conversations WHERE id = %s",
                     (conv_id,))[0]


@router.post("/conversations/bulk-delete")
def bulk_delete(body: BulkDelete, request: Request) -> dict:
    from . import conversations

    with request.app.state.pool.connection() as conn:
        removed = sum(conversations.delete_conversation(conn, cid) for cid in body.ids)
    return {"removed": removed}


@router.get("/conversations/{conv_id}/export")
def export(conv_id: UUID, request: Request, format: Literal["md", "json"] = "md") -> Response:
    with request.app.state.pool.connection() as conn:
        conv = _rows(conn, "SELECT id, title, created_at, updated_at FROM conversations WHERE id = %s", (conv_id,))
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        msgs = _rows(conn, """SELECT role, content, model, created_at, prompt_tokens, completion_tokens
                              FROM messages WHERE conversation_id = %s ORDER BY created_at, id""", (conv_id,))
    head = conv[0]
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in head["title"])[:60].strip() or "chat"
    if format == "json":
        body = json.dumps({**head, "messages": msgs}, default=str, indent=2, ensure_ascii=False)
        media, ext = "application/json", "json"
    else:
        parts = [f"# {head['title']}\n", f"_Exported from pal, {head['created_at']:%Y-%m-%d}_\n"]
        for m in msgs:
            who = "You" if m["role"] == "user" else f"pal ({m['model']})" if m["model"] else "pal"
            parts.append(f"\n**{who}** · {m['created_at']:%Y-%m-%d %H:%M}\n\n{m['content']}\n")
        body, media, ext = "".join(parts), "text/markdown; charset=utf-8", "md"
    return PlainTextResponse(body, media_type=media,
                             headers={"Content-Disposition": f'attachment; filename="{safe}.{ext}"'})


# ---------- analytics ----------

@router.get("/analytics/usage")
def usage(request: Request, days: int = 30) -> dict:
    days = max(1, min(days, 365))
    with request.app.state.pool.connection() as conn:
        daily = _rows(conn, """
            SELECT d::date AS day,
                   count(m.id) AS replies,
                   COALESCE(sum(m.prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(sum(m.completion_tokens), 0) AS completion_tokens
            FROM generate_series((now() - make_interval(days => %s - 1))::date, now()::date, interval '1 day') d
            LEFT JOIN messages m ON m.role = 'assistant' AND m.created_at::date = d::date
                 AND m.model IS DISTINCT FROM 'command'
            GROUP BY d ORDER BY d""", (days,))
        by_model = _rows(conn, """
            SELECT model, count(*) AS replies, COALESCE(sum(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(sum(completion_tokens), 0) AS completion_tokens
            FROM messages WHERE role = 'assistant' AND model IS NOT NULL AND model <> 'command'
              AND created_at > now() - make_interval(days => %s)
            GROUP BY model ORDER BY replies DESC""", (days,))
        untracked = conn.execute(
            """SELECT count(*) FROM messages WHERE role = 'assistant' AND model <> 'command'
               AND prompt_tokens IS NULL AND created_at > now() - make_interval(days => %s)""", (days,)
        ).fetchone()[0]
    totals = {
        "replies": sum(d["replies"] for d in daily),
        "prompt_tokens": sum(d["prompt_tokens"] for d in daily),
        "completion_tokens": sum(d["completion_tokens"] for d in daily),
        "untracked_replies": untracked,
    }
    return {"days": days, "daily": daily, "by_model": by_model, "totals": totals}


# ---------- logs and settings (this PC only) ----------

@owner.get("/logs")
def logs(level: Literal["debug", "info", "warning", "error"] = "info", q: str = "", limit: int = 500) -> list[dict]:
    return logbuffer.install().lines(level, q[:200], max(1, min(limit, 2000)))


@owner.get("/settings")
def get_settings(request: Request) -> dict:
    state = request.app.state
    with state.pool.connection() as conn:
        overrides = settings_store.load(conn)
    try:
        installed = state.llm.installed_models()
    except (LLMError, AttributeError):
        installed = None
    allowed = sorted(getattr(state.llm, "allowed", {state.llm.model}))
    return {
        "fields": settings_store.describe(state.settings, state.base_settings, overrides),
        "allowed_models": allowed,
        "installed_models": installed,
    }


@owner.put("/settings")
def put_settings(body: dict, request: Request) -> dict:
    state = request.app.state
    try:
        installed = state.llm.installed_models()
    except (LLMError, AttributeError):
        installed = None
    base_allowed = frozenset(getattr(state.llm, "allowed", {state.llm.model})) - frozenset(state.settings.extra_models)
    try:
        clean = settings_store.validate(body, base_allowed, installed)
        with state.pool.connection() as conn:
            merged = {**settings_store.load(conn), **clean}
            candidate = settings_store.apply(state.base_settings, merged)
            if candidate.answer_reserve * 2 > candidate.num_ctx:
                raise settings_store.SettingsError("The reply budget must be at most half the context size.")
            if candidate.chat_model not in base_allowed | frozenset(candidate.extra_models):
                raise settings_store.SettingsError(f"{candidate.chat_model} is no longer allowed; pick another default.")
            settings_store.save(conn, clean)
    except settings_store.SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings_store.install(request.app, candidate)
    logging.getLogger("pal.settings").info("settings changed: %s", ", ".join(sorted(clean)))
    return get_settings(request)


@owner.delete("/settings/{key}")
def reset_setting(key: str, request: Request) -> dict:
    state = request.app.state
    if key not in settings_store.BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown setting")
    with state.pool.connection() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = %s", (key,))
        candidate = settings_store.apply(state.base_settings, settings_store.load(conn))
    settings_store.install(request.app, candidate)
    return get_settings(request)


@owner.post("/status/encryption/ack")
def ack_encryption(request: Request) -> dict:
    """S13: the owner has seen the encryption warning; don't show it again."""
    with request.app.state.pool.connection() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, 'true') ON CONFLICT (key) DO NOTHING",
                     (encryption.ACK_KEY,))
    return {"acknowledged": True}


# ---------- schedule controls ----------

@router.post("/reminders/{reminder_id}/pause")
def pause(reminder_id: UUID, request: Request) -> dict:
    return _set_paused(request, reminder_id, True)


@router.post("/reminders/{reminder_id}/resume")
def resume(reminder_id: UUID, request: Request) -> dict:
    return _set_paused(request, reminder_id, False)


def _set_paused(request: Request, reminder_id: UUID, paused: bool) -> dict:
    with request.app.state.pool.connection() as conn:
        found = reminders.set_paused(conn, reminder_id, paused)
    if found is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return found


@router.post("/reminders/{reminder_id}/trigger")
def trigger(reminder_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        note = reminders.trigger(conn, reminder_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return note
