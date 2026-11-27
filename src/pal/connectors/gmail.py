"""Gmail, read-only and live: search, then read the messages that search returned. Nothing is stored.

There is no sync. Mail is looked up during a chat turn and lives only in that turn's prompt; it is never written to
the database, a trace, a log line or a backup. A `Lookup` reads only message ids its own search returned, so neither
a model nor text inside an email can make pal open some other message.
"""

import base64
import binascii
import html
import re

from . import google_api

API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESULTS = 5
BODY_MAX = 4000  # characters of a body kept in memory for this turn (the context gets less)
HEADERS = ("From", "To", "Subject", "Date")
_TAGS = re.compile(r"<(script|style)\b.*?</\1\s*>|<[^>]+>", re.I | re.S)
_ID = re.compile(r"^[0-9a-f]{6,32}$")


class GmailError(Exception):
    pass


class NotFromThisSearch(GmailError):
    """A read for a message id the current lookup's search didn't return."""


def _decode(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return ""


def _flatten(text: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", text)).split())


def _parts(payload: dict) -> list[dict]:
    out = [payload]
    for part in payload.get("parts") or []:
        out.extend(_parts(part))
    return out


def body_text(payload: dict) -> str:
    """The readable text of a message: its first text/plain part, else its first text/html part flattened.

    Attachments (parts with a filename) are skipped; pal doesn't open them.
    """
    parts = [p for p in _parts(payload) if not p.get("filename")]
    for mime, flatten in (("text/plain", False), ("text/html", True)):
        for p in parts:
            if p.get("mimeType") == mime:
                text = _decode((p.get("body") or {}).get("data"))
                text = _flatten(text) if flatten else " ".join(text.split())
                if text:
                    return text[:BODY_MAX]
    return ""


def parse_message(msg: dict) -> dict:
    payload = msg.get("payload") or {}
    headers = {h.get("name", "").title(): h.get("value", "") for h in payload.get("headers") or []}
    attachments = sorted({p["filename"] for p in _parts(payload) if p.get("filename")})
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId"),
        "from": " ".join(headers.get("From", "").split())[:200],
        "to": " ".join(headers.get("To", "").split())[:200],
        "subject": " ".join(headers.get("Subject", "").split())[:300] or "(no subject)",
        "date": headers.get("Date", "")[:60],
        "labels": msg.get("labelIds") or [],
        "snippet": html.unescape(msg.get("snippet") or ""),
        "body": body_text(payload),
        "attachments": attachments[:10],
    }


class Gmail:
    def __init__(self, api: google_api.GoogleAPI) -> None:
        self._api = api

    def search_ids(self, query: str, limit: int = MAX_RESULTS) -> list[str]:
        data = self._api.get(f"{API}/messages", {"q": query, "maxResults": limit, "fields": "messages(id)"})
        return [m["id"] for m in data.get("messages") or [] if isinstance(m.get("id"), str)][:limit]

    def get(self, message_id: str) -> dict:
        return parse_message(self._api.get(f"{API}/messages/{message_id}", {"format": "full"}))

    def lookup(self) -> "Lookup":
        return Lookup(self)


class Lookup:
    """One chat turn's access to Gmail: a search, then reads limited to what that search returned."""

    def __init__(self, gmail: Gmail) -> None:
        self._gmail = gmail
        self._allowed: set[str] = set()

    def search(self, query: str, limit: int = MAX_RESULTS) -> list[str]:
        ids = self._gmail.search_ids(query, limit)
        self._allowed.update(ids)
        return ids

    def read(self, message_id: str) -> dict:
        if message_id not in self._allowed or not _ID.match(message_id):
            raise NotFromThisSearch("pal only opens messages its own search returned this turn")
        return self._gmail.get(message_id)


def make() -> Gmail:
    return Gmail(google_api.GoogleAPI(google_api.shared_tokens(), google_api.shared_client(), "Gmail"))
