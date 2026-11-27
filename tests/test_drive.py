"""Google Drive: full listing of chosen folder trees, the change feed, exports, skips, replays, folder picking."""

import json

import httpx
import pytest

from pal.connectors import gdrive, google_api, google_auth, store, sync

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
FOLDER = gdrive.FOLDER


class FakeDrive:
    """Drive's files, export, download and change-feed endpoints over an in-memory account."""

    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        self.content: dict[str, bytes] = {}
        self.changes: list[dict] = []
        self.downloads: list[str] = []
        self.fail_download: str | None = None

    def add(self, fid, name, mime="text/plain", parent="root", text="", record=True, **extra):
        f = {"id": fid, "name": name, "mimeType": mime, "parents": [parent], "trashed": False,
             "modifiedTime": "2026-11-01T00:00:00Z", **extra}
        if mime != FOLDER and not mime.startswith("application/vnd.google-apps."):
            f["md5Checksum"] = f"md5-{text}"
            f["size"] = str(len(text))
        self.files[fid] = f
        self.content[fid] = text.encode()
        if record:
            self.changes.append({"fileId": fid, "removed": False, "file": dict(f)})
        return f

    def edit(self, fid, text=None, **updates):
        f = self.files[fid]
        f.update(updates)
        if text is not None:
            self.content[fid] = text.encode()
            f["modifiedTime"] = f"2026-11-{len(self.changes) + 2:02}T00:00:00Z"
            if "md5Checksum" in f:
                f["md5Checksum"] = f"md5-{text}"
        self.changes.append({"fileId": fid, "removed": False, "file": dict(f)})

    def delete(self, fid):
        self.files.pop(fid)
        self.changes.append({"fileId": fid, "removed": True})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        path, q = request.url.path.removeprefix("/drive/v3"), request.url.params
        if path == "/changes/startPageToken":
            return httpx.Response(200, json={"startPageToken": str(len(self.changes))})
        if path == "/changes":
            return httpx.Response(200, json={"changes": self.changes[int(q["pageToken"]):],
                                             "newStartPageToken": str(len(self.changes))})
        if path == "/files":
            parent = q["q"].split("'")[1]
            kids = [f for f in self.files.values() if parent in f["parents"] and not f["trashed"]]
            return httpx.Response(200, json={"files": kids})
        fid = path.split("/")[2]
        if fid not in self.files:
            return httpx.Response(404, json={"error": {"code": 404, "message": "File not found"}})
        if path.endswith("/export") or q.get("alt") == "media":
            if fid == self.fail_download:
                return httpx.Response(500, json={"error": {"code": 500, "message": "backend error"}})
            self.downloads.append(fid)
            body = self.content[fid]
            if path.endswith("/export") and q["mimeType"] == "text/markdown":
                body = b"# " + self.files[fid]["name"].encode() + b"\n\n" + body
            return httpx.Response(200, content=body)
        return httpx.Response(200, json=self.files[fid])


def api(fake: FakeDrive) -> google_api.GoogleAPI:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return google_api.GoogleAPI(google_auth.Tokens(http, google_auth.Client("c", "s"), "r"), http, "Google Drive")


def connect(db, folders=("work",)):
    row = store.create(db, "google", "drive", "owner@example.com", ["drive.readonly"])
    store.set_config(db, row["id"], {"folders": [{"id": f, "name": f.title()} for f in folders]})
    return row


def run(db, row, fake):
    return sync.sync_one(db, row["id"], registry={("google", "drive"): lambda r: gdrive.GoogleDrive(api(fake))})


def docs(db) -> dict[str, str]:
    """source_path -> all chunk text, for what pal holds from Drive."""
    rows = db.execute("SELECT d.source_path, string_agg(c.content, ' ' ORDER BY c.ord) FROM documents d "
                      "JOIN chunks c ON c.document_id = d.id GROUP BY d.source_path").fetchall()
    return dict(rows)


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add("work", "Work", FOLDER, parent="root")
    fake.add("plan", "Plan", DOC, parent="work", text="ship the beta in March")
    fake.add("sub", "Archive", FOLDER, parent="work")
    fake.add("old", "old notes.txt", parent="sub", text="old notes about the alpha")
    fake.add("budget", "Budget", SHEET, parent="work", text="item,cost\nrent,1200")
    fake.add("photo", "cat.jpg", "image/jpeg", parent="work", text="jpegbytes")
    fake.add("elsewhere", "private.txt", parent="root", text="not in a chosen folder")
    return fake


def test_first_sync_lists_the_whole_tree_and_exports_native_files(db, drive):
    row = connect(db)
    result = run(db, row, drive)
    assert result["status"] == "ok" and (result["added"], result["skipped"]) == (3, 1)  # the jpg is skipped
    held = docs(db)
    assert set(held) == {"google-drive:plan", "google-drive:old", "google-drive:budget"}
    assert "ship the beta" in held["google-drive:plan"] and "rent,1200" in held["google-drive:budget"]
    assert db.execute("SELECT title FROM documents WHERE source_path = 'google-drive:plan'").fetchone()[0] == "Plan"
    cur = gdrive.Cursor.load(store.get(db, row["id"])["cursor"])
    assert cur.folders == ["work"] and set(cur.tree) == {"work", "sub"}
    assert "elsewhere" not in drive.downloads


def test_unchanged_files_are_not_downloaded_again(db, drive):
    row = connect(db)
    run(db, row, drive)
    drive.downloads.clear()
    result = run(db, row, drive)
    assert result["status"] == "ok" and drive.downloads == [] and result["added"] == 0


