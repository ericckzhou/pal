"""PDF reading for retrieval: header/footer removal, cross-page packing, Docling routing and re-reading stale PDFs."""

import os
import time

import pytest

from pal import embed, ingest, pdf


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF, one Helvetica line per string; a blank string leaves a gap pypdf reads as a blank line."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        ops = []
        y = 760
        for line in lines:
            if line:
                ops.append(f"BT /F1 11 Tf 72 {y} Td ({line}) Tj ET")
            y -= 14 if line else 28
        stream = "\n".join(ops).encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_no = len(objs)
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    b"/Contents %d 0 R >>" % content_no)
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def report_pages(n: int) -> list[list[str]]:
    """Header at the top, a body paragraph mid-page, page number at the bottom (as a layout model expects)."""
    body = [f"Section text about topic{{i}} continues here with ordinary sentences, line {k}." for k in range(1, 6)]
    return [["ACME Corp confidential", *[""] * 5, *[ln.format(i=i) for ln in body], *[""] * 18, f"Page {i} of {n}"]
            for i in range(1, n + 1)]


# ---------- pypdf path ----------

def test_running_headers_and_page_numbers_are_dropped():
    pages = ["ACME Corp confidential\nreal text one\nPage 1 of 3",
             "ACME Corp confidential\nreal text two\nPage 2 of 3",
             "ACME Corp confidential\nreal text three\nPage 3 of 3"]
    assert pdf.strip_running_lines(pages) == ["real text one", "real text two", "real text three"]


def test_short_documents_keep_every_line():
    pages = ["Title\nbody", "Title\nmore"]
    assert pdf.strip_running_lines(pages) == pages


def test_pdf_header_footer_gone_and_chunk_spans_pages():
    _, _, chunks, parser = ingest.parse_with_parser("report.pdf", make_pdf(report_pages(4)))
    assert parser == pdf.PARSER_PYPDF
    text = " ".join(c["content"] for c in chunks)
    assert "ACME Corp" not in text and "of 4" not in text
    assert "topic1" in text and "topic4" in text
    assert chunks[0]["page"] == 1 and len(chunks) < 4  # short pages share chunks across page breaks


def test_words_hyphenated_at_line_end_are_rejoined():
    _, _, chunks = ingest.parse("h.pdf", make_pdf([["The gar-", "den grows."]]))
    assert "garden grows" in chunks[0]["content"]


# ---------- packing ----------

def test_pack_blocks_keeps_headings_apart_and_records_first_page():
    blocks = [
        {"heading": "Intro", "page": 1, "text": "a" * 100},
        {"heading": "Intro", "page": 2, "text": "b" * 100},
        {"heading": "Method", "page": 2, "text": "c" * 100},
    ]
    chunks = ingest.pack_blocks(blocks)
    assert [(c["heading"], c["page"]) for c in chunks] == [("Intro", 1), ("Method", 2)]
    assert chunks[0]["content"] == "a" * 100 + "\n\n" + "b" * 100


def test_pack_blocks_bounds_chunk_size():
    blocks = [{"heading": None, "page": p, "text": ("word " * 200).strip()} for p in range(1, 6)]
    chunks = ingest.pack_blocks(blocks)
    assert len(chunks) > 1 and all(len(c["content"]) <= ingest.CHUNK_CHARS for c in chunks)


# ---------- engine routing ----------

@pytest.fixture
def fake_docling(monkeypatch):
    calls = []

    def convert(filename, data):
        calls.append(filename)
        return "Annual Report 2025", [
            {"heading": "Revenue", "page": 2, "text": "Revenue grew four percent."},
            {"heading": "Revenue › By region", "page": 3, "text": "| Region | Growth |\n| EU | 3% |"},
        ]

    monkeypatch.setattr(pdf, "docling_ready", lambda: True)
    monkeypatch.setattr(pdf, "_docling", convert)
    return calls


def test_docling_blocks_become_chunks_with_headings_and_title(fake_docling):
    kind, title, chunks, parser = ingest.parse_with_parser("ar.pdf", b"%PDF-whatever")
    assert (kind, title, parser) == ("pdf", "Annual Report 2025", pdf.PARSER_DOCLING)
    assert [(c["heading"], c["page"]) for c in chunks] == [("Revenue", 2), ("Revenue › By region", 3)]


def test_docling_failure_falls_back_to_pypdf(monkeypatch):
    def boom(filename, data):
        raise RuntimeError("model crashed")

    monkeypatch.setattr(pdf, "docling_ready", lambda: True)
    monkeypatch.setattr(pdf, "_docling", boom)
    _, _, chunks, parser = ingest.parse_with_parser("r.pdf", make_pdf([["plain text survives"]]))
    assert parser == pdf.PARSER_PYPDF and "plain text survives" in chunks[0]["content"]


def test_pypdf_is_forced_by_env(monkeypatch):
    pdf.docling_ready.cache_clear()
    monkeypatch.setenv(pdf.ENGINE_ENV, "pypdf")
    try:
        assert pdf.docling_ready() is False
    finally:
        pdf.docling_ready.cache_clear()


# ---------- re-reading PDFs stored by an older parser ----------

def _old_pdf(db, tmp_path):
    """A PDF from a folder, stored (by pypdf) before pal gained Docling."""
    f = tmp_path / "old.pdf"
    f.write_bytes(make_pdf([["Old extraction"]]))
    t = time.time() - 3600
    os.utime(f, (t, t))
    ingest.ingest_path(db, str(tmp_path))
    return f


