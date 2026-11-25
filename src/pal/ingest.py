"""Document ingestion: PDF, Markdown and plain text, from uploads or from files and folders on this PC.

S7: document text is stored and later shown to the model as data inside a <context> block. Nothing here
interprets it, and no memories are ever derived from documents.
"""

import hashlib
import io
import logging
import os
import re
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

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


def parse(filename: str, data: bytes) -> tuple[str, str, list[dict]]:
    """Return (kind, title, chunks) where each chunk is {heading, page, content}."""
    kind = kind_for(filename)
    if kind is None:
        raise IngestError("Only PDF, Markdown and text files are supported.")
    if len(data) > MAX_BYTES:
        raise IngestError(TOO_BIG)
    title = Path(filename).stem
    chunks: list[dict] = []
    if kind == "pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            meta_title = reader.metadata.title if reader.metadata else None
            if _useful_pdf_title(meta_title):
                title = str(meta_title).strip()
            for page_no, page in enumerate(reader.pages, start=1):
                for piece in _pack(page.extract_text() or ""):
                    chunks.append({"heading": None, "page": page_no, "content": piece})
        except Exception as exc:
            raise IngestError(f"Could not read this PDF ({type(exc).__name__}).") from exc
    else:
        text = _decode(data).replace("\x00", "")
        sections = _markdown_sections(text) if kind == "markdown" else [(None, text)]
        if kind == "markdown":
            h1 = next((m.group(2) for line in text.splitlines()[:40] if (m := _HEADING.match(line)) and m.group(1) == "#"), None)
            title = h1 or title
            # The H1 is the title already; keep only the part of the heading path below it.
            sections = [(_below_title(h, h1), body) for h, body in sections]
        for heading, body in sections:
            for piece in _pack(body):
                chunks.append({"heading": heading, "page": None, "content": piece})
    if not chunks:
        raise IngestError("No text found in this file." + (" Scanned PDFs aren't supported." if kind == "pdf" else ""))
    return kind, title[:200], chunks


# ---------- storage ----------
def _no_nul(text: str | None) -> str | None:
    return text.replace("\x00", "") if text else text



def store(
    conn: psycopg.Connection, filename: str, data: bytes, source_path: str | None = None, folder: str | None = None,
    project_id: UUID | None = None,
) -> tuple[dict, str]:
    """Insert or refresh a document. Returns (document, outcome) with outcome added|updated|unchanged.

    `folder` is the folder the owner added (for grouping); re-adding a folder claims files already stored.
    `project_id`, when given, tags the document (new or already stored); otherwise a new file in a tagged folder
    takes the folder's project.
    """
    digest = hashlib.sha256(data).digest()
    with conn.cursor(row_factory=dict_row) as cur:
        if source_path:
            existing = cur.execute("SELECT id, sha256 FROM documents WHERE source_path = %s", (source_path,)).fetchone()
        else:
            existing = cur.execute(
                "SELECT id, sha256 FROM documents WHERE source_path IS NULL AND sha256 = %s", (digest,)
            ).fetchone()
        if existing and bytes(existing["sha256"]) == digest:
            if folder:
                cur.execute("UPDATE documents SET folder = %s WHERE id = %s AND folder IS DISTINCT FROM %s",
                            (folder, existing["id"], folder))
            if project_id:
                set_project(conn, existing["id"], project_id)
            return get(conn, existing["id"]), "unchanged"
        kind, title, chunks = parse(filename, data)
        # Postgres text can't hold NUL; PDF extraction sometimes yields it.
        title = _no_nul(title)
        chunks = [{**c, "heading": _no_nul(c["heading"]), "content": _no_nul(c["content"])} for c in chunks]
        with conn.transaction():
            if existing:
                doc_id = existing["id"]
                cur.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                cur.execute(
                    "UPDATE documents SET title = %s, kind = %s, sha256 = %s, size_bytes = %s, folder = COALESCE(%s, folder), project_id = COALESCE(%s, project_id), updated_at = now() WHERE id = %s",
                    (title, kind, digest, len(data), folder, project_id, doc_id),
                )
            else:
                # A new file in a folder that belongs to a project joins that project.
                doc_id = cur.execute(
                    "INSERT INTO documents (title, filename, source_path, folder, kind, sha256, size_bytes, project_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, (SELECT project_id FROM documents WHERE folder = %s "
                    "AND project_id IS NOT NULL ORDER BY updated_at DESC LIMIT 1))) RETURNING id",
                    (title, Path(filename).name[:255], source_path, folder, kind, digest, len(data), project_id, folder),
                ).fetchone()["id"]
            cur.executemany(
                "INSERT INTO chunks (document_id, ord, heading, page, content) VALUES (%s, %s, %s, %s, %s)",
                [(doc_id, i, c["heading"], c["page"], c["content"]) for i, c in enumerate(chunks)],
            )
    return get(conn, doc_id), "updated" if existing else "added"


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


def rescan_folders(conn: psycopg.Connection) -> dict:
    """Refresh every folder the owner added: new files are added, changed files re-read.

    A file pal already has is only read again when it was modified after pal stored it, so a quiet folder costs a
    directory walk, not a re-read. Files deleted from disk, and folders that are gone (an unplugged drive), are left
    in pal; removing them stays the owner's choice on the Documents page.
    """
    summary: dict = {"added": 0, "updated": 0, "unchanged": 0, "skipped": [], "missing": []}
    folders = [r[0] for r in conn.execute(
        "SELECT DISTINCT folder FROM documents WHERE folder IS NOT NULL ORDER BY folder").fetchall()]
    for folder in folders:
        root = Path(folder)
        if not root.is_dir():
            summary["missing"].append(folder)
            continue
        stored = dict(conn.execute(
            "SELECT source_path, updated_at FROM documents WHERE folder = %s AND source_path IS NOT NULL", (folder,)
        ).fetchall())
        try:
            files = _walk(root)
        except (IngestError, OSError) as exc:
            summary["skipped"].append({"path": folder, "reason": str(exc)})
            continue
        for f in files:
            path = str(f.resolve())
            try:
                seen = stored.get(path)
                if seen is not None and f.stat().st_mtime <= seen.timestamp():
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
    """S10: the document row and every chunk (text + vector) are deleted."""
    return conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,)).rowcount == 1
