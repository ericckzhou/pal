"""Connector foundation: connection rows, replay-safe syncs, disconnect purges, egress hosts, the sync loop and API."""

import threading

import pytest

from pal.connectors import hosts, store, sync


class FakeDrive:
    """A connector over an in-memory 'remote': {id: (filename, text, version)}; cursor = number of syncs seen."""

    def __init__(self, remote: dict, fail_after: int | None = None, stop_after: tuple[int, threading.Event] | None = None):
        self.remote = remote
        self.fail_after = fail_after
        self.stop_after = stop_after

    def sync(self, ctx: sync.SyncContext) -> str:
        for seen, (ext_id, (name, text, version)) in enumerate(self.remote.items()):
            if self.fail_after is not None and seen >= self.fail_after:
                raise RuntimeError("remote went away")
            if self.stop_after and seen >= self.stop_after[0]:
                self.stop_after[1].set()
            if text is None:
                ctx.remove(ext_id)
            elif ctx.known_version(ext_id) != version:
                ctx.put_document(ext_id, name, text.encode(), version)
        return str(int(ctx.cursor or 0) + 1)


def _registry(connector) -> dict:
    return {("google", "drive"): lambda row: connector}


def _drive(db, **config):
    return store.create(db, "google", "drive", "owner@example.com", ["drive.readonly"], config)


def _doc_count(db) -> int:
    return db.execute("SELECT count(*) FROM documents").fetchone()[0]


def test_create_is_idempotent_and_keeps_state(db):
    first = _drive(db)
    store.record_success(db, first["id"], "c1")
    again = store.create(db, "google", "drive", "new@example.com", ["drive.readonly"])
    assert again["id"] == first["id"]
    assert again["account"] == "new@example.com" and again["cursor"] == "c1"


def test_unknown_service_is_refused(db):
    with pytest.raises(store.ConnectorError):
        store.create(db, "google", "photos", None, [])


def test_sync_stores_documents_and_moves_the_cursor(db):
    row = _drive(db)
    remote = {"f1": ("plan.md", "# Plan\nship it", "v1"), "f2": ("notes.txt", "groceries", "v1")}
    result = sync.sync_one(db, row["id"], registry=_registry(FakeDrive(remote)))
    assert result["status"] == "ok" and result["added"] == 2
    after = store.get(db, row["id"])
    assert after["cursor"] == "1" and after["last_error"] is None and after["last_ok_at"] is not None
    paths = {r[0] for r in db.execute("SELECT source_path FROM documents").fetchall()}
    assert paths == {"google-drive:f1", "google-drive:f2"}
    assert store.list_all(db)[0]["items"] == 2


def test_failed_sync_keeps_the_cursor_and_a_rerun_completes_without_duplicates(db):
    row = _drive(db)
    store.record_success(db, row["id"], "5")
    remote = {f"f{i}": (f"n{i}.txt", f"text {i}", "v1") for i in range(4)}
    failed = sync.sync_one(db, row["id"], registry=_registry(FakeDrive(remote, fail_after=2)))
    assert failed["status"] == "failed" and failed["added"] == 2
    after = store.get(db, row["id"])
    assert after["cursor"] == "5" and "remote went away" in after["last_error"]
    ok = sync.sync_one(db, row["id"], registry=_registry(FakeDrive(remote)))
    assert ok["status"] == "ok" and ok["added"] == 2  # the first two are known by version and skipped
    assert _doc_count(db) == 4 and store.get(db, row["id"])["cursor"] == "6"


def test_stopped_sync_keeps_the_cursor(db):
    row = _drive(db)
    stop = threading.Event()
    remote = {f"f{i}": (f"n{i}.txt", f"text {i}", "v1") for i in range(4)}
    result = sync.sync_one(db, row["id"], stop=stop, registry=_registry(FakeDrive(remote, stop_after=(1, stop))))
    assert result["status"] == "stopped" and result["added"] == 1
    assert store.get(db, row["id"])["cursor"] is None


def test_changed_and_removed_items_follow_the_remote(db):
    row = _drive(db)
    remote = {"f1": ("a.txt", "first draft", "v1"), "f2": ("b.txt", "keep me", "v1")}
    sync.sync_one(db, row["id"], registry=_registry(FakeDrive(remote)))
    remote = {"f1": ("a.txt", "second draft", "v2"), "f2": ("b.txt", None, "v1")}
    result = sync.sync_one(db, row["id"], registry=_registry(FakeDrive(remote)))
    assert result["updated"] == 1 and result["removed"] == 1
    texts = [r[0] for r in db.execute("SELECT content FROM chunks").fetchall()]
    assert texts == ["second draft"]


