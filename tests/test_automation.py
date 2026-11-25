import os
import time

import psycopg
from datetime import datetime, timedelta

from pal import brief, ingest, reminders, work

NOW = datetime(2026, 11, 24, 8, 30)  # local wall clock, a Tuesday
TODAY = NOW.date()


def notes(db) -> list[str]:
    return [r[0] for r in db.execute("SELECT title FROM notifications ORDER BY fired_at").fetchall()]


# ---------- morning brief ----------

def test_brief_counts_today_and_leads_with_the_oldest_overdue(db):
    p = work.create_project(db, "Kitchen")
    work.create_task(db, "Get quotes", p["id"], TODAY - timedelta(days=3))
    work.create_task(db, "Older thing", None, TODAY - timedelta(days=9))
    work.create_task(db, "Pick countertop", p["id"], TODAY)
    started = work.create_task(db, "Draft budget")
    work.update_task(db, started["id"], stage="doing")
    done = work.create_task(db, "Done today", None, TODAY)
    work.update_task(db, done["id"], status="done")
    reminders.create(db, "Call Dana", NOW + timedelta(hours=2))
    reminders.create(db, "Tomorrow's thing", NOW + timedelta(days=1))
    paused = reminders.create(db, "Paused one", NOW + timedelta(hours=3))
    reminders.set_paused(db, paused["id"], True)

    b = brief.build(db, TODAY)
    assert (b["overdue"], b["due_today"], b["reminders_today"], b["in_progress"]) == (2, 1, 1, 1)
    assert b["first"] == "Older thing"
    assert brief.text(b) == "Morning brief: 2 overdue · 1 due today · 1 reminder today · 1 in progress. Start with: Older thing"


def test_brief_is_sent_once_a_day_after_the_hour(db):
    work.create_task(db, "Pay rent", None, TODAY)
    assert brief.daily_tick(db, hour=9, now=NOW) is None  # 08:30 is before 09:00
    sent = brief.daily_tick(db, hour=8, now=NOW)
    assert sent and "1 due today" in sent
    assert brief.daily_tick(db, hour=8, now=NOW + timedelta(hours=3)) is None  # already sent today
    assert notes(db) == [sent]
    assert brief.daily_tick(db, hour=8, now=NOW + timedelta(days=1)) is not None  # next day, again


def test_brief_off_or_with_nothing_due_sends_nothing(db):
    work.create_task(db, "Someday", None)  # no due date, not started
    assert brief.daily_tick(db, hour=0, now=NOW) is None  # 0 = off
    assert brief.daily_tick(db, hour=8, now=NOW) is None  # nothing to say
    assert notes(db) == []
    work.create_task(db, "Added later today", None, TODAY)
    assert brief.daily_tick(db, hour=8, now=NOW + timedelta(hours=1)) is None  # today's brief already settled


# ---------- folder auto-rescan ----------

def _age(path, seconds: int) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_rescan_picks_up_new_and_changed_files_only(db, tmp_path):
    (tmp_path / "a.md").write_text("# A\n\nalpha")
    (tmp_path / "b.txt").write_text("beta")
    for f in ("a.md", "b.txt"):
        _age(tmp_path / f, 3600)  # written well before pal stored them
    assert ingest.ingest_path(db, str(tmp_path))["added"] == 2

    assert ingest.rescan_folders(db) == {"added": 0, "updated": 0, "unchanged": 2, "skipped": [], "missing": []}

    (tmp_path / "b.txt").write_text("beta, edited")
    (tmp_path / "c.md").write_text("# C\n\ngamma")
    _age(tmp_path / "c.md", 7200)  # copied in with an old modified time: still new to pal
    summary = ingest.rescan_folders(db)
    assert (summary["added"], summary["updated"], summary["unchanged"]) == (1, 1, 1)
    folder = str(tmp_path.resolve())
    assert {r[0] for r in db.execute("SELECT folder FROM documents").fetchall()} == {folder}


def test_rescan_leaves_documents_of_a_missing_folder_alone(db, tmp_path):
    gone = tmp_path / "usb"
    gone.mkdir()
    (gone / "n.txt").write_text("notes on a drive that gets unplugged")
    ingest.ingest_path(db, str(gone))
    (gone / "n.txt").unlink()
    gone.rmdir()

    summary = ingest.rescan_folders(db)
    assert summary["missing"] == [str(gone.resolve())]
    assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 1


def test_nul_bytes_from_extracted_text_are_dropped(db, monkeypatch):
    # pypdf can hand back text with NUL bytes, which Postgres text columns refuse.
    monkeypatch.setattr(ingest, "parse", lambda name, data: (
        "pdf", "Ti\x00tle", [{"heading": "H\x00", "page": 1, "content": "par\x00t one"}]))
    doc, outcome = ingest.store(db, "scan.pdf", b"%PDF-fake")
    assert outcome == "added"
    assert doc["title"] == "Title"
    assert db.execute("SELECT heading, content FROM chunks").fetchone() == ("H", "part one")


def test_one_bad_file_does_not_stop_the_rescan(db, tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("alpha")
    (tmp_path / "b.txt").write_text("beta")
    ingest.ingest_path(db, str(tmp_path / "a.txt"))
    db.execute("UPDATE documents SET folder = %s", (str(tmp_path.resolve()),))
    real_store = ingest.store

    def flaky(conn, filename, *args, **kw):
        if filename == "a.txt":
            raise psycopg.DataError("boom")
        return real_store(conn, filename, *args, **kw)

    _age(tmp_path / "a.txt", -60)  # touched after pal stored it, so it's re-read
    monkeypatch.setattr(ingest, "store", flaky)
    summary = ingest.rescan_folders(db)
    assert summary["added"] == 1
    assert [s["path"] for s in summary["skipped"]] == [str(tmp_path / "a.txt")]
