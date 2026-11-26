"""Thinking levels: request bodies per provider, /think, PATCH, the Settings default, and streamed reasoning."""

import json

import httpx
import pytest

from pal import claude, thinking
from pal.llm import OllamaGateway
from pal.thinking import Thought


def _send(client, conv_id: str, text: str) -> list[dict]:
    resp = client.post(f"/api/conversations/{conv_id}/messages", json={"content": text})
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines() if line]


# ---------- gateways ----------

def _ollama(model: str, seen: dict) -> OllamaGateway:
    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        lines = [{"message": {"thinking": "hmm"}}, {"message": {"content": "ok"}, "done": True}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaGateway("http://127.0.0.1:11434", model, 8192, client=client, extra_models=(model,))


@pytest.mark.parametrize(("model", "level", "think"), [
    ("qwen3.5:4b", "off", False),
    ("qwen3.5:4b", "high", True),
    ("gpt-oss:20b", "medium", "medium"),
    ("gpt-oss:20b", "off", False),
])
def test_ollama_think_field(model, level, think):
    seen: dict = {}
    out = list(_ollama(model, seen).stream_chat([{"role": "user", "content": "hi"}], thinking=level))
    assert seen["body"]["think"] == think
    assert out == ["hmm", "ok"] and isinstance(out[0], Thought) and not isinstance(out[1], Thought)


def test_ollama_default_is_off():
    seen: dict = {}
    list(_ollama("qwen3.5:4b", seen).stream_chat([{"role": "user", "content": "hi"}]))
    assert seen["body"]["think"] is False


def _sse(*events: dict) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


@pytest.mark.parametrize("level", ["off", "low", "high"])
def test_claude_request_and_thinking_deltas(level):
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=_sse(
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "let me see"}},
            {"type": "content_block_delta", "delta": {"type": "signature_delta", "signature": "x"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
            {"type": "message_stop"},
        ))

    gw = claude.ClaudeGateway("sk-ant-test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = list(gw.stream_chat([{"role": "user", "content": "hi"}], "claude-sonnet-5-5", thinking=level))
    assert [str(x) for x in out] == ["let me see", "Hi"] and isinstance(out[0], Thought)
    body = seen["body"]
    if level == "off":
        assert "thinking" not in body and "output_config" not in body and body["max_tokens"] == claude.MAX_TOKENS
    else:
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": level}
        assert body["max_tokens"] == thinking.CLAUDE_MAX_TOKENS


def test_resolve_falls_back_to_default_then_off():
    assert thinking.resolve("high", "off") == "high"
    assert thinking.resolve(None, "low") == "low"
    assert thinking.resolve(None, "bogus") == "off"


# ---------- API ----------

def test_think_command_sets_level_and_reply_streams_reasoning(authed, llm):
    llm.thoughts = ["step one. ", "step two."]
    conv = authed.post("/api/conversations").json()["id"]

    shown = "".join(e.get("text", "") for e in _send(authed, conv, "/think"))
    assert "**off**" in shown and "default" in shown

    events = _send(authed, conv, "/think high")
    assert {"type": "thinking_changed", "thinking": "high"} in events
    assert authed.get(f"/api/conversations/{conv}").json()["thinking"] == "high"

    events = _send(authed, conv, "why is the sky blue?")
    assert llm.thinking_used[-1] == "high"
    assert "".join(e["text"] for e in events if e["type"] == "thinking") == "step one. step two."
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Hello there"
    stored = authed.get(f"/api/conversations/{conv}").json()["messages"][-1]
    assert stored["content"] == "Hello there" and "step one" not in json.dumps(stored)  # reasoning isn't kept

    _send(authed, conv, "/think default")
    assert authed.get(f"/api/conversations/{conv}").json()["thinking"] is None


def test_think_command_rejects_unknown_level(authed):
    conv = authed.post("/api/conversations").json()["id"]
    events = _send(authed, conv, "/think extreme")
    assert not any(e["type"] == "thinking_changed" for e in events)
    assert "isn't a level" in "".join(e.get("text", "") for e in events)


def test_patch_sets_and_validates_thinking(authed, llm):
    conv = authed.post("/api/conversations").json()["id"]
    resp = authed.patch(f"/api/conversations/{conv}", json={"thinking": "low"})
    assert resp.status_code == 200 and resp.json()["thinking"] == "low"
    assert authed.patch(f"/api/conversations/{conv}", json={"thinking": "max"}).status_code == 422
    _send(authed, conv, "hi")
    assert llm.thinking_used[-1] == "low"


def test_settings_default_applies_to_chats_without_a_level(authed, llm):
    resp = authed.put("/api/settings", json={"thinking": "medium"})
    assert resp.status_code == 200, resp.text
    conv = authed.post("/api/conversations").json()["id"]
    _send(authed, conv, "hi")
    assert llm.thinking_used[-1] == "medium"
    assert authed.put("/api/settings", json={"thinking": "huge"}).status_code == 422
    field = next(f for f in authed.get("/api/settings").json()["fields"] if f["key"] == "thinking")
    assert field["choices"] == list(thinking.LEVELS) and field["value"] == "medium"
