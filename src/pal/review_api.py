"""v1.1 routes: what needs attention, and the weekly digests."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from . import review
from .api import require_session

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


def _found(value, what: str):
    if value is None or value is False:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return value


@router.get("/attention")
def attention(request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return review.neglected(conn)


@router.post("/tasks/{task_id}/still-on-it")
def still_on_it(task_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        _found(review.still_on_it(conn, task_id), "Open task")
    return {"ok": True}


@router.get("/digests")
def digests(request: Request) -> list[dict]:
    with request.app.state.pool.connection() as conn:
        return review.list_digests(conn)


@router.post("/digests/generate")
def generate(request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return review.generate(conn)


@router.get("/digests/{digest_id}")
def digest(digest_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return _found(review.get_digest(conn, digest_id), "Digest")


@router.post("/digests/{digest_id}/read")
def mark_read(digest_id: UUID, request: Request) -> dict:
    with request.app.state.pool.connection() as conn:
        return _found(review.mark_read(conn, digest_id), "Digest")
