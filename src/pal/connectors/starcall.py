"""StarcallOS, read-only, over one long-lived local MCP stdio session.

pal starts the StarcallOS MCP server (`node <checkout>/packages/mcp-server/dist/index.js`) as a child process and
keeps one session open for the life of the pal process. pal decides every call: a model never sees these tools.

Trust: StarcallOS is trusted local code at a path the user chose. The child runs as the same Windows user, with no
sandbox and no filesystem isolation, so it can read whatever that user can (keyring entries included). The
environment allowlist below only means pal doesn't *hand over* its own secrets (database login, `PAL_*` settings);
it is not isolation. Native Windows only: the Docker build never constructs a client.

Threads: one dedicated thread runs an asyncio event loop that owns the session. Chat turns run on FastAPI worker
threads and submit calls to that loop. MCP matches each response to its request id, so simultaneous chats share the
session safely; every call carries its own arguments and the client keeps no per-chat state.

Failure handling, all fail-soft (callers catch `StarcallError` and answer without StarcallOS):
- each call has a deadline enforced inside the loop; a timed-out call is cancelled there, the server is told
  (`notifications/cancelled`, best effort), and a late reply for it is dropped by the SDK
- at most `MAX_IN_FLIGHT` calls run at once; past that a turn skips StarcallOS at once (`busy`) instead of queuing
- one timeout keeps the session; `TIMEOUTS_BEFORE_RESTART` in a row, a transport error or the child exiting tear it
  down, and the next start waits out a cooldown that grows with each failure, so a broken setup never spawns a
  process per message
- a server whose tools or results don't match what pal reads is `incompatible_server` and stays off until `check()`
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, TextIO, TypeVar, cast
from urllib.parse import urlparse

import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

MIN_NODE = (22, 13)  # StarcallOS reads its database with node:sqlite
START_TIMEOUT = 10.0  # seconds for the child to finish MCP initialize and list its tools
CALL_TIMEOUT = 4.0  # seconds a chat turn waits for StarcallOS, including waiting for a session to start
SAFETY_MARGIN = 1.0  # the waiting thread's own limit sits this far past the in-loop deadline
MAX_IN_FLIGHT = 4
TIMEOUTS_BEFORE_RESTART = 3
COOLDOWNS = (10.0, 30.0, 120.0, 300.0)  # seconds before the next start after the 1st, 2nd, 3rd, 4th+ failure
CLOSE_TIMEOUT = 3.0
CANCEL_NOTIFY_TIMEOUT = 0.5
STDERR_TAIL = 2000  # characters of the child's stderr kept for an error message
NODE_VERSION_TIMEOUT = 5.0

SCOPES = ("passages", "concepts")  # the only scopes pal asks for: highlights, notes and progress are never used
REQUIRED_TOOLS = {"passages": "search_passages", "concepts": "search_concepts"}
REQUIRED_INPUTS = ("query", "limit")
# Passed explicitly. The SDK adds its own small default set (PATH, SYSTEMROOT, USERPROFILE, ...) on top; neither
# includes pal's secrets.
ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP")
LOOPBACK_HOSTS = frozenset({"localhost"})

# StarcallOS refuses a database behind or ahead of its code. The server has no structured code for that, so it is
# recognised from the message (startup: stderr; per call: the error text).
_SCHEMA = re.compile(r"migration\(s\) behind|upgraded by a newer version|not a StarcallOS database", re.I)
_DB_MISSING = re.compile(r"StarcallOS database not found", re.I)
_NODE_VERSION = re.compile(r"v?(\d+)\.(\d+)")

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)


class StarcallError(Exception):
    """A StarcallOS failure with a stable `kind` for the trace, the Connections page and tests.

    Kinds: node_missing, node_too_old, server_missing, db_missing, start_failed, schema_mismatch,
    incompatible_server, starting, timeout, busy, cooldown, crashed, tool_error, closed.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message

    def as_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message}


# The fields pal reads. Extra fields are ignored; a missing one makes the server incompatible.


