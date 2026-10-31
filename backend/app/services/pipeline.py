"""capture -> transcribe -> understand -> remember. Idempotent: re-running replaces AI-derived rows but keeps anything
a human touched (user_modified / manual / reviewed)."""
import re
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import SessionLocal, utcnow
from ..models import (AudioAsset, Commitment, ConversationSummary, ExtractedDecision,
                      ExtractedTask, Insight, KnowledgeChunk, KnowledgeDocument, MemoryEntity, Meeting,
                      MeetingParticipant, Notification, Project, RecordingSession, Speaker, TimelineEvent,
                      TranscriptionSegment, User, Workspace)
from . import day, embeddings, extraction, stt
from .rag import local, tzinfo
from .storage import get_storage

TRANSCRIPT_LINE = re.compile(r"^\s*(?:\[?(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})\]?\s*)?(?:(?P<sp>[^:\n]{1,40}):\s+)?(?P<text>.+)$")


def parse_transcript_text(raw: str) -> list[dict]:
    """Manual transcript import. Lines like `[00:01:23] Rahul: text`, `Rahul: text` or bare text."""
    out, t = [], 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        m = TRANSCRIPT_LINE.match(line)
        text = m["text"].strip()
        sp = m["sp"].strip() if m["sp"] else None
        if m["ts"]:
            parts = [int(x) for x in m["ts"].split(":")]
            t = ((parts[0] * 3600 + parts[1] * 60 + parts[2]) if len(parts) == 3 else parts[0] * 60 + parts[1]) * 1000
        out.append({"start_ms": t, "speaker": sp, "text": text})
        t += max(2000, len(text.split()) * 400)
    for i, s in enumerate(out):
        s["end_ms"] = out[i + 1]["start_ms"] if i + 1 < len(out) and out[i + 1]["start_ms"] > s["start_ms"] else s["start_ms"] + 2000
    return out


def save_segments(db: Session, s: RecordingSession, items: list[dict], language=None):
    db.execute(delete(TranscriptionSegment).where(TranscriptionSegment.session_id == s.id))
    existing = {sp.label: sp for sp in db.scalars(select(Speaker).where(Speaker.session_id == s.id))}
    for i, it in enumerate(items):
        sp = None
        if it.get("speaker"):
            sp = existing.get(it["speaker"])
            if not sp:
                sp = existing[it["speaker"]] = Speaker(session_id=s.id, label=it["speaker"])
                db.add(sp)
                db.flush()
        db.add(TranscriptionSegment(session_id=s.id, workspace_id=s.workspace_id, idx=i, start_ms=it["start_ms"],
                                    end_ms=it["end_ms"], speaker_id=sp.id if sp else None, text=it["text"],
                                    original_text=it["text"], confidence=it.get("confidence"),
                                    language=it.get("language") or language))
    db.flush()


def assemble_audio(db: Session, s: RecordingSession) -> Optional[str]:
    """Chunks (index >= 0) are concatenated in order into one file, registered as asset chunk_index=-1 (used for
    playback and STT). Chunks must be a continuous byte stream (MediaRecorder output or a split file)."""
    assets = db.scalars(select(AudioAsset).where(AudioAsset.session_id == s.id, AudioAsset.deleted_at.is_(None),
                                                 AudioAsset.chunk_index >= 0).order_by(AudioAsset.chunk_index)).all()
    if not assets:
        return None
    store = get_storage()
    if len(assets) == 1:
        return str(store.path(assets[0].storage_key))
    ext = assets[0].storage_key[assets[0].storage_key.rfind("."):]
    key = f"{s.workspace_id}/{s.id}/_assembled{ext}"
    out = store.path(key)
    with open(out, "wb") as w:
        for a in assets:
            with open(store.path(a.storage_key), "rb") as r:
                while c := r.read(1 << 20):
                    w.write(c)
    old = db.scalar(select(AudioAsset).where(AudioAsset.session_id == s.id, AudioAsset.chunk_index == -1))
    if old:
        old.storage_key, old.size_bytes = key, out.stat().st_size
    else:
        db.add(AudioAsset(session_id=s.id, workspace_id=s.workspace_id, chunk_index=-1, storage_key=key, filename="assembled" + ext,
                          mime=assets[0].mime, size_bytes=out.stat().st_size, sha256=""))
    db.flush()
    return str(out)


def load_segs(db: Session, session_id) -> list[extraction.Seg]:
    names = {sp.id: sp.name for sp in db.scalars(select(Speaker).where(Speaker.session_id == session_id))}
    rows = db.scalars(select(TranscriptionSegment).where(TranscriptionSegment.session_id == session_id)
                      .order_by(TranscriptionSegment.idx)).all()
    return [extraction.Seg(str(r.id), r.idx, r.start_ms, names.get(r.speaker_id), r.text, r.end_ms) for r in rows]


