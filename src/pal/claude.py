"""Claude provider: opt-in per conversation, only that conversation's own text is sent.

The API key lives in Windows Credential Manager (service "pal", account "anthropic-api-key"); it is never in a
file, the database or the web UI. With no key stored, no Claude model is offered and the egress guard keeps
api.anthropic.com blocked.
"""

import json
from collections.abc import Iterator

import httpx

from .llm import LLMError
from .thinking import Thought, claude_params

API_HOST = "api.anthropic.com"
API_URL = f"https://{API_HOST}/v1/messages"
API_VERSION = "2023-06-01"
KEYRING_ACCOUNT = "anthropic-api-key"
MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")
MAX_TOKENS = 4096


def is_claude(model: str | None) -> bool:
    return bool(model) and model in MODELS


def load_key() -> str | None:
    import keyring

    from .config import KEYRING_SERVICE

    return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or None


def store_key(key: str) -> None:
    import keyring

    from .config import KEYRING_SERVICE

    key = key.strip()
    if not key.startswith("sk-ant-"):
        raise ValueError("That doesn't look like an Anthropic API key (they start with sk-ant-).")
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, key)


def clear_key() -> bool:
    import keyring
    from keyring.errors import PasswordDeleteError

    from .config import KEYRING_SERVICE

    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        return True
    except PasswordDeleteError:
        return False


def since_switch(past: list[dict]) -> list[dict]:
    """The part of a chat Claude may see: everything after the last reply a local model wrote.

    A chat switched to Claude midway keeps its earlier local turns on the PC, since those replies may quote
    memories or documents. `past` rows carry `model`; the result has only role and content.
    """
    cut = 0
    for i, m in enumerate(past):
        if m["role"] == "assistant" and not is_claude(m.get("model")):
            cut = i + 1
    return [{"role": m["role"], "content": m["content"]} for m in past[cut:]]


def to_claude(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split pal's prompt into Claude's (system, messages): roles alternate and the first one is the user's."""
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    turns: list[dict] = []
    for m in messages:
        if m["role"] not in ("user", "assistant") or not m["content"].strip():
            continue
        if turns and turns[-1]["role"] == m["role"]:
            turns[-1] = {"role": m["role"], "content": turns[-1]["content"] + "\n\n" + m["content"]}
        else:
            turns.append({"role": m["role"], "content": m["content"]})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return system, turns


class ClaudeGateway:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("no Anthropic API key")
        self._key = api_key
        self.allowed = frozenset(MODELS)
        self._client = client or httpx.Client(timeout=httpx.Timeout(15.0, read=300.0))

    def stream_chat(
        self, messages: list[dict], model: str, usage: dict | None = None, thinking: str = "off"
    ) -> Iterator[str]:
        if model not in self.allowed:
            raise LLMError(f"model {model!r} is not an allowed Claude model")
        system, turns = to_claude(messages)
        if not turns:
            raise LLMError("nothing to send")
        # Cache everything up to the newest turn, so the next message re-reads the history at the cached rate.
        newest = turns[-1]
        turns = turns[:-1] + [{"role": newest["role"], "content": [
            {"type": "text", "text": newest["content"], "cache_control": {"type": "ephemeral"}}]}]
        body = {"model": model, "max_tokens": MAX_TOKENS, "system": system, "messages": turns, "stream": True,
                **claude_params(thinking)}
        headers = {"x-api-key": self._key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        try:
            with self._client.stream("POST", API_URL, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    resp.read()
                    raise LLMError(_http_error(resp))
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:].strip() or "{}")
                    kind = data.get("type")
                    delta = data.get("delta", {}) if kind == "content_block_delta" else {}
                    if delta.get("type") == "text_delta":
                        yield delta.get("text", "")
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        yield Thought(delta["thinking"])
                    elif kind == "message_start" and usage is not None:
                        u = data.get("message", {}).get("usage", {})
                        # Cached tokens are still prompt tokens; Analytics counts what the model read.
                        usage["prompt_tokens"] = sum(u.get(k) or 0 for k in (
                            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                    elif kind == "message_delta" and usage is not None:
                        usage["completion_tokens"] = data.get("usage", {}).get("output_tokens")
                    elif kind == "error":
                        raise LLMError(f"Claude error: {data.get('error', {}).get('message', 'unknown')}")
                    elif kind == "message_stop":
                        return
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach Claude: {exc}") from exc


def _http_error(resp: httpx.Response) -> str:
    if resp.status_code == 401:
        return "Claude rejected the API key. Set a new one with: uv run pal claude-key --set"
    if resp.status_code == 429:
        return "Claude is rate-limiting this key. Try again in a minute."
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = resp.text[:200]
    return f"Claude returned {resp.status_code}: {detail}"
