"""Connected services (PC only): list them, sync now, pause or resume, disconnect. Plus calendar events to read."""

from datetime import timedelta
from uuid import UUID

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import calendar, reminders
from .api import require_owner, require_session
from .config import in_container
from .connectors import drive_folders, google, google_auth, store, sync
from .connectors.google_cli import DISCLOSURE

router = APIRouter(prefix="/api/connections", dependencies=[Depends(require_owner)])
events = APIRouter(prefix="/api/calendar", dependencies=[Depends(require_session)])
EVENT_DAYS_MAX = 60


class Patch(BaseModel):
    enabled: bool | None = None
    project_id: UUID | None = None  # the project synced files go in (null: none)


class FolderAdd(BaseModel):
    link: str = Field(min_length=1, max_length=drive_folders.LINK_MAX)


def _services() -> list[dict]:
    """What can be connected, and the command that does it. Signing in stays on the PC's command line."""
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
            "services": _services()}


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