class PassageHit(BaseModel):
    chunk_id: int
    source_id: int
    source_title: str
    page_start: int | None
    page_end: int | None
    heading: str | None = None
    text: str
    score: float
    keyword_rank: int | None  # required key: null means "not found by keyword search"
    vector_rank: int | None


class PassageResult(BaseModel):
    mode_used: str
    vector_note: str | None = None
    results: list[PassageHit]


class ConceptHit(BaseModel):
    concept_id: int
    name: str
    source_id: int
    source_title: str | None = None
    definition: str | None = None
    matched_in: list[str]


class ConceptResult(BaseModel):
    results: list[ConceptHit]


@dataclass(frozen=True)
class StarcallConfig:
    server_path: str
    scopes: tuple[str, ...] = ("passages",)
    embed_url: str | None = None  # used only when loopback; otherwise the server runs keyword-only

    def __post_init__(self) -> None:
        if not self.scopes or any(s not in SCOPES for s in self.scopes):
            raise ValueError(f"scopes must be a non-empty subset of {', '.join(SCOPES)}")


def is_loopback(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if host.lower() in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_args(cfg: StarcallConfig) -> list[str]:
    """Server arguments after the script path. The server checks the embedding URL is loopback again itself."""
    args = ["--scopes", ",".join(cfg.scopes)]
    if cfg.embed_url and is_loopback(cfg.embed_url):
        args += ["--embed-url", cfg.embed_url]
    else:
        args.append("--no-embeddings")
    return args


def child_env(environ: os._Environ[str] | dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    return {k: env[k] for k in ENV_ALLOWLIST if env.get(k)}


def preflight(
    cfg: StarcallConfig,
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> StdioServerParameters:
    """Check Node and the server file before spawning, so each common setup mistake gets its own message."""
    node = which("node")
    if not node:
        raise StarcallError("node_missing", "Node.js was not found on PATH. Install Node 22.13 or newer.")
    try:
        out = run([node, "--version"], capture_output=True, text=True, timeout=NODE_VERSION_TIMEOUT)
        found = _NODE_VERSION.search(out.stdout or "")
    except (OSError, subprocess.SubprocessError) as e:
        raise StarcallError("node_missing", f"Could not run Node.js ({e}).") from e
    if not found or (int(found[1]), int(found[2])) < MIN_NODE:
        have = (out.stdout or "").strip() or "an unknown version"
        need = ".".join(map(str, MIN_NODE))
        raise StarcallError("node_too_old", f"StarcallOS needs Node {need} or newer; this PC has {have}.")
    path = Path(cfg.server_path)
    if path.name != "index.js" or path.parent.name != "dist" or not path.is_file():
        raise StarcallError(
            "server_missing",
            f"No StarcallOS MCP server at {cfg.server_path}. Point it at packages/mcp-server/dist/index.js in "
            "your StarcallOS checkout, after building it.",
        )
    return StdioServerParameters(
        command=node, args=[str(path), *build_args(cfg)], env=child_env(), cwd=str(path.parent)
    )


def _stderr_tail(errlog: IO[str]) -> str:
    try:
        errlog.flush()
        errlog.seek(0)
        return errlog.read()[-STDERR_TAIL:].strip()
    except (OSError, ValueError):
        return ""


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _leaves(e: BaseException) -> list[BaseException]:
    if isinstance(e, BaseExceptionGroup):
        return [leaf for sub in e.exceptions for leaf in _leaves(sub)]
    return [e]


def _is_transport_error(e: BaseException) -> bool:
    if isinstance(e, McpError):
        return e.error.code == types.CONNECTION_CLOSED
    return isinstance(e, anyio.ClosedResourceError | anyio.BrokenResourceError | anyio.EndOfStream | BrokenPipeError)


def classify_tool_error(text: str) -> StarcallError:
    if _SCHEMA.search(text):
        return StarcallError("schema_mismatch", f"StarcallOS refused its database: {text[:300]}")
    return StarcallError("tool_error", f"StarcallOS returned an error: {text[:300]}")


class StarcallClient:
    """One StarcallOS session per pal process. Every public method is safe to call from any thread."""

    def __init__(
        self,
        cfg: StarcallConfig,
        *,
        spawn: Callable[[], StdioServerParameters] | None = None,
        call_timeout: float = CALL_TIMEOUT,
        start_timeout: float = START_TIMEOUT,
        cooldowns: tuple[float, ...] = COOLDOWNS,
        max_in_flight: int = MAX_IN_FLIGHT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self._spawn = spawn or (lambda: preflight(cfg))
        self.call_timeout = call_timeout
        self.start_timeout = start_timeout
        self._cooldowns = cooldowns
        self._clock = clock
        self._max_in_flight = max_in_flight
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._count_lock = threading.Lock()
        self._in_flight = 0
        self._closed = False
        # Loop-thread state: written only on the loop thread.
        self._session: ClientSession | None = None
        self._closing: asyncio.Event | None = None
        self._init_scope: anyio.CancelScope | None = None
        self._supervisor: asyncio.Task | None = None
        self._starting: asyncio.Future | None = None
        self._calls: set[asyncio.Task] = set()
        self._consecutive_timeouts = 0
        self._failures = 0
        self._down_until = 0.0
        self._disabled: StarcallError | None = None
        self.last_error: StarcallError | None = None
        self.last_ok_at: float | None = None  # wall-clock time of the last successful call
        self.spawn_count = 0
        self.stray_responses = 0  # replies that arrived after their call had timed out, dropped
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="starcall-mcp", daemon=True)
        self._thread.start()

    # ── public, sync ────────────────────────────────────────────────────────────────────────────────────────────

    def search_passages(self, query: str, limit: int = 6) -> PassageResult:
        args = {"query": query.strip()[:500], "limit": max(1, min(int(limit), 10))}
        return self._call("search_passages", args, PassageResult)

    def search_concepts(self, query: str, limit: int = 8) -> ConceptResult:
        if "concepts" not in self.cfg.scopes:
            raise StarcallError("incompatible_server", "The concepts scope is off for this connection.")
        args = {"query": query.strip()[:200], "limit": max(1, min(int(limit), 20))}
        return self._call("search_concepts", args, ConceptResult)

    @property
    def in_flight(self) -> int:
        with self._count_lock:
            return self._in_flight

    def status(self) -> dict:
        """A snapshot for the Connections page. Reads loop-thread state without locking; it's advisory."""
        err = self._disabled or self.last_error
        if self._closed:
            state = "closed"
        elif self._disabled:
            state = "disabled"
        elif self._session is not None:
            state = "running"
        elif self._down_until > self._clock():
            state = "cooldown"
        else:
            state = "stopped"
        out: dict[str, Any] = {"state": state, "error": err.as_dict() if err else None, "last_ok_at": self.last_ok_at}
        if state == "cooldown":
            out["retry_in"] = round(self._down_until - self._clock(), 1)
        return out

    def check(self) -> dict:
        """The Connections page's Test: clear a disabled or cooling-down state, start if needed, report status."""
        if self._closed:
            return self.status()
        fut = asyncio.run_coroutine_threadsafe(self._acheck(), self._loop)
        try:
            fut.result(self.start_timeout + SAFETY_MARGIN)
        except StarcallError:
            pass  # recorded in last_error
        except concurrent.futures.TimeoutError:
            fut.cancel()
        return self.status()

    def close(self, timeout: float = CLOSE_TIMEOUT) -> None:
        """Close the session (the child exits when its stdin closes; the SDK kills the tree if it doesn't)."""
        if self._closed:
            return
        self._closed = True
        try:
            fut = asyncio.run_coroutine_threadsafe(self._aclose(timeout), self._loop)
            fut.result(timeout + 2 * SAFETY_MARGIN)
        except Exception:  # noqa: BLE001 - shutdown must not raise
            log.warning("StarcallOS session did not close cleanly", exc_info=True)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2.0)
        if not self._thread.is_alive():
            self._loop.close()

    # ── call path ───────────────────────────────────────────────────────────────────────────────────────────────

    def _call(self, tool: str, args: dict, model: type[M]) -> M:
        if self._closed:
            raise StarcallError("closed", "StarcallOS is shut down.")
        if not self._slots.acquire(blocking=False):
            raise StarcallError("busy", f"StarcallOS already has {self._max_in_flight} lookups running.")
        with self._count_lock:
            self._in_flight += 1
        try:
            result, cancel = self._submit(self._acall(tool, args, model))
        except RuntimeError as e:  # the loop is gone
            self._release_slot()
            raise StarcallError("closed", "StarcallOS is shut down.") from e
        try:
            return result.result(self.call_timeout + SAFETY_MARGIN)
        except concurrent.futures.TimeoutError:
            cancel()  # only reached if the loop itself is stalled; the in-loop deadline normally fires first
            raise StarcallError("timeout", f"StarcallOS took longer than {self.call_timeout:g} s.") from None
        except concurrent.futures.CancelledError:
            raise StarcallError("closed", "StarcallOS is shut down.") from None

    def _submit(self, coro: Coroutine[Any, Any, T]) -> tuple[concurrent.futures.Future[T], Callable[[], None]]:
        """Run `coro` as a loop task. The slot is released when the task really ends, not when the caller stops
        waiting, so a stuck server can't push more than `max_in_flight` tasks into the loop."""
        result: concurrent.futures.Future[T] = concurrent.futures.Future()
        holder: dict[str, asyncio.Task] = {}

        def done(task: asyncio.Task) -> None:
            self._calls.discard(task)
            self._release_slot()
            if task.cancelled():
                if not result.done():
                    result.cancel()
                return
            exc = task.exception()
            if result.done():
                return
            if exc is not None:
                result.set_exception(exc)
            else:
                result.set_result(task.result())

        def start() -> None:
            task = self._loop.create_task(coro)
            holder["task"] = task
            self._calls.add(task)
            task.add_done_callback(done)

        try:
            self._loop.call_soon_threadsafe(start)
        except RuntimeError:
            coro.close()
            raise

        def cancel() -> None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(lambda: holder["task"].cancel() if "task" in holder else None)

        return result, cancel

    def _release_slot(self) -> None:
        with self._count_lock:
            self._in_flight -= 1
        self._slots.release()

    async def _acall(self, tool: str, args: dict, model: type[M]) -> M:
        session: ClientSession | None = None
        request_id: int | None = None
        try:
            with anyio.fail_after(self.call_timeout):
                session = await self._ensure_session()
                closing = self._closing
                request_id = getattr(session, "_request_id", None)  # the id call_tool is about to use
                result = await self._until_session_ends(session.call_tool(tool, args), closing)
        except TimeoutError:
            if session is None:
                raise StarcallError("starting", "StarcallOS is still starting.") from None
            await self._notify_cancelled(session, request_id)
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= TIMEOUTS_BEFORE_RESTART:
                n = self._consecutive_timeouts
                self._teardown(StarcallError("timeout", f"StarcallOS timed out {n} times in a row; restarting it."))
            raise StarcallError("timeout", f"StarcallOS took longer than {self.call_timeout:g} s.") from None
        except StarcallError:
            raise
        except Exception as e:
            if any(_is_transport_error(leaf) for leaf in _leaves(e)):
                err = StarcallError("crashed", "The StarcallOS process stopped responding.")
                self._teardown(err)
                raise err from e
            if isinstance(e, McpError):
                raise StarcallError("tool_error", f"StarcallOS returned an error: {e.error.message[:300]}") from e
            raise StarcallError("tool_error", f"StarcallOS call failed: {e}") from e
        text = next((c.text for c in result.content if isinstance(c, types.TextContent)), "")
        if result.isError:
            raise classify_tool_error(text)
        try:
            parsed = model.model_validate(json.loads(text))
        except (ValueError, ValidationError) as e:
            err = StarcallError(
                "incompatible_server",
                f"StarcallOS returned {tool} results pal can't read; update StarcallOS or pal ({str(e)[:200]}).",
            )
            self._disable(err)
            raise err from e
        self._consecutive_timeouts = 0
        self._failures = 0
        self.last_error = None
        self.last_ok_at = time.time()
        return parsed

    async def _until_session_ends(self, coro: Coroutine[Any, Any, T], closing: asyncio.Event | None) -> T:
        """Await `coro`, but fail at once if its session ends (crash, teardown, close) while it waits.

        The SDK fails pending requests when the stream closes, but that runs in a cleanup that tearing the session
        down can cancel, so a call would otherwise sit until its deadline.
        """
        call = asyncio.ensure_future(coro)
        if closing is None:
            return await call
        ended = asyncio.ensure_future(closing.wait())
        try:
            await asyncio.wait({call, ended}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ended.cancel()
            if not call.done():
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)
        if call.cancelled():
            kind = self.last_error.kind if self.last_error else "crashed"  # why the session ended
            raise StarcallError(kind, "The StarcallOS session ended during this lookup.")
        return call.result()

    async def _notify_cancelled(self, session: ClientSession, request_id: int | None) -> None:
        """Best effort: the server's handlers run synchronous SQLite, so it will likely finish the work anyway."""
        if request_id is None:
            return
        note = types.ClientNotification(
            types.CancelledNotification(params=types.CancelledNotificationParams(requestId=request_id, reason="timeout"))
        )
        with contextlib.suppress(Exception):
            with anyio.fail_after(CANCEL_NOTIFY_TIMEOUT):
                await session.send_notification(note)

    # ── session lifecycle (loop thread) ─────────────────────────────────────────────────────────────────────────

    async def _ensure_session(self) -> ClientSession:
        if self._disabled:
            raise self._disabled
        if self._session is not None:
            return self._session
        if self._starting is None or self._starting.done():
            wait = self._down_until - self._clock()
            if wait > 0:
                last = f" Last error: {self.last_error.message}" if self.last_error else ""
                raise StarcallError("cooldown", f"StarcallOS is paused; retrying in {wait:.0f} s.{last}")
            self._starting = self._loop.create_future()
            self._starting.add_done_callback(lambda f: f.cancelled() or f.exception())  # mark retrieved
            self._supervisor = self._loop.create_task(self._supervise(self._starting))
        await asyncio.shield(self._starting)
        if self._session is None:
            raise StarcallError("crashed", "The StarcallOS process exited.")
        return self._session

    async def _supervise(self, ready: asyncio.Future) -> None:
        closing = asyncio.Event()
        self._closing = closing
        errlog = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")  # noqa: SIM115 - closed below
        try:
            params = await anyio.to_thread.run_sync(self._spawn)
            self.spawn_count += 1
            # A real file: the child's stderr handle needs a file descriptor, which StringIO lacks.
            async with stdio_client(params, errlog=cast(TextIO, errlog)) as (read, write):
                pump_send, pump_recv = anyio.create_memory_object_stream[Any](0)

                async def pump() -> None:
                    # Forward the child's messages to the session. When its stdout ends the child has exited:
                    # notice it now rather than when the next call times out.
                    async with pump_send:
                        async for msg in read:
                            await pump_send.send(msg)
                    # Before the session is up, a failed initialize reports the exit (with the stderr reason).
                    if not closing.is_set() and ready.done():
                        tail = _last_line(_stderr_tail(errlog))
                        self._record_failure(
                            StarcallError("crashed", "The StarcallOS process exited." + (f" {tail}" if tail else ""))
                        )
                    closing.set()

                async with anyio.create_task_group() as tg:
                    tg.start_soon(pump)
                    async with ClientSession(pump_recv, write, message_handler=self._on_message) as session:
                        with anyio.CancelScope() as scope:
                            self._init_scope = scope
                            with anyio.fail_after(self.start_timeout):
                                await session.initialize()
                                listed = await session.list_tools()
                        self._init_scope = None
                        if scope.cancelled_caught:
                            raise StarcallError("closed", "StarcallOS was shut down while starting.")
                        self._check_contract(listed)
                        self._session = session
                        if not ready.done():
                            ready.set_result(None)
                        await closing.wait()
                        self._session = None
                    tg.cancel_scope.cancel()
        except BaseException as e:  # noqa: BLE001 - every start failure becomes a typed error for the waiters
            if not ready.done():
                err = self._classify_start(e, _stderr_tail(errlog))
                self._record_failure(err)
                ready.set_exception(err)
            if isinstance(e, asyncio.CancelledError):
                raise
        finally:
            self._session = None
            self._init_scope = None
            if self._closing is closing:
                self._closing = None
            errlog.close()

    def _classify_start(self, e: BaseException, stderr: str) -> StarcallError:
        leaves = _leaves(e)
        for leaf in leaves:
            if isinstance(leaf, StarcallError):
                return leaf
        if _SCHEMA.search(stderr):
            return StarcallError("schema_mismatch", f"StarcallOS refused its database: {_last_line(stderr)}")
        if _DB_MISSING.search(stderr):
            return StarcallError("db_missing", f"{_last_line(stderr)}. Open StarcallOS once to create it.")
        if any(isinstance(leaf, TimeoutError) for leaf in leaves):
            return StarcallError("start_failed", f"StarcallOS did not finish starting within {self.start_timeout:g} s.")
        detail = _last_line(stderr) or f"{type(leaves[0]).__name__}: {leaves[0]}"
        return StarcallError("start_failed", f"StarcallOS failed to start: {detail[:300]}")

    def _check_contract(self, listed: types.ListToolsResult) -> None:
        tools = {t.name: t for t in listed.tools}
        for scope in self.cfg.scopes:
            name = REQUIRED_TOOLS[scope]
            tool = tools.get(name)
            if tool is None:
                raise StarcallError(
                    "incompatible_server", f"This StarcallOS server has no {name} tool; update your StarcallOS checkout."
                )
            props = (tool.inputSchema or {}).get("properties") or {}
            missing = [k for k in REQUIRED_INPUTS if k not in props]
            if missing:
                raise StarcallError(
                    "incompatible_server",
                    f"StarcallOS's {name} tool takes no {', '.join(missing)}; update your StarcallOS checkout.",
                )

    async def _on_message(self, message: Any) -> None:
        if isinstance(message, Exception):
            if "unknown request ID" in str(message):
                self.stray_responses += 1  # a reply to a call that already timed out: dropped
            else:
                log.debug("StarcallOS session message error: %s", message)

    def _record_failure(self, err: StarcallError) -> None:
        self.last_error = err
        self._consecutive_timeouts = 0
        if err.kind == "incompatible_server":
            self._disabled = err
            return
        if err.kind == "closed":
            return
        delay = self._cooldowns[min(self._failures, len(self._cooldowns) - 1)]
        self._failures += 1
        self._down_until = self._clock() + delay

    def _teardown(self, err: StarcallError) -> None:
        """End the current session (idempotent) and start the cooldown."""
        closing = self._closing
        if closing is None or closing.is_set():
            return
        self._record_failure(err)
        self._session = None
        closing.set()

    def _disable(self, err: StarcallError) -> None:
        self.last_error = err
        self._disabled = err
        closing = self._closing
        self._session = None
        if closing is not None:
            closing.set()

    async def _acheck(self) -> None:
        self._disabled = None
        self._failures = 0
        self._down_until = 0.0
        with anyio.fail_after(self.start_timeout):
            await self._ensure_session()

    async def _aclose(self, timeout: float) -> None:
        for task in list(self._calls):
            task.cancel()
        if self._init_scope is not None:
            self._init_scope.cancel()
        if self._closing is not None:
            self._closing.set()
        sup = self._supervisor
        if sup is not None and not sup.done():
            try:
                with anyio.fail_after(timeout):
                    await asyncio.shield(sup)
            except TimeoutError:
                sup.cancel()
                await asyncio.gather(sup, return_exceptions=True)
        if self._calls:
            await asyncio.gather(*self._calls, return_exceptions=True)
