"""`/model`: see or switch this chat's model without leaving the conversation (as in Hermes Agent)."""

from uuid import UUID

import psycopg

from . import claude, db

RESET_WORDS = ("default", "reset", "none")


def _match(arg: str, names: list[str]) -> list[str]:
    """Exact name first, then every name containing `arg` (so `/model sonnet` finds claude-sonnet-5-5)."""
    arg = arg.lower()
    exact = [n for n in names if n.lower() == arg]
    return exact or [n for n in names if arg in n.lower()]


def run(conn: psycopg.Connection, conv_id: UUID, arg: str, local: list[str], default: str, has_claude: bool) -> dict:
    """Returns {reply, model_changed: bool, model}. `local` is the allowlist of models on this PC."""
    current = db.scalar(conn, "SELECT model FROM conversations WHERE id = %s", (conv_id,))
    arg = arg.strip()
    if not arg:
        return {"reply": _listing(current, local, default, has_claude), "model_changed": False, "model": current}
    if arg.lower() in RESET_WORDS:
        return _switch(conn, conv_id, None, f"Back to the default model, **{default}**.")

    found = _match(arg, sorted(local) + list(claude.MODELS))
    if not found:
        return {"reply": f"No model matches `{arg}`. Type `/model` to see the list.", "model_changed": False,
                "model": current}
    if len(found) > 1:
        options = ", ".join(f"`{n}`" for n in found)
        return {"reply": f"Which one? {options}", "model_changed": False, "model": current}
    name = found[0]
    if claude.is_claude(name):
        if not has_claude:
            return {"reply": "Claude needs an API key first. On the PC run `uv run pal claude-key --set`, then "
                             "restart pal.", "model_changed": False, "model": current}
        return _switch(conn, conv_id, name, f"This chat now uses **{name}**. From your next message, this chat's "
                       "messages are sent to Anthropic. Memories, documents and notes stay on this PC. "
                       "`/model default` switches back.")
    return _switch(conn, conv_id, name, f"This chat now uses **{name}**.")


def _switch(conn: psycopg.Connection, conv_id: UUID, model: str | None, reply: str) -> dict:
    conn.execute("UPDATE conversations SET model = %s WHERE id = %s", (model, conv_id))
    return {"reply": reply, "model_changed": True, "model": model}


def _listing(current: str | None, local: list[str], default: str, has_claude: bool) -> str:
    in_use = current or default

    def line(name: str, note: str = "") -> str:
        mark = " ← this chat" if name == in_use else ""
        return f"- `{name}`{note}{mark}"

    lines = ["**On this PC**"] + [line(n, " (default)" if n == default else "") for n in sorted(local)]
    lines += ["", "**Claude** (sends this chat to Anthropic)"]
    lines += [line(n) for n in claude.MODELS] if has_claude else ["- No API key stored (`pal claude-key --set`)."]
    lines += ["", "Switch with `/model name` (part of the name is enough), or `/model default`."]
    return "\n".join(lines)
