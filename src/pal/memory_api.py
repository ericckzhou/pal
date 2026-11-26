"""Memory review and management API. Only the signed-in owner can confirm, edit or delete (S2, S6)."""

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from . import conversations, embed, memory, memory_replace
from .api import require_session
from .llm import LLMError
from .work_api import _check_project

log = logging.getLogger("pal.memory_api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
Kind = Literal["fact", "preference", "person", "project", "decision"]


class _TextCheck(BaseModel):
    @field_validator("text", check_fields=False)
    @classmethod
    def not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("memory text is empty")
        return v


class NewMemory(_TextCheck):
    text: str = Field(max_length=memory.MAX_TEXT)
    kind: Kind = "fact"
    pinned: bool = False
    project_id: UUID | None = None


class MemoryPatch(_TextCheck):
    text: str | None = Field(default=None, max_length=memory.MAX_TEXT)
    kind: Kind | None = None
    pinned: bool | None = None
    project_id: UUID | None = None  # sent as null = remove from project; left out = unchanged


def _found(item: dict | None) -> dict:
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return item


class ReplaceBody(BaseModel):
    old_id: UUID


class ReplaceWithText(_TextCheck):
    text: str = Field(max_length=memory.MAX_TEXT)
    kind: Kind | None = None


def _replace_call(fn, *args) -> dict:
    try:
        return _found(fn(*args))
    except memory_replace.ReplaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/memories")
def list_memories(
    request: Request, status: Literal["proposed", "confirmed", "replaced"] = "confirmed", q: str = ""
) -> list[dict]:
    with request.app.state.pool.connection() as conn:
        return memory.list_memories(conn, status, q[:500])


@router.get("/memories/inbox-count")
def inbox_count(request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return {"count": memory.proposed_count(conn)}


@router.post("/memories", status_code=201)
def create_memory(body: NewMemory, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        _check_project(conn, body.project_id)
        return memory.create(conn, body.text, body.kind, origin="stated", status="confirmed", pinned=body.pinned,
                             project_id=body.project_id)


@router.patch("/memories/{memory_id}")
def update_memory(memory_id: UUID, body: MemoryPatch, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        _check_project(conn, body.project_id)
        extra = {"project_id": body.project_id} if "project_id" in body.model_fields_set else {}
        return _found(memory.update(conn, memory_id, text=body.text, kind=body.kind, pinned=body.pinned, **extra))


@router.post("/memories/{memory_id}/confirm")
def confirm_memory(memory_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return _found(memory.confirm(conn, memory_id))


@router.post("/memories/{memory_id}/replace")
def replace_memory(memory_id: UUID, body: ReplaceBody, request: Request) -> dict:
    """This memory replaces `old_id` (also accepts a suggestion: a proposed memory is confirmed)."""
    with request.app.state.pool.connection() as conn:
        return _replace_call(memory_replace.replace, conn, memory_id, body.old_id)


@router.post("/memories/{memory_id}/replace-with", status_code=201)
def replace_memory_with_text(memory_id: UUID, body: ReplaceWithText, request: Request) -> dict:
    """Save new text as a memory that replaces this out-of-date one."""
    with request.app.state.pool.connection() as conn:
        return _replace_call(memory_replace.replace_with_text, conn, memory_id, body.text, body.kind)


@router.post("/memories/{memory_id}/restore")
def restore_memory(memory_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return _found(memory_replace.restore(conn, memory_id))


@router.post("/memories/{memory_id}/keep-both")
def keep_both(memory_id: UUID, request: Request) -> dict:
    """Dismiss a replacement suggestion."""
    with request.app.state.pool.connection() as conn:
        return _found(memory_replace.keep_both(conn, memory_id))


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: UUID, request: Request) -> Response:
    """Used for both 'reject' (a proposal) and 'delete' (a confirmed memory): either way the row is gone."""
    with request.app.state.pool.connection() as conn:
        if not memory.delete(conn, memory_id):
            raise HTTPException(status_code=404, detail="Memory not found")
    return Response(status_code=204)


@router.post("/conversations/{conv_id}/suggest-memories")
def suggest_memories(conv_id: UUID, request: Request) -> dict:
    state = request.app.state
    with state.pool.connection() as conn:
        if conversations.get_conversation(conn, conv_id) is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        try:
            created = memory.scan_conversation(conn, state.llm, conv_id)
        except LLMError as exc:
            raise HTTPException(status_code=503, detail="The local model isn't responding. Is Ollama running?") from exc
        if created:  # the owner is waiting anyway: check the new suggestions for replacements now, not next tick
            try:
                embed.embed_pending(conn, state.llm)
                for m in created:
                    memory_replace.check(conn, state.llm, m["id"])
            except Exception:
                log.warning("replacement check after a scan failed; the background tick will retry", exc_info=True)
            created = [memory.get(conn, m["id"]) or m for m in created]
    return {"proposed": created}
