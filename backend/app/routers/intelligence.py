import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..access import Ctx, get_owned_session, get_visible_session
from ..db import get_db
from ..deps import audit, client_ip, require
from ..models import (Commitment, ExtractedDecision, ExtractedTask, Insight, RecordingSession, Speaker, TranscriptionSegment)
from ..services import jobs, pipeline, queries

router = APIRouter(prefix="/api", tags=["intelligence"])


# ---------------------------------------------------------------- transcripts
@router.get("/sessions/{sid}/transcript")
def transcript(sid: str, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), limit: int = Query(500, le=2000), offset: int = 0):
    s = get_visible_session(db, ctx, sid)
    if s.exclude_from_processing and s.owner_id != ctx.user.id:
        raise HTTPException(404, "Not found")
    names = {x.id: x for x in db.scalars(select(Speaker).where(Speaker.session_id == s.id))}
    rows = db.scalars(select(TranscriptionSegment).where(TranscriptionSegment.session_id == s.id).order_by(TranscriptionSegment.idx).limit(limit).offset(offset)).all()
    return {"session_id": str(s.id), "segments": [{"id": str(r.id), "idx": r.idx, "start_ms": r.start_ms, "end_ms": r.end_ms, "text": r.text,
                                                    "original_text": r.original_text if r.edited else None, "source_text": r.source_text, "edited": r.edited, "confidence": r.confidence,
                                                    "speaker_id": str(r.speaker_id) if r.speaker_id else None,
                                                    "speaker": names[r.speaker_id].name if r.speaker_id in names else None, "language": r.language} for r in rows]}


