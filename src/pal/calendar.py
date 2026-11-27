"""Events from connected calendars: what the morning brief, the Schedule page and chat answers use.

pal only knows the calendars the owner connected. Answers about availability must say so: "I don't see anything on
your connected calendars", never "you're free". Event text comes from outside (anyone can send an invite), so it
goes into the context block as data like any other item and can never start an action.
"""

import re
from datetime import date, datetime, time, timedelta, tzinfo

import psycopg
from psycopg.rows import dict_row

from .when import find_when

MAX_CONTEXT_EVENTS = 15
DEFAULT_DAYS = 7
DESCRIPTION_IN_CONTEXT = 200
COVERAGE = "connected calendars only"
_ASKS = re.compile(
    r"\b(calendar|schedule[ds]?|meetings?|appointments?|events?|agenda|busy|available|availability|booked|plans|"
    r"free\s+(?:on|at|this|next|tomorrow|today|tonight|in)|what'?s\s+on|what\s+is\s+on|anything\s+on|"
    r"when(?:'s|\s+is)\s+my)\b", re.I)
_NEXT_WEEK = re.compile(r"\bnext\s+week\b", re.I)
_THIS_WEEK = re.compile(r"\b(?:this|the)\s+week\b", re.I)


def connected(conn: psycopg.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM connections WHERE provider = 'google' AND service = 'calendar' AND enabled").fetchone()
    return row is not None


def _bounds(day: date, tz: tzinfo | None) -> datetime:
    start = datetime.combine(day, time())
    return start.replace(tzinfo=tz) if tz else start.astimezone()


def between(conn: psycopg.Connection, start: date, end: date, tz: tzinfo | None = None) -> list[dict]:
    """Events overlapping [start, end) in local days, in time order (all-day events first on their day).

    Timed events get local `start`/`end` datetimes; all-day events keep their dates untouched.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            """SELECT e.id, e.title, e.location, e.description, e.all_day, e.start_at, e.end_at, e.start_date,
                      e.end_date, e.time_zone, e.attendees, e.status
               FROM calendar_events e JOIN connections c ON c.id = e.connection_id AND c.enabled
               WHERE (NOT e.all_day AND e.start_at < %(hi)s AND e.end_at > %(lo)s)
                  OR (e.all_day AND e.start_date < %(end)s AND e.end_date > %(start)s)""",
            {"lo": _bounds(start, tz), "hi": _bounds(end, tz), "start": start, "end": end},
        ).fetchall()
    out = []
    for r in rows:
        if r["all_day"]:
            out.append({**r, "day": max(r["start_date"], start), "sort": (max(r["start_date"], start), 0, time())})
        else:
            s, e = r["start_at"].astimezone(tz), r["end_at"].astimezone(tz)
            out.append({**r, "start": s, "end": e, "day": s.date(), "sort": (s.date(), 1, s.time())})
    out.sort(key=lambda r: (r["sort"], r["title"]))
    return out


def _day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def describe(event: dict) -> str:
    """'Tue 2 Dec, 10:00–11:00' or 'Tue 2 Dec, all day' or 'Tue 2 Dec – Thu 4 Dec, all day'."""
    if event["all_day"]:
        last = event["end_date"] - timedelta(days=1)
        span = _day(event["start_date"]) if last == event["start_date"] else f"{_day(event['start_date'])} – {_day(last)}"
        return f"{span}, all day"
    s, e = event["start"], event["end"]
    if e.date() != s.date():
        return f"{_day(s.date())} {s:%H:%M} – {_day(e.date())} {e:%H:%M}"
    return f"{_day(s.date())}, {s:%H:%M}–{e:%H:%M}"


def asks_about_calendar(text: str) -> bool:
    return bool(_ASKS.search(text))


def question_window(text: str, now: datetime) -> tuple[date, date]:
    """The local days a question is about: a named day, this or next week, else the coming week."""
    today = now.date()
    if _NEXT_WEEK.search(text):
        monday = today + timedelta(days=7 - today.weekday())
        return monday, monday + timedelta(days=7)
    if _THIS_WEEK.search(text):
        return today, today + timedelta(days=7 - today.weekday())
    when, _ = find_when(text, now)
    if when is not None:
        return when.at.date(), when.at.date() + timedelta(days=1)
    return today, today + timedelta(days=DEFAULT_DAYS)


def _item(handle: str, kind: str, title: str, text: str, item_id: str | None = None) -> dict:
    return {"handle": handle, "kind": kind, "id": item_id, "title": title, "text": text, "snippet": text[:300],
            "page": None, "conversation_id": None, "document_id": None, "at": None, "pinned": False}


def context_items(conn: psycopg.Connection, text: str, now: datetime, tz: tzinfo | None = None) -> list[dict]:
    """Calendar items for a chat turn: matching events as [e1], [e2]…, or one item saying there are none.

    Nothing at all unless a calendar is connected and the message asks about plans or times.
    """
    if not asks_about_calendar(text) or not connected(conn):
        return []
    start, end = question_window(text, now)
    events = between(conn, start, end, tz)
    span = _day(start) if end - start == timedelta(days=1) else f"{_day(start)} to {_day(end - timedelta(days=1))}"
    if not events:
        return [_item("e1", "calendar", "calendar", f"No events on the {COVERAGE} for {span}.")]
    items = []
    for i, e in enumerate(events[:MAX_CONTEXT_EVENTS], start=1):
        extra = [f"at {e['location']}" if e["location"] else "", "tentative" if e["status"] == "tentative" else "",
                 f"{e['attendees']} attendees" if e["attendees"] > 1 else ""]
        desc = (e["description"] or "")[:DESCRIPTION_IN_CONTEXT]
        line = " · ".join(p for p in (describe(e), e["title"], *extra) if p) + (f" — {desc}" if desc else "")
        items.append(_item(f"e{i}", "event", e["title"], line, str(e["id"])))
    if len(events) > MAX_CONTEXT_EVENTS:
        items.append(_item(f"e{len(items) + 1}", "calendar", "calendar",
                           f"{len(events) - MAX_CONTEXT_EVENTS} more events for {span} are not shown."))
    return items


def today_summary(conn: psycopg.Connection, today: date, tz: tzinfo | None = None) -> tuple[int, str | None]:
    """(how many events today, the first timed one as '10:00 Standup') for the morning brief."""
    events = between(conn, today, today + timedelta(days=1), tz)
    timed = [e for e in events if not e["all_day"] and e["start"].date() == today]
    first = f"{timed[0]['start']:%H:%M} {timed[0]['title']}" if timed else None
    return len(events), first


def public(event: dict) -> dict:
    """An event for the Schedule page."""
    return {"id": str(event["id"]), "title": event["title"], "when": describe(event), "all_day": event["all_day"],
            "day": event["day"].isoformat(), "location": event["location"], "status": event["status"],
            "start": event["start"].isoformat() if not event["all_day"] else None}
