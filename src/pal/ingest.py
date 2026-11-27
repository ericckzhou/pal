"""Document ingestion: PDF, Markdown and plain text, from uploads or from files and folders on this PC.

S7: document text is stored and later shown to the model as data inside a <context> block. Nothing here
interprets it, and no memories are ever derived from documents.
"""

import hashlib
import logging
import os
import re
import threading
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from . import db, pdf

log = logging.getLogger("pal.ingest")

MAX_MB = 200
MAX_BYTES = MAX_MB * 1024 * 1024
TOO_BIG = f"File is larger than {MAX_MB} MB."
MAX_FOLDER_FILES = 2000
CHUNK_CHARS = 1200
OVERLAP_CHARS = 150
KINDS = {".pdf": "pdf", ".md": "markdown", ".markdown": "markdown", ".txt": "text", ".text": "text", ".rst": "text"}
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache"}
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class IngestError(ValueError):
    pass


def kind_for(filename: str) -> str | None:
    return KINDS.get(Path(filename).suffix.lower())


# ---------- parsing ----------

def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _pack(text: str) -> list[str]:
    """Split text into ~CHUNK_CHARS pieces on paragraph boundaries; hard-split very long paragraphs."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paras:
        while len(p) > CHUNK_CHARS:
            cut = p.rfind(" ", 0, CHUNK_CHARS)
            cut = cut if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
            pieces.append(p[:cut].strip())
            p = p[max(cut - OVERLAP_CHARS, 1):].strip()
        pieces.append(p)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > CHUNK_CHARS:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _markdown_sections(text: str) -> list[tuple[str | None, str]]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    path: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            level = len(match.group(1))
            path = path[: level - 1] + [match.group(2)]
            sections.append((" › ".join(path), []))
        else:
            sections[-1][1].append(line)
    return [(h, "\n".join(lines)) for h, lines in sections if "\n".join(lines).strip()]


_JUNK_TITLE = re.compile(
    r"""(\.[a-z0-9]{1,5}$)          # a file name: notes.dvi, report.docx (LaTeX and Word tools write these)
      | [\\/]                       # a path
      | ^(untitled|title|document|slide\s*\d*|microsoft\s+\w+\s+-\s.*)$""",
    re.I | re.X,
)


def _useful_pdf_title(title) -> bool:
    """PDF metadata titles are often junk; only use one that reads like a real title."""
    text = str(title or "").strip()
    return len(text) >= 3 and not _JUNK_TITLE.search(text)


def _below_title(heading: str | None, h1: str | None) -> str | None:
    if not heading or not h1:
        return heading
    if heading == h1:
        return None
    prefix = h1 + " › "
    return heading[len(prefix):] if heading.startswith(prefix) else heading


TEXT_PARSER = "text-1"


def parser_for(kind: str) -> str:
    """Which extractor a file of this kind would be read with now; a stored document read differently is stale."""
    return pdf.current_parser() if kind == "pdf" else TEXT_PARSER


def pack_blocks(blocks: list[dict]) -> list[dict]:
    """Pack {heading, page, text} blocks into ~CHUNK_CHARS chunks.

    Consecutive blocks under the same heading share a chunk even across a page break; a chunk's page is the page
    it starts on.
    """
    chunks: list[dict] = []
    current: dict | None = None
    for block in blocks:
        for piece in _pack(block["text"]):
            fits = current is not None and len(current["content"]) + len(piece) + 2 <= CHUNK_CHARS
            if fits and current is not None and current["heading"] == block["heading"]:
                current = {**current, "content": current["content"] + "\n\n" + piece}
            else:
                if current:
                    chunks.append(current)
                current = {"heading": block["heading"], "page": block["page"], "content": piece}
    if current:
        chunks.append(current)
    return chunks


def parse(filename: str, data: bytes) -> tuple[str, str, list[dict]]:
    """Return (kind, title, chunks) where each chunk is {heading, page, content}."""
    kind, title, chunks, _ = parse_with_parser(filename, data)
    return kind, title, chunks


def parse_with_parser(filename: str, data: bytes) -> tuple[str, str, list[dict], str]:
    """parse(), plus the id of the extractor that was actually used."""
    kind = kind_for(filename)
    if kind is None:
        raise IngestError("Only PDF, Markdown and text files are supported.")
    if len(data) > MAX_BYTES:
        raise IngestError(TOO_BIG)
    title = Path(filename).stem
    parser = TEXT_PARSER
    if kind == "pdf":
        try:
            meta_title, blocks, parser = pdf.extract(filename, data)
        except pdf.PdfError as exc:
            raise IngestError(str(exc)) from exc
        if _useful_pdf_title(meta_title):
            title = str(meta_title).strip()
        chunks = pack_blocks(blocks)
    else:
        text = _decode(data).replace("\x00", "")
        sections = _markdown_sections(text) if kind == "markdown" else [(None, text)]
        if kind == "markdown":
            h1 = next((m.group(2) for line in text.splitlines()[:40] if (m := _HEADING.match(line)) and m.group(1) == "#"), None)
            title = h1 or title
            # The H1 is the title already; keep only the part of the heading path below it.
            sections = [(_below_title(h, h1), body) for h, body in sections]
        chunks = [{"heading": heading, "page": None, "content": piece} for heading, body in sections for piece in _pack(body)]
    if not chunks:
        raise IngestError("No text found in this file." + (" It may be a scan; pal reads scans only when Docling is set up." if kind == "pdf" and parser != pdf.PARSER_DOCLING else ""))
    return kind, title[:200], chunks, parser


# ---------- storage ----------
def _no_nul(text: str | None) -> str | None:
    return text.replace("\x00", "") if text else text



def store(
    conn: psycopg.Connection, filename: str, data: bytes, source_path: str | None = None, folder: str | None = None,
    project_id: UUID | None = None, reread: bool = False,
) -> tuple[dict, str]:
    """Insert or refresh a document. Returns (document, outcome) with outcome added|updated|unchanged.

    `folder` is the folder the owner added (for grouping); re-adding a folder claims files already stored.
    `project_id`, when given, tags the document (new or already stored); otherwise a new file in a tagged folder
    takes the folder's project.
    `reread` re-extracts an unchanged file that an older extractor read. Only the owner-started job (reread.py)
    sets it: re-reading is slow, so adds and rescans never do it implicitly.
    """
    digest = hashlib.sha256(data).digest()
    with conn.cursor(row_factory=dict_row) as cur:
        if source_path:
            existing = cur.execute("SELECT id, sha256, kind, parser FROM documents WHERE source_path = %s", (source_path,)).fetchone()
        else:
            existing = cur.execute(
                "SELECT id, sha256, kind, parser FROM documents WHERE source_path IS NULL AND sha256 = %s", (digest,)
            ).fetchone()
        stale = reread and existing and existing["parser"] != parser_for(existing["kind"])
        if existing and bytes(existing["sha256"]) == digest and not stale:
            if folder:
                cur.execute("UPDATE documents SET folder = %s WHERE id = %s AND folder IS DISTINCT FROM %s",
                            (folder, existing["id"], folder))
            if project_id:
                set_project(conn, existing["id"], project_id)
            return db.require(get(conn, existing["id"]), "document"), "unchanged"
        kind, title, chunks, parser = parse_with_parser(filename, data)
        # Postgres text can't hold NUL; PDF extraction sometimes yields it.
        title = _no_nul(title) or Path(filename).stem
        chunks = [{**c, "heading": _no_nul(c["heading"]), "content": _no_nul(c["content"])} for c in chunks]
        with conn.transaction():
            if existing:
                doc_id = existing["id"]
                cur.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                cur.execute(
                    "UPDATE documents SET title = %s, kind = %s, sha256 = %s, size_bytes = %s, parser = %s, folder = COALESCE(%s, folder), project_id = COALESCE(%s, project_id), updated_at = now() WHERE id = %s",
                    (title, kind, digest, len(data), parser, folder, project_id, doc_id),
                )
            else:
                # A new file in a folder that belongs to a project joins that project.
                doc_id = db.require(cur.execute(
                    "INSERT INTO documents (title, filename, source_path, folder, kind, sha256, size_bytes, parser, project_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, (SELECT project_id FROM documents WHERE folder = %s "
                    "AND project_id IS NOT NULL ORDER BY updated_at DESC LIMIT 1))) RETURNING id",
                    (title, Path(filename).name[:255], source_path, folder, kind, digest, len(data), parser, project_id, folder),
                ).fetchone(), "document")["id"]
            cur.executemany(
                "INSERT INTO chunks (document_id, ord, heading, page, content) VALUES (%s, %s, %s, %s, %s)",
                [(doc_id, i, c["heading"], c["page"], c["content"]) for i, c in enumerate(chunks)],
            )
    return db.require(get(conn, doc_id), "document"), "updated" if existing else "added"


def ingest_path(conn: psycopg.Connection, path: str, project_id: UUID | None = None) -> dict:
    """Add a file or every supported file under a folder (recursively, skipping tool/build folders)."""
    root = Path(path).expanduser()
    if not root.is_absolute():
        raise IngestError("Use a full path, like C:\\Users\\you\\Documents\\notes.")
    if not root.exists():
        raise IngestError("That path doesn't exist on this PC.")
    folder = None if root.is_file() else str(root.resolve())
    files = [root] if root.is_file() else _walk(root)
    summary = {"added": 0, "updated": 0, "unchanged": 0, "skipped": []}
    for f in files:
        _store_file(conn, f, folder, project_id, summary)
    return summary


def _walk(root: Path) -> list[Path]:
    """Every supported file under `root`, skipping tool/build and hidden folders."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        files.extend(Path(dirpath) / f for f in sorted(filenames) if kind_for(f))
        if len(files) > MAX_FOLDER_FILES:
            raise IngestError(f"More than {MAX_FOLDER_FILES} files; pick a smaller folder.")
    return files


