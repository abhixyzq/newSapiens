"""Stateless processing for the wearable companion app: audio in -> transcript + extraction out.
Nothing is written to the database or object store; the upload only touches a temp file that is always removed."""
import os
import shutil
import tempfile
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import utcnow
from ..deps import require_api_token
from ..services import day, extraction, llm, stt
from .sessions import AUDIO_EXT

router = APIRouter(prefix="/api", tags=["process"])


@router.post("/process", dependencies=[Depends(require_api_token)])
def process(file: UploadFile = File(...), occurred_at: Optional[str] = Form(None)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(415, f"Unsupported file type {ext or '(none)'}; allowed: {', '.join(sorted(AUDIO_EXT))}")
    limit = get_settings().max_upload_mb * 1024 * 1024
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(file.file, out)
        size = os.path.getsize(path)
        if size > limit:
            raise HTTPException(413, f"File exceeds {get_settings().max_upload_mb} MB")
        if size == 0:
            raise HTTPException(422, "Empty file")
        try:
            res = stt.get_stt().transcribe(path)
        except (stt.ProviderNotConfigured, stt.ProviderError) as e:
            raise HTTPException(503, str(e))
        except RuntimeError as e:
            raise HTTPException(422, str(e))
    finally:
        os.unlink(path)

    try:
        ref = (datetime.fromisoformat(occurred_at) if occurred_at else utcnow()).date()
    except ValueError:
        raise HTTPException(422, "occurred_at must be ISO 8601")
    segs = [extraction.Seg(str(i), i, g.start_ms, g.speaker, g.text, g.end_ms) for i, g in enumerate(res.segments)]
    out = {"language": res.language, "duration_s": res.duration_s,
           "segments": [{"idx": i, "start_ms": g.start_ms, "end_ms": g.end_ms, "speaker": g.speaker, "text": g.text,
                         "source_text": g.source_text, "confidence": g.confidence} for i, g in enumerate(res.segments)]}
    if not segs:  # silence: nothing to extract
        return {**out, "engine": None, "note": "No speech detected", "summary": None, "decisions": [], "tasks": [],
                "commitments": [], "insights": [], "events": [], "processed_at": utcnow().isoformat() + "Z"}
    try:
        ex, note = extraction.extract(segs, ref, [])
    except stt.ProviderError as e:
        raise HTTPException(503, str(e))
    return {**out, "engine": ex["engine"], "note": note, "summary": ex["summary"], "decisions": ex["decisions"],
            "tasks": ex["tasks"], "commitments": ex["commitments"], "insights": ex["insights"], "events": ex["events"],
            "processed_at": utcnow().isoformat() + "Z"}


ASK_BUDGET = 16000  # chars of memories per question
ASK_PROMPT = ("You are MindCapture, the user's AI second brain. MEMORIES are notes from their own recorded conversations, newest "
              "first. If the question is about their life, work, meetings, people or tasks, answer from MEMORIES and mention the "
              "date of each memory you use; if MEMORIES don't cover it, say so in one short line. Anything else, answer from "
              "general knowledge. Be concise. Plain text only, no markdown; short '- ' lists are fine.")


class AskTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    today: Optional[str] = Field(None, max_length=40)  # phone's local date, so "yesterday" means the user's yesterday
    memories: list[str] = Field(default=[], max_length=200)
    history: list[AskTurn] = Field(default=[], max_length=40)


@router.post("/ask", dependencies=[Depends(require_api_token)])
def ask(body: AskBody):
    """Stateless Q&A for the phone app: its memories live on the device, so it sends them along with the question."""
    picked, used = [], 0
    for m in body.memories:  # ponytail: newest-first char budget (fits Groq's free tier); add retrieval once memories outgrow it
        m = m[:3000]
        if used + len(m) > ASK_BUDGET:
            break
        picked.append(m)
        used += len(m)
    hist = "\n".join(f"{t.role.upper()}: {t.content[:2000]}" for t in body.history[-6:])
    user = (f"TODAY: {body.today or utcnow().date()}\n\nMEMORIES:\n" + ("\n\n---\n\n".join(picked) or "(none yet)")
            + (f"\n\nCONVERSATION SO FAR:\n{hist}" if hist else "") + f"\n\nQUESTION:\n{body.question}")
    try:
        return {"answer": llm.get_llm().complete(ASK_PROMPT, user, max_tokens=1000).strip()}
    except (stt.ProviderNotConfigured, stt.ProviderError) as e:
        raise HTTPException(503, str(e))


class DayBody(BaseModel):
    today: str = Field(max_length=40)
    items: list[str] = Field(min_length=1, max_length=200)


@router.post("/day", dependencies=[Depends(require_api_token)])
def day_summary(body: DayBody):
    """Stateless day consolidation for the phone: it sends that day's per-capture summaries and event titles."""
    try:
        return day.summarize_day([i[:2000] for i in body.items], body.today)
    except (stt.ProviderNotConfigured, stt.ProviderError, ValueError) as e:
        raise HTTPException(503, str(e))
