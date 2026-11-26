"""Model gateway: Ollama on loopback, allowlisted models, explicit context size."""

import json
from collections.abc import Iterator
from urllib.parse import urlparse

import httpx

from .thinking import Thought, ollama_think

ALLOWED_MODELS = frozenset({"qwen3.5:4b"})
EMBED_MODELS = frozenset({"nomic-embed-text"})
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class LLMError(RuntimeError):
    pass


class OllamaGateway:
    def __init__(
        self,
        base_url: str,
        model: str,
        num_ctx: int,
        client: httpx.Client | None = None,
        embed_model: str = "nomic-embed-text",
        extra_models: frozenset[str] | tuple[str, ...] = (),
    ) -> None:
        # The owner can add locally installed models in Settings; the gateway still only talks to loopback.
        self.allowed = ALLOWED_MODELS | frozenset(extra_models)
        if model not in self.allowed or embed_model not in EMBED_MODELS:
            raise ValueError(f"model {model!r} / {embed_model!r} is not on the allowlist")
        self.embed_model = embed_model
        if urlparse(base_url).hostname not in _LOOPBACK:
            raise ValueError("Ollama must be reached on loopback")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0, read=300.0))

    def check_model(self, model: str | None) -> str:
        chosen = model or self.model
        if chosen not in self.allowed:
            raise LLMError(f"model {chosen!r} is not on the allowlist")
        return chosen

    def stream_chat(
        self, messages: list[dict], model: str | None = None, usage: dict | None = None, thinking: str = "off"
    ) -> Iterator[str]:
        """Yields reply text, and `Thought` chunks of reasoning when thinking is on.

        When `usage` is given it is filled with Ollama's token counts at the end.
        """
        chosen = self.check_model(model)
        body = {
            "model": chosen,
            "messages": messages,
            "stream": True,
            "think": ollama_think(chosen, thinking),
            "options": {"num_ctx": self.num_ctx},
        }
        try:
            with self._client.stream("POST", f"{self.base_url}/api/chat", json=body) as resp:
                if resp.status_code != 200:
                    resp.read()
                    raise LLMError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise LLMError(data["error"])
                    thought = data.get("message", {}).get("thinking")
                    if thought:
                        yield Thought(thought)
                    text = data.get("message", {}).get("content")
                    if text:
                        yield text
                    if data.get("done"):
                        if usage is not None:
                            usage["prompt_tokens"] = data.get("prompt_eval_count")
                            usage["completion_tokens"] = data.get("eval_count")
                        return
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach Ollama: {exc}") from exc

    def complete_json(self, messages: list[dict], schema: dict) -> str:
        """One non-streaming call constrained to `schema` (Ollama structured output). Returns raw text."""
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": schema,
            "options": {"num_ctx": self.num_ctx, "temperature": 0},
        }
        try:
            resp = self._client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach Ollama: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("message", {}).get("content", "")

    def installed_models(self) -> list[str]:
        """Model names Ollama has on this PC (raises LLMError when Ollama is down)."""
        try:
            resp = self._client.get(f"{self.base_url}/api/tags", timeout=3.0)
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach Ollama: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"Ollama returned {resp.status_code}")
        return sorted(m.get("name", "") for m in resp.json().get("models", []) if m.get("name"))

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Unit-length 768-d vectors. Callers add nomic's 'search_query: ' / 'search_document: ' prefixes."""
        try:
            resp = self._client.post(
                f"{self.base_url}/api/embed", json={"model": self.embed_model, "input": texts, "truncate": True}
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach Ollama: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
        vectors = resp.json().get("embeddings") or []
        if len(vectors) != len(texts):
            raise LLMError("Ollama returned the wrong number of embeddings")
        return vectors
