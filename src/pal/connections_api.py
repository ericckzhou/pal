"""Connected services (PC only): list them, sync now, pause or resume, disconnect. Plus calendar events to read.

StarcallOS has its own routes under /starcall (a server path to run, not a Google sign-in); they're declared before
the /{connection_id} routes, which would otherwise claim the path.
"""

from datetime import date, datetime, time, timedelta
from uuid import UUID

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import calendar, egress, reminders, starcall
from .api import is_local, require_owner, require_session
from .config import in_container
from .connectors import drive_folders, google, google_auth, google_web, hosts, store, sync
from .connectors.google_cli import DISCLOSURE

router = APIRouter(prefix="/api/connections", dependencies=[Depends(require_owner)])
google_return = APIRouter()  # Google's redirect back carries no session cookie; see connectors/google_web.py
events = APIRouter(prefix="/api/calendar", dependencies=[Depends(require_session)])
EVENT_DAYS_MAX = 60
SERVICE_NAMES = {"calendar": "Google Calendar", "drive": "Google Drive", "gmail": "Gmail"}


class Patch(BaseModel):
    enabled: bool | None = None
    project_id: UUID | None = None  # the project synced files go in (null: none)


class ConnectStart(BaseModel):
    service: str = Field(pattern="^(calendar|drive|gmail)$")


class FolderAdd(BaseModel):
    link: str = Field(min_length=1, max_length=drive_folders.LINK_MAX)


class StarcallSetup(BaseModel):
    server_path: str = Field(min_length=1, max_length=starcall.SERVER_PATH_MAX + 10)  # room for quotes and spaces
    scopes: list[str] = Field(default_factory=lambda: ["passages"], max_length=len(starcall.SCOPE_INFO))


class StarcallPatch(BaseModel):
    enabled: bool


def _services() -> list[dict]:
    """What can be connected, and the terminal command that also does it (the page has a sign-in button)."""
    docker = in_container()
    return [{"provider": "google", "service": s, "grants": text,
             "command": f"bash scripts/pal-docker.sh connect {s}" if docker else f"pal connect google {s}"}
            for s, text in DISCLOSURE.items()]


def _public(row: dict) -> dict:
    """What the Connections page shows. The cursor is internal bookkeeping and stays out."""
    return {k: v for k, v in row.items() if k != "cursor"}


