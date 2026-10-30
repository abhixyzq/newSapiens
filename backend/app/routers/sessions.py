import os
import uuid
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..access import Ctx, get_owned_session, get_visible_session, visible_session_filter, RANK
from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import audit, client_ip, require
from ..models import (AudioAsset, Commitment, ConsentRecord, ConversationSummary, ExtractedDecision, ExtractedTask, Insight,
                      KnowledgeChunk, KnowledgeDocument, Meeting, MeetingParticipant, Project, RecordingSession, Speaker,
                      TranscriptionSegment, User, Workspace)
from ..security import sign_audio, verify_audio_sig
from ..services import jobs, pipeline
from ..services.storage import get_storage, new_key

router = APIRouter(prefix="/api", tags=["capture"])
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".opus", ".flac", ".aac", ".amr", ".3gp", ".wma"}
LIVE = {"mic_desktop", "mic_mobile", "voice_note", "meeting_integration"}


class Consent(BaseModel):
    self_recording: bool = False       # live capture: "I am recording and have the right to"
    upload_rights: bool = False        # upload/import: "I have the right to process this recording"
    has_other_participants: bool = False
    participants_notified: bool = False
    statement: str = ""


class SessionIn(BaseModel):
    title: str = Field(default="Untitled session", max_length=300)
    kind: Literal["meeting", "conversation", "voice_note"] = "conversation"
    source: Literal["mic_desktop", "mic_mobile", "upload", "meeting_integration", "voice_note", "transcript_import"] = "upload"
    visibility: Literal["private", "shared"] = "private"
    occurred_at: Optional[datetime] = None
    project_id: Optional[uuid.UUID] = None
    language: Optional[str] = Field(default=None, max_length=20)
    exclude_from_processing: bool = False
    consent: Consent


def session_out(s: RecordingSession, db: Session, detail=False) -> dict:
    n_seg = db.scalar(select(func.count()).select_from(TranscriptionSegment).where(TranscriptionSegment.session_id == s.id))
    n_aud = db.scalar(select(func.count()).select_from(AudioAsset).where(AudioAsset.session_id == s.id, AudioAsset.deleted_at.is_(None), AudioAsset.chunk_index >= 0))
    out = {"id": str(s.id), "title": s.title, "kind": s.kind, "source": s.source, "status": s.status, "visibility": s.visibility,
           "occurred_at": s.occurred_at.isoformat() + "Z", "ended_at": s.ended_at.isoformat() + "Z" if s.ended_at else None,
           "duration_seconds": s.duration_seconds, "language": s.language, "project_id": str(s.project_id) if s.project_id else None,
           "exclude_from_processing": s.exclude_from_processing, "owner_id": str(s.owner_id), "error": s.error,
           "retention_until": s.retention_until.isoformat() + "Z" if s.retention_until else None,
           "segments": n_seg, "audio_chunks": n_aud, "workspace_id": str(s.workspace_id)}
    if detail:
        sm = db.scalar(select(ConversationSummary).where(ConversationSummary.session_id == s.id))
        out["summary"] = {"executive_summary": sm.executive_summary, "topics": sm.topics, "key_statements": sm.key_statements,
                          "outcomes": sm.outcomes, "generated_by": sm.generated_by} if sm else None
        out["speakers"] = [{"id": str(x.id), "label": x.label, "name": x.name} for x in db.scalars(select(Speaker).where(Speaker.session_id == s.id))]
        out["decisions"] = [dict(id=str(d.id), text=d.text, confidence=d.confidence, status=d.confirmation_status, timestamp_ms=d.timestamp_ms, segment_ids=d.segment_ids)
                            for d in db.scalars(select(ExtractedDecision).where(ExtractedDecision.session_id == s.id).order_by(ExtractedDecision.timestamp_ms))]
        out["tasks"] = [dict(id=str(t.id), title=t.title, assignee=t.assignee_name, due_date=t.due_date.isoformat() if t.due_date else None, due_text=t.due_text,
                             status=t.status, priority=t.priority, confidence=t.confidence, timestamp_ms=t.timestamp_ms)
                        for t in db.scalars(select(ExtractedTask).where(ExtractedTask.session_id == s.id, ExtractedTask.deleted_at.is_(None)).order_by(ExtractedTask.timestamp_ms))]
        out["commitments"] = [dict(id=str(c.id), person=c.person_name, text=c.text, status=c.status, confidence=c.confidence, timestamp_ms=c.timestamp_ms)
                              for c in db.scalars(select(Commitment).where(Commitment.session_id == s.id).order_by(Commitment.timestamp_ms))]
        out["insights"] = [dict(id=str(i.id), kind=i.kind, text=i.text, confidence=i.confidence, timestamp_ms=i.timestamp_ms)
                           for i in db.scalars(select(Insight).where(Insight.session_id == s.id).order_by(Insight.timestamp_ms))]
        out["processing"] = (s.meta or {}).get("extraction")
    return out