def test_disabled_or_unregistered_connections_are_skipped(db):
    row = _drive(db)
    store.set_enabled(db, row["id"], False)
    assert sync.sync_one(db, row["id"], registry=_registry(FakeDrive({})))["status"] == "skipped"
    store.set_enabled(db, row["id"], True)
    assert sync.sync_one(db, row["id"], registry={})["status"] == "skipped"


def test_a_connection_never_syncs_twice_at_once(db):
    row = _drive(db)
    lock = sync._lock_for(row["id"])
    lock.acquire()
    try:
        assert sync.sync_one(db, row["id"], registry=_registry(FakeDrive({})))["status"] == "busy"
    finally:
        lock.release()


def test_project_of_the_connection_tags_its_documents(db):
    project = db.execute("INSERT INTO projects (name) VALUES ('Garden') RETURNING id").fetchone()[0]
    row = _drive(db)
    db.execute("UPDATE connections SET project_id = %s WHERE id = %s", (project, row["id"]))
    sync.sync_one(db, row["id"], registry=_registry(FakeDrive({"f1": ("bed.txt", "tomatoes", "v1")})))
    assert db.execute("SELECT project_id FROM documents").fetchone()[0] == project


def test_disconnect_removes_every_document_it_brought_in_and_nothing_else(db, tmp_path):
    from pal import ingest

    (tmp_path / "local.txt").write_text("my own file")
    ingest.ingest_path(db, str(tmp_path / "local.txt"))
    row = _drive(db)
    sync.sync_one(db, row["id"], registry=_registry(FakeDrive({"f1": ("a.txt", "remote one", "v1"),
                                                                 "f2": ("b.txt", "remote two", "v1")})))
    assert store.remove(db, row["id"]) == 2
    assert store.get(db, row["id"]) is None
    assert [r[0] for r in db.execute("SELECT filename FROM documents").fetchall()] == ["local.txt"]
    assert db.execute("SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
                      "WHERE d.source_path LIKE 'google-%'").fetchone()[0] == 0


def test_egress_hosts_only_for_enabled_services(db):
    assert hosts.enabled_hosts(db) == ()
    row = store.create(db, "google", "calendar", None, ["calendar.readonly"])
    assert hosts.enabled_hosts(db) == ("oauth2.googleapis.com", "www.googleapis.com")
    store.create(db, "google", "gmail", None, ["gmail.readonly"])
    assert "gmail.googleapis.com" in hosts.enabled_hosts(db)
    store.set_enabled(db, row["id"], False)
    assert hosts.enabled_hosts(db) == ("gmail.googleapis.com", "oauth2.googleapis.com")


def test_sync_all_runs_each_enabled_connection(settings, db, monkeypatch):
    from psycopg_pool import ConnectionPool

    from pal.config import dsn

    row = _drive(db)
    monkeypatch.setitem(sync.REGISTRY, ("google", "drive"), lambda r: FakeDrive({"f1": ("a.txt", "hello", "v1")}))
    with ConnectionPool(dsn(settings), min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        out = sync.sync_all(pool)
    assert out == {"google/drive": {"added": 1, "updated": 0, "unchanged": 0, "removed": 0, "status": "ok"}}
    assert store.get(db, row["id"])["cursor"] == "1"


# --- API (PC only) ---------------------------------------------------------------------------------------------

def test_connections_api_lists_syncs_pauses_and_disconnects(authed, db, monkeypatch):
    row = _drive(db)
    monkeypatch.setitem(sync.REGISTRY, ("google", "drive"), lambda r: FakeDrive({"f1": ("a.txt", "hello", "v1")}))
    listed = authed.get("/api/connections").json()
    assert [(c["service"], c["account"], c["items"]) for c in listed["connections"]] == [("drive", "owner@example.com", 0)]
    synced = authed.post(f"/api/connections/{row['id']}/sync").json()
    assert synced["status"] == "ok" and synced["added"] == 1
    assert authed.patch(f"/api/connections/{row['id']}", json={"enabled": False}).json()["enabled"] is False
    gone = authed.delete(f"/api/connections/{row['id']}")
    assert gone.status_code == 200 and gone.json()["documents_removed"] == 1
    assert authed.get("/api/connections").json()["connections"] == []
    assert authed.post(f"/api/connections/{row['id']}/sync").status_code == 404


def test_connections_api_is_pc_only(remote, db):
    assert remote.get("/api/connections").status_code in (401, 403)
