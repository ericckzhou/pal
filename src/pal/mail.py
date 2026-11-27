"""Gmail in a chat turn: decide whether to search, write the search, and hand the results over as untrusted data.

- pal searches only when Gmail is connected and switched on, the chat allows it (`/gmail off` stops it), the chat
  runs on a local model, and the message is about mail. The local model then decides whether a search would help and
  drafts the Gmail query; code checks the query (known operators only, bounded length) and falls back to plain
  keywords when the model fails.
- Results are transient. Their text goes into this turn's prompt only. What's stored with the reply is a stub per
  message (handle and Gmail link, no sender, subject or text); the step card records the query, which is the
  owner's own words.
- Email is written by other people, so it is labelled as outside content, can never close the context block, and
  can never start an action (proposals and memories come from the owner's message alone).
"""

import json
import logging
import re
from datetime import datetime
from uuid import UUID

import psycopg

from . import db
from .connectors import gmail as gmail_mod
from .connectors.google_api import GoogleAPIError
from .connectors.google_auth import GoogleAuthError

log = logging.getLogger("pal.mail")

BODY_IN_CONTEXT = 600
QUERY_MAX = 200
GMAIL_LINK = "https://mail.google.com/mail/u/0/#all/{}"
OPERATORS = frozenset({"from", "to", "cc", "subject", "after", "before", "older_than", "newer_than", "has",
                       "filename", "in", "is", "label", "category", "larger", "smaller", "list"})
_ASKS = re.compile(
    r"\b(e-?mails?|e-?mailed|mail|inbox|gmail|newsletters?|receipts?|invoices?|"
    r"confirmation\s+(?:e-?mail|number|code)|order\s+(?:confirmation|number)|tracking\s+number|"
    r"(?:sent|wrote|replied|responded|messaged)\s+(?:me|back|to\s+me)|did\s+\w+(?:\s+\w+)?\s+(?:reply|respond|write|send|forward))\b",
    re.I)
# Asking pal to write, send or change mail, or how-to questions: never a lookup, model or no model.
_NOT_A_LOOKUP = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+you\s+|please\s+)?(?:write|draft|compose|send|reply\s+to|forward|delete|"
    r"archive|trash|unsubscribe|mark|explain|how\s+(?:do|does|can|should|to)|what(?:'s|\s+is|\s+are)\s+(?:a\s+)?good)\b",
    re.I)
