"""The one path for model-proposed writes, plus deterministic slash commands.

S6: the model can only *propose* (a row in `proposals`). A task or reminder is created when the owner
confirms through the API, or types a slash command themselves. Dates are parsed in code (when.py);
the model only copies the date phrase it saw.
"""

import json
import logging
import re
from datetime import date, datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import reminders, when, work

log = logging.getLogger("pal.actions")

ACTIONS = ("create_task", "set_reminder")
_NULLABLE = {"type": ["string", "null"]}
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["create_task", "set_reminder", "none"]},
        "title": _NULLABLE,
        "due_text": _NULLABLE,
        "recurrence_text": _NULLABLE,
        "project": _NULLABLE,
    },
    "required": ["action", "title", "due_text", "recurrence_text", "project"],
}
# Only messages that could plausibly ask for a task or reminder get the extra model call.
_TRIGGER = re.compile(
    r"\b(remind|reminder|task|todo|to-do|to do|my list|on the list|add|schedule|deadline|due|"
    r"don'?t (let me )?forget|need to|have to|got to|gotta|must|by (mon|tue|wed|thu|fri|sat|sun|tomorrow|tonight|next|the end))",
    re.I,
)


def action_system(now: datetime, projects: list[str]) -> str:
    known = ", ".join(projects) if projects else "none yet"
    return (
        f"Now is {now:%A %Y-%m-%d %H:%M}. Decide whether the user's message asks you to create a task or set a reminder. "
        "Copy any date/time phrase verbatim into due_text and any repeat phrase into recurrence_text; never compute dates. "
        'Use action "none" for questions, vague wishes, statements, missing details, and anything else '
        "(including deleting or completing items). "
        f"Known projects: {known}. Set project to one of these names when the message is about it, else null. "
        'Return JSON: {"action": "create_task|set_reminder|none", "title": str|null, "due_text": str|null, '
        '"recurrence_text": str|null, "project": str|null}.'
    )


def might_be_action(message: str) -> bool:
    return bool(_TRIGGER.search(message)) and not message.lstrip().startswith("/")