def _store_file(conn: psycopg.Connection, f: Path, folder: str | None, project_id: UUID | None, summary: dict) -> None:
    try:
        if f.stat().st_size > MAX_BYTES:
            raise IngestError(f"larger than {MAX_MB} MB")
        _, outcome = store(conn, f.name, f.read_bytes(), str(f.resolve()), folder, project_id)
        summary[outcome] += 1
    except (IngestError, OSError) as exc:
        summary["skipped"].append({"path": str(f), "reason": str(exc)})
    except psycopg.Error as exc:
        # One file the database refuses must not stop the rest of the folder (or every later rescan).
        log.warning("could not store %s: %s", f, exc)
        summary["skipped"].append({"path": str(f), "reason": "pal couldn't save this file's text"})


def rescan_folders(conn: psycopg.Connection, stop: threading.Event | None = None) -> dict:
    """Refresh every folder the owner added: new files are added, changed files re-read.

    A file pal already has is only read again when it was modified after pal stored it, so a quiet folder costs a
    directory walk. PDFs read by an older extractor are NOT re-read here: that's slow, so the owner starts it
    (reread.py, from the Documents page). Files deleted from disk, and folders that are gone (an unplugged drive), are left
    in pal; removing them stays the owner's choice on the Documents page.

    `stop` (set when pal shuts down) is checked between files; a stopped scan reports `"stopped": True`, and the
    files it didn't reach are picked up by the next one.
    """
    summary: dict = {"added": 0, "updated": 0, "unchanged": 0, "skipped": [], "missing": []}
    folders = [r[0] for r in conn.execute(
        "SELECT DISTINCT folder FROM documents WHERE folder IS NOT NULL ORDER BY folder").fetchall()]
    for folder in folders:
        root = Path(folder)
        if not root.is_dir():
            summary["missing"].append(folder)
            continue
        stored = {path: (at,) for path, at in conn.execute(
            "SELECT source_path, updated_at FROM documents WHERE folder = %s AND source_path IS NOT NULL", (folder,)
        ).fetchall()}
        try:
            files = _walk(root)
        except (IngestError, OSError) as exc:
            summary["skipped"].append({"path": folder, "reason": str(exc)})
            continue
        for f in files:
            if stop is not None and stop.is_set():
                summary["stopped"] = True
                return summary
            path = str(f.resolve())
            try:
                seen = stored.get(path)
                if seen is not None and f.stat().st_mtime <= seen[0].timestamp():
                    summary["unchanged"] += 1
                    continue
            except OSError as exc:
                summary["skipped"].append({"path": path, "reason": str(exc)})
                continue
            _store_file(conn, f, folder, None, summary)
    return summary


