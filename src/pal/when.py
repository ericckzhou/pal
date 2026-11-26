"""Deterministic date, time and repeat parsing. The model never computes dates; it only copies phrases.

All datetimes here are naive local wall-clock times; callers attach the zone (`.astimezone()`).
Supported: today / tonight / tomorrow / day after tomorrow, weekday names ("friday", "next friday"),
"Dec 1", "1 December", "2026-12-01", "12/1", "in April", "this weekend", "next week", "next month",
"in 2 hours" / "in 3 days", times ("6pm", "6:30 pm", "14:45", "at 9", "noon", "morning"),
and "15 minutes before 3pm". Repeats: daily, weekdays, every Monday (and Thursday), weekly,
every other week, every N days/weeks/months, monthly, yearly, hourly.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
RRULE_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
DEFAULT_TIME = time(9, 0)
PARTS = {"morning": time(9), "afternoon": time(15), "evening": time(18), "tonight": time(20), "night": time(20)}
NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45,
}

_WD = r"(?P<wd>mon|tue(?:s)?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)day"
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_NUM = r"(?P<n>\d+|" + "|".join(sorted(NUMBERS, key=len, reverse=True)) + r"|half an)"
_LEAD = r"(?:(?:by|on|due|at|for|before|until)\s+)?"

_RELATIVE = re.compile(rf"\bin\s+{_NUM}\s+(?P<unit>min(?:ute)?s?|h(?:ou)?rs?|hours?|days?|weeks?|months?)\b")
_BEFORE = re.compile(rf"\b{_NUM}\s+(?P<unit>min(?:ute)?s?|hours?)\s+before\b")
_DAYWORD = re.compile(
    _LEAD + r"\b(?P<w>(?:the\s+)?day\s+after\s+tomorrow|tomorrow|tmrw|today|tonight|this\s+weekend|next\s+week|next\s+month)\b"
)
_WEEKDAY = re.compile(_LEAD + rf"\b(?:(?P<rel>next|this|coming)\s+)?{_WD}\b(?!s)")
_ISO = re.compile(_LEAD + r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b")
_SLASH = re.compile(_LEAD + r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?\b")
_MONTH_DAY = re.compile(_LEAD + rf"\b{_MON}\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y>\d{{4}}))?")
_DAY_MONTH = re.compile(_LEAD + rf"\b(?:the\s+)?(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}\b(?:,?\s+(?P<y>\d{{4}}))?")
_IN_MONTH = re.compile(rf"\bin\s+{_MON}\b")
_CLOCK = re.compile(r"(?:\bat\s+)?\b(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?=\W|$)")
_CLOCK24 = re.compile(r"(?:\bat\s+)?\b(?P<h>[01]?\d|2[0-3]):(?P<mi>[0-5]\d)\b")
_AT_HOUR = re.compile(r"\bat\s+(?P<h>\d{1,2})\b(?![:/\d])")
_NAMED_TIME = re.compile(r"(?:\bat\s+)?\b(?:(?:this|in\s+the)\s+)?(?P<t>noon|midday|midnight|morning|afternoon|evening|night)\b")


@dataclass(frozen=True)
class When:
    at: datetime
    has_time: bool


def _num(word: str) -> float:
    if word == "half an":
        return 0.5
    return float(word) if word.isdigit() else float(NUMBERS[word])


def _month(word: str) -> int:
    return MONTHS.index(word[:3]) + 1


def _weekday(word: str) -> int:
    return next(i for i, d in enumerate(WEEKDAYS) if d.startswith(word[:3]))


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _upcoming(today: date, m: int, d: int, y: int | None) -> date | None:
    if y is not None:
        return _safe_date(y if y > 99 else 2000 + y, m, d)
    found = _safe_date(today.year, m, d)
    if found and found < today:
        found = _safe_date(today.year + 1, m, d)
    return found


def _first(text: str, patterns, spans: list[tuple[int, int]]):
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            spans.append(match.span())
            return match
    return None


def _scan(text: str, now: datetime) -> tuple[When | None, list[tuple[int, int]]]:
    text = text.lower()
    today = now.date()
    spans: list[tuple[int, int]] = []

    rel = _first(text, [_RELATIVE], spans)
    if rel:
        n, unit = _num(rel["n"]), rel["unit"]
        if unit.startswith("min"):
            return When((now + timedelta(minutes=n)).replace(second=0, microsecond=0), True), spans
        if unit.startswith("h"):
            return When((now + timedelta(hours=n)).replace(second=0, microsecond=0), True), spans
        days = n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
        day: date | None = today + timedelta(days=round(days))
    else:
        day = None

    # A time of day.
    clock: time | None = None
    m = _first(text, [_CLOCK], spans)
    if m:
        h, mi = int(m["h"]) % 12, int(m["mi"] or 0)
        clock = time(h + (12 if m["ap"].startswith("p") else 0), mi) if mi < 60 else None
    if clock is None:
        m = _first(text, [_CLOCK24], spans)
        if m:
            clock = time(int(m["h"]), int(m["mi"]))
    if clock is None:
        m = _first(text, [_AT_HOUR], spans)
        if m and 1 <= int(m["h"]) <= 12:
            h = int(m["h"])
            clock = time(h + 12 if h <= 6 else h)
    named = _NAMED_TIME.search(text)
    if clock is None and named:
        spans.append(named.span())
        t = named["t"]
        clock = time(12) if t in ("noon", "midday") else time(0) if t == "midnight" else PARTS[t]

    before = _first(text, [_BEFORE], spans)

    # A day.
    if day is None:
        m = _first(text, [_DAYWORD], spans)
        if m:
            w = " ".join(m["w"].split())
            if w.endswith("day after tomorrow"):
                day = today + timedelta(days=2)
            elif w in ("tomorrow", "tmrw"):
                day = today + timedelta(days=1)
            elif w == "today":
                day = today
            elif w == "tonight":
                day = today
                clock = clock or PARTS["tonight"]
            elif w == "this weekend":
                day = today + timedelta(days=(5 - today.weekday()) % 7)
            elif w == "next week":
                day = today + timedelta(days=7 - today.weekday())
            elif w == "next month":
                day = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    if day is None:
        m = _first(text, [_WEEKDAY], spans)
        if m:
            wd = _weekday(m["wd"])
            if m["rel"] == "next":
                day = today + timedelta(days=7 - today.weekday() + wd)
            else:
                ahead = (wd - today.weekday()) % 7
                if ahead == 0 and clock is not None and datetime.combine(today, clock) <= now:
                    ahead = 7
                day = today + timedelta(days=ahead)
    if day is None:
        m = _first(text, [_ISO], spans)
        if m:
            day = _safe_date(int(m["y"]), int(m["m"]), int(m["d"]))
    if day is None:
        m = _first(text, [_MONTH_DAY, _DAY_MONTH], spans)
        if m:
            day = _upcoming(today, _month(m["mon"]), int(m["d"]), int(m["y"]) if m["y"] else None)
    if day is None:
        m = _first(text, [_SLASH], spans)
        if m:
            day = _upcoming(today, int(m["m"]), int(m["d"]), int(m["y"]) if m["y"] else None)
    if day is None:
        m = _first(text, [_IN_MONTH], spans)
        if m:
            day = _upcoming(today, _month(m["mon"]), 1, None)

    if day is None and clock is None:
        return None, []
    if day is None:  # only a time (clock is set, checked above): today if it's still ahead, else tomorrow
        day = today if datetime.combine(today, clock or DEFAULT_TIME) > now else today + timedelta(days=1)
    at = datetime.combine(day, clock or DEFAULT_TIME)
    if before and clock is not None:
        n = _num(before["n"])
        at -= timedelta(hours=n) if before["unit"].startswith("h") else timedelta(minutes=n)
    return When(at, clock is not None), spans


def parse_when(text: str | None, now: datetime) -> When | None:
    """Find a date/time anywhere in `text`. None when there isn't one."""
    if not text:
        return None
    return _scan(text, now)[0]


