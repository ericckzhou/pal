"""Google Calendar connector: re-read a fixed window of events and replace it, one calendar at a time.

Why not Google's incremental sync: its syncToken can't be combined with timeMin/timeMax, and without a time bound
open-ended repeating events would expand without end. A personal calendar has a few hundred events in the window,
so a full re-read every sync costs a handful of requests and makes cancelled or moved events disappear on their own.
Each calendar's replacement is one short transaction (no model calls inside), so a failed sync changes nothing.
"""

import html
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

from . import google_api, sync

API = "https://www.googleapis.com/calendar/v3"
DAYS_BACK = 30
DAYS_AHEAD = 180
DESCRIPTION_MAX = 1000
FIELDS = ("items(id,recurringEventId,status,summary,location,description,start,end,attendees(self,responseStatus),"
          "updated),nextPageToken,timeZone")
_TAGS = re.compile(r"<[^>]+>")


class CalendarError(Exception):
    pass


def window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(days=DAYS_BACK), now + timedelta(days=DAYS_AHEAD)


def _clean(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = " ".join(html.unescape(_TAGS.sub(" ", text)).split())
    return text[:limit] or None


def _declined(event: dict) -> bool:
    return any(a.get("self") and a.get("responseStatus") == "declined" for a in event.get("attendees") or [])


def parse_event(event: dict, calendar_tz: str | None) -> dict | None:
    """One Google event as a calendar_events row, or None for cancelled, declined or malformed ones."""
    if event.get("status") == "cancelled" or _declined(event) or not event.get("id"):
        return None
    start, end = event.get("start") or {}, event.get("end") or {}
    row = {
        "event_id": event["id"], "recurring_event_id": event.get("recurringEventId"),
        "title": _clean(event.get("summary"), 300) or "(no title)", "location": _clean(event.get("location"), 300),
        "description": _clean(event.get("description"), DESCRIPTION_MAX),
        "attendees": len(event.get("attendees") or []), "status": event.get("status") or "confirmed",
        "updated": datetime.fromisoformat(event["updated"]) if event.get("updated") else None,
        "start_at": None, "end_at": None, "start_date": None, "end_date": None,
    }
    try:
        if "date" in start:  # all-day: a date range with the end date not included
            s = date.fromisoformat(start["date"])
            e = date.fromisoformat(end["date"]) if "date" in end else s + timedelta(days=1)
            return {**row, "all_day": True, "start_date": s, "end_date": max(e, s + timedelta(days=1)),
                    "time_zone": calendar_tz}
        s_at = datetime.fromisoformat(start["dateTime"])
        e_at = datetime.fromisoformat(end["dateTime"]) if "dateTime" in end else s_at
    except (KeyError, ValueError):
        return None
    if s_at.tzinfo is None or e_at.tzinfo is None:  # Google always sends an offset; refuse to guess one
        return None
    return {**row, "all_day": False, "start_at": s_at, "end_at": max(e_at, s_at),
            "time_zone": start.get("timeZone") or calendar_tz}


class GoogleCalendar:
    def __init__(self, api: google_api.GoogleAPI, now: datetime | None = None) -> None:
        self._api = api
        self._now = now

    def fetch(self, ctx: sync.SyncContext, calendar_id: str, start: datetime, end: datetime) -> list[dict]:
        rows: list[dict] = []
        page: str | None = None
        while True:
            ctx.check_stop()
            data = self._api.get(f"{API}/calendars/{quote(calendar_id, safe='')}/events", {
                "timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                "showDeleted": "false", "maxResults": 2500, "fields": FIELDS, **({"pageToken": page} if page else {}),
            })
            tz = data.get("timeZone")
            rows.extend(r for r in (parse_event(e, tz) for e in data.get("items", [])) if r)
            page = data.get("nextPageToken")
            if not page:
                return rows

    def sync(self, ctx: sync.SyncContext) -> str | None:
        start, end = window(self._now or datetime.now(UTC))
        calendars = ctx.config.get("calendars") or ["primary"]
        fetched = {cal: self.fetch(ctx, cal, start, end) for cal in calendars}  # all reads before any write
        ctx.check_stop()
        for cal, rows in fetched.items():
            replace(ctx, cal, rows)
        # Calendars no longer chosen lose their events.
        ctx.conn.execute("DELETE FROM calendar_events WHERE connection_id = %s AND NOT (calendar_id = ANY(%s))",
                         (ctx.connection["id"], list(calendars)))
        return None  # no cursor: every sync re-reads the window


def replace(ctx: sync.SyncContext, calendar_id: str, rows: list[dict]) -> None:
    """Swap one calendar's stored events for `rows` in one transaction, counting what changed."""
    conn, cid = ctx.conn, ctx.connection["id"]
    with conn.transaction():
        before: dict[str, datetime | None] = dict(conn.execute(
            "SELECT event_id, updated FROM calendar_events WHERE connection_id = %s AND calendar_id = %s",
            (cid, calendar_id)).fetchall())
        conn.execute("DELETE FROM calendar_events WHERE connection_id = %s AND calendar_id = %s", (cid, calendar_id))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO calendar_events (connection_id, calendar_id, event_id, recurring_event_id, title, location, "
                "description, all_day, start_at, end_at, start_date, end_date, time_zone, attendees, status, updated) "
                "VALUES (%(cid)s, %(cal)s, %(event_id)s, %(recurring_event_id)s, %(title)s, %(location)s, "
                "%(description)s, %(all_day)s, %(start_at)s, %(end_at)s, %(start_date)s, %(end_date)s, %(time_zone)s, "
                "%(attendees)s, %(status)s, %(updated)s) ON CONFLICT (connection_id, calendar_id, event_id) DO NOTHING",
                [{**r, "cid": cid, "cal": calendar_id} for r in rows],
            )
    after = {r["event_id"]: r["updated"] for r in rows}
    ctx.summary["added"] += len(after.keys() - before.keys())
    ctx.summary["removed"] += len(before.keys() - after.keys())
    changed = len([k for k in after.keys() & before.keys() if after[k] != before[k]])
    ctx.summary["updated"] += changed
    ctx.summary["unchanged"] += len(after.keys() & before.keys()) - changed


def factory(row: dict) -> GoogleCalendar:
    return GoogleCalendar(google_api.GoogleAPI(google_api.shared_tokens(), google_api.shared_client(), "Google Calendar"))


sync.REGISTRY[("google", "calendar")] = factory