def list_documents(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            """SELECT d.id, d.title, d.filename, d.source_path, d.folder, d.kind, d.size_bytes, d.created_at, d.updated_at,
                      d.project_id,
                      count(c.id) AS chunk_count, count(c.id) FILTER (WHERE c.embedding IS NULL) AS pending
               FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
               GROUP BY d.id ORDER BY d.updated_at DESC"""
        ).fetchall()


def get(conn: psycopg.Connection, doc_id: UUID) -> dict | None:
    return next((d for d in list_documents(conn) if d["id"] == doc_id), None)


def set_project(conn: psycopg.Connection, doc_id: UUID, project_id: UUID | None) -> bool:
    return conn.execute("UPDATE documents SET project_id = %s WHERE id = %s", (project_id, doc_id)).rowcount == 1


def set_folder_project(conn: psycopg.Connection, folder: str, project_id: UUID | None) -> int:
    """Tag every document from `folder`; files added to it later inherit the tag."""
    return conn.execute("UPDATE documents SET project_id = %s WHERE folder = %s", (project_id, folder)).rowcount


def delete_folder(conn: psycopg.Connection, folder: str) -> int:
    """S10: every document added from `folder` goes, with its chunks (text + vectors). Files on disk are untouched."""
    return conn.execute("DELETE FROM documents WHERE folder = %s", (folder,)).rowcount


def delete(conn: psycopg.Connection, doc_id: UUID) -> bool:
    """S10: the document row, every chunk (text + vector) and, for an uploaded PDF, its kept encrypted copy."""
    from . import kept_uploads

    row = conn.execute(
        "DELETE FROM documents WHERE id = %s RETURNING sha256, source_path, kind", (doc_id,)
    ).fetchone()
    if row is None:
        return False
    sha256, source_path, kind = row
    if source_path is None and kind == "pdf":
        kept_uploads.discard(bytes(sha256))
    return True
