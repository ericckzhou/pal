"""HTTP API. Every route except login/me requires a valid session (S2)."""

import json
import logging
import re
from collections.abc import Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from . import actions, auth, claude, conversations, model_cmd, devices, memory, reminders, retrieval, work
from .context import build_messages
from .llm import LLMError

log = logging.getLogger("pal.api")
CLAUDE_CONTEXT = 100_000  # tokens of this chat's history sent to Claude at most
public = APIRouter(prefix="/api")


class LoginBody(BaseModel):
    password: str = Field(max_length=1024)


class MessageBody(BaseModel):
    content: str = Field(max_length=100_000)

    @field_validator("content")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message is empty")
        return v


def require_session(request: Request) -> auth.Session:
    with request.app.state.pool.connection() as conn:
        session = auth.lookup_session(
            conn, request.cookies.get(auth.SESSION_COOKIE), request.app.state.settings.device_idle_days
        )
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return session


def require_owner(session: auth.Session = Depends(require_session)) -> auth.Session:
    """Device admin and this-PC-only features: only the PC's password session, never a paired device."""
    if not session.is_owner_pc:
        raise HTTPException(status_code=403, detail="Only available on the PC running pal.")
    return session


PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "tailscale-user-login", "forwarded")


def is_local(request: Request) -> bool:
    """True when the request came straight to this PC's loopback address, not through Tailscale Serve.

    Proxy/identity headers only ever make a request count as remote (deny-only), so forging them grants nothing.
    """
    if any(h in request.headers for h in PROXY_HEADERS):
        return False
    return request.headers.get("host", "").lower() in {h.lower() for h in request.app.state.settings.allowed_hosts}


def rp_origin(request: Request) -> tuple[str, str]:
    """WebAuthn relying-party id and origin for the address the browser used (already Host-allowlisted)."""
    host = request.headers.get("host", "").lower()
    if is_local(request):
        return host.split(":")[0], f"http://{host}"
    public = request.app.state.settings.public_host
    return public, f"https://{public}"


def set_session_cookie(request: Request, response: Response, token: str, days: int) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=days * 86400,
        httponly=True,
        samesite="strict",
        secure=not is_local(request),  # https through Tailscale Serve
        path="/",
    )


private = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


@public.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    state = request.app.state
    if not is_local(request):
        raise HTTPException(status_code=403, detail="Password sign-in only works at the PC. Use this device's passkey.")
    if state.limiter.locked():
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")
    with state.pool.connection() as conn:
        if not auth.verify_password(conn, body.password):
            state.limiter.record_failure()
            raise HTTPException(status_code=401, detail="Wrong password")
        token = auth.create_session(conn, state.settings.session_days, request.headers.get("user-agent", "")[:200])
    state.limiter.record_success()
    set_session_cookie(request, response, token, state.settings.session_days)
    return {"ok": True}


@public.get("/auth/me")
def me(request: Request) -> dict:
    settings = request.app.state.settings
    with request.app.state.pool.connection() as conn:
        session = auth.lookup_session(conn, request.cookies.get(auth.SESSION_COOKIE), settings.device_idle_days)
        name = devices.device_name(conn, session.device_id) if session and session.device_id else None
    return {
        "authenticated": session is not None,
        "owner": bool(session and session.is_owner_pc),
        "device": name,
        "password_login": is_local(request),
        "remote_url": f"https://{settings.public_host}" if settings.public_host else None,
    }


@private.post("/auth/logout")
def logout(request: Request, response: Response, session: auth.Session = Depends(require_session)) -> dict:
    with request.app.state.pool.connection() as conn:
        auth.revoke_session(conn, session.id)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@private.get("/conversations")
def list_conversations(request: Request) -> list[dict]:
    with request.app.state.pool.connection() as conn:
        return conversations.list_conversations(conn)


