"""Documents API: upload files, add files/folders from this PC, list, delete. Owner session required (S2)."""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import db, ingest, kept_uploads, pdf, reread
from .api import require_owner, require_session
from .work_api import _check_project

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


class FolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class AddFolderBody(FolderBody):
    project_id: UUID | None = None


class ProjectBody(BaseModel):
    project_id: UUID | None  # null = no project


class FolderProjectBody(FolderBody, ProjectBody):
    pass


@router.get("/documents")
def list_documents(request: Request) -> list[dict]:
    with request.app.state.pool.connection() as conn:
        return ingest.list_documents(conn)


def processing_state(conn) -> dict:
    """What pal is doing (or offering to do) with documents in the background, for the Documents and Status pages."""
    pending, total = conn.execute(
        "SELECT count(*) FILTER (WHERE embedding IS NULL), count(*) FROM chunks").fetchone()
    return {
        "pdf_reader": "docling" if pdf.docling_ready() else "pypdf",
        "indexing": {"pending": pending, "total": total},
        "stale_pdfs": reread.stale(conn),
        "reread": reread.snapshot(),
    }


@router.get("/documents/processing")
def processing(request: Request, session=Depends(require_session)) -> dict:
    with request.app.state.pool.connection() as conn:
        # Re-reading opens files on this PC, so (like adding folders) only the PC may start it.
        return {**processing_state(conn), "can_reread": session.is_owner_pc}


@router.post("/documents/reread", dependencies=[Depends(require_owner)])
def start_reread(request: Request) -> dict:
    """Owner-started: re-read PDFs from this PC that an older PDF reader read. Slow; shows progress; can be stopped."""
    try:
        return reread.start(request.app.state.pool)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/reread/stop", dependencies=[Depends(require_owner)])
def stop_reread() -> dict:
    """Stops after the file being read now; files already re-read keep their new text."""
    return reread.stop()


@router.post("/documents/upload", status_code=201)
async def upload(request: Request, filename: str, project_id: UUID | None = None) -> dict:
    """Raw file bytes in the body; the name in ?filename= (avoids a multipart parser dependency)."""
    if ingest.kind_for(filename) is None:
        raise HTTPException(status_code=415, detail="Only PDF, Markdown and text files are supported.")
    with request.app.state.pool.connection() as conn:
        _check_project(conn, project_id)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > ingest.MAX_BYTES:
        raise HTTPException(status_code=413, detail=ingest.TOO_BIG)
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > ingest.MAX_BYTES:
            raise HTTPException(status_code=413, detail=ingest.TOO_BIG)
    from starlette.concurrency import run_in_threadpool

    def save() -> dict:
        with request.app.state.pool.connection() as conn:
            # An upload is an explicit, single-file request, so an unchanged PDF is re-read if an older reader read it.
            doc, outcome = ingest.store(conn, filename, bytes(data), project_id=project_id, reread=True)
        if doc["kind"] == "pdf":  # kept encrypted so a better PDF reader can re-read it later (kept_uploads.py)
            digest = hashlib.sha256(data).digest()
            if not kept_uploads.has(digest):
                kept_uploads.keep(digest, bytes(data))
        return {"document": doc, "outcome": outcome}

    try:
        return await run_in_threadpool(save)
    except ingest.IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/documents/folder", dependencies=[Depends(require_owner)])
def add_folder(body: AddFolderBody, request: Request) -> dict:
    """Add a file or folder that lives on this PC. Re-adding refreshes changed files."""
    try:
        with request.app.state.pool.connection() as conn:
            _check_project(conn, body.project_id)
            return ingest.ingest_path(conn, body.path.strip().strip('"'), body.project_id)
    except ingest.IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


_PICKER = (
    "import tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
    "p = filedialog.askdirectory(parent=r, title='Choose a folder for pal', mustexist=True)\n"
    "r.destroy()\n"
    "print(p or '', end='')\n"
)


def _pick_folder() -> str | None:
    """Open the native folder picker on this PC (separate process so Tk never touches server threads)."""
    import os
    import subprocess
    import sys

    try:
        out = subprocess.run(
            [sys.executable, "-c", _PICKER], capture_output=True, text=True, encoding="utf-8", timeout=600, check=True
        ).stdout.strip()
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail="Could not open the folder picker on this PC.") from exc
    return os.path.normpath(out) if out else None


@router.post("/documents/folder/pick", dependencies=[Depends(require_owner)])
async def pick_folder() -> dict:
    """Show a Windows folder picker; returns the chosen path, or null if cancelled."""
    from starlette.concurrency import run_in_threadpool

    return {"path": await run_in_threadpool(_pick_folder)}


@router.post("/documents/folder/remove")
def remove_folder(body: FolderBody, request: Request) -> dict:
    """Remove a folder's documents from pal (the files on disk are not touched)."""
    with request.app.state.pool.connection() as conn:
        removed = ingest.delete_folder(conn, body.path)
    if not removed:
        raise HTTPException(status_code=404, detail="No documents from that folder")
    return {"removed": removed}


@router.post("/documents/folder/project")
def set_folder_project(body: FolderProjectBody, request: Request) -> dict:
    """Put every document from a folder in a project (or none). New files in the folder follow it."""
    with request.app.state.pool.connection() as conn:
        _check_project(conn, body.project_id)
        updated = ingest.set_folder_project(conn, body.path, body.project_id)
    if not updated:
        raise HTTPException(status_code=404, detail="No documents from that folder")
    return {"updated": updated}


@router.put("/documents/{doc_id}/project")
def set_document_project(doc_id: UUID, body: ProjectBody, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        _check_project(conn, body.project_id)
        if not ingest.set_project(conn, doc_id, body.project_id):
            raise HTTPException(status_code=404, detail="Document not found")
        return db.require(ingest.get(conn, doc_id), "document")


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: UUID, request: Request) -> Response:
    with request.app.state.pool.connection() as conn:
        if not ingest.delete(conn, doc_id):
            raise HTTPException(status_code=404, detail="Document not found")
    return Response(status_code=204)
