"""The StarcallOS MCP client against a fake stdio server: startup, calls, timeouts, crashes, contract checks, shutdown."""

import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS
from pydantic import BaseModel

from pal.connectors import starcall
from pal.connectors.starcall import (
    ENV_ALLOWLIST,
    StarcallClient,
    StarcallConfig,
    StarcallError,
    build_args,
    child_env,
    preflight,
)

FAKE = Path(__file__).parent / "fixtures" / "fake_starcall_server.py"
MARKER = "FAKE-STARCALL-MARKER"
QUICK = (0.3,)  # cooldown used by restart tests


class _Env(BaseModel):
    keys: list[str]


def _alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _wait_for(cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


@pytest.fixture
def make():
    made: list[StarcallClient] = []

    def _make(*extra: str, scopes=("passages",), **kw) -> StarcallClient:
        cfg = StarcallConfig(server_path="unused", scopes=scopes)

        def spawn() -> StdioServerParameters:
            return StdioServerParameters(
                command=sys.executable, args=[str(FAKE), *build_args(cfg), *extra], env=child_env()
            )

        kw.setdefault("call_timeout", 15.0)  # generous: a cold Python child takes a second or two to start
        kw.setdefault("start_timeout", 15.0)
        c = StarcallClient(cfg, spawn=spawn, **kw)
        made.append(c)
        return c

    yield _make
    for c in made:
        c.close()


def _warm(c: StarcallClient) -> None:
    """Start the session with a long deadline, so later calls can use a short one."""
    t, c.call_timeout = c.call_timeout, 15.0
    try:
        c.search_passages("warm up")
    finally:
        c.call_timeout = t


# ── pre-launch checks and launch arguments ──────────────────────────────────────────────────────────────────────


def _ok_run(version: str):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{version}\n", stderr="")

    return run


def _server_file(tmp_path: Path) -> Path:
    dist = tmp_path / "mcp-server" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.js").write_text("// server")
    return dist / "index.js"


def test_preflight_reports_missing_node(tmp_path):
    cfg = StarcallConfig(server_path=str(_server_file(tmp_path)))
    with pytest.raises(StarcallError) as e:
        preflight(cfg, which=lambda _: None)
    assert e.value.kind == "node_missing"


def test_preflight_reports_old_node(tmp_path):
    cfg = StarcallConfig(server_path=str(_server_file(tmp_path)))
    with pytest.raises(StarcallError) as e:
        preflight(cfg, which=lambda _: "node", run=_ok_run("v22.12.0"))
    assert e.value.kind == "node_too_old" and "v22.12.0" in e.value.message


@pytest.mark.parametrize("name", ["missing", "not-dist"])
def test_preflight_reports_missing_server(tmp_path, name):
    path = tmp_path / "nothing" / "dist" / "index.js" if name == "missing" else tmp_path / "index.js"
    if name == "not-dist":
        path.write_text("// not in dist")
    cfg = StarcallConfig(server_path=str(path))
    with pytest.raises(StarcallError) as e:
        preflight(cfg, which=lambda _: "node", run=_ok_run("v22.16.0"))
    assert e.value.kind == "server_missing"


def test_preflight_builds_launch_with_allowlisted_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PAL_DB_PASSWORD", "secret")
    server = _server_file(tmp_path)
    cfg = StarcallConfig(server_path=str(server), scopes=("passages", "concepts"), embed_url="http://127.0.0.1:11434/v1")
    params = preflight(cfg, which=lambda _: "node", run=_ok_run("v22.13.0"))
    assert params.command == "node"
    assert params.args == [str(server), "--scopes", "passages,concepts", "--embed-url", "http://127.0.0.1:11434/v1"]
    assert params.env is not None and set(params.env) <= set(ENV_ALLOWLIST)
    assert params.cwd == str(server.parent)


def test_non_loopback_embedding_url_means_keyword_only():
    cfg = StarcallConfig(server_path="x", embed_url="http://192.168.1.5:11434/v1")
    assert build_args(cfg) == ["--scopes", "passages", "--no-embeddings"]
    assert build_args(StarcallConfig(server_path="x")) == ["--scopes", "passages", "--no-embeddings"]
    assert "--embed-url" in build_args(StarcallConfig(server_path="x", embed_url="http://localhost:11434/v1"))


def test_config_refuses_scopes_pal_never_uses():
    for scopes in [(), ("highlights",), ("passages", "notes")]:
        with pytest.raises(ValueError):
            StarcallConfig(server_path="x", scopes=scopes)


def test_child_env_holds_only_the_allowlist():
    env = child_env({"PATH": "p", "APPDATA": "a", "PAL_DB_PASSWORD": "s", "ANTHROPIC_API_KEY": "k", "TEMP": ""})
    assert env == {"PATH": "p", "APPDATA": "a"}


# ── startup and calls ───────────────────────────────────────────────────────────────────────────────────────────


def test_starts_lazily_and_reuses_one_session(make):
    c = make(scopes=("passages", "concepts"))
    assert c.spawn_count == 0 and c.status()["state"] == "stopped"
    r = c.search_passages("gradient descent")
    assert r.mode_used == "keyword" and len(r.results) == 3
    hit = r.results[0]
    assert (hit.source_title, hit.page_start, hit.keyword_rank, hit.vector_rank) == ("Fake Book", 1, 1, 1)
    concepts = c.search_concepts("gradient descent")
    assert concepts.results[0].name == "Gradient Descent" and concepts.results[0].matched_in == ["name"]
    assert c.spawn_count == 1 and c.status()["state"] == "running" and c.last_ok_at is not None


def test_semantic_only_hits_carry_a_null_keyword_rank(make):
    r = make().search_passages("semantic hits:2")
    assert [h.keyword_rank for h in r.results] == [None, None]


def test_concepts_need_the_concepts_scope(make):
    with pytest.raises(StarcallError) as e:
        make().search_concepts("anything")
    assert e.value.kind == "incompatible_server"


def test_a_turn_that_arrives_while_starting_skips_without_a_second_spawn(make):
    c = make(call_timeout=0.05)
    with pytest.raises(StarcallError) as e:
        c.search_passages("too early")
    assert e.value.kind == "starting"
    c.call_timeout = 15.0
    assert c.search_passages("later").results
    assert c.spawn_count == 1


def test_ten_simultaneous_calls_get_their_own_replies(make):
    c = make(max_in_flight=10)
    _warm(c)
    out: dict[int, str] = {}

    def ask(i: int) -> None:
        out[i] = c.search_passages(f"q{i} hits:1").results[0].text

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(out[i].endswith(f"for q{i} hits:1") for i in range(10))
    assert c.spawn_count == 1


# ── timeouts ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_one_timeout_keeps_the_session(make):
    c = make()
    _warm(c)
    c.call_timeout = 0.5
    with pytest.raises(StarcallError) as e:
        c.search_passages("sleep:2")
    assert e.value.kind == "timeout"
    assert c.search_passages("next").results
    assert c.spawn_count == 1


def test_three_timeouts_in_a_row_restart_after_the_cooldown(make):
    c = make(cooldowns=QUICK)
    _warm(c)
    c.call_timeout = 0.5
    kinds = []
    for _ in range(3):
        with pytest.raises(StarcallError) as e:
            c.search_passages("sleep:2")
        kinds.append(e.value.kind)
        if len(kinds) < 3:
            assert c.status()["state"] == "running"  # the first two keep the session
    assert kinds == ["timeout"] * 3
    with pytest.raises(StarcallError) as e:
        c.search_passages("during cooldown")
    assert e.value.kind == "cooldown" and c.spawn_count == 1  # no spawn while cooling down
    time.sleep(QUICK[0] + 0.1)
    _warm(c)
    assert c.spawn_count == 2


def test_a_success_resets_the_timeout_count(make):
    c = make()
    _warm(c)
    c.call_timeout = 0.5
    for query in ["sleep:2", "sleep:2", "fine", "sleep:2", "sleep:2"]:
        try:
            c.search_passages(query)
        except StarcallError as e:
            assert e.kind == "timeout"
    assert c.status()["state"] == "running" and c.spawn_count == 1


def test_simultaneous_timeouts_never_exceed_the_in_flight_limit(make):
    c = make(max_in_flight=4, cooldowns=QUICK)
    _warm(c)
    c.call_timeout = 0.5
    kinds: list[str] = []
    peak = [0]
    lock = threading.Lock()
    start = threading.Barrier(6)

    def ask() -> None:
        start.wait()
        try:
            c.search_passages("sleep:3")
        except StarcallError as e:
            with lock:
                kinds.append(e.kind)
                peak[0] = max(peak[0], c.in_flight)

    threads = [threading.Thread(target=ask) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(kinds) == ["busy", "busy", "timeout", "timeout", "timeout", "timeout"]
    assert peak[0] <= 4
    # Timed-out calls were cancelled in the loop: nothing is left running or holding a slot.
    assert _wait_for(lambda: c.in_flight == 0 and not c._calls)
    time.sleep(QUICK[0] + 0.1)
    _warm(c)  # four timeouts together tore the session down; it comes back after the cooldown


def test_a_late_reply_is_dropped_and_the_next_call_gets_its_own_answer(make):
    c = make()
    _warm(c)
    c.call_timeout = 0.5
    with pytest.raises(StarcallError):
        c.search_passages("sleep:1.5 first")
    c.call_timeout = 15.0
    assert c.search_passages("second hits:1").results[0].text.endswith("for second hits:1")
    # The server answers the abandoned request (with its result or a cancellation error); the SDK drops it.
    assert _wait_for(lambda: c.stray_responses >= 1, timeout=4.0)
    assert c.search_passages("third hits:1").results[0].text.endswith("for third hits:1")


# ── crashes and start failures ──────────────────────────────────────────────────────────────────────────────────


def test_crash_during_a_call_restarts_only_after_the_cooldown(make):
    c = make(cooldowns=QUICK)
    _warm(c)
    with pytest.raises(StarcallError) as e:
        c.search_passages("crash")
    assert e.value.kind == "crashed"
    with pytest.raises(StarcallError) as e:
        c.search_passages("too soon")
    assert e.value.kind == "cooldown" and c.spawn_count == 1
    time.sleep(QUICK[0] + 0.1)
    assert c.search_passages("back").results
    assert c.spawn_count == 2


def test_a_crash_while_idle_is_noticed_without_a_call(make, tmp_path):
    pid_file = tmp_path / "pid"
    c = make("--pid-file", str(pid_file), cooldowns=(60.0,))
    _warm(c)
    pid = int(pid_file.read_text())
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
    else:
        os.kill(pid, 9)
    assert _wait_for(lambda: c.status()["state"] == "cooldown")
    assert c.status()["error"]["kind"] == "crashed"
    with pytest.raises(StarcallError) as e:
        c.search_passages("anything")
    assert e.value.kind == "cooldown" and c.spawn_count == 1


@pytest.mark.parametrize(
    ("mode", "kind", "text"),
    [
        ("schema", "schema_mismatch", "migration(s) behind"),
        ("dbmissing", "db_missing", "database not found"),
        ("crash", "start_failed", "something unexpected"),
    ],
)
def test_start_failures_get_their_own_kind(make, mode, kind, text):
    c = make("--startup", mode, cooldowns=(60.0,))
    with pytest.raises(StarcallError) as e:
        c.search_passages("hello")
    assert e.value.kind == kind and text in e.value.message
    assert c.status()["state"] == "cooldown" and c.status()["error"]["kind"] == kind
    with pytest.raises(StarcallError) as e:
        c.search_passages("again")
    assert e.value.kind == "cooldown" and c.spawn_count == 1


def test_a_server_that_never_finishes_starting_times_out(make):
    c = make("--startup", "hang", start_timeout=1.0, call_timeout=5.0)
    with pytest.raises(StarcallError) as e:
        c.search_passages("hello")
    assert e.value.kind == "start_failed" and "within 1 s" in e.value.message


def test_cooldowns_grow_with_each_failure(make):
    now = [1000.0]
    c = make("--startup", "crash", cooldowns=(10.0, 30.0), clock=lambda: now[0])
    retry = []
    for _ in range(3):
        with pytest.raises(StarcallError):
            c.search_passages("hello")
        retry.append(c.status()["retry_in"])
        now[0] += 1000
    assert retry == [10.0, 30.0, 30.0]


# ── contract checks ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "extra", [("--drop-tool", "search_passages"), ("--drop-input", "limit"), ("--drop-input", "query")]
)
def test_a_server_without_the_expected_tools_is_incompatible_until_checked(make, extra):
    c = make(*extra)
    with pytest.raises(StarcallError) as e:
        c.search_passages("hello")
    assert e.value.kind == "incompatible_server"
    with pytest.raises(StarcallError) as e:
        c.search_passages("again")
    assert e.value.kind == "incompatible_server" and c.spawn_count == 1  # stays off: no respawn
    assert c.status()["state"] == "disabled"
    status = c.check()  # Test on the Connections page tries again
    assert c.spawn_count == 2 and status["state"] == "disabled"


def test_concepts_scope_requires_the_concepts_tool(make):
    c = make("--drop-tool", "search_concepts", scopes=("passages", "concepts"))
    with pytest.raises(StarcallError) as e:
        c.search_passages("hello")
    assert e.value.kind == "incompatible_server" and "search_concepts" in e.value.message


def test_results_missing_a_field_pal_reads_disable_the_server(make):
    c = make("--drop-field", "keyword_rank")
    with pytest.raises(StarcallError) as e:
        c.search_passages("hello")
    assert e.value.kind == "incompatible_server"
    assert c.status()["state"] == "disabled"


def test_database_mismatch_on_a_call_keeps_the_session(make):
    c = make()
    _warm(c)
    with pytest.raises(StarcallError) as e:
        c.search_passages("schema")
    assert e.value.kind == "schema_mismatch"
    with pytest.raises(StarcallError) as e:
        c.search_passages("boom")
    assert e.value.kind == "tool_error" and "something broke" in e.value.message
    assert c.search_passages("fine").results and c.spawn_count == 1


# ── environment and shutdown ────────────────────────────────────────────────────────────────────────────────────


def test_the_child_gets_none_of_pals_secrets(make, monkeypatch):
    monkeypatch.setenv("PAL_DB_PASSWORD", "secret")
    monkeypatch.setenv("PGPASSWORD", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    c = make()
    keys = set(c._call("debug_env", {}, _Env).keys)
    assert not keys & {"PAL_DB_PASSWORD", "PGPASSWORD", "ANTHROPIC_API_KEY"}
    # Windows itself may add a handful of variables to every new process; none carry pal's settings. On Linux the
    # fake server (a Python child started with no locale) sets LC_CTYPE in its own environment (PEP 538 locale
    # coercion); pal doesn't pass it.
    allowed = set(ENV_ALLOWLIST) | set(DEFAULT_INHERITED_ENV_VARS) | {"COMSPEC", "PROMPT", "LC_CTYPE"}
    assert not {k for k in keys if k.upper().startswith("PAL_")}
    assert {k.upper() for k in keys} <= {a.upper() for a in allowed}, keys - allowed


def test_close_leaves_no_child_process(make, tmp_path):
    pid_file = tmp_path / "pid"
    c = make("--pid-file", str(pid_file))
    _warm(c)
    pid = int(pid_file.read_text())
    assert _alive(pid)
    started = time.monotonic()
    c.close()
    assert time.monotonic() - started < starcall.CLOSE_TIMEOUT + 3
    assert _wait_for(lambda: not _alive(pid), timeout=3.0)
    assert c.status()["state"] == "closed"
    c.close()  # a second close is harmless
    with pytest.raises(StarcallError) as e:
        c.search_passages("after close")
    assert e.value.kind == "closed"


def test_close_while_a_call_is_running(make, tmp_path):
    pid_file = tmp_path / "pid"
    c = make("--pid-file", str(pid_file))
    _warm(c)
    pid = int(pid_file.read_text())
    err: list[StarcallError] = []

    def ask() -> None:
        try:
            c.search_passages("sleep:10")
        except StarcallError as e:
            err.append(e)

    t = threading.Thread(target=ask)
    t.start()
    _wait_for(lambda: c.in_flight == 1)
    c.close()
    t.join(timeout=5)
    assert not t.is_alive() and err and err[0].kind in {"closed", "crashed", "tool_error"}
    assert _wait_for(lambda: not _alive(pid), timeout=3.0)


def test_close_before_any_start_spawns_nothing(make):
    c = make()
    c.close()
    assert c.spawn_count == 0
