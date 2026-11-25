"""Claude provider. No test talks to Anthropic: the gateway runs on a mock transport, the API on a fake."""

import json
from collections.abc import Iterator

import httpx
import pytest

from pal import claude
from pal.llm import LLMError


# ---------- gateway (mock transport) ----------

def _sse(*events: dict) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def _gateway(handler) -> claude.ClaudeGateway:
    return claude.ClaudeGateway("sk-ant-test", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_streams_text_and_usage_and_sends_the_right_request():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=_sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}},
            {"type": "message_delta", "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ))

    usage: dict = {}
    prompt = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert "".join(_gateway(handler).stream_chat(prompt, "claude-sonnet-5-5", usage)) == "Hello"
    assert usage == {"prompt_tokens": 12, "completion_tokens": 2}
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant-test"
    assert seen["headers"]["anthropic-version"] == claude.API_VERSION
    body = seen["body"]
    assert (body["model"], body["system"], body["stream"]) == ("claude-sonnet-5-5", "be brief", True)
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]


def test_bad_key_and_stream_errors_become_llm_errors():
    gw = _gateway(lambda req: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(LLMError, match="rejected the API key"):
        list(gw.stream_chat([{"role": "user", "content": "hi"}], "claude-sonnet-5-5"))
    gw = _gateway(lambda req: httpx.Response(200, content=_sse({"type": "error", "error": {"message": "overloaded"}})))
    with pytest.raises(LLMError, match="overloaded"):
        list(gw.stream_chat([{"role": "user", "content": "hi"}], "claude-sonnet-5-5"))


def test_only_allowlisted_models():
    gw = _gateway(lambda req: pytest.fail("must not send"))
    with pytest.raises(LLMError):
        list(gw.stream_chat([{"role": "user", "content": "hi"}], "qwen3.5:4b"))


def test_turns_alternate_and_start_with_the_user():
    system, turns = claude.to_claude([
        {"role": "system", "content": "S"},
        {"role": "assistant", "content": "orphan"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
    ])
    assert system == "S"
    assert turns == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}]


def test_key_format_is_checked_before_storing():
    with pytest.raises(ValueError):
        claude.store_key("not-a-key")


# ---------- API (fake gateway) ----------

class FakeClaude:
    allowed = frozenset(claude.MODELS)

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def stream_chat(self, messages, model, usage=None) -> Iterator[str]:
        self.calls.append(messages)
        yield "from claude"
        if usage is not None:
            usage.update(prompt_tokens=5, completion_tokens=2)


def _send(client, conv_id: str, text: str) -> list[dict]:
    resp = client.post(f"/api/conversations/{conv_id}/messages", json={"content": text})
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines() if line]


def test_claude_models_unavailable_without_a_key(authed):
    listed = authed.get("/api/models").json()
    assert listed["claude_key"] is False
    assert {m["name"]: m["installed"] for m in listed["models"] if m["provider"] == "claude"} == \
        dict.fromkeys(claude.MODELS, False)
    conv = authed.post("/api/conversations").json()
    assert authed.patch(f"/api/conversations/{conv['id']}", json={"model": "claude-sonnet-5-5"}).status_code == 422


def test_claude_chat_gets_only_its_own_text(authed, db, llm):
    fake = FakeClaude()
    authed.app.state.claude = fake
    db.execute("INSERT INTO memories (text, kind, origin, status, pinned) "
               "VALUES ('Secret: my locker code is 4471', 'fact', 'stated', 'confirmed', true)")

    local = authed.post("/api/conversations").json()
    _send(authed, local["id"], "what is my locker code?")
    assert "4471" in json.dumps(llm.calls[-1])  # local chats do see memories

    conv = authed.post("/api/conversations").json()
    assert authed.patch(f"/api/conversations/{conv['id']}", json={"model": "claude-opus-5-5"}).status_code == 200
    events = _send(authed, conv["id"], "what is my locker code?")
    assert [e for e in events if e["type"] == "done"][0]["model"] == "claude-opus-5-5"
    sent = json.dumps(fake.calls[-1])
    assert "4471" not in sent and "<context>" not in sent
    assert "what is my locker code?" in sent
    assert len(llm.calls) == 1  # the local model wasn't asked for the Claude chat's reply