_OPERATOR = re.compile(r"(-?)\b([a-z0-9_]+):", re.I)
_WORD = re.compile(r"[\w@.'-]+", re.U)
_STOP = frozenset(["a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "for", "from", "with", "about", "my", "me", "i", "you", "your", "we", "our", "did", "do", "does", "is", "are", "was", "were", "be", "been", "has", "have", "had", "any", "anything", "something", "what", "whats", "what's", "when", "where", "who", "whom", "which", "why", "how", "can", "could", "would", "should", "will", "please", "find", "show", "tell", "look", "search", "check", "email", "emails", "e-mail", "mail", "mails", "inbox", "gmail", "message", "messages", "sent", "send", "got", "get", "there", "that", "this", "these", "those", "it", "its", "last", "recent", "latest", "new"])

QUERY_SCHEMA = {
    "type": "object",
    "properties": {"search": {"type": "boolean"}, "query": {"type": "string"}},
    "required": ["search", "query"],
}


def query_system(now: datetime) -> str:
    return (
        f"Today is {now:%A %Y-%m-%d}. The user is chatting with their personal assistant, which can search the user's "
        "own Gmail. Decide whether searching their mailbox would help answer the message. "
        'Set search to false for general questions (e.g. "how do I write a polite email?") and for requests to send, '
        "delete or change mail, which the assistant cannot do. "
        "Otherwise write one Gmail search query: a few distinctive keywords plus operators where the message implies "
        "them: from:, to:, subject:, after:YYYY/MM/DD, before:YYYY/MM/DD, newer_than:7d, has:attachment, is:unread, "
        "and in:sent only when the user asks about mail they sent themselves. Use quotes for exact phrases. Never "
        "invent names or dates the message doesn't give. "
        'Return JSON: {"search": true|false, "query": str}.'
    )


def asks_about_mail(text: str) -> bool:
    return bool(_ASKS.search(text))


def chat_allows(conn: psycopg.Connection, conv_id: UUID) -> bool:
    return bool(db.scalar(conn, "SELECT gmail FROM conversations WHERE id = %s", (conv_id,)))


def connected(conn: psycopg.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM connections WHERE provider = 'google' AND service = 'gmail' AND enabled").fetchone()
    return row is not None


def clean_query(query: str) -> str:
    """A model-written query made safe to send: one line, known operators only, bounded length.

    An unknown `word:` loses its colon (it is searched as a word), so the model can't reach Gmail features pal
    doesn't use. Unbalanced quotes are dropped.
    """
    query = " ".join(str(query).split())
    query = _OPERATOR.sub(lambda m: m.group(0) if m.group(2).lower() in OPERATORS else f"{m.group(1)}{m.group(2)} ",
                          query)
    if query.count('"') % 2:
        query = query.replace('"', "")
    return " ".join(query.split())[:QUERY_MAX].strip()


_OWNER_SENT = re.compile(
    r"\b(?:(?:i|we)\s+(?:sent|emailed|wrote|replied|forwarded|mailed)|did\s+(?:i|we)\s+(?:send|email|reply|write|"
    r"forward|mail)|my\s+(?:reply|email\s+to|message\s+to)|sent\s+(?:items|folder|mail))\b", re.I)
_TIME_WORDS = re.compile(
    r"\b(?:today|yesterday|tonight|weeks?|weekend|months?|years?|since|ago|recent(?:ly)?|lately|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\b|\d", re.I)
_STATE_IN = re.compile(r"\bin:(unread|read|starred|important|snoozed)\b", re.I)
_DATE_OP = re.compile(r"(?<!\S)-?(after|before|newer_than|older_than):(\S+)", re.I)


def _valid_date_op(op: str, value: str) -> bool:
    if op.lower() in ("newer_than", "older_than"):
        return bool(re.fullmatch(r"\d{1,4}[dmy]", value, re.I))
    try:
        datetime.strptime(value.replace("-", "/"), "%Y/%m/%d")
    except ValueError:
        return False
    return True


def fit_to_message(query: str, text: str) -> str:
    """Undo the small model's usual slips against the owner's own message.

    - `in:unread` and friends are written `is:unread` in Gmail.
    - `in:sent` only when the owner is asking about mail they sent (otherwise it hides the message they want).
    - Date limits only when the message mentions a time at all, and only real dates.
    """
    query = _STATE_IN.sub(lambda m: f"is:{m.group(1).lower()}", query)
    if not _OWNER_SENT.search(text):
        query = re.sub(r"(?<!\S)-?in:sent\b", " ", query, flags=re.I)
    has_time = bool(_TIME_WORDS.search(text))
    query = _DATE_OP.sub(lambda m: m.group(0) if has_time and _valid_date_op(m.group(1), m.group(2)) else " ", query)
    return " ".join(query.split())


def keyword_query(text: str) -> str:
    """The fallback search: the message's distinctive words."""
    words = [w.strip(".'-") for w in _WORD.findall(text.lower())]
    keep = [w for w in words if w and w not in _STOP and len(w) > 1]
    return " ".join(dict.fromkeys(keep))[:QUERY_MAX]


def write_query(llm, text: str, now: datetime) -> str | None:
    """The Gmail query for a message, or None when mail won't help (decided in code first, then by the model)."""
    if _NOT_A_LOOKUP.search(text):
        return None
    try:
        raw = llm.complete_json(
            [{"role": "system", "content": query_system(now)}, {"role": "user", "content": text[:2000]}], QUERY_SCHEMA
        )
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))  # type: ignore[union-attr]
    except Exception:
        log.warning("gmail query model failed; searching keywords")
        return keyword_query(text) or None
    if not isinstance(data, dict) or data.get("search") is False:
        return None
    return fit_to_message(clean_query(data.get("query") or ""), text) or keyword_query(text) or None


def _item(handle: str, kind: str, title: str, text: str, message_id: str | None = None) -> dict:
    return {"handle": handle, "kind": kind, "id": message_id, "title": title, "text": text, "snippet": text[:300],
            "page": None, "conversation_id": None, "document_id": None, "at": None, "pinned": False,
            "link": GMAIL_LINK.format(message_id) if message_id else None}


def render(msg: dict) -> str:
    body = msg["body"] or msg["snippet"]
    extra = [f"to {msg['to']}" if msg["to"] else "", "unread" if "UNREAD" in msg["labels"] else "",
             f"attachments: {', '.join(msg['attachments'])}" if msg["attachments"] else ""]
    head = " · ".join(p for p in (f"from {msg['from']}", msg["date"], f"subject: {msg['subject']}", *extra) if p)
    return f"{head} — {body[:BODY_IN_CONTEXT]}" if body else head


def lookup(llm, text: str, now: datetime, gmail: gmail_mod.Gmail) -> tuple[list[dict], dict | None]:
    """(context items [g1]…, step card) for one turn. ([], None) when the model decides not to search."""
    query = write_query(llm, text, now)
    if not query:
        return [], None
    turn = gmail.lookup()
    try:
        ids = turn.search(query)
        messages = [turn.read(i) for i in ids]
    except (GoogleAPIError, GoogleAuthError, gmail_mod.GmailError) as exc:
        return [], {"kind": "notice", "label": "Couldn't search Gmail", "detail": str(exc)[:200]}
    if not messages:
        items = [_item("g1", "mail", "Gmail", f'No Gmail messages match the search "{query}".')]
        return items, {"kind": "mail", "label": "Searched Gmail", "detail": f'Nothing matched "{query}"'}
    items = [_item(f"g{i}", "email", m["subject"], render(m), m["id"]) for i, m in enumerate(messages, start=1)]
    plural = "s" if len(messages) > 1 else ""
    return items, {"kind": "mail", "label": f"Searched Gmail: {len(messages)} message{plural}",
                   "detail": f'"{query}" (read for this reply only, not kept)'}


def stored_source(source: dict) -> dict:
    """What is kept with a reply for an email it cited: the handle and a Gmail link, nothing the email said."""
    if source.get("kind") not in ("email", "mail"):
        return source
    return {"handle": source["handle"], "kind": source["kind"], "id": source.get("id"),
            "title": "Gmail message" if source["kind"] == "email" else "Gmail", "snippet": "", "page": None,
            "conversation_id": None, "document_id": None, "at": None, "pinned": False, "link": source.get("link")}


def run_command(conn: psycopg.Connection, conv_id: UUID, arg: str) -> dict:
    """`/gmail`, `/gmail on`, `/gmail off`: whether pal may search Gmail in this chat."""
    arg = arg.strip().lower()
    if arg in ("on", "off"):
        conn.execute("UPDATE conversations SET gmail = %s WHERE id = %s", (arg == "on", conv_id))
        if arg == "off":
            return {"reply": "pal won't search Gmail in this chat. Turn it back on with `/gmail on`.", "changed": False}
        note = "" if connected(conn) else " Gmail isn't connected yet: run `pal connect google gmail` on the PC."
        return {"reply": f"pal may search Gmail in this chat when you ask about mail.{note}", "changed": False}
    if arg:
        return {"reply": "Use `/gmail on` or `/gmail off`.", "changed": False}
    state = "on" if chat_allows(conn, conv_id) else "off"
    link = "connected" if connected(conn) else "not connected"
    return {"reply": f"Gmail search is **{state}** for this chat (Gmail is {link}). Change it with `/gmail on` or "
                     "`/gmail off`.", "changed": False}
