"""Projects, dashboard, search, memory, analytics, notifications, integrations."""
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..access import Ctx, visible_session_filter
from ..db import get_db, utcnow
from ..deps import audit, client_ip, require
from ..models import (Commitment, ExtractedDecision, ExtractedTask, Insight, Integration, MemoryEntity, Notification, Project,
                      RecordingSession, Speaker)
from ..services import queries, retrieval
from ..services.rag import local, to_utc, tzinfo

router = APIRouter(prefix="/api", tags=["workspace"])


# ------------------------------------------------------------------ projects
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    aliases: list[str] = []


def _proj(db, ctx, pid) -> Project:
    try:
        p = db.scalar(select(Project).where(Project.id == uuid.UUID(pid), Project.workspace_id == ctx.workspace.id, Project.deleted_at.is_(None)))
    except ValueError:
        p = None
    if not p:
        raise HTTPException(404, "Not found")
    return p


def _pout(p: Project) -> dict:
    return {"id": str(p.id), "name": p.name, "description": p.description, "aliases": p.aliases, "status": p.status}


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    if db.scalar(select(Project.id).where(Project.workspace_id == ctx.workspace.id, func.lower(Project.name) == body.name.strip().lower(), Project.deleted_at.is_(None))):
        raise HTTPException(409, "A project with this name exists")
    p = Project(workspace_id=ctx.workspace.id, name=body.name.strip(), description=body.description, aliases=[a.strip() for a in body.aliases if a.strip()], created_by=ctx.user.id)
    db.add(p)
    audit(db, user=ctx.user, ws=ctx.workspace, action="project.create", rtype="project", rid=p.id, ip=client_ip(request))
    db.commit()
    return _pout(p)