def _parse(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_title(title: str) -> str:
    title = work.clean(title, 300).strip(" .!")
    title = re.sub(r"^(?:to|me to|a task to|task:?)\s+", "", title, flags=re.I)
    return title[:1].upper() + title[1:] if title else ""


def _phrase_in(phrase, message: str) -> str | None:
    """Use the model's phrase only if it really appears in the message (it was told to copy, not invent)."""
    if not isinstance(phrase, str) or not phrase.strip():
        return None
    norm = " ".join(phrase.lower().split())
    return phrase if norm in " ".join(message.lower().split()) else None


def build_payload(data: dict, message: str, now: datetime, conn: psycopg.Connection,
                  focus: dict | None) -> tuple[str, dict] | None:
    """Validate the model's JSON in code. Returns (action, payload) or None."""
    action = data.get("action")
    if action not in ACTIONS:
        return None
    title = _clean_title(data.get("title") or "")
    if not title:
        return None
    due_phrase = _phrase_in(data.get("due_text"), message)
    found = when.parse_when(due_phrase, now) or when.parse_when(message, now)
    rrule = when.parse_recurrence(_phrase_in(data.get("recurrence_text"), message)) or when.parse_recurrence(message)
    project = work.find_project(conn, data["project"]) if isinstance(data.get("project"), str) else None
    project = project or focus
    payload = {
        "title": title,
        "project_id": str(project["id"]) if project else None,
        "project": project["name"] if project else None,
        "due_text": due_phrase,
        "rrule": None,
        "repeat": "",
        "due_date": None,
        "at": None,
    }
    if action == "create_task":
        payload["due_date"] = found.at.date().isoformat() if found else None
    else:
        if found is None and rrule is None:
            return None  # "remind me" with no time at all: nothing to propose
        at = found.at if found else datetime.combine(now.date(), when.DEFAULT_TIME)
        if rrule:
            at = when.first_occurrence(rrule, at, now) or at
        payload.update(at=at.isoformat(timespec="minutes"), rrule=rrule, repeat=when.describe_rrule(rrule))
    return action, payload


def _rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


PROPOSAL_COLS = "id, conversation_id, message_id, action, payload, status, result_id, created_at"


def propose(conn: psycopg.Connection, llm, conv_id: UUID, message_id: UUID, message: str,
            now: datetime | None = None) -> dict | None:
    now = now or reminders.local_now()
    raw = llm.complete_json(
        [{"role": "system", "content": action_system(now, work.project_names(conn))},
         {"role": "user", "content": message[:4000]}],
        ACTION_SCHEMA,
    )
    data = _parse(raw)
    if data is None:
        return None
    built = build_payload(data, message, now, conn, work.focus_project(conn, conv_id))
    if built is None:
        return None
    action, payload = built
    return _rows(
        conn,
        f"""INSERT INTO proposals (conversation_id, message_id, action, payload) VALUES (%s, %s, %s, %s)
            RETURNING {PROPOSAL_COLS}""",
        (conv_id, message_id, action, Jsonb(payload)),
    )[0]


def draft_note(proposal: dict) -> str:
    """Tells the chat model what was drafted, so its reply matches the card.

    Dates are deliberately left out: the card shows the date parsed in code, and the small model
    mis-copies dates it is given (M4 check: it wrote Nov 26 for a card saying Nov 25).
    """
    p = proposal["payload"]
    kind = "task" if proposal["action"] == "create_task" else "reminder"
    what = f'a {kind} "{p["title"]}"' + (f" in project {p['project']}" if p.get("project") else "")
    return (
        f"pal has drafted {what}. It is NOT saved yet: the user checks the date, edits and confirms it on a card "
        f'shown below your reply. Reply with one short sentence such as "I\'ve drafted that {kind} below. '
        'Add it if it looks right." Do not mention any date or time.'
    )


def pending(conn: psycopg.Connection, conv_id: UUID) -> list[dict]:
    return _rows(
        conn,
        f"SELECT {PROPOSAL_COLS} FROM proposals WHERE conversation_id = %s AND status = 'proposed' ORDER BY created_at",
        (conv_id,),
    )


def reject(conn: psycopg.Connection, proposal_id: UUID) -> bool:
    return conn.execute("DELETE FROM proposals WHERE id = %s AND status = 'proposed'", (proposal_id,)).rowcount == 1


def confirm(conn: psycopg.Connection, proposal_id: UUID, edits: dict | None = None,
            now: datetime | None = None) -> dict | None:
    """Create what the proposal describes, with the owner's edits applied. Returns {proposal, task|reminder}."""
    edits = {k: v for k, v in (edits or {}).items() if v is not None or k in ("due_date", "project_id", "rrule")}
    with conn.transaction():
        found = _rows(conn, f"SELECT {PROPOSAL_COLS} FROM proposals WHERE id = %s AND status = 'proposed' FOR UPDATE",
                      (proposal_id,))
        if not found:
            return None
        prop = found[0]
        p = {**prop["payload"], **edits}
        title = _clean_title(p.get("title") or "")
        if not title:
            raise ValueError("The title is empty.")
        project_id = UUID(p["project_id"]) if p.get("project_id") else None
        result: dict
        if prop["action"] == "create_task":
            due = date.fromisoformat(p["due_date"]) if p.get("due_date") else None
            item = work.create_task(conn, title, project_id, due, prop["conversation_id"])
            result = {"task": item}
        else:
            if not p.get("at"):
                raise ValueError("Pick a time for the reminder.")
            at = datetime.fromisoformat(p["at"]).replace(tzinfo=None, second=0, microsecond=0)
            rule = when.validate_rrule(p["rrule"]) if p.get("rrule") else None
            item = reminders.create(conn, title, at, rule, project_id=project_id, now=now)
            result = {"reminder": item}
        conn.execute("UPDATE proposals SET status = 'confirmed', result_id = %s WHERE id = %s",
                     (item["id"], proposal_id))
    return result


# ---------- slash commands (typed by the owner, so they act directly) ----------

HELP = (
    "Commands:\n"
    "- `/task renew passport by friday` adds a task (to this chat's project, if one is set)\n"
    "- `/remind tomorrow 6pm to call mom` or `/remind every monday 9am review the week`\n"
    "- `/note text` and `/decision text` save a note or decision to this chat's project\n"
    "- `/project name` sets this chat's project (creating it if new); `/project none` clears it\n"
    "- `/model` lists models; `/model name` switches this chat (part of the name is enough); `/model default` resets"
)


def _strip_lead(text: str, words: str) -> str:
    return re.sub(rf"^(?:{words})\s+", "", text.strip(), flags=re.I).strip(" ,.;:-")


def run_command(conn: psycopg.Connection, conv_id: UUID, text: str, now: datetime | None = None) -> dict:
    """Returns {reply, changed: bool, task?/reminder?/note?/project?}."""
    now = now or reminders.local_now()
    cmd, _, rest = text.strip().partition(" ")
    cmd, rest = cmd.lower(), rest.strip()
    focus = work.focus_project(conn, conv_id)
    pid = focus["id"] if focus else None
    in_project = f" in **{focus['name']}**" if focus else ""

    if cmd == "/task":
        found, title = when.find_when(rest, now)
        title = _clean_title(re.sub(r"\s+(?:by|due|on|before)$", "", title, flags=re.I))
        if not title:
            return {"reply": "What's the task? e.g. `/task renew passport by friday`", "changed": False}
        task = work.create_task(conn, title, pid, found.at.date() if found else None, conv_id)
        return {"reply": f"Added task **{task['title']}**{_due(task['due_date'])}{in_project}.", "changed": True,
                "task": task}

    if cmd == "/remind":
        rest = _strip_lead(rest, "me")
        rule, rest2 = when.find_recurrence(rest)
        found, title = when.find_when(rest2, now)
        title = _clean_title(_strip_lead(title, "to|about|that"))
        if not title or (found is None and rule is None):
            return {"reply": "When, and what? e.g. `/remind tomorrow 6pm to call mom`", "changed": False}
        at = found.at if found else datetime.combine(now.date(), when.DEFAULT_TIME)
        if found and not found.has_time:
            at = datetime.combine(at.date(), when.DEFAULT_TIME)
        if rule is None and at <= now:
            return {"reply": f"That time ({at:%Y-%m-%d %H:%M}) has already passed.", "changed": False}
        item = reminders.create(conn, title, at, rule, project_id=pid, now=now)
        repeat = f", {item['repeat']}" if item["repeat"] else ""
        return {"reply": f"Reminder set: **{item['title']}** at {_fmt(reminders.to_local(item['due_at']))}{repeat}.",
                "changed": True, "reminder": item}

    if cmd in ("/note", "/decision"):
        if not rest:
            return {"reply": f"Write the {cmd[1:]} after the command, e.g. `{cmd} we use PostgreSQL`", "changed": False}
        note = work.create_note(conn, rest, cmd[1:], pid)
        return {"reply": f"Saved {cmd[1:]}{in_project}.", "changed": True, "note": note}

    if cmd == "/project":
        if not rest:
            return {"reply": f"This chat's project is **{focus['name']}**." if focus
                    else "This chat has no project. Set one with `/project name`.", "changed": False}
        if rest.lower() in ("none", "clear", "off"):
            work.set_focus(conn, conv_id, None)
            return {"reply": "This chat no longer has a project.", "changed": True}
        project = work.find_project(conn, rest)
        created = project is None
        project = project or work.create_project(conn, rest)
        work.set_focus(conn, conv_id, project["id"])
        verb = "Created project" if created else "This chat is now about"
        return {"reply": f"{verb} **{project['name']}**" + (" and set it for this chat." if created else "."),
                "changed": True, "project": project}

    return {"reply": HELP, "changed": False}


def _fmt(at: datetime) -> str:
    return f"{at:%a %b} {at.day}, {at:%H:%M}"


def _due(d: date | None) -> str:
    return f", due {d:%a %b} {d.day}" if d else ""


def is_command(text: str) -> bool:
    return bool(re.match(r"^/[a-z]+(\s|$)", text.strip(), re.I))
