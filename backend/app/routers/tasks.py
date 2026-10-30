import uuid
from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..access import Ctx
from ..db import get_db, utcnow
from ..deps import audit, client_ip, require
from ..models import ExtractedTask, Project
from ..services import queries

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
Status = Literal["detected", "needs_confirmation", "pending", "in_progress", "completed", "cancelled"]


def task_out(t: ExtractedTask) -> dict:
    return {"id": str(t.id), "title": t.title, "description": t.description, "assignee": t.assignee_name, "assignee_evidence": t.assignee_evidence,
            "due_date": t.due_date.isoformat() if t.due_date else None, "due_text": t.due_text, "priority": t.priority, "status": t.status,
            "project_id": str(t.project_id) if t.project_id else None, "session_id": str(t.session_id) if t.session_id else None,
            "timestamp_ms": t.timestamp_ms, "segment_ids": t.segment_ids, "confidence": t.confidence, "source": t.source,
            "completed_at": t.completed_at.isoformat() + "Z" if t.completed_at else None, "remind_at": t.remind_at.isoformat() + "Z" if t.remind_at else None,
            "history": t.history, "created_at": t.created_at.isoformat() + "Z", "visibility": t.visibility}


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    assignee: Optional[str] = None
    due_date: Optional[date] = None
    priority: Literal["low", "medium", "high"] = "medium"
    project_id: Optional[uuid.UUID] = None
    visibility: Literal["private", "shared"] = "private"
    remind_at: Optional[datetime] = None


@router.post("", status_code=201)
def create_task(body: TaskIn, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    if body.visibility == "shared" and ctx.workspace.type == "individual":
        raise HTTPException(422, "Individual workspaces cannot share content")
    if body.project_id and not db.scalar(select(Project.id).where(Project.id == body.project_id, Project.workspace_id == ctx.workspace.id)):
        raise HTTPException(404, "Project not found")
    t = ExtractedTask(workspace_id=ctx.workspace.id, owner_id=ctx.user.id, visibility=body.visibility, title=body.title.strip(),
                      description=body.description, assignee_name=body.assignee, assignee_evidence="manual" if body.assignee else "none",
                      due_date=body.due_date, priority=body.priority, project_id=body.project_id, status="pending", source="manual",
                      confidence=1.0, user_modified=True, remind_at=body.remind_at.replace(tzinfo=None) if body.remind_at else None,
                      history=[{"at": utcnow().isoformat(), "event": "created", "by": str(ctx.user.id)}])
    db.add(t)
    audit(db, user=ctx.user, ws=ctx.workspace, action="task.create", rtype="task", rid=t.id, ip=client_ip(request))
    db.commit()
    return task_out(t)


@router.get("")
def list_tasks(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), status: Optional[str] = Query(None, description="comma-separated"),
               assignee: Optional[str] = None, project_id: Optional[uuid.UUID] = None, open_only: bool = False, due_before: Optional[date] = None,
               q: Optional[str] = None, limit: int = Query(50, le=200), offset: int = 0):
    qq = queries.tasks_q(ctx)
    if status:
        qq = qq.where(ExtractedTask.status.in_(status.split(",")))
    if open_only:
        qq = qq.where(ExtractedTask.status.in_(queries.OPEN_TASK))
    if assignee:
        qq = qq.where(ExtractedTask.assignee_name.ilike(f"%{assignee}%"))
    if project_id:
        qq = qq.where(ExtractedTask.project_id == project_id)
    if due_before:
        qq = qq.where(ExtractedTask.due_date <= due_before)
    if q:
        qq = qq.where(ExtractedTask.title.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(qq.subquery()))
    rows = db.scalars(qq.order_by(ExtractedTask.due_date.is_(None), ExtractedTask.due_date, ExtractedTask.created_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [task_out(t) for t in rows]}


class TaskPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = None
    assignee: Optional[str] = None
    clear_assignee: bool = False
    due_date: Optional[date] = None
    clear_due: bool = False
    priority: Optional[Literal["low", "medium", "high"]] = None
    status: Optional[Status] = None
    project_id: Optional[uuid.UUID] = None
    remind_at: Optional[datetime] = None
    visibility: Optional[Literal["private", "shared"]] = None


@router.patch("/{tid}")
def patch_task(tid: str, body: TaskPatch, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    try:
        t = db.scalar(queries.tasks_q(ctx).where(ExtractedTask.id == uuid.UUID(tid)))
    except ValueError:
        t = None
    if not t:
        raise HTTPException(404, "Not found")
    log = []

    def note(field, old, new):
        if old != new:
            log.append({"at": utcnow().isoformat(), "by": str(ctx.user.id), "field": field, "from": str(old) if old is not None else None, "to": str(new) if new is not None else None})

    if body.title is not None:
        note("title", t.title, body.title)
        t.title = body.title
    if body.description is not None:
        t.description = body.description
    if body.clear_assignee:
        note("assignee", t.assignee_name, None)
        t.assignee_name, t.assignee_evidence = None, "none"
    elif body.assignee is not None:
        note("assignee", t.assignee_name, body.assignee)
        t.assignee_name, t.assignee_evidence = body.assignee.strip(), "manual"
    if body.clear_due:
        note("due_date", t.due_date, None)
        t.due_date, t.due_text = None, None
    elif body.due_date is not None:
        note("due_date", t.due_date, body.due_date)
        t.due_date = body.due_date
    if body.priority:
        note("priority", t.priority, body.priority)
        t.priority = body.priority
    if body.project_id:
        t.project_id = body.project_id
    if body.remind_at:
        t.remind_at = body.remind_at.replace(tzinfo=None)
    if body.visibility and t.session_id is None and t.owner_id == ctx.user.id:
        t.visibility = body.visibility
    if body.status and body.status != t.status:
        note("status", t.status, body.status)
        t.status = body.status
        t.completed_at = utcnow() if body.status == "completed" else None
    t.user_modified = True
    t.history = [*(t.history or []), *log]
    audit(db, user=ctx.user, ws=ctx.workspace, action="task.update", rtype="task", rid=t.id, details={"changes": log}, ip=client_ip(request))
    db.commit()
    return task_out(t)


@router.delete("/{tid}")
def delete_task(tid: str, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    t = db.scalar(queries.tasks_q(ctx).where(ExtractedTask.id == uuid.UUID(tid)))
    if not t:
        raise HTTPException(404, "Not found")
    t.deleted_at, t.user_modified = utcnow(), True  # soft delete; user_modified keeps re-processing from resurrecting it
    db.commit()
    return {"deleted": tid}
