"""Connected services (PC only): list them, sync now, pause or resume, disconnect. Plus calendar events to read."""

from datetime import timedelta
from uuid import UUID

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import calendar, egress, reminders
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
    client_cmd = ("bash scripts/pal-docker.sh google-client client.json" if in_container()
                  else "pal google-client --set client.json")
    return {"connections": rows, "google_client": google_auth.load_client() is not None, "client_command": client_cmd,
            "services": _services(), "signin": _sign_ins(request).pop_outcome()}


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
        if store.get(conn, connection_id) is None:
            raise HTTPException(status_code=404, detail="No such connection")
        stop = getattr(getattr(state, "jobs", None), "stop", None)
        return sync.sync_one(conn, connection_id, stop)


@router.patch("/{connection_id}")
def update(connection_id: UUID, body: Patch, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        if store.get(conn, connection_id) is None:
            raise HTTPException(status_code=404, detail="No such connection")
        if body.enabled is not None:
            store.set_enabled(conn, connection_id, body.enabled)
        if "project_id" in body.model_fields_set:
            try:
                store.set_project(conn, connection_id, body.project_id)
            except psycopg.errors.ForeignKeyViolation as exc:
                raise HTTPException(status_code=422, detail="No such project") from exc
        return _public(store.get(conn, connection_id) or {})


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
        if store.get(conn, connection_id) is None:
            raise HTTPException(status_code=404, detail="No such connection")
        with httpx.Client() as http:
            return google.disconnect(conn, connection_id, http)


@events.get("/events")
def upcoming_events(request: Request, days: int = 14) -> dict:
    """Events on connected calendars from today, for the Schedule page (any signed-in device may read them)."""
    days = max(1, min(days, EVENT_DAYS_MAX))
    today = reminders.local_now().date()
    with request.app.state.pool.connection() as conn:
        return {"connected": calendar.connected(conn),
                "events": [calendar.public(e) for e in calendar.between(conn, today, today + timedelta(days=days))]}
