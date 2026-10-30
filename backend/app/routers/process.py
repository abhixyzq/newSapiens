"""Stateless processing for the wearable companion app: audio in -> transcript + extraction out.
Nothing is written to the database or object store; the upload only touches a temp file that is always removed."""
import os
import shutil
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..config import get_settings
from ..db import utcnow
from ..deps import require_api_token
from ..services import extraction, stt
from .sessions import AUDIO_EXT

router = APIRouter(prefix="/api", tags=["process"])


@router.post("/process", dependencies=[Depends(require_api_token)])
def process(file: UploadFile = File(...), language: Optional[str] = Form(None), occurred_at: Optional[str] = Form(None)):
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
            res = stt.get_stt().transcribe(path, language)
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
    segs = [extraction.Seg(str(i), i, g.start_ms, g.speaker, g.text) for i, g in enumerate(res.segments)]
    out = {"language": res.language, "duration_s": res.duration_s,
           "segments": [{"idx": i, "start_ms": g.start_ms, "end_ms": g.end_ms, "speaker": g.speaker, "text": g.text,
                         "confidence": g.confidence} for i, g in enumerate(res.segments)]}
    if not segs:  # silence: nothing to extract
        return {**out, "engine": None, "note": "No speech detected", "summary": None, "decisions": [], "tasks": [],
                "commitments": [], "insights": []}
    try:
        ex, note = extraction.extract(segs, ref, [])
    except stt.ProviderError as e:
        raise HTTPException(503, str(e))
    return {**out, "engine": ex["engine"], "note": note, "summary": ex["summary"], "decisions": ex["decisions"],
            "tasks": ex["tasks"], "commitments": ex["commitments"], "insights": ex["insights"]}
