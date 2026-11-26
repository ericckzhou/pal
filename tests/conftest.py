import math
import os
import re
import zlib
from collections.abc import Iterator
from dataclasses import replace

import psycopg
import pytest
from fastapi.testclient import TestClient

from pal.app import create_app
from pal.config import Settings, dsn
from pal.migrate import migrate

# Tests read PDFs with pypdf even where Docling is installed; tests/test_pdf.py covers Docling on its own.
os.environ["PAL_PDF_ENGINE"] = "pypdf"
TEST_DB = os.environ.get("PAL_TEST_DB", "pal_test")  # set per session when several test runs share the server
PASSWORD = "correct horse battery staple"
PUBLIC_HOST = "pc.example.ts.net"  # stands in for the Tailscale Serve name


class FakeLLM:
    model = "fake-model"
    allowed = frozenset({"fake-model", "fake-large"})

    def __init__(self) -> None:
        self.reply = ["Hel", "lo ", "there"]
        self.fail = False
        self.calls: list[list[dict]] = []
        self.json_reply = '{"memories": []}'
        self.json_calls: list[list[dict]] = []
        self.embed_fail = False
        self.embed_calls = 0
        self.models_used: list[str] = []
        self.ollama_down = False
        self.thinking_used: list[str] = []
        self.thoughts: list[str] = []

    def stream_chat(
        self, messages: list[dict], model: str | None = None, usage: dict | None = None, thinking: str = "off"
    ) -> Iterator[str]:
        from pal.thinking import Thought

        self.calls.append(messages)
        self.models_used.append(model or self.model)
        self.thinking_used.append(thinking)
        if thinking != "off":
            yield from (Thought(t) for t in self.thoughts)
        for part in self.reply:
            yield part
        if self.fail:
            raise RuntimeError("model crashed")
        if usage is not None:
            usage.update(prompt_tokens=100, completion_tokens=len(self.reply))

    def installed_models(self) -> list[str]:
        if self.ollama_down:
            from pal.llm import LLMError

            raise LLMError("could not reach Ollama")
        return ["fake-model", "fake-large", "other:7b"]

    def complete_json(self, messages: list[dict], schema: dict) -> str:
        self.json_calls.append(messages)
        return self.json_reply

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Deterministic bag-of-words vectors on the real model's scale.

        Like nomic-embed-text, unrelated texts still score about 0.44 (a shared component in dim 0) and texts
        sharing words score higher, so pal's similarity cut-offs behave the same way in tests.
        """
        if self.embed_fail:
            raise RuntimeError("embedding model down")
        self.embed_calls += 1
        out = []
        for text in texts:
            text = re.sub(r"^search_(query|document): ", "", text).lower()
            vec = [0.0] * 768
            for word in re.findall(r"[a-z0-9]+", text):
                if word not in STOP:
                    vec[1 + zlib.crc32(word.rstrip("s").encode()) % 767] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            vec[0] = BASELINE  # bag-of-words dims are 1..767 below; dim 0 is the shared component
            scale = math.sqrt(sum(x * x for x in vec))
            out.append([x / scale for x in vec])
        return out


BASELINE = math.sqrt(0.8)  # unrelated similarity = 0.8 / 1.8 = 0.44
STOP = frozenset("a an the is are was my me i you your what when where how do does of to in on for and or it".split())

@pytest.fixture(scope="session")
def settings() -> Settings:
    return replace(
        Settings(),
        db_name=TEST_DB,
        chat_model="fake-model",  # matches FakeLLM, so settings checks see an allowed default
        allowed_hosts=("testserver", "127.0.0.1:8765"),
        public_host=PUBLIC_HOST,
    )


@pytest.fixture(scope="session")
def _database(settings: Settings) -> None:
    with psycopg.connect(dsn(settings, "pal"), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    migrate(dsn(settings))


@pytest.fixture
def db(settings: Settings, _database: None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(dsn(settings), autocommit=True) as conn:
        conn.execute("TRUNCATE owner, sessions, devices, passkeys, pairing_codes, conversations, messages, memories, documents, chunks, projects, tasks, notes, "
            "reminders, notifications, proposals, app_settings, digests CASCADE")
        yield conn


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client(settings: Settings, db: psycopg.Connection, llm: FakeLLM) -> Iterator[TestClient]:
    app = create_app(settings, llm=llm, start_background=False)
    with TestClient(app, base_url="http://testserver", headers={"X-Pal-Request": "1"}) as c:
        yield c


@pytest.fixture
def authed(client: TestClient, db: psycopg.Connection) -> TestClient:
    from pal.auth import set_password

    set_password(db, PASSWORD)
    resp = client.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 200
    return client


@pytest.fixture
def remote(settings: Settings, db: psycopg.Connection, llm: FakeLLM) -> Iterator[TestClient]:
    """A browser on a phone, reaching pal through Tailscale Serve (https, tailnet name)."""
    app = create_app(settings, llm=llm, start_background=False)
    with TestClient(app, base_url=f"https://{PUBLIC_HOST}", headers={"X-Pal-Request": "1"}) as c:
        yield c
