"""Connected services (PC only): list them, sync now, pause or resume, disconnect. Plus calendar events to read."""

from datetime import timedelta
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import calendar, reminders
from .api import require_owner, require_session
from .connectors import google, store, sync

router = APIRouter(prefix="/api/connections", dependencies=[Depends(require_owner)])
events = APIRouter(prefix="/api/calendar", dependencies=[Depends(require_session)])
EVENT_DAYS_MAX = 60


class Patch(BaseModel):
    enabled: bool


def _public(row: dict) -> dict:
    """What the Connections page shows. The cursor is internal bookkeeping and stays out."""
    return {k: v for k, v in row.items() if k != "cursor"}


@router.get("")
def list_connections(request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return {"connections": [_public(r) for r in store.list_all(conn)]}


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
        if not store.set_enabled(conn, connection_id, body.enabled):
            raise HTTPException(status_code=404, detail="No such connection")
        return _public(store.get(conn, connection_id) or {})


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
