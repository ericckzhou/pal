"""Google Drive connector: the folders the owner picked (and the folders inside them), read-only.

- First sync, or after the chosen folders change: note Drive's change position, then list every folder tree in
  full, store what's supported, and remove anything pal holds that wasn't seen (a full reconciliation).
- After that: read Drive's change feed from the saved position. Drive reports changes for the whole account, so each
  file is kept only if one of its parents is a folder in the chosen trees. A file moved out, trashed, deleted or
  unshared is removed from pal. A change to a folder itself (moved, renamed into or out of the trees, trashed) makes
  pal redo the full listing instead of guessing what moved with it.
- Google Docs are exported as Markdown, Sheets as CSV and Slides as plain text (stored as text documents). Other
  files are downloaded only if pal can read them (PDF, Markdown, text). Shortcuts and shared drives are left out.
- A file pal can't read (no text, too big) is skipped and counted, not a failure, so it can't block the cursor.

The cursor is JSON: Drive's page token, the chosen folder ids it was made for, and the folder ids in the trees.
"""

import json
from dataclasses import dataclass

from .. import ingest
from . import google_api, sync

API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
EXPORTS = {  # Google-native type -> (export type, extension pal reads it as)
    "application/vnd.google-apps.document": ("text/markdown", ".md"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".txt"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}
MAX_DOWNLOAD = 50 * 1024 * 1024
FILE_FIELDS = "id,name,mimeType,modifiedTime,md5Checksum,size,parents,trashed"
MAX_TREE_FOLDERS = 2000


class DriveError(Exception):
    pass


@dataclass
class Cursor:
    token: str
    folders: list[str]  # the chosen folder ids this cursor belongs to
    tree: list[str]  # every folder id inside the chosen trees, the chosen ones included

    def dump(self) -> str:
        return json.dumps({"token": self.token, "folders": self.folders, "tree": self.tree})

    @staticmethod
    def load(raw: str | None) -> "Cursor | None":
        if not raw:
            return None
        try:
            d = json.loads(raw)
            return Cursor(str(d["token"]), list(d["folders"]), list(d["tree"]))
        except (ValueError, KeyError, TypeError):
            return None


def chosen(config: dict) -> list[str]:
    return sorted({f["id"] if isinstance(f, dict) else str(f) for f in config.get("folders") or []})


def version(f: dict) -> str:
    return f.get("md5Checksum") or f.get("modifiedTime") or ""


def target(f: dict) -> tuple[str, str | None] | None:
    """(filename pal stores it as, export type or None to download), or None if pal can't read it."""
    mime, name = f.get("mimeType", ""), f.get("name") or "untitled"
    if mime in EXPORTS:
        export, ext = EXPORTS[mime]
        return f"{name}{ext}", export
    if mime.startswith("application/vnd.google-apps."):
        return None  # forms, drawings, shortcuts, sites…
    if ingest.kind_for(name) is None or int(f.get("size") or 0) > MAX_DOWNLOAD:
        return None
    return name, None


class GoogleDrive:
    def __init__(self, api: google_api.GoogleAPI) -> None:
        self._api = api

    # --- Drive reads ---
    def children(self, ctx: sync.SyncContext, folder_id: str) -> list[dict]:
        out: list[dict] = []
        page: str | None = None
        while True:
            ctx.check_stop()
            data = self._api.get(f"{API}/files", {
                "q": f"'{folder_id}' in parents and trashed = false", "pageSize": 1000,
                "fields": f"nextPageToken,files({FILE_FIELDS})", **({"pageToken": page} if page else {}),
            })
            out.extend(data.get("files", []))
            page = data.get("nextPageToken")
            if not page:
                return out

    def content(self, file_id: str, export: str | None) -> bytes:
        if export:
            return self._api.get_bytes(f"{API}/files/{file_id}/export", {"mimeType": export})
        return self._api.get_bytes(f"{API}/files/{file_id}", {"alt": "media"})

    # --- storing ---
    def store(self, ctx: sync.SyncContext, f: dict) -> None:
        t = target(f)
        if t is None:
            ctx.summary.setdefault("skipped", 0)
            ctx.summary["skipped"] += 1
            if ctx.known_version(f["id"]) is not None:  # it became unreadable (e.g. renamed to .zip)
                ctx.remove(f["id"])
            return
        if ctx.known_version(f["id"]) == version(f):
            ctx.summary["unchanged"] += 1
            return
        filename, export = t
        data = self.content(f["id"], export)
        try:
            ctx.put_document(f["id"], filename, data, version(f))
        except ingest.IngestError:  # no text, too big: skip it rather than block every later sync
            ctx.summary.setdefault("skipped", 0)
            ctx.summary["skipped"] += 1

    # --- sync ---
    def full(self, ctx: sync.SyncContext, folders: list[str]) -> Cursor:
        token = self._api.get(f"{API}/changes/startPageToken")["startPageToken"]  # before listing: nothing slips by
        tree: list[str] = []
        files: dict[str, dict] = {}
        queue = list(folders)
        while queue:
            folder = queue.pop()
            if folder in tree:
                continue
            tree.append(folder)
            if len(tree) > MAX_TREE_FOLDERS:
                raise DriveError(f"The chosen Drive folders hold more than {MAX_TREE_FOLDERS} folders; pick smaller ones.")
            for f in self.children(ctx, folder):
                if f.get("mimeType") == FOLDER:
                    queue.append(f["id"])
                else:
                    files[f["id"]] = f
        for f in files.values():
            self.store(ctx, f)
        for ext_id in set(_mapped(ctx)) - files.keys():  # held by pal but no longer in the trees
            ctx.remove(ext_id)
        return Cursor(token, folders, sorted(tree))

    def incremental(self, ctx: sync.SyncContext, cur: Cursor) -> Cursor | None:
        """Apply the change feed. None means a folder changed and a full listing is needed instead."""
        tree, page = set(cur.tree), cur.token
        changes: list[dict] = []
        while True:
            ctx.check_stop()
            data = self._api.get(f"{API}/changes", {
                "pageToken": page, "pageSize": 1000, "includeRemoved": "true", "spaces": "drive",
                "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
            })
            changes.extend(data.get("changes", []))
            if data.get("newStartPageToken"):
                new_token = data["newStartPageToken"]
                break
            page = data["nextPageToken"]
        for ch in changes:
            f = ch.get("file") or {}
            if f.get("mimeType") == FOLDER:
                inside = bool(set(f.get("parents") or []) & tree) and not f.get("trashed")
                if (f["id"] in tree) != (inside or f["id"] in cur.folders) or (f["id"] in tree and f.get("trashed")):
                    return None  # a folder moved into or out of the trees, or was trashed: re-list everything
        for ch in changes:
            f = ch.get("file") or {}
            if f.get("mimeType") == FOLDER:
                continue
            in_scope = not ch.get("removed") and not f.get("trashed") and bool(set(f.get("parents") or []) & tree)
            if in_scope:
                self.store(ctx, f)
            elif ctx.known_version(ch["fileId"]) is not None:
                ctx.remove(ch["fileId"])
        return Cursor(new_token, cur.folders, cur.tree)

    def sync(self, ctx: sync.SyncContext) -> str | None:
        folders = chosen(ctx.config)
        if not folders:
            for ext_id in _mapped(ctx):  # every folder was un-picked
                ctx.remove(ext_id)
            return None
        cur = Cursor.load(ctx.cursor)
        result = self.incremental(ctx, cur) if cur and cur.folders == folders else None
        if result is None:
            result = self.full(ctx, folders)
        return result.dump()


def _mapped(ctx: sync.SyncContext) -> list[str]:
    return [r[0] for r in ctx.conn.execute(
        "SELECT external_id FROM connector_items WHERE connection_id = %s", (ctx.connection["id"],)).fetchall()]


def factory(row: dict) -> GoogleDrive:
    return GoogleDrive(google_api.GoogleAPI(google_api.shared_tokens(), google_api.shared_client(), "Google Drive"))


sync.REGISTRY[("google", "drive")] = factory