def test_change_feed_applies_edits_moves_trash_and_deletes(db, drive):
    row = connect(db)
    run(db, row, drive)
    drive.edit("plan", text="ship the beta in April")
    drive.add("new", "ideas.md", parent="sub", text="# Ideas\nrooftop garden")
    drive.edit("old", parents=["root"])  # moved out of the chosen tree
    drive.edit("budget", trashed=True)
    drive.add("outside", "x.txt", parent="root", text="never mind")
    result = run(db, row, drive)
    assert (result["added"], result["updated"], result["removed"]) == (1, 1, 2)
    held = docs(db)
    assert set(held) == {"google-drive:plan", "google-drive:new"}
    assert "April" in held["google-drive:plan"] and "rooftop garden" in held["google-drive:new"]
    drive.delete("new")
    assert run(db, row, drive)["removed"] == 1


def test_a_moved_folder_triggers_a_full_relisting(db, drive):
    row = connect(db)
    run(db, row, drive)
    drive.edit("sub", parents=["root"])  # Archive leaves Work, taking old notes with it
    result = run(db, row, drive)
    assert result["status"] == "ok" and "google-drive:old" not in docs(db)
    assert set(gdrive.Cursor.load(store.get(db, row["id"])["cursor"]).tree) == {"work"}
    drive.add("later", "Later", FOLDER, parent="work")
    drive.add("deep", "deep.txt", parent="later", text="found in a new folder")
    run(db, row, drive)
    assert "google-drive:deep" in docs(db)


def test_changing_the_chosen_folders_relists(db, drive):
    row = connect(db)
    run(db, row, drive)
    store.set_config(db, row["id"], {"folders": [{"id": "sub", "name": "Archive"}]})
    run(db, row, drive)
    assert set(docs(db)) == {"google-drive:old"}
    store.set_config(db, row["id"], {"folders": []})
    run(db, row, drive)
    assert docs(db) == {}


def test_a_failed_download_keeps_the_cursor_and_the_rerun_catches_up(db, drive):
    row = connect(db)
    run(db, row, drive)
    before = store.get(db, row["id"])["cursor"]
    drive.edit("plan", text="version three")
    drive.add("n2", "n2.txt", parent="work", text="second new file")
    drive.fail_download = "n2"
    failed = run(db, row, drive)
    assert failed["status"] == "failed" and store.get(db, row["id"])["cursor"] == before
    drive.fail_download = None
    ok = run(db, row, drive)
    assert ok["status"] == "ok" and "version three" in docs(db)["google-drive:plan"]
    assert "google-drive:n2" in docs(db)


def test_a_file_without_text_is_skipped_not_fatal(db, drive):
    row = connect(db)
    drive.add("empty", "empty.txt", parent="work", text="   ")
    result = run(db, row, drive)
    assert result["status"] == "ok" and result["skipped"] == 2  # the jpg and the empty file


def test_a_file_renamed_to_an_unreadable_type_leaves_pal(db, drive):
    row = connect(db)
    run(db, row, drive)
    drive.edit("old", name="old notes.zip", mimeType="application/zip")
    run(db, row, drive)
    assert "google-drive:old" not in docs(db)


def test_disconnect_removes_drive_documents(db, drive):
    row = connect(db)
    run(db, row, drive)
    store.remove(db, row["id"])
    assert docs(db) == {}


# --- picking folders -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, fid", [
    ("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn?usp=sharing", "1AbCdEfGhIjKlMn"),
    ("https://drive.google.com/open?id=1AbCdEfGhIjKlMn", "1AbCdEfGhIjKlMn"),
    ("1AbCdEfGhIjKlMn", "1AbCdEfGhIjKlMn"),
])
def test_folder_links_are_understood(text, fid):
    from pal.connectors import google_cli

    assert google_cli.folder_id(text) == fid


def test_drive_folders_command(settings, db, capsys):
    from pal.connectors import google_cli

    fake = FakeDrive()
    fake.add("folderAAAAAA1", "Work", FOLDER)
    fake.add("fileBBBBBBBB2", "a.txt", text="x")
    with pytest.raises(SystemExit, match="isn't connected"):
        google_cli.cmd_drive_folders(settings, "list", None)
    row = store.create(db, "google", "drive", "owner@example.com", ["drive.readonly"])
    google_cli.cmd_drive_folders(settings, "add", "https://drive.google.com/drive/folders/folderAAAAAA1", api=api(fake))
    with pytest.raises(SystemExit, match="not a folder"):
        google_cli.cmd_drive_folders(settings, "add", "fileBBBBBBBB2", api=api(fake))
    with pytest.raises(SystemExit, match="already chosen"):
        google_cli.cmd_drive_folders(settings, "add", "folderAAAAAA1", api=api(fake))
    assert store.get(db, row["id"])["config"] == {"folders": [{"id": "folderAAAAAA1", "name": "Work"}]}
    google_cli.cmd_drive_folders(settings, "list", None)
    google_cli.cmd_drive_folders(settings, "remove", "Work")
    assert store.get(db, row["id"])["config"] == {"folders": []}
    out = capsys.readouterr().out
    assert "Added Work" in out and "Work  (folderAAAAAA1)" in out and "Removed" in out


def test_cursor_survives_garbage():
    assert gdrive.Cursor.load("not json") is None and gdrive.Cursor.load(json.dumps({"token": 1})) is None