@private.post("/conversations", status_code=201)
def create_conversation(request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return conversations.create_conversation(conn)


@private.get("/conversations/{conv_id}")
def get_conversation(conv_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        conv = conversations.get_conversation(conn, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@private.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: UUID, request: Request) -> Response:
    with request.app.state.pool.connection() as conn:
        if not conversations.delete_conversation(conn, conv_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@private.get("/search")
def search(q: str, request: Request) -> list[dict]:
    if not q.strip():
        return []
    with request.app.state.pool.connection() as conn:
        return conversations.search(conn, q[:500])


def _retrieval_query(past: list[dict]) -> str:
    """The latest user message; a short follow-up ("and her husband?") also gets the previous user message."""
    users = [m["content"] for m in past if m["role"] == "user"]
    if not users:
        return ""
    if len(users[-1].split()) < 5 and len(users) > 1:
        return f"{users[-2][-500:]}\n{users[-1]}"
    return users[-1]


def _event(**data) -> str:
    return json.dumps(data, default=str) + "\n"


_CITE = re.compile(r"\s?\[([mdcnp]\d+)\](?!\()")


def strip_unknown_handles(reply: str, sources: list[dict]) -> str:
    """Citation handles the model invented (not given this turn) are removed before the reply is stored."""
    known = {s["handle"] for s in sources}
    return _CITE.sub(lambda m: m.group(0) if m.group(1) in known else "", reply)


def _project_item(conn, conv_id: UUID) -> list[dict]:
    project = work.focus_project(conn, conv_id)
    if project is None:
        return []
    card = work.project_card(conn, project, reminders.local_now().date())
    return [{
        "handle": "p1", "kind": "project", "id": str(project["id"]), "title": project["name"], "text": card,
        "snippet": " ".join(card.split())[:300], "page": None, "conversation_id": None, "document_id": None,
        "at": None, "pinned": False,
    }]


def _command_reply(state, conv_id: UUID, content: str) -> StreamingResponse:
    """Slash commands are the owner acting directly: no model call, no retrieval."""
    with state.pool.connection() as conn:
        conversations.add_message(conn, conv_id, "user", content)
        cmd, _, arg = content.strip().partition(" ")
        if cmd.lower() == "/model":
            result = model_cmd.run(conn, conv_id, arg, sorted(getattr(state.llm, "allowed", {state.llm.model})),
                                   state.settings.chat_model, getattr(state, "claude", None) is not None)
            result["changed"] = False
        else:
            result = actions.run_command(conn, conv_id, content)
        step = {"kind": "command", "label": f"Ran {content.split()[0]}", "detail": None}
        msg_id = conversations.add_message(
            conn, conv_id, "assistant", result["reply"], conversations.COMMAND_MODEL, steps=[step]
        )

    def generate() -> Iterator[str]:
        yield _event(type="step", step=step)
        if result["changed"]:
            yield _event(type="work_changed")
        if result.get("model_changed"):
            yield _event(type="model_changed", model=result["model"])
        yield _event(type="token", text=result["reply"])
        yield _event(type="done", message_id=msg_id)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


_KIND_LABEL = {"memory": "memory", "document": "document", "conversation": "past chat", "note": "note",
               "project": "project"}


def _steps(sources: list[dict], saved: dict | None, proposal: dict | None) -> list[dict]:
    """What pal did before replying, shown as small cards above the reply (and stored with it)."""
    steps: list[dict] = []
    if sources:
        kinds: dict[str, int] = {}
        for src in sources:
            kinds[src["kind"]] = kinds.get(src["kind"], 0) + 1
        detail = ", ".join(f"{n} {_KIND_LABEL.get(k, k)}{'s' if n > 1 else ''}" for k, n in kinds.items())
        plural = "s" if len(sources) > 1 else ""
        steps.append({"kind": "retrieval", "label": f"Looked up {len(sources)} source{plural}", "detail": detail})
    if saved:
        steps.append({"kind": "memory", "label": "Saved to memory", "detail": saved["text"][:200]})
    if proposal:
        what = "task" if proposal["action"] == "create_task" else "reminder"
        steps.append({"kind": "proposal", "label": f"Drafted a {what}, waiting for your OK",
                      "detail": proposal["payload"].get("title")})
    return steps


@private.post("/conversations/{conv_id}/messages")
def post_message(conv_id: UUID, body: MessageBody, request: Request) -> StreamingResponse:
    state = request.app.state
    with state.pool.connection() as conn:
        conv = conversations.get_conversation(conn, conv_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    chosen = conv.get("model")
    cloud = getattr(state, "claude", None) if claude.is_claude(chosen) else None
    if claude.is_claude(chosen) and cloud is None:
        raise HTTPException(status_code=409, detail="This chat uses Claude, but no API key is stored. Pick a local model.")
    if cloud:
        model, gateway = chosen, cloud
    else:
        model = chosen if chosen and chosen in getattr(state.llm, "allowed", {chosen}) else state.llm.model
        gateway = state.llm
    if actions.is_command(body.content):
        return _command_reply(state, conv_id, body.content)
    with state.pool.connection() as conn:
        user_msg_id = conversations.add_message(conn, conv_id, "user", body.content)
        past = conversations.history(conn, conv_id)
        # Retrieve before saving a stated memory, so the reply isn't "about" the note it was just given.
        if cloud:
            items = []  # Claude gets only this chat's own text: no memories, documents, notes or project card
        else:
            focus = _project_item(conn, conv_id)
            focus_id = UUID(focus[0]["id"]) if focus else None
            items = focus + retrieval.retrieve(conn, state.llm, _retrieval_query(past), conv_id, focus_id)
        saved = None
        note = None
        stated = memory.parse_remember(body.content)
        if stated:  # the owner typed it, so it is confirmed; the model plays no part (S6)
            saved = memory.create(
                conn, stated, origin="stated", status="confirmed", conversation_id=conv_id, message_id=user_msg_id
            )
            note = f"pal has saved this to the user's memory: \"{stated}\". Confirm in one short sentence."
        proposal = None
        if not stated and actions.might_be_action(body.content):
            # Drafted before the reply, so the reply can describe exactly what the card says.
            try:
                proposal = actions.propose(conn, state.llm, conv_id, user_msg_id, body.content)
            except Exception:
                log.exception("action proposal failed")
            if proposal:
                note = actions.draft_note(proposal)
        num_ctx = CLAUDE_CONTEXT if cloud else state.settings.num_ctx
        prompt, used = build_messages(past, num_ctx, state.settings.answer_reserve, items, note)
        sources = retrieval.public_sources(used)
    steps = _steps(sources, saved, proposal)

    def generate() -> Iterator[str]:
        parts: list[str] = []
        usage: dict = {}
        for step in steps:
            yield _event(type="step", step=step)
        if saved:
            yield _event(type="memory_saved", memory=saved)
        if sources:
            yield _event(type="sources", sources=sources)
        try:
            for text in gateway.stream_chat(prompt, model=model, usage=usage):
                parts.append(text)
                yield _event(type="token", text=text)
        except Exception as exc:
            log.exception("model stream failed")
            message = (str(exc) if isinstance(exc, LLMError) else "Claude stopped responding.") if cloud                 else "The local model stopped responding. Is Ollama running?"
            yield _event(type="error", message=message)
            return
        reply = strip_unknown_handles("".join(parts), sources)
        if not reply.strip():
            yield _event(type="error", message="The model returned an empty reply.")
            return
        with state.pool.connection() as conn:
            msg_id = conversations.add_message(
                conn, conv_id, "assistant", reply, model, sources or None, steps=steps or None, usage=usage
            )
        yield _event(type="done", message_id=msg_id, model=model, usage=usage)
        if proposal:
            yield _event(type="proposal", proposal=proposal)

    return StreamingResponse(generate(), media_type="application/x-ndjson")