def test_claude_chat_without_key_is_refused(authed, db):
    conv = authed.post("/api/conversations").json()
    db.execute("UPDATE conversations SET model = 'claude-sonnet-5-5' WHERE id = %s", (conv["id"],))
    resp = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hi"})
    assert resp.status_code == 409


# ---------- /model (switch the model mid-chat, Hermes-style) ----------

def _model_of(client, conv_id: str):
    return client.get(f"/api/conversations/{conv_id}").json()["model"]


def test_model_command_lists_and_switches_local_models(authed, llm):
    conv = authed.post("/api/conversations").json()["id"]
    listing = "".join(e.get("text", "") for e in _send(authed, conv, "/model"))
    assert "fake-model" in listing and "fake-large" in listing and "(default)" in listing

    events = _send(authed, conv, "/model large")  # a unique part of the name is enough
    assert {"type": "model_changed", "model": "fake-large"} in events
    assert _model_of(authed, conv) == "fake-large"
    _send(authed, conv, "hello")
    assert llm.models_used[-1] == "fake-large"
    assert llm.calls and not any("/model" in json.dumps(c) for c in llm.calls)  # commands stay out of history

    _send(authed, conv, "/model default")
    assert _model_of(authed, conv) is None


def test_model_command_refuses_unknown_ambiguous_and_keyless_claude(authed):
    conv = authed.post("/api/conversations").json()["id"]
    for arg, words in (("gpt-9", "No model"), ("fake", "Which one"), ("sonnet", "API key")):
        reply = "".join(e.get("text", "") for e in _send(authed, conv, f"/model {arg}"))
        assert words in reply, (arg, reply)
    assert _model_of(authed, conv) is None


def test_model_command_switches_to_claude_with_a_warning(authed):
    authed.app.state.claude = FakeClaude()
    conv = authed.post("/api/conversations").json()["id"]
    events = _send(authed, conv, "/model sonnet")
    reply = "".join(e.get("text", "") for e in events)
    assert _model_of(authed, conv) == "claude-sonnet-5-5"
    assert "Anthropic" in reply


# ---------- switching mid-chat: earlier local turns stay on the PC ----------

def test_switching_to_claude_sends_only_messages_after_the_switch(authed, llm):
    fake = FakeClaude()
    authed.app.state.claude = fake
    conv = authed.post("/api/conversations").json()["id"]
    llm.reply = ["Your locker code is 4471."]  # a local reply that quoted a memory
    _send(authed, conv, "what's my locker code?")
    _send(authed, conv, "/model sonnet")
    _send(authed, conv, "write a haiku about tea")
    sent = json.dumps(fake.calls[-1])
    assert "4471" not in sent and "locker" not in sent
    assert "haiku about tea" in sent

    _send(authed, conv, "another one")  # later Claude turns keep the Claude part of the chat
    sent = json.dumps(fake.calls[-1])
    assert "haiku about tea" in sent and "from claude" in sent and "4471" not in sent


# ---------- prompt caching ----------

def test_request_marks_the_history_for_caching_and_counts_cached_tokens():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=_sse(
            {"type": "message_start", "message": {"usage": {
                "input_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
            {"type": "message_delta", "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ))

    usage: dict = {}
    prompt = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"},
              {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
    list(_gateway(handler).stream_chat(prompt, "claude-sonnet-5-5", usage))
    last = seen["body"]["messages"][-1]["content"]
    assert last == [{"type": "text", "text": "c", "cache_control": {"type": "ephemeral"}}]
    assert seen["body"]["messages"][0]["content"] == "a"  # only the newest turn carries the marker
    assert usage == {"prompt_tokens": 960, "completion_tokens": 1}
