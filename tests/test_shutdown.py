"""Shutdown: background jobs stop between items, and pal waits a bounded time for the one in flight."""

import asyncio
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from pal import app as app_module
from pal import embed, ingest, memory, reread
from pal.app import create_app
from pal.config import dsn


def test_rescan_stops_between_files_when_asked(db, tmp_path):
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(f"note {name}")
    ingest.ingest_path(db, str(tmp_path))
    for name in ("d.txt", "e.txt"):
        (tmp_path / name).write_text(f"new {name}")
    stop = threading.Event()
    stop.set()
    summary = ingest.rescan_folders(db, stop=stop)
    assert summary["stopped"] is True and summary["added"] == 0
    assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 3
    # Without a stop, the next rescan picks the new files up.
    assert ingest.rescan_folders(db)["added"] == 2


def test_rescan_result_has_no_stopped_key_when_it_finishes(db, tmp_path):
    (tmp_path / "a.txt").write_text("alpha")
    ingest.ingest_path(db, str(tmp_path))
    assert "stopped" not in ingest.rescan_folders(db, stop=threading.Event())


def test_embed_all_stops_between_batches(settings, db, llm):
    for i in range(3):
        memory.create(db, f"fact number {i}", origin="stated", status="confirmed")
    stop = threading.Event()
    stop.set()
    with ConnectionPool(dsn(settings), min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        assert embed.embed_all(pool, llm, stop=stop) == 0
        assert embed.embed_all(pool, llm) == 3


def test_shutdown_signals_jobs_and_waits_for_the_one_in_flight(settings, db, llm, monkeypatch):
    started, finished = threading.Event(), threading.Event()
    seen: dict = {}

    def slow_rescan(pool, stop):
        started.set()
        while not stop.is_set():  # a long folder: keeps going until told to stop
            time.sleep(0.01)
        seen["stop_was_set"] = True
        finished.set()
        return {"added": 0, "updated": 0, "missing": [], "stopped": True}

    monkeypatch.setattr(app_module, "_rescan", slow_rescan)
    monkeypatch.setattr(app_module, "RESCAN_CHECK_S", 0)
    # A just-booted machine: monotonic time starts near 0, yet the first rescan must still run (CI caught this).
    t0 = time.monotonic()
    monkeypatch.setattr(app_module, "time", SimpleNamespace(monotonic=lambda: time.monotonic() - t0 + 1.0))
    monkeypatch.setattr(app_module, "start_nightly", lambda *a: None)
    monkeypatch.setattr(app_module.encryption, "start_check", lambda: None)
    stopped_reread = []
    monkeypatch.setattr(reread, "stop", lambda: stopped_reread.append(True) or {})
    app = create_app(settings, llm=llm, start_background=True)
    with TestClient(app):
        assert started.wait(5)
    assert finished.is_set() and seen["stop_was_set"] and stopped_reread


def test_shutdown_gives_up_waiting_after_the_limit(monkeypatch):
    monkeypatch.setattr(app_module, "SHUTDOWN_WAIT_S", 0.2)
    release = threading.Event()
    jobs = app_module.BackgroundJobs()

    async def run() -> float:
        async def stuck():
            await jobs.run(release.wait, 10)  # ignores the stop signal, like one long PDF parse

        task = asyncio.create_task(stuck())
        await asyncio.sleep(0.05)
        task.cancel()
        t0 = time.monotonic()
        await jobs.shutdown()
        return time.monotonic() - t0

    elapsed = asyncio.run(run())
    release.set()
    assert jobs.stop.is_set() and elapsed < 2
