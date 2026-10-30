import json
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import Ctx, visible_session_filter
from ..db import get_db, utcnow
from ..deps import audit, require
from ..models import AudioAsset, ChatCitation, ChatMessage, ChatSession, RecordingSession
from ..security import sign_audio
from ..services import rag

router = APIRouter(prefix="/api/chat", tags=["ask-sapiens"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    chat_id: Optional[uuid.UUID] = None
    regenerate: bool = False


def _chat(db: Session, ctx: Ctx, cid) -> ChatSession:
    c = db.scalar(select(ChatSession).where(ChatSession.id == cid, ChatSession.user_id == ctx.user.id,
                                            ChatSession.workspace_id == ctx.workspace.id, ChatSession.deleted_at.is_(None)))
    if not c:
        raise HTTPException(404, "Chat not found")
    return c


def _cite_out(db: Session, ctx: Ctx, c: ChatCitation) -> dict:
    """Citations are re-authorised on every read: a source that has since been unshared or deleted comes back redacted."""
    out = {"ref": c.ref, "type": c.record_type, "label": c.label, "snippet": c.snippet, "start_ms": c.start_ms,
           "session_id": str(c.session_id) if c.session_id else None, "segment_id": str(c.segment_id) if c.segment_id else None,
           "record_id": str(c.record_id) if c.record_id else None, "audio_url": None, "available": True}
    s = db.scalar(select(RecordingSession).where(RecordingSession.id == c.session_id, visible_session_filter(ctx))) if c.session_id else None
    if not s:
        return {**out, "snippet": "", "label": "Source no longer available", "available": False, "session_id": None, "segment_id": None}
    a = db.scalars(select(AudioAsset).where(AudioAsset.session_id == s.id, AudioAsset.deleted_at.is_(None)).order_by(AudioAsset.chunk_index.desc())).all()
    real = [x for x in a if x.chunk_index >= 0]
    pick = next((x for x in a if x.chunk_index == -1), None) or (real[0] if len(real) == 1 else None)
    if pick:
        out["audio_url"] = sign_audio(pick.id, ctx.user.id)
        out["audio_start_s"] = (c.start_ms or 0) / 1000
    return out


def _msg_out(db: Session, ctx: Ctx, m: ChatMessage) -> dict:
    cites = db.scalars(select(ChatCitation).where(ChatCitation.message_id == m.id)).all()
    meta = {k: v for k, v in (m.meta or {}).items() if k in ("found", "mode", "clarify")}
    return {"id": str(m.id), "role": m.role, "content": m.content, "created_at": m.created_at.isoformat() + "Z", "meta": meta,
            "citations": sorted((_cite_out(db, ctx, c) for c in cites), key=lambda x: int(x["ref"][1:]))}


def _answer(db: Session, ctx: Ctx, chat: ChatSession, question: str, prev_assistant_meta: Optional[dict]) -> ChatMessage:
    res = rag.answer(db, ctx, question, prev_assistant_meta)
    m = ChatMessage(chat_id=chat.id, role="assistant", content=res["content"], meta=res["meta"])
    db.add(m)
    db.flush()
    for e in res["evidence"]:
        db.add(ChatCitation(message_id=m.id, ref=e.ref, session_id=e.session_id, record_type=e.type, record_id=e.record_id,
                            segment_id=e.segment_id, start_ms=e.start_ms, label=e.label, snippet=e.text[:500]))
    chat.updated_at = utcnow()
    return m


def _prev_meta(db: Session, chat: ChatSession, before=None) -> Optional[dict]:
    q = select(ChatMessage).where(ChatMessage.chat_id == chat.id, ChatMessage.role == "assistant")
    if before:
        q = q.where(ChatMessage.created_at < before)
    m = db.scalars(q.order_by(ChatMessage.created_at.desc()).limit(1)).first()
    return m.meta if m else None


def _run(db: Session, ctx: Ctx, body: ChatIn):
    if body.chat_id:
        chat = _chat(db, ctx, body.chat_id)
    else:
        chat = ChatSession(workspace_id=ctx.workspace.id, user_id=ctx.user.id, title=body.message[:80])
        db.add(chat)
        db.flush()
    if body.regenerate:
        last_user = db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat.id, ChatMessage.role == "user").order_by(ChatMessage.created_at.desc()).limit(1)).first()
        if not last_user:
            raise HTTPException(409, "Nothing to regenerate")
        last_a = db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat.id, ChatMessage.role == "assistant").order_by(ChatMessage.created_at.desc()).limit(1)).first()
        before = last_user.created_at
        if last_a and last_a.created_at >= last_user.created_at:
            for c in db.scalars(select(ChatCitation).where(ChatCitation.message_id == last_a.id)):
                db.delete(c)
            db.delete(last_a)
            db.flush()
        question, prev = last_user.content, _prev_meta(db, chat, before)
    else:
        prev = _prev_meta(db, chat)
        question = body.message
        db.add(ChatMessage(chat_id=chat.id, role="user", content=question))
        db.flush()
    m = _answer(db, ctx, chat, question, prev)
    audit(db, user=ctx.user, ws=ctx.workspace, action="chat.ask", rtype="chat", rid=chat.id, details={"found": m.meta.get("found")})
    db.commit()
    return chat, m


