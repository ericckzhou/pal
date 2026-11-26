"""Thinking levels: how much a model reasons before it replies (Claude's effort, Hermes Agent's /reasoning).

Each chat may pick a level; NULL means the Settings default. Reasoning text is streamed to the screen while the
reply is written, but it is never stored: not in messages, history, prompts or backups.
"""

from uuid import UUID

import psycopg

LEVELS = ("off", "low", "medium", "high")
RESET_WORDS = ("default", "reset", "none")
# Ollama models that take a named level for `think`; every other model takes true/false.
_NAMED_LEVEL_MODELS = ("gpt-oss",)
CLAUDE_MAX_TOKENS = 16000  # room for reasoning plus the reply when Claude thinks


class Thought(str):
    """A chunk of the model's reasoning, as opposed to reply text. Gateways yield both from stream_chat."""


def resolve(chosen: str | None, default: str) -> str:
    return chosen if chosen in LEVELS else default if default in LEVELS else "off"


def ollama_think(model: str, level: str) -> bool | str:
    if level == "off":
        return False
    return level if model.split(":")[0] in _NAMED_LEVEL_MODELS else True


def claude_params(level: str) -> dict:
    """Extra request fields for Claude: adaptive thinking with an effort level, or nothing when off."""
    if level == "off":
        return {}
    return {"thinking": {"type": "adaptive"}, "output_config": {"effort": level}, "max_tokens": CLAUDE_MAX_TOKENS}


def run(conn: psycopg.Connection, conv_id: UUID, arg: str, default: str) -> dict:
    """`/think`: show or set this chat's level. Returns {reply, thinking_changed, thinking}."""
    current = conn.execute("SELECT thinking FROM conversations WHERE id = %s", (conv_id,)).fetchone()[0]
    arg = arg.strip().lower()
    if not arg:
        in_use = resolve(current, default)
        source = "this chat's setting" if current else "the default"
        return {"reply": f"Thinking is **{in_use}** ({source}). Levels: {', '.join(f'`{x}`' for x in LEVELS)}. "
                         "Set with `/think high`, or `/think default`.", "thinking_changed": False, "thinking": current}
    if arg in RESET_WORDS:
        return _set(conn, conv_id, None, f"Back to the default thinking level, **{resolve(None, default)}**.")
    if arg not in LEVELS:
        return {"reply": f"`{arg}` isn't a level. Pick one of {', '.join(f'`{x}`' for x in LEVELS)}.",
                "thinking_changed": False, "thinking": current}
    note = "" if arg == "off" else " Replies may take longer; models that can't think will say so."
    return _set(conn, conv_id, arg, f"Thinking set to **{arg}** for this chat.{note}")


def _set(conn: psycopg.Connection, conv_id: UUID, level: str | None, reply: str) -> dict:
    conn.execute("UPDATE conversations SET thinking = %s WHERE id = %s", (level, conv_id))
    return {"reply": reply, "thinking_changed": True, "thinking": level}