# -------------------------------------------------------------------------------------------- entities
def _norm(n: str) -> str:
    return re.sub(r"\s+", " ", n.strip().lower())


def resolve_person(db: Session, ws_id, name: str, session_id=None) -> Optional[MemoryEntity]:
    """Exact (case-insensitive) name/alias match only. Similar-but-different names are kept SEPARATE and flagged
    for review — never auto-merged."""
    if not name or _norm(name) in ("unknown", ""):
        return None
    ents = db.scalars(select(MemoryEntity).where(MemoryEntity.workspace_id == ws_id, MemoryEntity.type == "person")).all()
    for e in ents:
        if _norm(e.name) == _norm(name) or _norm(name) in [_norm(a) for a in e.aliases or []]:
            return e
    first = _norm(name).split()[0]
    similar = [str(e.id) for e in ents if _norm(e.name).split()[0] == first or first.startswith(_norm(e.name).split()[0])]
    e = MemoryEntity(workspace_id=ws_id, type="person", name=name.strip(), needs_review=bool(similar),
                     attributes={"similar_to": similar} if similar else {})
    db.add(e)
    db.flush()
    return e


# -------------------------------------------------------------------------------------------- indexing
def chunk_segments(rows: list[TranscriptionSegment], names: dict, max_chars=700, max_gap_ms=90_000):
    chunks, cur = [], []

    def flush():
        if cur:
            chunks.append(list(cur))
    size = 0
    for r in rows:
        line_len = len(r.text) + 12
        if cur and (size + line_len > max_chars or r.start_ms - cur[-1].end_ms > max_gap_ms):
            flush()
            cur[:] = cur[-1:]  # one-segment overlap keeps context across boundaries
            size = sum(len(x.text) + 12 for x in cur)
        cur.append(r)
        size += line_len
    flush()
    return chunks


def reindex_session(db: Session, s: RecordingSession):
    db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.session_id == s.id))
    db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.session_id == s.id))
    if s.exclude_from_processing or s.deleted_at:
        return
    names = {sp.id: sp.name for sp in db.scalars(select(Speaker).where(Speaker.session_id == s.id))}
    rows = db.scalars(select(TranscriptionSegment).where(TranscriptionSegment.session_id == s.id)
                      .order_by(TranscriptionSegment.idx)).all()
    docs = []
    if rows:
        doc = KnowledgeDocument(workspace_id=s.workspace_id, session_id=s.id, kind="transcript", title=s.title)
        db.add(doc)
        db.flush()
        for i, grp in enumerate(chunk_segments(rows, names)):
            text = "\n".join(f"{names.get(r.speaker_id) + ': ' if names.get(r.speaker_id) else ''}{r.text}" for r in grp)
            docs.append((doc.id, "transcript", i, text, [str(r.id) for r in grp], grp[0].start_ms, grp[-1].end_ms,
                         sorted({names[r.speaker_id] for r in grp if r.speaker_id in names})))
    summ = db.scalar(select(ConversationSummary).where(ConversationSummary.session_id == s.id))
    if summ and summ.executive_summary:
        sd = KnowledgeDocument(workspace_id=s.workspace_id, session_id=s.id, kind="summary", title=s.title)
        db.add(sd)
        db.flush()
        docs.append((sd.id, "summary", 0, f"{s.title}. {summ.executive_summary} Topics: {', '.join(summ.topics or [])}",
                     [], 0, 0, []))
    if not docs:
        return
    vecs = embeddings.embed([d[3] for d in docs])
    for d, v in zip(docs, vecs):
        db.add(KnowledgeChunk(document_id=d[0], session_id=s.id, workspace_id=s.workspace_id, project_id=s.project_id,
                              kind=d[1], chunk_index=d[2], text=d[3], segment_ids=d[4], start_ms=d[5], end_ms=d[6],
                              speakers=d[7], occurred_at=s.occurred_at, embedding=v,
                              embedding_model=embeddings.model_name()))


# -------------------------------------------------------------------------------------------- main
def _clear_ai_rows(db: Session, sid):
    for M in (ExtractedDecision, Commitment, Insight):
        db.execute(delete(M).where(M.session_id == sid, M.user_modified.is_(False)))
    db.execute(delete(ExtractedTask).where(ExtractedTask.session_id == sid, ExtractedTask.source == "ai",
                                           ExtractedTask.user_modified.is_(False)))