class SegPatch(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


@router.patch("/transcripts/{seg_id}")
def edit_segment(seg_id: str, body: SegPatch, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    try:
        seg = db.get(TranscriptionSegment, uuid.UUID(seg_id))
    except ValueError:
        seg = None
    if not seg:
        raise HTTPException(404, "Not found")
    s = get_owned_session(db, ctx, seg.session_id)
    if s.owner_id != ctx.user.id:
        raise HTTPException(403, "Only the owner can edit the transcript")
    seg.text, seg.edited = body.text.strip(), True  # original_text is preserved
    audit(db, user=ctx.user, ws=ctx.workspace, action="transcript.edit", rtype="segment", rid=seg.id, ip=client_ip(request))
    db.commit()
    jobs.submit(pipeline.reindex_job, s.id)
    return {"id": str(seg.id), "text": seg.text, "original_text": seg.original_text, "edited": True}


class SpeakerPatch(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


@router.patch("/speakers/{speaker_id}")
def rename_speaker(speaker_id: str, body: SpeakerPatch, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    try:
        sp = db.get(Speaker, uuid.UUID(speaker_id))
    except ValueError:
        sp = None
    if not sp:
        raise HTTPException(404, "Not found")
    s = get_owned_session(db, ctx, sp.session_id)
    if s.owner_id != ctx.user.id:
        raise HTTPException(403, "Only the owner can rename speakers")
    sp.display_name = body.display_name.strip()
    e = pipeline.resolve_person(db, s.workspace_id, sp.display_name, s.id)
    sp.entity_id = e.id if e else None
    db.commit()
    jobs.submit(pipeline.reindex_job, s.id)
    return {"id": str(sp.id), "name": sp.name}


# ---------------------------------------------------------------- processing
@router.post("/intelligence/process/{sid}", status_code=202)
def process(sid: str, request: Request, retranscribe: bool = False, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    """(Re)run the pipeline. Idempotent: AI rows are replaced, human-edited rows are kept."""
    s = get_owned_session(db, ctx, sid)
    if s.owner_id != ctx.user.id:
        raise HTTPException(403, "Only the owner can reprocess")
    if s.exclude_from_processing:
        raise HTTPException(409, "This session is excluded from processing")
    s.status = "uploaded"
    audit(db, user=ctx.user, ws=ctx.workspace, action="session.process", rtype="session", rid=s.id, ip=client_ip(request))
    db.commit()
    jobs.submit(pipeline.process_session, s.id, force=True, retranscribe=retranscribe)
    db.refresh(s)
    return {"id": str(s.id), "status": s.status}


@router.post("/intelligence/reindex", status_code=202)
def reindex_all(ctx: Ctx = Depends(require("admin")), db: Session = Depends(get_db)):
    """Re-embed everything in this workspace (after changing EMBEDDING_PROVIDER)."""
    ids = db.scalars(select(RecordingSession.id).where(RecordingSession.workspace_id == ctx.workspace.id, RecordingSession.deleted_at.is_(None),
                                                       RecordingSession.status == "ready")).all()
    for i in ids:
        jobs.submit(pipeline.reindex_job, i)
    return {"queued": len(ids)}


# ---------------------------------------------------------------- lists
def _page(q, db, limit, offset, ser):
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    return {"total": total, "items": [ser(r) for r in db.scalars(q.limit(limit).offset(offset))]}


@router.get("/intelligence/decisions")
def decisions(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), limit: int = Query(50, le=200), offset: int = 0,
              date_from: Optional[datetime] = None, date_to: Optional[datetime] = None, project_id: Optional[uuid.UUID] = None, q: Optional[str] = None):
    qq = queries.decisions_q(ctx)
    if project_id:
        qq = qq.where(ExtractedDecision.project_id == project_id)
    if q:
        qq = qq.where(ExtractedDecision.text.ilike(f"%{q}%"))
    if date_from or date_to:
        qq = qq.join(RecordingSession, RecordingSession.id == ExtractedDecision.session_id)
        if date_from:
            qq = qq.where(RecordingSession.occurred_at >= date_from.replace(tzinfo=None))
        if date_to:
            qq = qq.where(RecordingSession.occurred_at < date_to.replace(tzinfo=None))
    return _page(qq.order_by(ExtractedDecision.created_at.desc()), db, limit, offset,
                 lambda d: dict(id=str(d.id), text=d.text, context=d.context, participants=d.participants, confidence=d.confidence,
                                status=d.confirmation_status, session_id=str(d.session_id), timestamp_ms=d.timestamp_ms, segment_ids=d.segment_ids))


class DecisionPatch(BaseModel):
    status: Literal["confirmed", "needs_review", "rejected"]


@router.patch("/intelligence/decisions/{did}")
def review_decision(did: str, body: DecisionPatch, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    d = db.scalar(queries.decisions_q(ctx).where(ExtractedDecision.id == uuid.UUID(did)))
    if not d:
        raise HTTPException(404, "Not found")
    s = get_owned_session(db, ctx, d.session_id)
    if s.owner_id != ctx.user.id:
        raise HTTPException(403, "Only the session owner can review")
    d.confirmation_status, d.user_modified = body.status, True
    db.commit()
    return {"id": did, "status": d.confirmation_status}


@router.get("/intelligence/tasks")
def ai_tasks(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), limit: int = Query(50, le=200), offset: int = 0, session_id: Optional[uuid.UUID] = None):
    qq = queries.tasks_q(ctx).where(ExtractedTask.source == "ai")
    if session_id:
        qq = qq.where(ExtractedTask.session_id == session_id)
    from .tasks import task_out
    return _page(qq.order_by(ExtractedTask.created_at.desc()), db, limit, offset, task_out)


@router.get("/intelligence/commitments")
def commitments(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), status: Optional[str] = "open", person: Optional[str] = None,
                limit: int = Query(100, le=300), offset: int = 0):
    qq = queries.commitments_q(ctx)
    if status:
        qq = qq.where(Commitment.status == status)
    if person:
        qq = qq.where(Commitment.person_name.ilike(f"%{person}%"))
    return _page(qq.order_by(Commitment.created_at.desc()), db, limit, offset,
                 lambda c: dict(id=str(c.id), person=c.person_name, text=c.text, kind=c.kind, due_date=c.due_date.isoformat() if c.due_date else None,
                                due_text=c.due_text, status=c.status, confidence=c.confidence, needs_review=c.needs_review,
                                session_id=str(c.session_id), timestamp_ms=c.timestamp_ms))


class CommitmentPatch(BaseModel):
    status: Optional[Literal["open", "fulfilled", "cancelled"]] = None
    person_name: Optional[str] = None
    needs_review: Optional[bool] = None


@router.patch("/intelligence/commitments/{cid}")
def patch_commitment(cid: str, body: CommitmentPatch, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    c = db.scalar(queries.commitments_q(ctx).where(Commitment.id == uuid.UUID(cid)))
    if not c:
        raise HTTPException(404, "Not found")
    s = get_owned_session(db, ctx, c.session_id)
    if s.owner_id != ctx.user.id:
        raise HTTPException(403, "Only the session owner can edit")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    c.user_modified = True
    db.commit()
    return {"id": cid, "status": c.status}


@router.get("/intelligence/insights")
def insights(kind: Optional[Literal["risk", "idea", "question"]] = None, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db),
             limit: int = Query(100, le=300), offset: int = 0):
    qq = queries.insights_q(ctx)
    if kind:
        qq = qq.where(Insight.kind == kind)
    return _page(qq.order_by(Insight.created_at.desc()), db, limit, offset,
                 lambda i: dict(id=str(i.id), kind=i.kind, text=i.text, confidence=i.confidence, session_id=str(i.session_id), timestamp_ms=i.timestamp_ms))