@router.get("")
def list_connections(request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        rows = [_public(r) for r in store.list_all(conn)]
        library = _starcall_card(request, conn)
    client_cmd = ("bash scripts/pal-docker.sh google-client client.json" if in_container()
                  else "pal google-client --set client.json")
    return {"connections": rows, "google_client": google_auth.load_client() is not None, "client_command": client_cmd,
            "services": _services(), "signin": _sign_ins(request).pop_outcome(), "starcall": library}


def _starcall_card(request: Request, conn: psycopg.Connection) -> dict:
    state = request.app.state
    return starcall.public(state, starcall.connection(conn), state.settings.ollama_url)


@router.put("/starcall")
def starcall_save(body: StarcallSetup, request: Request) -> dict:
    """Set the server path and scopes. Only from a browser on this PC: pal runs the file at that path."""
    if not is_local(request):
        raise HTTPException(status_code=403, detail="Set up StarcallOS from a browser on the PC running pal.")
    with request.app.state.pool.connection() as conn:
        try:
            starcall.save(conn, body.server_path, body.scopes)
        except starcall.SetupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _starcall_card(request, conn)


@router.patch("/starcall")
def starcall_pause(body: StarcallPatch, request: Request) -> dict:
    """Pause or resume. Pausing also stops the server process; the next chat that may use it starts it again."""
    with request.app.state.pool.connection() as conn:
        if not starcall.set_enabled(conn, body.enabled):
            raise HTTPException(status_code=404, detail="StarcallOS isn't set up")
        if not body.enabled:
            starcall.close(request.app.state)
        return _starcall_card(request, conn)


@router.post("/starcall/test")
def starcall_test(request: Request) -> dict:
    """Start the server for the saved settings and say whether it answered. Waits up to the start timeout."""
    state = request.app.state
    with state.pool.connection() as conn:
        try:
            status = starcall.test(state, conn, state.settings.ollama_url)
        except starcall.SetupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": status["state"] == "running", "status": status, "card": _starcall_card(request, conn)}


@router.delete("/starcall")
def starcall_remove(request: Request) -> dict:
    """Remove the connection and stop the server. Each chat's switch is kept but does nothing without it."""
    with request.app.state.pool.connection() as conn:
        if not starcall.remove(conn):
            raise HTTPException(status_code=404, detail="StarcallOS isn't set up")
        starcall.close(request.app.state)
        return _starcall_card(request, conn)


def _sign_ins(request: Request) -> google_web.SignIns:
    state = request.app.state
    if getattr(state, "google_sign_ins", None) is None:
        state.google_sign_ins = google_web.SignIns()
    return state.google_sign_ins


@router.post("/google/start")
def google_start(body: ConnectStart, request: Request) -> dict:
    """The Google sign-in link for one service. Only from a browser on this PC, since Google returns to loopback."""
    if not is_local(request):
        raise HTTPException(status_code=403, detail="Connect Google from a browser on the PC running pal.")
    client = google_auth.load_client()
    if client is None:
        raise HTTPException(status_code=409, detail="Store your Google OAuth client first.")
    egress.allow((hosts.GOOGLE_TOKEN_HOST,))  # a fixed name from code: needed to swap Google's code for a token
    origin = f"http://{request.headers['host']}"
    return {"url": _sign_ins(request).start(client, body.service, origin)}


@google_return.get(google_web.CALLBACK_PATH)
def google_callback(request: Request) -> RedirectResponse:
    """Google's answer. Trusted only on loopback with a state pal just issued; the outcome shows on Connections."""
    back = RedirectResponse("/?view=connections", status_code=303)
    if not is_local(request):
        return RedirectResponse("/", status_code=303)
    sign_ins = _sign_ins(request)
    query = dict(request.query_params)
    pending = sign_ins.take(query.get("state"))
    if pending is None:
        sign_ins.record(False, "That sign-in link expired or was already used. Press Connect again.")
        return back
    client = google_auth.load_client()
    try:
        if client is None:
            raise google_auth.GoogleAuthError("The Google OAuth client was removed. Store it again first.")
        with getattr(request.app.state, "google_http", httpx.Client)() as http:  # tests swap in a fake Google
            grant = google_auth.finish(client, pending.service, query, pending.verifier, pending.redirect_uri, http)
            with request.app.state.pool.connection() as conn:
                row = google.save_grant(conn, pending.service, grant, http)
    except google_auth.GoogleAuthError as exc:
        sign_ins.record(False, str(exc))
        return back
    egress.allow(hosts.for_services([("google", pending.service)]))
    who = row.get("account") or "your Google account"
    nxt = " Next, pick folders to sync below." if pending.service == "drive" else " The first sync starts shortly."
    sign_ins.record(True, f"Connected {SERVICE_NAMES[pending.service]} for {who}.{nxt}")
    return back


@router.post("/{connection_id}/sync")
def sync_now(connection_id: UUID, request: Request) -> dict:
    state = request.app.state
    with state.pool.connection() as conn:
        _google(conn, connection_id)
        stop = getattr(getattr(state, "jobs", None), "stop", None)
        return sync.sync_one(conn, connection_id, stop)


@router.patch("/{connection_id}")
def update(connection_id: UUID, body: Patch, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        _google(conn, connection_id)
        if body.enabled is not None:
            store.set_enabled(conn, connection_id, body.enabled)
        if "project_id" in body.model_fields_set:
            try:
                store.set_project(conn, connection_id, body.project_id)
            except psycopg.errors.ForeignKeyViolation as exc:
                raise HTTPException(status_code=422, detail="No such project") from exc
        return _public(store.get(conn, connection_id) or {})


def _google(conn: psycopg.Connection, connection_id: UUID) -> dict:
    """A Google connection. The StarcallOS row has its own routes (no sync, tokens or items to remove)."""
    row = store.get(conn, connection_id)
    if row is None or row["provider"] != "google":
        raise HTTPException(status_code=404, detail="No such connection")
    return row


def _drive(conn: psycopg.Connection, connection_id: UUID) -> dict:
    row = store.get(conn, connection_id)
    if row is None or (row["provider"], row["service"]) != ("google", "drive"):
        raise HTTPException(status_code=404, detail="No such Drive connection")
    return row


@router.get("/{connection_id}/folders")
def folders(connection_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return {"folders": drive_folders.listed(_drive(conn, connection_id))}


@router.post("/{connection_id}/folders")
def add_folder(connection_id: UUID, body: FolderAdd, request: Request) -> dict:
    """Check a Drive folder link with Google and add it to what pal syncs."""
    with request.app.state.pool.connection() as conn:
        row = _drive(conn, connection_id)
        try:
            added = drive_folders.add(conn, row, body.link, getattr(request.app.state, "drive_api", None))
        except drive_folders.FolderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"added": added, "folders": drive_folders.listed(store.get(conn, connection_id) or row)}


@router.delete("/{connection_id}/folders/{folder_id}")
def remove_folder(connection_id: UUID, folder_id: str, request: Request) -> dict:
    """Stop syncing a folder. Its files leave pal at the next sync."""
    with request.app.state.pool.connection() as conn:
        row = _drive(conn, connection_id)
        if folder_id not in {f["id"] for f in drive_folders.listed(row)}:
            raise HTTPException(status_code=404, detail="That folder isn't chosen")
        drive_folders.remove(conn, row, folder_id)
        return {"folders": drive_folders.listed(store.get(conn, connection_id) or row)}


@router.delete("/{connection_id}")
def disconnect(connection_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        _google(conn, connection_id)
        with httpx.Client() as http:
            return google.disconnect(conn, connection_id, http)


@events.get("/free")
def free(request: Request, day: date, start: time, end: time) -> dict:
    """"Am I free?" for one local day and time range, from the connected calendars only."""
    if end <= start:
        raise HTTPException(status_code=422, detail="The end time must be after the start time.")
    lo = reminders.aware(datetime.combine(day, start))
    hi = reminders.aware(datetime.combine(day, end))
    with request.app.state.pool.connection() as conn:
        if not calendar.connected(conn):
            return {"connected": False}
        return {"connected": True, **calendar.free_between(conn, lo, hi)}


@events.get("/events")
def upcoming_events(request: Request, days: int = 14) -> dict:
    """Events on connected calendars from today, for the Schedule page (any signed-in device may read them)."""
    days = max(1, min(days, EVENT_DAYS_MAX))
    today = reminders.local_now().date()
    with request.app.state.pool.connection() as conn:
        return {"connected": calendar.connected(conn),
                "events": [calendar.public(e) for e in calendar.between(conn, today, today + timedelta(days=days))]}