def find_when(text: str, now: datetime) -> tuple[When | None, str]:
    """Like parse_when, and also returns `text` with the date/time words removed (for /task and /remind)."""
    found, spans = _scan(text, now)
    return found, _remove(text, spans)


# ---------- repeats ----------

_EVERY_N = re.compile(r"\bevery\s+(?P<n>\d+|other|second|two|three|four)\s+(?P<unit>hour|day|week|month|year)s?\b")
_WEEKDAYS_ONLY = re.compile(r"\b(?:every\s+weekday|on\s+weekdays|weekdays|every\s+work\s*day)\b")
_DAY = r"(?:mon|tue(?:s)?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)day"
_DAY_LIST = re.compile(
    rf"\b(?:every|each)\s+{_DAY}s?(?:\s*(?:,\s*and|,|and|&)\s*{_DAY}s?)*"  # every Monday and Thursday
    rf"|\b(?:on\s+)?{_DAY}s(?:\s*(?:,\s*and|,|and|&)\s*{_DAY}s)*\b"  # (on) Mondays; "on Monday" is a date
)
_SIMPLE = [
    (re.compile(r"\b(?:every|each)\s+(?:day|morning|evening|night)\b|\bdaily\b|\bnightly\b"), "FREQ=DAILY"),
    (re.compile(r"\bbi-?weekly\b|\bfortnightly\b"), "FREQ=WEEKLY;INTERVAL=2"),
    (re.compile(r"\b(?:every|each)\s+week\b|\bweekly\b"), "FREQ=WEEKLY"),
    (re.compile(r"\b(?:every|each)\s+month\b|\bmonthly\b"), "FREQ=MONTHLY"),
    (re.compile(r"\b(?:every|each)\s+year\b|\byearly\b|\bannually\b"), "FREQ=YEARLY"),
    (re.compile(r"\b(?:every|each)\s+hour\b|\bhourly\b"), "FREQ=HOURLY"),
]
_FREQ = {"hour": "HOURLY", "day": "DAILY", "week": "WEEKLY", "month": "MONTHLY", "year": "YEARLY"}