@router.post("")
def ask(body: ChatIn, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    chat, m = _run(db, ctx, body)
    return {"chat_id": str(chat.id), "title": chat.title, "message": _msg_out(db, ctx, m)}


@router.post("/stream")
def ask_stream(body: ChatIn, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    """Server-sent events. The answer is generated and citation-verified first, then streamed token-group by token-group
    (so an unverified answer is never shown to the user, at the cost of first-token latency)."""
    chat, m = _run(db, ctx, body)
    msg = _msg_out(db, ctx, m)

    def gen():
        yield f"event: meta\ndata: {json.dumps({'chat_id': str(chat.id), 'title': chat.title})}\n\n"
        for piece in re.findall(r"\S+\s*|\n", m.content):
            yield f"event: token\ndata: {json.dumps(piece)}\n\n"
        yield f"event: done\ndata: {json.dumps(msg)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/history")
def history(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), q: Optional[str] = None, limit: int = Query(50, le=200), offset: int = 0):
    qq = select(ChatSession).where(ChatSession.user_id == ctx.user.id, ChatSession.workspace_id == ctx.workspace.id, ChatSession.deleted_at.is_(None))
    if q:
        hit = select(ChatMessage.chat_id).where(ChatMessage.content.ilike(f"%{q}%"))
        qq = qq.where(ChatSession.title.ilike(f"%{q}%") | ChatSession.id.in_(hit))
    rows = db.scalars(qq.order_by(ChatSession.updated_at.desc()).limit(limit).offset(offset)).all()
    return [{"id": str(c.id), "title": c.title, "updated_at": c.updated_at.isoformat() + "Z"} for c in rows]


@router.get("/suggestions")
def suggestions(ctx: Ctx = Depends(require("viewer"))):
    return ["What happened today?", "Show all pending commitments", "Summarize my previous seven days",
            "What were the important decisions this month?", "Compare this week's progress with last week",
            "What tasks were assigned to me?"]


@router.get("/{cid}")
def get_chat(cid: uuid.UUID, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    c = _chat(db, ctx, cid)
    msgs = db.scalars(select(ChatMessage).where(ChatMessage.chat_id == c.id).order_by(ChatMessage.created_at, ChatMessage.role.desc())).all()
    return {"id": str(c.id), "title": c.title, "messages": [_msg_out(db, ctx, m) for m in msgs]}


@router.delete("/{cid}")
def delete_chat(cid: uuid.UUID, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    c = _chat(db, ctx, cid)
    c.deleted_at = utcnow()
    db.commit()
    return {"deleted": str(cid)}


@router.get("/{cid}/messages/{mid}/pdf")
def export_pdf(cid: uuid.UUID, mid: uuid.UUID, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    """Export one answer (with its sources) as a PDF."""
    c = _chat(db, ctx, cid)
    m = db.scalar(select(ChatMessage).where(ChatMessage.id == mid, ChatMessage.chat_id == c.id, ChatMessage.role == "assistant"))
    if not m:
        raise HTTPException(404, "Message not found")
    q = db.scalars(select(ChatMessage).where(ChatMessage.chat_id == c.id, ChatMessage.role == "user", ChatMessage.created_at <= m.created_at)
                   .order_by(ChatMessage.created_at.desc()).limit(1)).first()
    out = _msg_out(db, ctx, m)
    from ..services.answer_pdf import render
    return Response(render(c.title, q.content if q else "", out), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="sapiens-answer.pdf"'})