def _retention(ctx: Ctx) -> Optional[datetime]:
    days = ctx.workspace.audio_retention_days or ctx.user.audio_retention_days
    return utcnow() + timedelta(days=days) if days else None


@router.post("/sessions", status_code=201)
def create_session(body: SessionIn, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    c = body.consent
    if body.source in LIVE:
        if not c.self_recording:
            raise HTTPException(422, "Recording consent is required: confirm you are recording and are entitled to")
        if (body.kind == "meeting" or c.has_other_participants or body.source == "meeting_integration") and not c.participants_notified:
            raise HTTPException(422, "Other participants must be notified of the recording before it starts (participants_notified=true)")
    elif not c.upload_rights:
        raise HTTPException(422, "Confirm you have the right to process this recording/transcript (upload_rights=true)")
    if body.visibility == "shared" and ctx.workspace.type == "individual":
        raise HTTPException(422, "Individual workspaces cannot share content; use an organization or team workspace")
    if body.project_id and not db.scalar(select(Project.id).where(Project.id == body.project_id, Project.workspace_id == ctx.workspace.id, Project.deleted_at.is_(None))):
        raise HTTPException(404, "Project not found")
    when = body.occurred_at.replace(tzinfo=None) if body.occurred_at else utcnow()
    if body.occurred_at and body.occurred_at.tzinfo:
        from datetime import timezone as _tz
        when = body.occurred_at.astimezone(_tz.utc).replace(tzinfo=None)
    if when > utcnow() + timedelta(days=1):
        raise HTTPException(422, "occurred_at cannot be in the future")
    s = RecordingSession(workspace_id=ctx.workspace.id, owner_id=ctx.user.id, title=body.title.strip() or "Untitled session", kind=body.kind,
                         source=body.source, visibility=body.visibility, occurred_at=when, project_id=body.project_id, language=body.language,
                         exclude_from_processing=body.exclude_from_processing, status="created", meta={})
    db.add(s)
    db.flush()
    db.add(ConsentRecord(session_id=s.id, workspace_id=ctx.workspace.id, user_id=ctx.user.id,
                         consent_type="self_recording" if body.source in LIVE else "upload_rights", granted=True,
                         statement=c.statement[:2000], participants_notified=c.participants_notified, ip=client_ip(request)))
    audit(db, user=ctx.user, ws=ctx.workspace, action="session.create", rtype="session", rid=s.id, details={"source": body.source, "kind": body.kind}, ip=client_ip(request))
    db.commit()
    return session_out(s, db)


@router.get("/sessions")
def list_sessions(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), limit: int = Query(25, le=100), offset: int = 0,
                  status: Optional[str] = None, kind: Optional[str] = None, q: Optional[str] = None, project_id: Optional[uuid.UUID] = None,
                  date_from: Optional[datetime] = None, date_to: Optional[datetime] = None):
    base = select(RecordingSession).where(visible_session_filter(ctx))
    if status:
        base = base.where(RecordingSession.status == status)
    if kind:
        base = base.where(RecordingSession.kind == kind)
    if q:
        base = base.where(RecordingSession.title.ilike(f"%{q}%"))
    if project_id:
        base = base.where(RecordingSession.project_id == project_id)
    if date_from:
        base = base.where(RecordingSession.occurred_at >= date_from.replace(tzinfo=None))
    if date_to:
        base = base.where(RecordingSession.occurred_at < date_to.replace(tzinfo=None))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.order_by(RecordingSession.occurred_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [session_out(s, db) for s in rows]}


