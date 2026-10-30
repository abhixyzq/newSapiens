"""Single in-process scheduler thread (run one app instance per DB, or move this to a cron/worker in production):
 * daily report generation at each workspace's local report_time (once per day, skipped if one already exists)
 * audio retention purge (audio files only, per retention_until)
 * recovery of sessions stuck in transcribing/analyzing"""
import threading
from datetime import datetime, timedelta

from sqlalchemy import select

from ..access import Ctx
from ..db import SessionLocal, utcnow
from ..models import AudioAsset, DailyReport, Notification, RecordingSession, User, Workspace
from . import jobs, pipeline
from .rag import tzinfo
from .storage import get_storage

_stop = threading.Event()


def run_daily_reports(db=None, now_utc: datetime = None):
    from ..routers.reports import generate
    own = db is None
    db = db or SessionLocal()
    now_utc = now_utc or utcnow()
    made = 0
    try:
        for ws in db.scalars(select(Workspace).where(Workspace.auto_daily_report.is_(True), Workspace.deleted_at.is_(None), Workspace.type == "individual")):
            owner = db.get(User, ws.owner_user_id) if ws.owner_user_id else None
            if not owner:
                continue
            tz = tzinfo(ws.timezone or owner.timezone)
            local_now = now_utc.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(tz)
            hh, mm = map(int, ws.report_time.split(":"))
            if (local_now.hour, local_now.minute) < (hh, mm):
                continue
            day = local_now.date()
            if db.scalar(select(DailyReport.id).where(DailyReport.workspace_id == ws.id, DailyReport.period_type == "daily", DailyReport.period_start == day,
                                                      DailyReport.deleted_at.is_(None))):
                continue
            owner_tz = owner.timezone
            owner.timezone = ws.timezone or owner.timezone  # report day boundaries follow the workspace's configured timezone
            r = generate(db, Ctx(user=owner, workspace=ws, role="owner"), "daily", day, auto=True)
            owner.timezone = owner_tz
            db.add(Notification(user_id=owner.id, kind="report_ready", title=f"Your daily report for {day.isoformat()} is ready (draft)", link=f"/reports/{r.id}"))
            db.commit()
            made += 1
    finally:
        if own:
            db.close()
    return made


def purge_expired_audio(db=None):
    own = db is None
    db = db or SessionLocal()
    n = 0
    try:
        st = get_storage()
        for s in db.scalars(select(RecordingSession).where(RecordingSession.retention_until.is_not(None), RecordingSession.retention_until < utcnow(),
                                                           RecordingSession.deleted_at.is_(None))):
            for a in db.scalars(select(AudioAsset).where(AudioAsset.session_id == s.id)):
                st.delete(a.storage_key)
                db.delete(a)
                n += 1
            s.retention_until = None
            s.meta = {**(s.meta or {}), "audio_purged_by_retention": utcnow().isoformat()}
        db.commit()
    finally:
        if own:
            db.close()
    return n


def recover_stuck(db=None):
    own = db is None
    db = db or SessionLocal()
    try:
        cutoff = utcnow() - timedelta(minutes=15)
        for s in db.scalars(select(RecordingSession).where(RecordingSession.status.in_(["transcribing", "analyzing", "uploaded"]),
                                                           RecordingSession.updated_at < cutoff, RecordingSession.deleted_at.is_(None))):
            if (s.attempts or 0) < 5:
                jobs.submit(pipeline.process_session, s.id, force=True)
    finally:
        if own:
            db.close()


def _loop():
    tick = 0
    while not _stop.wait(60):
        tick += 1
        try:
            run_daily_reports()
            if tick % 10 == 0:
                purge_expired_audio()
                recover_stuck()
        except Exception:
            import traceback
            traceback.print_exc()


def start():
    threading.Thread(target=_loop, name="ns-scheduler", daemon=True).start()


def stop():
    _stop.set()