def test_rescan_and_readd_leave_old_pdfs_alone(db, tmp_path, monkeypatch):
    f = _old_pdf(db, tmp_path)
    monkeypatch.setattr(pdf, "current_parser", lambda: pdf.PARSER_DOCLING)
    monkeypatch.setattr(pdf, "extract", lambda *a: pytest.fail("re-read without the owner asking"))
    assert ingest.rescan_folders(db)["unchanged"] == 1
    assert ingest.ingest_path(db, str(tmp_path))["unchanged"] == 1  # the Rescan button
    assert ingest.store(db, f.name, f.read_bytes(), str(f.resolve()))[1] == "unchanged"


def test_owner_starts_reread_and_sees_progress(authed, db, tmp_path, monkeypatch):
    _old_pdf(db, tmp_path)
    before = authed.get("/api/documents/processing").json()
    assert before["stale_pdfs"]["files"] == 0  # pypdf is still the current reader in tests

    monkeypatch.setattr(pdf, "current_parser", lambda: pdf.PARSER_DOCLING)
    monkeypatch.setattr(pdf, "extract", lambda name, d: ("Q", [{"heading": "Numbers", "page": 1, "text": "new text"}],
                                                          pdf.PARSER_DOCLING))
    state = authed.get("/api/documents/processing").json()
    assert state["can_reread"] is True
    assert state["stale_pdfs"]["files"] == 1 and state["reread"]["running"] is False
    assert authed.get("/api/status").json()["counts"]["stale_pdfs"] == 1

    job = authed.post("/api/documents/reread").json()
    assert job["total"] == 1
    for _ in range(100):
        job = authed.get("/api/documents/processing").json()["reread"]
        if not job["running"]:
            break
        time.sleep(0.05)
    assert (job["done"], job["failed"], job["stopped"]) == (1, [], False)
    assert db.execute("SELECT parser FROM documents").fetchone()[0] == pdf.PARSER_DOCLING
    assert db.execute("SELECT heading FROM chunks").fetchone()[0] == "Numbers"
    assert authed.post("/api/documents/reread").status_code == 404  # nothing left to do


def test_reread_can_be_stopped(db, tmp_path, monkeypatch, settings):
    from psycopg_pool import ConnectionPool

    from pal import reread
    from pal.config import dsn

    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / name).write_bytes(make_pdf([[f"text of {name}"]]))
    ingest.ingest_path(db, str(tmp_path))
    monkeypatch.setattr(pdf, "current_parser", lambda: pdf.PARSER_DOCLING)

    def slow(name, d):
        reread.stop()  # the owner presses Stop while the first file is being read
        return None, [{"heading": None, "page": 1, "text": "new"}], pdf.PARSER_DOCLING

    monkeypatch.setattr(pdf, "extract", slow)
    with ConnectionPool(dsn(settings), min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        reread.start(pool)
        for _ in range(100):
            if not reread.snapshot()["running"]:
                break
            time.sleep(0.05)
    job = reread.snapshot()
    assert (job["done"], job["total"], job["stopped"]) == (1, 3, True)
    assert db.execute("SELECT count(*) FROM documents WHERE parser = %s", (pdf.PARSER_DOCLING,)).fetchone()[0] == 1


def test_reuploading_an_old_pdf_rereads_it(authed, db, monkeypatch):
    data = make_pdf([["Quarterly numbers"]])
    upload = lambda: authed.post("/api/documents/upload?filename=q.pdf", content=data,
                                 headers={"Content-Type": "application/octet-stream"}).json()["outcome"]
    assert upload() == "added"
    assert upload() == "unchanged"
    monkeypatch.setattr(pdf, "current_parser", lambda: pdf.PARSER_DOCLING)
    monkeypatch.setattr(pdf, "extract", lambda name, d: ("Q", [{"heading": "Numbers", "page": 1, "text": "x"}],
                                                          pdf.PARSER_DOCLING))
    assert upload() == "updated"


# ---------- embedding ----------

def test_chunks_are_embedded_with_the_document_title(db, llm, monkeypatch):
    ingest.store(db, "garden.md", b"# Garden notes\n\n## Tomatoes\n\nWater deeply twice a week.")
    seen = []
    real = llm.embed
    monkeypatch.setattr(llm, "embed", lambda texts: seen.extend(texts) or real(texts))
    embed.embed_pending(db, llm)
    chunk_text = next(t for t in seen if "Water deeply" in t)
    assert chunk_text == f"{embed.DOC_PREFIX}Garden notes\nTomatoes\nWater deeply twice a week."


# ---------- the real thing, where Docling and its models are installed ----------

def test_real_docling_reads_headings_and_pages():
    pytest.importorskip("docling")
    if not (pdf.models_dir().is_dir() and any(pdf.models_dir().iterdir())):
        pytest.skip("Docling models not downloaded (pal pdf-models)")
    _, blocks = pdf._docling("r.pdf", make_pdf(report_pages(3)))
    text = " ".join(b["text"] for b in blocks)
    assert "topic1" in text and "topic3" in text
    assert "ACME Corp" not in text and "Page 2 of 3" not in text  # page furniture is left out
    assert {b["page"] for b in blocks} >= {1, 3}

    # A page whose only text sits where headers go is still kept, not dropped.
    _, blocks = pdf._docling("top.pdf", make_pdf([["Only line, at the very top"]] * 3))
    assert any("Only line" in b["text"] for b in blocks)
