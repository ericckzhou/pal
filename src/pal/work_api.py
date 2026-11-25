"""Projects, tasks, notes, reminders, notifications and action proposals. Owner session required (S2)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from . import actions, conversations, project_overview, reminders, when, work
from .api import require_session

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


class _NotBlank(BaseModel):
    @field_validator("name", "title", "text", check_fields=False)
    @classmethod
    def not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("must not be empty")
        return v


class _Rule(BaseModel):
    @field_validator("rrule", check_fields=False)
    @classmethod
    def valid_rule(cls, v: str | None) -> str | None:
        if v:
            when.validate_rrule(v)
        return v or None


class NewProject(_NotBlank):
    name: str = Field(max_length=80)
    summary: str = Field(default="", max_length=2000)


class ProjectPatch(_NotBlank):
    name: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None


class NewTask(_NotBlank):
    title: str = Field(max_length=300)
    project_id: UUID | None = None
    due_date: date | None = None


class TaskPatch(_NotBlank):
    title: str | None = Field(default=None, max_length=300)
    status: Literal["open", "done"] | None = None
    stage: Literal["todo", "doing"] | None = None  # board column while open
    project_id: UUID | None = None
    due_date: date | None = None


class NewNote(_NotBlank):
    text: str = Field(max_length=5000)
    kind: Literal["note", "decision"] = "note"
    project_id: UUID | None = None


class NotePatch(_NotBlank):
    text: str | None = Field(default=None, max_length=5000)
    kind: Literal["note", "decision"] | None = None


class NewReminder(_NotBlank, _Rule):
    title: str = Field(max_length=300)
    at: datetime  # local wall-clock (the time zone, if any, is ignored)
    rrule: str | None = Field(default=None, max_length=200)
    project_id: UUID | None = None


class ReminderPatch(_NotBlank, _Rule):
    title: str | None = Field(default=None, max_length=300)
    at: datetime | None = None
    rrule: str | None = Field(default=None, max_length=200)


class ProposalEdits(_NotBlank, _Rule):
    title: str | None = Field(default=None, max_length=300)
    due_date: date | None = None
    at: datetime | None = None
    rrule: str | None = Field(default=None, max_length=200)
    project_id: UUID | None = None


class Focus(BaseModel):
    project_id: UUID | None = None


class Snooze(BaseModel):
    minutes: int = Field(default=10, ge=1, le=7 * 24 * 60)


def _found(item, what: str):
    if item is None or item is False:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return item


def _pool(request: Request):
    return request.app.state.pool.connection()


def _check_project(conn, project_id: UUID | None) -> None:
    if project_id is not None and work.get_project(conn, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")


# ---------- projects ----------

@router.get("/projects")
def list_projects(request: Request, archived: bool = False) -> list[dict]:
    with _pool(request) as conn:
        return work.list_projects(conn, archived)


@router.post("/projects", status_code=201)
def create_project(body: NewProject, request: Request) -> dict:
    with _pool(request) as conn:
        try:
            return work.create_project(conn, body.name, body.summary)
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="A project with that name already exists")


@router.get("/projects/{project_id}")
def get_project(project_id: UUID, request: Request) -> dict:
    with _pool(request) as conn:
        project = _found(work.get_project(conn, project_id), "Project")
        project["card"] = work.project_card(conn, project, reminders.local_now().date())
        return project


@router.get("/projects/{project_id}/overview")
def get_project_overview(project_id: UUID, request: Request) -> dict:
    """Open tasks, scheduled reminders, memories, documents, notes and chats for one project."""
    with _pool(request) as conn:
        return _found(project_overview.overview(conn, project_id), "Project")


@router.patch("/projects/{project_id}")
def update_project(project_id: UUID, body: ProjectPatch, request: Request) -> dict:
    with _pool(request) as conn:
        try:
            project = work.update_project(conn, project_id, name=body.name, summary=body.summary, status=body.status)
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="A project with that name already exists")
        return _found(project, "Project")


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: UUID, request: Request) -> Response:
    with _pool(request) as conn:
        _found(work.delete_project(conn, project_id), "Project")
    return Response(status_code=204)


# ---------- tasks ----------

@router.get("/tasks")
def list_tasks(request: Request, project_id: UUID | None = None, status: Literal["open", "done", "all"] = "open",
               no_project: bool = False) -> list[dict]:
    with _pool(request) as conn:
        return work.list_tasks(conn, project_id, status, no_project)


@router.post("/tasks", status_code=201)
def create_task(body: NewTask, request: Request) -> dict:
    with _pool(request) as conn:
        _check_project(conn, body.project_id)
        return work.create_task(conn, body.title, body.project_id, body.due_date)


@router.patch("/tasks/{task_id}")
def update_task(task_id: UUID, body: TaskPatch, request: Request) -> dict:
    fields = body.model_fields_set
    with _pool(request) as conn:
        _check_project(conn, body.project_id)
        kwargs = {}
        if "project_id" in fields:
            kwargs["project_id"] = body.project_id
        if "due_date" in fields:
            kwargs["due_date"] = body.due_date
        return _found(work.update_task(conn, task_id, title=body.title, status=body.status,
                                            stage=body.stage, **kwargs), "Task")


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: UUID, request: Request) -> Response:
    with _pool(request) as conn:
        _found(work.delete_task(conn, task_id), "Task")
    return Response(status_code=204)


# ---------- notes ----------

@router.get("/notes")
def list_notes(request: Request, project_id: UUID | None = None,
               kind: Literal["note", "decision"] | None = None) -> list[dict]:
    with _pool(request) as conn:
        return work.list_notes(conn, project_id, kind)


@router.post("/notes", status_code=201)
def create_note(body: NewNote, request: Request) -> dict:
    with _pool(request) as conn:
        _check_project(conn, body.project_id)
        return work.create_note(conn, body.text, body.kind, body.project_id)


@router.patch("/notes/{note_id}")
def update_note(note_id: UUID, body: NotePatch, request: Request) -> dict:
    with _pool(request) as conn:
        return _found(work.update_note(conn, note_id, text=body.text, kind=body.kind), "Note")


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: UUID, request: Request) -> Response:
    with _pool(request) as conn:
        _found(work.delete_note(conn, note_id), "Note")
    return Response(status_code=204)


# ---------- reminders and notifications ----------

def _local(at: datetime) -> datetime:
    return (at.astimezone().replace(tzinfo=None) if at.tzinfo else at).replace(second=0, microsecond=0)


@router.get("/reminders")
def list_reminders(request: Request, include_done: bool = False) -> list[dict]:
    with _pool(request) as conn:
        return reminders.list_reminders(conn, include_done)


@router.post("/reminders", status_code=201)
def create_reminder(body: NewReminder, request: Request) -> dict:
    at = _local(body.at)
    if not body.rrule and at <= reminders.local_now():
        raise HTTPException(status_code=422, detail="That time has already passed.")
    with _pool(request) as conn:
        _check_project(conn, body.project_id)
        return reminders.create(conn, body.title, at, body.rrule, project_id=body.project_id)


@router.patch("/reminders/{reminder_id}")
def update_reminder(reminder_id: UUID, body: ReminderPatch, request: Request) -> dict:
    kwargs = {"rrule": body.rrule} if "rrule" in body.model_fields_set else {}
    with _pool(request) as conn:
        return _found(reminders.update(conn, reminder_id, title=body.title,
                                       at=_local(body.at) if body.at else None, **kwargs), "Reminder")


@router.delete("/reminders/{reminder_id}", status_code=204)
def delete_reminder(reminder_id: UUID, request: Request) -> Response:
    with _pool(request) as conn:
        _found(reminders.delete(conn, reminder_id), "Reminder")
    return Response(status_code=204)


@router.get("/notifications")
def list_notifications(request: Request) -> list[dict]:
    with _pool(request) as conn:
        return reminders.notifications(conn)


@router.post("/notifications/dismiss-all")
def dismiss_all(request: Request) -> dict:
    with _pool(request) as conn:
        return {"dismissed": reminders.dismiss(conn)}


@router.post("/notifications/{notification_id}/dismiss")
def dismiss(notification_id: UUID, request: Request) -> dict:
    with _pool(request) as conn:
        return {"dismissed": reminders.dismiss(conn, notification_id)}


@router.post("/notifications/{notification_id}/snooze")
def snooze(notification_id: UUID, body: Snooze, request: Request) -> dict:
    with _pool(request) as conn:
        return _found(reminders.snooze(conn, notification_id, body.minutes), "Notification")


# ---------- proposals and chat focus ----------

@router.get("/conversations/{conv_id}/proposals")
def list_proposals(conv_id: UUID, request: Request) -> list[dict]:
    with _pool(request) as conn:
        return actions.pending(conn, conv_id)


@router.post("/proposals/{proposal_id}/confirm")
def confirm_proposal(proposal_id: UUID, body: ProposalEdits, request: Request) -> dict:
    edits = {}
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field == "at" and value is not None:
            value = _local(value).isoformat(timespec="minutes")
        elif isinstance(value, (date, UUID)):
            value = str(value) if isinstance(value, UUID) else value.isoformat()
        edits[field] = value
    with _pool(request) as conn:
        _check_project(conn, body.project_id)
        try:
            return _found(actions.confirm(conn, proposal_id, edits), "Proposal")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/proposals/{proposal_id}", status_code=204)
def reject_proposal(proposal_id: UUID, request: Request) -> Response:
    with _pool(request) as conn:
        _found(actions.reject(conn, proposal_id), "Proposal")
    return Response(status_code=204)


@router.put("/conversations/{conv_id}/project")
def set_focus(conv_id: UUID, body: Focus, request: Request) -> dict:
    with _pool(request) as conn:
        _found(conversations.get_conversation(conn, conv_id), "Conversation")
        _check_project(conn, body.project_id)
        work.set_focus(conn, conv_id, body.project_id)
        return {"project_id": body.project_id}