@router.get("/projects")
def list_projects(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    return [_pout(p) for p in db.scalars(select(Project).where(Project.workspace_id == ctx.workspace.id, Project.deleted_at.is_(None)).order_by(Project.name))]


@router.get("/projects/{pid}")
def get_project(pid: str, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    p = _proj(db, ctx, pid)
    sess = db.scalars(select(RecordingSession).where(visible_session_filter(ctx), RecordingSession.project_id == p.id).order_by(RecordingSession.occurred_at.desc()).limit(50)).all()
    tasks = db.scalars(queries.tasks_q(ctx).where(ExtractedTask.project_id == p.id).order_by(ExtractedTask.created_at.desc()).limit(50)).all()
    decs = db.scalars(queries.decisions_q(ctx).where(ExtractedDecision.project_id == p.id).order_by(ExtractedDecision.created_at.desc()).limit(50)).all()
    return {**_pout(p), "sessions": [{"id": str(s.id), "title": s.title, "kind": s.kind, "occurred_at": s.occurred_at.isoformat() + "Z", "status": s.status} for s in sess],
            "tasks": [{"id": str(t.id), "title": t.title, "assignee": t.assignee_name, "status": t.status, "due_date": t.due_date.isoformat() if t.due_date else None} for t in tasks],
            "decisions": [{"id": str(d.id), "text": d.text, "status": d.confirmation_status, "session_id": str(d.session_id)} for d in decs]}


@router.patch("/projects/{pid}")
def patch_project(pid: str, body: ProjectIn, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    p = _proj(db, ctx, pid)
    p.name, p.description, p.aliases = body.name.strip(), body.description, [a.strip() for a in body.aliases if a.strip()]
    db.commit()
    return _pout(p)


@router.delete("/projects/{pid}")
def delete_project(pid: str, ctx: Ctx = Depends(require("manager")), db: Session = Depends(get_db)):
    p = _proj(db, ctx, pid)
    p.deleted_at = utcnow()
    db.commit()
    return {"deleted": pid}


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard/overview")
def overview(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    tz = tzinfo(ctx.user.timezone)
    today = datetime.now(tz).date()
    s0, s1 = to_utc(today, tz), to_utc(today + timedelta(days=1), tz)
    sess = db.scalars(select(RecordingSession).where(visible_session_filter(ctx), RecordingSession.occurred_at >= s0, RecordingSession.occurred_at < s1)
                      .order_by(RecordingSession.occurred_at)).all()
    live = [s for s in sess if not s.exclude_from_processing]
    ids = [s.id for s in live]
    from ..models import ConversationSummary
    sums = {x.session_id: x for x in db.scalars(select(ConversationSummary).where(ConversationSummary.session_id.in_(ids)))} if ids else {}

    def cnt(M, extra=None):
        if not ids:
            return 0
        q = select(func.count()).select_from(M).where(M.session_id.in_(ids))
        return db.scalar(q.where(extra) if extra is not None else q)
    open_tasks = db.scalars(queries.tasks_q(ctx).where(ExtractedTask.status.in_(queries.OPEN_TASK))).all()
    soon = today + timedelta(days=3)
    pending_c = db.scalar(select(func.count()).select_from(queries.commitments_q(ctx).where(Commitment.status == "open").subquery()))
    return {
        "date": today.isoformat(), "timezone": ctx.user.timezone,
        "stats": {"sessions": len(sess), "meetings": sum(1 for s in live if s.kind == "meeting"),
                  "duration_seconds": sum(s.duration_seconds or 0 for s in live), "tasks_identified": cnt(ExtractedTask, ExtractedTask.deleted_at.is_(None)),
                  "decisions_identified": cnt(ExtractedDecision), "pending_commitments": pending_c, "open_tasks": len(open_tasks),
                  "needs_review": sum(1 for t in open_tasks if t.status in ("detected", "needs_confirmation"))},
        "timeline": [{"id": str(s.id), "time": local(s.occurred_at, tz).strftime("%H:%M"), "title": s.title, "kind": s.kind, "status": s.status,
                      "duration_seconds": s.duration_seconds, "participants": [x.name for x in db.scalars(select(Speaker).where(Speaker.session_id == s.id))],
                      "summary": (sums[s.id].executive_summary[:240] if s.id in sums else ""), "excluded": s.exclude_from_processing,
                      "events": {"decisions": cnt(ExtractedDecision) and db.scalar(select(func.count()).select_from(ExtractedDecision).where(ExtractedDecision.session_id == s.id)),
                                 "tasks": db.scalar(select(func.count()).select_from(ExtractedTask).where(ExtractedTask.session_id == s.id)) if ids else 0}} for s in sess],
        "highlights": {
            "decisions": [{"id": str(d.id), "text": d.text, "session_id": str(d.session_id)} for d in db.scalars(queries.decisions_q(ctx).where(ExtractedDecision.session_id.in_(ids)).limit(5))] if ids else [],
            "new_tasks": [{"id": str(t.id), "title": t.title, "assignee": t.assignee_name, "status": t.status} for t in db.scalars(queries.tasks_q(ctx).where(ExtractedTask.session_id.in_(ids)).limit(5))] if ids else [],
            "deadlines": [{"id": str(t.id), "title": t.title, "due_date": t.due_date.isoformat(), "assignee": t.assignee_name}
                          for t in sorted((t for t in open_tasks if t.due_date and t.due_date <= soon), key=lambda t: t.due_date)[:6]],
            "questions": [{"id": str(i.id), "text": i.text} for i in db.scalars(queries.insights_q(ctx).where(Insight.kind == "question", Insight.session_id.in_(ids)).limit(4))] if ids else [],
            "ideas": [{"id": str(i.id), "text": i.text} for i in db.scalars(queries.insights_q(ctx).where(Insight.kind == "idea", Insight.session_id.in_(ids)).limit(4))] if ids else []},
        "recommendations": _recs(open_tasks, today),
    }


def _recs(open_tasks, today) -> list[dict]:
    """Only statements that follow directly from stored data; empty list when there is nothing to say."""
    out = []
    overdue = [t for t in open_tasks if t.due_date and t.due_date < today]
    if overdue:
        out.append({"text": f"{len(overdue)} task(s) are past their due date.", "link": "/tasks?filter=overdue"})
    review = [t for t in open_tasks if t.status in ("detected", "needs_confirmation")]
    if review:
        out.append({"text": f"{len(review)} AI-detected task(s) need you to confirm owner and deadline.", "link": "/tasks?filter=review"})
    unowned = [t for t in open_tasks if not t.assignee_name and t.status not in ("detected", "needs_confirmation")]
    if unowned:
        out.append({"text": f"{len(unowned)} open task(s) have no owner.", "link": "/tasks"})
    return out


# ------------------------------------------------------------------ search
@router.get("/search")
def search(q: str = Query(min_length=2, max_length=300), ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), limit: int = Query(15, le=50)):
    """Global search: titles, tasks, decisions and transcript passages (hybrid keyword + semantic), permission-filtered."""
    like = f"%{q}%"
    sess = db.scalars(select(RecordingSession).where(visible_session_filter(ctx), RecordingSession.title.ilike(like)).limit(5)).all()
    tasks = db.scalars(queries.tasks_q(ctx).where(ExtractedTask.title.ilike(like)).limit(5)).all()
    decs = db.scalars(queries.decisions_q(ctx).where(ExtractedDecision.text.ilike(like)).limit(5)).all()
    hits = retrieval.hybrid_search(db, ctx, q, k=limit)
    names = {}
    out = {"sessions": [{"id": str(s.id), "title": s.title, "occurred_at": s.occurred_at.isoformat() + "Z"} for s in sess],
           "tasks": [{"id": str(t.id), "title": t.title, "status": t.status} for t in tasks],
           "decisions": [{"id": str(d.id), "text": d.text, "session_id": str(d.session_id)} for d in decs], "passages": []}
    for h in hits:
        s = names.get(h.chunk.session_id) or db.get(RecordingSession, h.chunk.session_id)
        names[s.id] = s
        out["passages"].append({"session_id": str(s.id), "title": s.title, "occurred_at": s.occurred_at.isoformat() + "Z", "start_ms": h.chunk.start_ms,
                                "text": h.chunk.text[:300], "segment_id": h.chunk.segment_ids[0] if h.chunk.segment_ids else None})
    return out


# ------------------------------------------------------------------ memory
@router.get("/memory/entities")
def entities(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), type: Optional[str] = None, needs_review: Optional[bool] = None):
    q = select(MemoryEntity).where(MemoryEntity.workspace_id == ctx.workspace.id)
    if type:
        q = q.where(MemoryEntity.type == type)
    if needs_review is not None:
        q = q.where(MemoryEntity.needs_review == needs_review)
    return [{"id": str(e.id), "type": e.type, "name": e.name, "aliases": e.aliases, "needs_review": e.needs_review, "similar_to": (e.attributes or {}).get("similar_to", [])}
            for e in db.scalars(q.order_by(MemoryEntity.name))]


class EntityPatch(BaseModel):
    name: Optional[str] = None
    aliases: Optional[list[str]] = None
    needs_review: Optional[bool] = None


@router.patch("/memory/entities/{eid}")
def patch_entity(eid: str, body: EntityPatch, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    e = db.scalar(select(MemoryEntity).where(MemoryEntity.id == uuid.UUID(eid), MemoryEntity.workspace_id == ctx.workspace.id))
    if not e:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(e, k, v)
    db.commit()
    return {"id": eid, "name": e.name, "aliases": e.aliases, "needs_review": e.needs_review}


class MergeIn(BaseModel):
    into_id: uuid.UUID


@router.post("/memory/entities/{eid}/merge")
def merge_entity(eid: str, body: MergeIn, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    """Merging is always an explicit user action — never automatic."""
    a = db.scalar(select(MemoryEntity).where(MemoryEntity.id == uuid.UUID(eid), MemoryEntity.workspace_id == ctx.workspace.id))
    b = db.scalar(select(MemoryEntity).where(MemoryEntity.id == body.into_id, MemoryEntity.workspace_id == ctx.workspace.id))
    if not a or not b or a.id == b.id:
        raise HTTPException(404, "Entity not found")
    b.aliases = sorted(set([*(b.aliases or []), a.name, *(a.aliases or [])]))
    for sp in db.scalars(select(Speaker).where(Speaker.entity_id == a.id)):
        sp.entity_id = b.id
    db.delete(a)
    db.commit()
    return {"merged_into": str(b.id), "aliases": b.aliases}


@router.get("/memory/people/{name}")
def person_memory(name: str, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    """Everything permitted that mentions a person: tasks, commitments, decisions they participated in."""
    tasks = db.scalars(queries.tasks_q(ctx).where(ExtractedTask.assignee_name.ilike(f"%{name}%")).limit(100)).all()
    cms = db.scalars(queries.commitments_q(ctx).where(Commitment.person_name.ilike(f"%{name}%")).limit(100)).all()
    return {"name": name, "tasks": [{"id": str(t.id), "title": t.title, "status": t.status, "session_id": str(t.session_id) if t.session_id else None} for t in tasks],
            "commitments": [{"id": str(c.id), "text": c.text, "status": c.status, "session_id": str(c.session_id)} for c in cms]}


# ------------------------------------------------------------------ analytics (observed counts only)
@router.get("/analytics")
def analytics(days: int = Query(30, ge=1, le=366), ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    tz = tzinfo(ctx.user.timezone)
    today = datetime.now(tz).date()
    start = today - timedelta(days=days - 1)
    s0, s1 = to_utc(start, tz), to_utc(today + timedelta(days=1), tz)
    sess = db.scalars(select(RecordingSession).where(visible_session_filter(ctx), RecordingSession.exclude_from_processing.is_(False),
                                                     RecordingSession.occurred_at >= s0, RecordingSession.occurred_at < s1)).all()
    per_day: dict = {}
    by_kind: dict = {}
    for s in sess:
        d = local(s.occurred_at, tz).date().isoformat()
        e = per_day.setdefault(d, {"date": d, "sessions": 0, "duration_seconds": 0})
        e["sessions"] += 1
        e["duration_seconds"] += s.duration_seconds or 0
        k = by_kind.setdefault(s.kind, {"kind": s.kind, "sessions": 0, "duration_seconds": 0})
        k["sessions"] += 1
        k["duration_seconds"] += s.duration_seconds or 0
    ids = [s.id for s in sess]
    tasks = db.scalars(queries.tasks_q(ctx)).all()
    return {"note": "Observed counts from stored records. AI-detected counts are not verified. No productivity scoring is performed.",
            "range": {"start": start.isoformat(), "end": today.isoformat()}, "per_day": sorted(per_day.values(), key=lambda x: x["date"]),
            "by_kind": list(by_kind.values()),
            "tasks": {"created_in_range": sum(1 for t in tasks if s0 <= t.created_at < s1), "completed_in_range": sum(1 for t in tasks if t.completed_at and s0 <= t.completed_at < s1),
                      "open_now": sum(1 for t in tasks if t.status in queries.OPEN_TASK), "by_status": _count(t.status for t in tasks)},
            "decisions_in_range": db.scalar(select(func.count()).select_from(ExtractedDecision).where(ExtractedDecision.session_id.in_(ids))) if ids else 0,
            "open_commitments": db.scalar(select(func.count()).select_from(queries.commitments_q(ctx).where(Commitment.status == "open").subquery()))}


def _count(it):
    out: dict = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out


# ------------------------------------------------------------------ notifications
@router.get("/notifications")
def notifications(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), unread: bool = False):
    q = select(Notification).where(Notification.user_id == ctx.user.id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    return [{"id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "read": bool(n.read_at), "created_at": n.created_at.isoformat() + "Z"}
            for n in db.scalars(q.order_by(Notification.created_at.desc()).limit(50))]


@router.post("/notifications/read")
def read_all(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id == ctx.user.id, Notification.read_at.is_(None))):
        n.read_at = utcnow()
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ integrations (registry only; no connectors are built)
PROVIDERS = [
    {"id": "google_meet", "name": "Google Meet", "status": "not_implemented", "note": "Requires Google Workspace OAuth + Meet transcript APIs; not built."},
    {"id": "zoom", "name": "Zoom", "status": "not_implemented", "note": "Requires a Zoom OAuth app and recording-completed webhooks; not built."},
    {"id": "teams", "name": "Microsoft Teams", "status": "not_implemented", "note": "Requires Graph API application access; not built."},
    {"id": "upload", "name": "Manual upload", "status": "available", "note": "Audio files or transcript text — fully working."},
    {"id": "browser_mic", "name": "Browser microphone", "status": "available", "note": "Chunked capture with explicit consent — fully working."},
]


@router.get("/integrations")
def integrations(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    mine = {i.provider: i for i in db.scalars(select(Integration).where(Integration.workspace_id == ctx.workspace.id))}
    return [{**p, "connected": p["id"] in mine and mine[p["id"]].status == "connected"} for p in PROVIDERS]