@router.get("/sessions/{sid}")
def get_session(sid: str, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    return session_out(get_visible_session(db, ctx, sid), db, detail=True)


class SessionPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    kind: Optional[Literal["meeting", "conversation", "voice_note"]] = None
    visibility: Optional[Literal["private", "shared"]] = None
    project_id: Optional[uuid.UUID] = None
    exclude_from_processing: Optional[bool] = None


def purge_derived(db: Session, sid):
    for M in (ExtractedDecision, Commitment, Insight, ConversationSummary, KnowledgeChunk, KnowledgeDocument):
        db.execute(delete(M).where(M.session_id == sid))
    db.execute(delete(ExtractedTask).where(ExtractedTask.session_id == sid, ExtractedTask.source == "ai", ExtractedTask.user_modified.is_(False)))
    db.execute(delete(Meeting).where(Meeting.session_id == sid))


@router.patch("/sessions/{sid}")
def patch_session(sid: str, body: SessionPatch, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    s = get_owned_session(db, ctx, sid)
    if s.owner_id != ctx.user.id:
        raise HTTPException(403, "Only the owner can change this session")
    if body.title:
        s.title = body.title
    if body.kind:
        s.kind = body.kind
    if body.visibility:
        if body.visibility == "shared" and ctx.workspace.type == "individual":
            raise HTTPException(422, "Individual workspaces cannot share content")
        s.visibility = body.visibility
        for t in db.scalars(select(ExtractedTask).where(ExtractedTask.session_id == s.id)):
            t.visibility = body.visibility
    if body.project_id:
        if not db.scalar(select(Project.id).where(Project.id == body.project_id, Project.workspace_id == ctx.workspace.id)):
            raise HTTPException(404, "Project not found")
        s.project_id = body.project_id
    if body.exclude_from_processing is not None and body.exclude_from_processing != s.exclude_from_processing:
        s.exclude_from_processing = body.exclude_from_processing
        if body.exclude_from_processing:  # sensitive: remove everything derived, keep nothing searchable
            purge_derived(db, s.id)
            s.status = "excluded"
        else:
            s.status = "uploaded"
    audit(db, user=ctx.user, ws=ctx.workspace, action="session.update", rtype="session", rid=s.id, details=body.model_dump(exclude_none=True, mode="json"), ip=client_ip(request))
    db.commit()
    return session_out(s, db)


def _event(s: RecordingSession, name: str):
    m = dict(s.meta or {})
    m.setdefault("events", []).append({"at": utcnow().isoformat(), "event": name})
    s.meta = m


def _own(db, ctx, sid) -> RecordingSession:
    s = get_owned_session(db, ctx, sid)
    if s.owner_id != ctx.user.id:
        raise HTTPException(403, "Only the owner can control this session")
    return s


@router.post("/sessions/{sid}/start")
def start(sid: str, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    s = _own(db, ctx, sid)
    if s.status != "created":
        raise HTTPException(409, f"Cannot start a session in status {s.status}")
    s.status = "recording"
    s.occurred_at = utcnow() if s.source in LIVE else s.occurred_at
    _event(s, "start")
    db.commit()
    return session_out(s, db)


@router.post("/sessions/{sid}/pause")
def pause(sid: str, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    s = _own(db, ctx, sid)
    if s.status != "recording":
        raise HTTPException(409, "Not recording")
    s.status = "paused"
    _event(s, "pause")
    db.commit()
    return session_out(s, db)


@router.post("/sessions/{sid}/resume")
def resume(sid: str, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    s = _own(db, ctx, sid)
    if s.status != "paused":
        raise HTTPException(409, "Not paused")
    s.status = "recording"
    _event(s, "resume")
    db.commit()
    return session_out(s, db)


def _finish(db: Session, s: RecordingSession):
    s.ended_at = utcnow()
    has_audio = db.scalar(select(func.count()).select_from(AudioAsset).where(AudioAsset.session_id == s.id, AudioAsset.deleted_at.is_(None)))
    _event(s, "stop")
    if s.exclude_from_processing:
        s.status = "excluded"
        db.commit()
        return
    if not has_audio and not db.scalar(select(func.count()).select_from(TranscriptionSegment).where(TranscriptionSegment.session_id == s.id)):
        s.status, s.error = "awaiting_transcription", "No audio received for this session."
        db.commit()
        return
    s.status, s.error = "uploaded", None
    db.commit()
    jobs.submit(pipeline.process_session, s.id)
    db.refresh(s)  # reflect what the worker has done (inline mode) in the response


@router.post("/sessions/{sid}/stop")
def stop(sid: str, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    s = _own(db, ctx, sid)
    if s.status not in ("recording", "paused", "created"):
        raise HTTPException(409, f"Cannot stop a session in status {s.status}")
    _finish(db, s)
    return session_out(s, db)


@router.get("/sessions/{sid}/upload")
def received_chunks(sid: str, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    """Resumable uploads: the client asks which chunks the server already has, then sends only the rest."""
    s = _own(db, ctx, sid) if ctx.role != "viewer" else get_visible_session(db, ctx, sid)
    rows = db.scalars(select(AudioAsset).where(AudioAsset.session_id == s.id, AudioAsset.deleted_at.is_(None), AudioAsset.chunk_index >= 0)).all()
    return {"received": sorted(a.chunk_index for a in rows), "sha256": {a.chunk_index: a.sha256 for a in rows}}


@router.post("/sessions/{sid}/upload", status_code=201)
def upload(sid: str, request: Request, file: UploadFile = File(...), chunk_index: int = Form(0), finalize: bool = Form(False),
           ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    s = _own(db, ctx, sid)
    if s.status in ("transcribing", "analyzing"):
        raise HTTPException(409, "Session is being processed")
    if chunk_index < 0 or chunk_index > 100_000:
        raise HTTPException(422, "bad chunk_index")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(415, f"Unsupported file type {ext or '(none)'}; allowed: {', '.join(sorted(AUDIO_EXT))}")
    if file.content_type and not (file.content_type.startswith(("audio/", "video/")) or file.content_type == "application/octet-stream"):
        raise HTTPException(415, "Unsupported content type")
    st = get_storage()
    key = new_key(s.workspace_id, s.id, ext)
    try:
        size, sha = st.put(key, file.file, get_settings().max_upload_mb * 1024 * 1024)
    except ValueError:
        raise HTTPException(413, f"File exceeds {get_settings().max_upload_mb} MB")
    if size == 0:
        st.delete(key)
        raise HTTPException(422, "Empty file")
    old = db.scalar(select(AudioAsset).where(AudioAsset.session_id == s.id, AudioAsset.chunk_index == chunk_index))
    if old and old.sha256 == sha and not old.deleted_at:  # idempotent retry of the same chunk
        st.delete(key)
        return {"chunk_index": chunk_index, "size_bytes": old.size_bytes, "sha256": sha, "duplicate": True}
    if old:
        st.delete(old.storage_key)
        db.delete(old)
        db.flush()
    a = AudioAsset(session_id=s.id, workspace_id=s.workspace_id, chunk_index=chunk_index, storage_key=key, filename=(file.filename or "")[:300],
                   mime=file.content_type or "application/octet-stream", size_bytes=size, sha256=sha)
    db.add(a)
    s.retention_until = s.retention_until or _retention(ctx)
    if s.status == "created" and s.source not in LIVE:
        s.status = "uploaded"
    audit(db, user=ctx.user, ws=ctx.workspace, action="session.upload", rtype="session", rid=s.id, details={"chunk": chunk_index, "bytes": size}, ip=client_ip(request))
    db.commit()
    if finalize:
        _finish(db, s)
    return {"chunk_index": chunk_index, "size_bytes": size, "sha256": sha, "duplicate": False, "status": s.status}


class TranscriptIn(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)


@router.post("/sessions/{sid}/transcript")
def import_transcript(sid: str, body: TranscriptIn, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    """Import an existing transcript (`[mm:ss] Speaker: text` lines). Skips STT and runs the intelligence pipeline."""
    s = _own(db, ctx, sid)
    items = pipeline.parse_transcript_text(body.text)
    if not items:
        raise HTTPException(422, "No transcript lines found")
    pipeline.save_segments(db, s, items)
    s.duration_seconds = s.duration_seconds or items[-1]["end_ms"] / 1000
    s.status, s.error, s.ended_at = "uploaded", None, s.ended_at or utcnow()
    db.commit()
    if not s.exclude_from_processing:
        jobs.submit(pipeline.process_session, s.id)
    db.refresh(s)
    return session_out(s, db)


@router.delete("/sessions/{sid}")
def delete_session(sid: str, request: Request, mode: Literal["audio", "everything"] = "everything",
                   ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    """mode=audio: delete the audio files only (transcript, tasks, decisions remain).
    mode=everything: delete audio, transcript and all derived data; the session row is soft-deleted (tombstone for audit)."""
    s = _own(db, ctx, sid)
    st = get_storage()
    for a in db.scalars(select(AudioAsset).where(AudioAsset.session_id == s.id)):
        st.delete(a.storage_key)
        db.delete(a)
    if mode == "everything":
        st.delete_prefix(f"{s.workspace_id}/{s.id}")
        purge_derived(db, s.id)
        db.execute(delete(ExtractedTask).where(ExtractedTask.session_id == s.id))
        db.execute(delete(TranscriptionSegment).where(TranscriptionSegment.session_id == s.id))
        db.execute(delete(Speaker).where(Speaker.session_id == s.id))
        s.deleted_at, s.title, s.status = utcnow(), "[deleted]", "deleted"
    else:
        s.meta = {**(s.meta or {}), "audio_deleted_at": utcnow().isoformat()}
    audit(db, user=ctx.user, ws=ctx.workspace, action=f"session.delete_{mode}", rtype="session", rid=s.id, ip=client_ip(request))
    db.commit()
    return {"deleted": mode, "id": sid}


@router.get("/sessions/{sid}/audio-url")
def audio_url(sid: str, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    s = get_visible_session(db, ctx, sid)
    assets = db.scalars(select(AudioAsset).where(AudioAsset.session_id == s.id, AudioAsset.deleted_at.is_(None)).order_by(AudioAsset.chunk_index.desc())).all()
    if not assets:
        raise HTTPException(404, "No audio stored for this session")
    real = [x for x in assets if x.chunk_index >= 0]
    a = next((x for x in assets if x.chunk_index == -1), None) or (real[0] if len(real) == 1 else None)
    if not a:
        raise HTTPException(409, "Audio is still being assembled; try again after processing")
    return {"url": sign_audio(a.id, ctx.user.id), "expires_in": get_settings().signed_url_ttl_seconds, "mime": a.mime}


@router.get("/audio/{asset_id}")
def stream_audio(asset_id: str, uid: str, exp: int, sig: str, db: Session = Depends(get_db)):
    if not verify_audio_sig(asset_id, uid, exp, sig):
        raise HTTPException(403, "Invalid or expired link")
    try:
        a = db.get(AudioAsset, uuid.UUID(asset_id))
        user = db.get(User, uuid.UUID(uid))
    except ValueError:
        raise HTTPException(404, "Not found")
    if not a or a.deleted_at or not user or user.deleted_at:
        raise HTTPException(404, "Not found")
    # re-check live permissions: a signed link never outlives a revoked membership or unshared session
    from ..access import workspace_role
    s = db.get(RecordingSession, a.session_id)
    role = workspace_role(db, user.id, s.workspace_id) if s and not s.deleted_at else None
    if not role or not (s.owner_id == user.id or s.visibility == "shared"):
        raise HTTPException(404, "Not found")
    p = get_storage().path(a.storage_key)
    if not p.exists():
        raise HTTPException(404, "Audio file is gone")
    return FileResponse(p, media_type=a.mime, headers={"Cache-Control": "private, no-store"})