def _recurrence(text: str) -> tuple[str | None, tuple[int, int] | None]:
    text = text.lower()
    m = _EVERY_N.search(text)
    if m:
        n = {"other": 2, "second": 2, "two": 2, "three": 3, "four": 4}.get(m["n"]) or int(m["n"])
        if 1 <= n <= 365:
            rule = f"FREQ={_FREQ[m['unit']]}" + (f";INTERVAL={n}" if n > 1 else "")
            return rule, m.span()
    m = _WEEKDAYS_ONLY.search(text)
    if m:
        return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", m.span()
    m = _DAY_LIST.search(text)
    if m:
        names = re.findall(r"(mon|tue|wed|thu|fri|sat|sun)", m.group(0))
        days = sorted({_weekday(d) for d in names})
        if days:
            return "FREQ=WEEKLY;BYDAY=" + ",".join(RRULE_DAYS[d] for d in days), m.span()
    for pattern, rule in _SIMPLE:
        m = pattern.search(text)
        if m:
            return rule, m.span()
    return None, None


def parse_recurrence(text: str | None) -> str | None:
    """A repeat phrase -> an RRULE subset (FREQ, INTERVAL, BYDAY). None when it doesn't repeat."""
    return _recurrence(text)[0] if text else None


def find_recurrence(text: str) -> tuple[str | None, str]:
    rule, span = _recurrence(text)
    return rule, _remove(text, [span] if span else [])


def _remove(text: str, spans: list[tuple[int, int]]) -> str:
    out = text
    for start, end in sorted(spans, reverse=True):
        out = out[:start] + " " + out[end:]
    return " ".join(out.split())


def _rule(rrule: str) -> tuple[str, int, list[int]]:
    parts = dict(p.split("=", 1) for p in rrule.split(";") if "=" in p)
    freq = parts.get("FREQ", "")
    if freq not in _FREQ.values():
        raise ValueError(f"unsupported repeat rule: {rrule}")
    interval = int(parts.get("INTERVAL", "1"))
    if not 1 <= interval <= 365:
        raise ValueError("repeat interval out of range")
    days = [RRULE_DAYS.index(d) for d in parts["BYDAY"].split(",")] if parts.get("BYDAY") else []
    return freq, interval, days


def validate_rrule(rrule: str) -> str:
    _rule(rrule)
    return rrule


def next_occurrence(rrule: str, anchor: datetime, after: datetime) -> datetime | None:
    """The first occurrence of `rrule` (repeating from `anchor`) strictly after `after`."""
    freq, interval, days = _rule(rrule)
    if freq == "HOURLY":
        step = timedelta(hours=interval)
        if after < anchor:
            return anchor
        k = int((after - anchor) / step) + 1
        return anchor + k * step
    if freq == "DAILY":
        step = timedelta(days=interval)
        if after < anchor:
            return anchor
        k = (after - anchor) // step + 1
        return anchor + k * step
    if freq == "WEEKLY":
        days = days or [anchor.weekday()]
        week0 = anchor.date() - timedelta(days=anchor.weekday())
        day = max(anchor.date(), after.date())
        for _ in range(7 * interval * 2 + 8):
            cand = datetime.combine(day, anchor.time())
            if day.weekday() in days and ((day - week0).days // 7) % interval == 0 and cand > after and cand >= anchor:
                return cand
            day += timedelta(days=1)
        return None
    # MONTHLY / YEARLY on the anchor's day of month (months without that day are skipped).
    months = interval if freq == "MONTHLY" else 12 * interval
    for k in range(0, 12 * 50):
        total = anchor.month - 1 + k * months
        d = _safe_date(anchor.year + total // 12, total % 12 + 1, anchor.day)
        if d is None:
            continue
        cand = datetime.combine(d, anchor.time())
        if cand > after:
            return cand
    return None


def first_occurrence(rrule: str, anchor: datetime, now: datetime) -> datetime | None:
    """First firing for a new repeating reminder: on or after the anchor, and in the future."""
    return next_occurrence(rrule, anchor, max(now, anchor - timedelta(seconds=1)))


def describe_rrule(rrule: str | None) -> str:
    if not rrule:
        return ""
    freq, interval, days = _rule(rrule)
    unit = {"HOURLY": "hour", "DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}[freq]
    if days == [0, 1, 2, 3, 4] and interval == 1:
        return "every weekday"
    if days:
        names = [WEEKDAYS[d].capitalize() for d in days]
        lead = "every" if interval == 1 else f"every {interval} weeks on"
        return f"{lead} {', '.join(names)}"
    return f"every {unit}" if interval == 1 else f"every {interval} {unit}s"