def _remind_at(t: dict, tz) -> Optional[datetime]:
    """UTC moment for a spoken 'date + clock time' deadline; None if either is missing or it is already past."""
    if not (t.get("due_date") and t.get("due_time")):
        return None
    hh, mm = map(int, t["due_time"].split(":"))
    at = datetime.combine(t["due_date"], datetime.min.time().replace(hour=hh, minute=mm), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return at if at > utcnow() else None


def understand(db: Session, s: RecordingSession) -> dict:
    segs = load_segs(db, s.id)
    projects = [{"id": str(p.id), "name": p.name, "aliases": p.aliases or []}
                for p in db.scalars(select(Project).where(Project.workspace_id == s.workspace_id, Project.deleted_at.is_(None)))]
    ws = db.get(Workspace, s.workspace_id)
    tz = tzinfo(ws.timezone if ws else "UTC")
    res, note = extraction.extract(segs, local(s.occurred_at, tz).date(), projects)
    sid, wid = s.id, s.workspace_id
    # link the session to a project only when exactly one project is mentioned most often in the transcript
    if not s.project_id and projects:
        counts = {p["id"]: sum(1 for sg in segs if extraction._project_for(sg.text, [p])) for p in projects}
        counts = {k: v for k, v in counts.items() if v}
        if counts:
            best = max(counts, key=counts.get)
            if list(counts.values()).count(counts[best]) == 1:
                s.project_id = uuid.UUID(best)
    _clear_ai_rows(db, sid)

    def kept(M, field):
        return {re.sub(r"\W+", " ", (getattr(r, field) or "").lower()).strip()
                for r in db.scalars(select(M).where(M.session_id == sid))}
    have_d, have_t, have_c, have_i = kept(ExtractedDecision, "text"), kept(ExtractedTask, "title"), kept(Commitment, "text"), kept(Insight, "text")
    key = lambda t: re.sub(r"\W+", " ", t.lower()).strip()  # noqa: E731

    for d in res["decisions"]:
        if key(d["text"]) in have_d:
            continue
        db.add(ExtractedDecision(session_id=sid, workspace_id=wid, text=d["text"], context=d.get("context", ""),
                                 participants=d.get("participants", []), segment_ids=d["segment_ids"],
                                 timestamp_ms=d["timestamp_ms"], confidence=d["confidence"],
                                 project_id=uuid.UUID(d["project_id"]) if d.get("project_id") else s.project_id))
    new_tasks = 0
    for t in res["tasks"]:
        if key(t["title"]) in have_t:
            continue
        status = "pending"  # auto-accepted; low-confidence items still show the "needs review" flag
        db.add(ExtractedTask(session_id=sid, workspace_id=wid, owner_id=s.owner_id, visibility=s.visibility,
                             title=t["title"], description=t.get("description", ""), assignee_name=t["assignee_name"],
                             assignee_evidence=t["assignee_evidence"], due_date=t["due_date"], due_text=t["due_text"],
                             priority=t["priority"], segment_ids=t["segment_ids"], timestamp_ms=t["timestamp_ms"],
                             confidence=t["confidence"], status=status, remind_at=_remind_at(t, tz),
                             project_id=uuid.UUID(t["project_id"]) if t.get("project_id") else s.project_id,
                             history=[{"at": utcnow().isoformat(), "event": "detected", "by": "ai"}]))
        new_tasks += 1
        if t["assignee_name"]:
            resolve_person(db, wid, t["assignee_name"], sid)
    for c in res["commitments"]:
        if key(c["text"]) in have_c:
            continue
        db.add(Commitment(session_id=sid, workspace_id=wid, person_name=c["person_name"], text=c["text"], kind=c["kind"],
                          due_date=c["due_date"], due_text=c["due_text"], segment_ids=c["segment_ids"],
                          timestamp_ms=c["timestamp_ms"], confidence=c["confidence"]))
    for i in res["insights"]:
        if key(i["text"]) in have_i:
            continue
        db.add(Insight(session_id=sid, workspace_id=wid, kind=i["kind"], text=i["text"], segment_ids=i["segment_ids"],
                       timestamp_ms=i["timestamp_ms"], confidence=i["confidence"]))

    db.execute(delete(TimelineEvent).where(TimelineEvent.session_id == sid))
    for e in res["events"]:  # speech time = session start + evidence offsets
        db.add(TimelineEvent(session_id=sid, workspace_id=wid, title=e["title"], description=e["description"], category=e["category"],
                             start_at=s.occurred_at + timedelta(milliseconds=e["start_ms"]),
                             end_at=s.occurred_at + timedelta(milliseconds=e["end_ms"]), start_ms=e["start_ms"],
                             importance=e["importance"], segment_ids=e["segment_ids"]))
    sm = res["summary"]
    if s.title.strip() in ("", "Untitled session") and sm.get("title"):
        s.title = sm["title"][:300]
    db.execute(delete(ConversationSummary).where(ConversationSummary.session_id == sid))
    db.add(ConversationSummary(session_id=sid, workspace_id=wid, executive_summary=sm["executive_summary"],
                               topics=sm["topics"], key_statements=sm["key_statements"], outcomes=sm["outcomes"],
                               generated_by=sm["generated_by"]))
    # speakers -> people entities, participants, meeting
    speakers = db.scalars(select(Speaker).where(Speaker.session_id == sid)).all()
    for sp in speakers:
        if not re.match(r"(?i)^(speaker[ _-]?\d+|unknown)$", sp.name):
            e = resolve_person(db, wid, sp.name, sid)
            sp.entity_id = e.id if e else None
    db.execute(delete(Meeting).where(Meeting.session_id == sid))
    if s.kind == "meeting":
        m = Meeting(workspace_id=wid, session_id=sid, title=s.title, start_time=s.occurred_at,
                    end_time=s.ended_at or (s.occurred_at + timedelta(seconds=s.duration_seconds) if s.duration_seconds else None),
                    project_id=s.project_id)
        db.add(m)
        db.flush()
        for sp in speakers:
            db.add(MeetingParticipant(meeting_id=m.id, name=sp.name, entity_id=sp.entity_id, speaker_id=sp.id))
    for p in db.scalars(select(Project).where(Project.workspace_id == wid, Project.deleted_at.is_(None))):
        if s.project_id == p.id and not db.scalar(select(MemoryEntity).where(MemoryEntity.workspace_id == wid,
                                                                             MemoryEntity.project_id == p.id)):
            db.add(MemoryEntity(workspace_id=wid, type="project", name=p.name, aliases=p.aliases or [], project_id=p.id))
    if new_tasks:
        db.add(Notification(user_id=s.owner_id, kind="tasks_detected", title=f"{new_tasks} task(s) added from “{s.title}”",
                            body="They're on your task list.", link="/tasks"))
    return {"engine": res["engine"], "note": note}


def process_session(session_id, *, force=False, retranscribe=False):
    """Run in a worker. Raises stt.ProviderError for transient failures (caller retries)."""
    db = SessionLocal()
    try:
        s = db.get(RecordingSession, session_id)
        if not s or s.deleted_at:
            return
        if s.exclude_from_processing:
            s.status = "excluded"
            db.commit()
            return
        if s.status in ("transcribing", "analyzing") and not force and s.updated_at > utcnow() - timedelta(minutes=15):
            return  # another worker owns it
        log = dict(s.meta or {})
        have = db.scalar(select(TranscriptionSegment.id).where(TranscriptionSegment.session_id == s.id).limit(1))
        if retranscribe or not have:
            audio = assemble_audio(db, s)
            if audio:
                s.status, s.error = "transcribing", None
                db.commit()
                res = stt.get_stt().transcribe(audio, s.language or (db.get(User, s.owner_id).stt_languages))
                save_segments(db, s, [{"start_ms": g.start_ms, "end_ms": g.end_ms, "text": g.text, "speaker": g.speaker,
                                       "confidence": g.confidence, "language": g.language} for g in res.segments], res.language)
                s.language = s.language or res.language
                s.duration_seconds = res.duration_s or (res.segments[-1].end_ms / 1000 if res.segments else None)
                log["stt"] = get_stt_name()
        if not db.scalar(select(TranscriptionSegment.id).where(TranscriptionSegment.session_id == s.id).limit(1)):
            s.status = "awaiting_transcription"
            s.error = "No transcript yet: upload audio with a configured STT provider, or import a transcript."
            db.commit()
            return
        s.status, s.error = "analyzing", None
        db.commit()
        log["extraction"] = understand(db, s)
        db.flush()
        reindex_session(db, s)
        s.status, s.attempts, s.meta = "ready", 0, log
        db.commit()
        day.refresh_day(db, s)
    except stt.ProviderNotConfigured as e:
        db.rollback()
        s = db.get(RecordingSession, session_id)
        s.status, s.error = "awaiting_transcription", str(e)
        db.commit()
    except Exception as e:
        db.rollback()
        s = db.get(RecordingSession, session_id)
        if s:
            s.status, s.error, s.attempts = "failed", f"{type(e).__name__}: {e}"[:500], (s.attempts or 0) + 1
            db.commit()
        if isinstance(e, stt.ProviderError):
            raise
        traceback.print_exc()
    finally:
        db.close()


def get_stt_name() -> str:
    return stt.get_stt().name


def reindex_job(session_id):
    """Re-chunk and re-embed one session (after transcript edits / provider change)."""
    db = SessionLocal()
    try:
        s = db.get(RecordingSession, session_id)
        if s and not s.deleted_at:
            reindex_session(db, s)
            db.commit()
    finally:
        db.close()
