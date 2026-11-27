"""Application factory: one process serving the API and the built web UI."""

import asyncio
import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool

from . import (
    api,
    brief,
    claude,
    dashboard_api,
    devices,
    devices_api,
    documents_api,
    embed,
    encryption,
    ingest,
    logbuffer,
    memory,
    memory_api,
    memory_replace,
    reminders,
    reread,
    review,
    review_api,
    security,
    settings_store,
    work_api,
)
from .auth import LoginLimiter
from .backup import start_nightly
from .config import Settings, dsn
from .llm import OllamaGateway

log = logging.getLogger("pal.app")
MEMORY_TICK_S = 60
EMBED_TICK_S = 5
REMINDER_TICK_S = 30
RESCAN_CHECK_S = 60  # how often the rescan loop looks at its interval setting
SHUTDOWN_WAIT_S = 15  # how long shutdown waits for a background job to finish the file or batch it is on


class BackgroundJobs:
    """Where the background loops run their blocking work, so shutdown can tell it to stop.

    Cancelling an asyncio task doesn't stop a thread it started: a folder rescan would keep reading PDFs after the
    database pool closed, and the process would wait for it on exit. Long jobs check `stop` between files or batches;
    `shutdown` sets it and waits up to SHUTDOWN_WAIT_S for whatever is still running.
    """

    def __init__(self) -> None:
        self.stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pal-bg")
        self._active = 0
        self._lock = threading.Lock()

    def _call(self, fn, *args):
        with self._lock:
            self._active += 1
        try:
            return fn(*args)
        finally:
            with self._lock:
                self._active -= 1

    async def run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, functools.partial(self._call, fn, *args))

    async def shutdown(self) -> None:
        self.stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + SHUTDOWN_WAIT_S
        while self._active and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if self._active:
            log.warning("shutting down with %d background job(s) still running", self._active)


async def _memory_loop(app: FastAPI, idle_minutes: int) -> None:
    while True:
        await asyncio.sleep(MEMORY_TICK_S)
        try:
            n = await app.state.jobs.run(memory.scan_idle, app.state.pool, app.state.llm, idle_minutes)
            if n:
                log.info("proposed %d memories", n)
            r = await app.state.jobs.run(memory_replace.check_pending, app.state.pool, app.state.llm)
            if r:
                log.info("suggested %d memory replacements", r)
        except Exception:
            log.exception("memory tick failed")


async def _embed_loop(app: FastAPI) -> None:
    """Give new chunks, memories and messages a vector. Quiet while Ollama is down."""
    while True:
        await asyncio.sleep(EMBED_TICK_S)
        try:
            n = await app.state.jobs.run(embed.embed_all, app.state.pool, app.state.llm, app.state.jobs.stop)
            if n:
                log.info("embedded %d items", n)
        except Exception as exc:
            log.warning("embedding tick failed: %s", exc)
            await asyncio.sleep(60)


async def _reminder_loop(app: FastAPI) -> None:
    while True:
        try:
            n = await app.state.jobs.run(reminders.tick, app.state.pool)
            if n:
                log.info("fired %d reminders", n)
        except Exception:
            log.exception("reminder tick failed")
        try:
            made = await app.state.jobs.run(review.tick, app.state.pool)
            if made:
                log.info("weekly digest made for %s", made["week_start"])
        except Exception:
            log.exception("weekly digest failed")
        try:
            sent = await app.state.jobs.run(brief.tick, app.state.pool, app.state.settings.brief_hour)
            if sent:
                log.info("morning brief sent")
        except Exception:
            log.exception("morning brief failed")
        await asyncio.sleep(REMINDER_TICK_S)


async def _rescan_loop(app: FastAPI) -> None:
    """Check added folders for new or changed files. The interval is read each time, so a settings change applies."""
    last: float | None = None  # never run: the first check runs it (monotonic time can start near 0 after a boot)
    while True:
        await asyncio.sleep(RESCAN_CHECK_S)
        minutes = app.state.settings.rescan_minutes
        if minutes == 0 or (last is not None and time.monotonic() - last < minutes * 60):
            continue
        last = time.monotonic()
        try:
            found = await app.state.jobs.run(_rescan, app.state.pool, app.state.jobs.stop)
            if found["added"] or found["updated"]:
                log.info("folder rescan: %d added, %d updated", found["added"], found["updated"])
            if found["missing"]:
                log.warning("folder rescan: %d folder(s) not found: %s", len(found["missing"]), ", ".join(found["missing"]))
        except Exception:
            log.exception("folder rescan failed")


def _rescan(pool, stop: threading.Event) -> dict:
    with pool.connection() as conn:
        return ingest.rescan_folders(conn, stop)


WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
ROOT_FILES = {"manifest.webmanifest": "application/manifest+json", "icon.svg": "image/svg+xml"}


def _claude_from_keyring():
    try:
        key = claude.load_key()
    except Exception:
        log.exception("could not read the Anthropic API key")
        return None
    return claude.ClaudeGateway(key) if key else None


def create_app(settings: Settings | None = None, llm=None, start_background: bool = True, claude_gw=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = ConnectionPool(dsn(settings), min_size=1, max_size=8, kwargs={"autocommit": True}, open=True)
        try:
            with app.state.pool.connection() as conn:
                settings_store.install(app, settings_store.apply(settings, settings_store.load(conn)))
        except Exception:
            log.exception("saved settings could not be applied; using defaults")
        live = app.state.settings
        app.state.jobs = BackgroundJobs()
        tasks: list[asyncio.Task] = []
        if start_background:
            start_nightly(live.backup_dir, live.db_name, live.backup_keep)
            encryption.start_check()
            tasks.append(asyncio.create_task(_memory_loop(app, live.memory_idle_minutes)))
            tasks.append(asyncio.create_task(_embed_loop(app)))
            tasks.append(asyncio.create_task(_reminder_loop(app)))
            tasks.append(asyncio.create_task(_rescan_loop(app)))
        try:
            yield
        finally:
            reread.stop()  # the owner-started PDF re-read stops after its current file
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await app.state.jobs.shutdown()
            app.state.pool.close()

    app = FastAPI(title="pal", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    logbuffer.install()
    app.state.settings = settings
    app.state.base_settings = settings  # built-in values; the Settings page layers saved changes over these
    app.state.llm = llm or OllamaGateway(
        settings.ollama_url, settings.chat_model, settings.num_ctx, extra_models=settings.extra_models
    )
    # Claude is opt-in per chat and exists only when a key is stored (`pal claude-key --set`).
    app.state.claude = claude_gw if claude_gw is not None else (_claude_from_keyring() if llm is None else None)
    app.state.limiter = LoginLimiter()

    app.state.pair_limiter = LoginLimiter()
    app.state.ceremonies = devices.Ceremonies()

    security.install(app, settings.all_hosts, settings.allowed_origins)
    app.include_router(api.public)
    app.include_router(api.private)
    app.include_router(devices_api.public)
    app.include_router(devices_api.owner)
    app.include_router(memory_api.router)
    app.include_router(documents_api.router)
    app.include_router(work_api.router)
    app.include_router(review_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(dashboard_api.owner)

    if (WEB_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        if path in ROOT_FILES and (WEB_DIST / path).is_file():  # PWA manifest and icon
            return FileResponse(WEB_DIST / path, media_type=ROOT_FILES[path])
        index = WEB_DIST / "index.html"
        if not index.exists():
            raise HTTPException(status_code=503, detail="Web UI not built. Run: pnpm --dir web build")
        return FileResponse(index)

    return app
