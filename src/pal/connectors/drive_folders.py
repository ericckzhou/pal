"""Which Google Drive folders pal syncs: shared by `pal drive-folders` and the Connections page.

The chosen folders live in the Drive connection's config as [{"id", "name"}]. Adding one checks with Google that the
link is a folder pal can read; removing one only edits the list (its files leave pal at the next sync).
"""

import re

import psycopg

from . import gdrive, google_api, google_auth, store

_FOLDER_LINK = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]{10,})")
_FOLDER_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
LINK_MAX = 2000


class FolderError(ValueError):
    pass


def folder_id(text: str) -> str:
    """The folder id from a Drive link like https://drive.google.com/drive/folders/<id>, or a bare id."""
    text = (text or "").strip()[:LINK_MAX]
    m = _FOLDER_LINK.search(text)
    if m:
        return m.group(1)
    if _FOLDER_ID.match(text):
        return text
    raise FolderError("That isn't a Drive folder link. Open the folder in Drive and copy the address bar.")


def _maybe_id(text: str) -> str | None:
    m = _FOLDER_LINK.search(text)
    return m.group(1) if m else None


def drive_row(conn: psycopg.Connection) -> dict:
    row = next((r for r in store.list_all(conn) if (r["provider"], r["service"]) == ("google", "drive")), None)
    if row is None:
        raise FolderError("Google Drive isn't connected. Connect it on the Connections page.")
    return row


def listed(row: dict) -> list[dict]:
    return [{"id": str(f["id"]), "name": str(f.get("name") or f["id"])}
            for f in row["config"].get("folders") or [] if isinstance(f, dict) and f.get("id")]


def add(conn: psycopg.Connection, row: dict, link: str, api: google_api.GoogleAPI | None = None) -> dict:
    """Check the folder with Google and add it. Returns the added {"id", "name"}."""
    fid = folder_id(link)
    folders = listed(row)
    if any(f["id"] == fid for f in folders):
        raise FolderError("That folder is already chosen.")
    api = api or google_api.GoogleAPI(google_api.shared_tokens(), google_api.shared_client(), "Google Drive")
    try:
        meta = api.get(f"{gdrive.API}/files/{fid}", {"fields": "id,name,mimeType,trashed"})
    except (google_api.GoogleAPIError, google_auth.GoogleAuthError) as exc:
        raise FolderError(str(exc)) from exc
    if meta.get("mimeType") != gdrive.FOLDER or meta.get("trashed"):
        raise FolderError("That link is a file, not a folder (or it's in the trash).")
    added = {"id": meta["id"], "name": meta.get("name") or meta["id"]}
    store.set_config(conn, row["id"], {**row["config"], "folders": [*folders, added]})
    return added


def remove(conn: psycopg.Connection, row: dict, folder: str) -> None:
    """Drop a folder by id, link or name. Its files leave pal at the next sync."""
    folders = listed(row)
    keep = [f for f in folders if folder not in (f["id"], f["name"]) and f["id"] != _maybe_id(folder)]
    if len(keep) == len(folders):
        raise FolderError("That folder isn't chosen. See: pal drive-folders list")
    store.set_config(conn, row["id"], {**row["config"], "folders": keep})
