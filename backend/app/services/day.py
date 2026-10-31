"""Day consolidation: one short LLM pass over a day's per-conversation summaries (never raw transcripts), so the
"your day at a glance" text is rebuilt from already-summarised pieces instead of re-reading every chunk."""
from . import llm
from .stt import ProviderNotConfigured

PROMPT = ("You write the user's 'day at a glance' from NOTES about their own recorded conversations, in time order. Rules: "
          "second person ('You discussed…'), 2-4 sentences, most important things first (decisions, tasks, plans, ideas) and skip "
          "small talk; never repeat a point; use ONLY the notes and never invent activities, people, times or deadlines; anything "
          "the notes only suggest must start with 'Likely'. Return JSON {\"summary\": str, \"highlights\": [up to 5 short strings]}.")


def summarize_day(items: list[str], today: str) -> dict:
    """{"summary", "highlights"}. Raises ProviderNotConfigured without an LLM; callers fall back to listing titles."""
    if not llm.llm_enabled():
        raise ProviderNotConfigured("no LLM configured")
    out = llm.parse_json(llm.get_llm().complete(PROMPT, f"DATE: {today}\nNOTES:\n" + "\n".join(f"- {i}" for i in items)[:12000],
                                                json_mode=True, max_tokens=600))
    return {"summary": str(out.get("summary") or "").strip(),
            "highlights": [str(h).strip() for h in (out.get("highlights") or [])[:5] if str(h).strip()]}


def refresh_day(db, s) -> None:
    """Rebuild the owner's DailySummary for the local day of session `s` from that day's session summaries and events.
    Runs after a session is ready; a failure here never fails the session."""
    from datetime import timedelta
    import traceback
    from sqlalchemy import select
    from ..db import utcnow
    from ..models import ConversationSummary, DailySummary, RecordingSession, TimelineEvent, User
    from .rag import local, to_utc, tzinfo

    try:
        tz = tzinfo(db.get(User, s.owner_id).timezone)
        d = local(s.occurred_at, tz).date()
        sess = db.scalars(select(RecordingSession).where(
            RecordingSession.owner_id == s.owner_id, RecordingSession.workspace_id == s.workspace_id, RecordingSession.status == "ready",
            RecordingSession.deleted_at.is_(None), RecordingSession.exclude_from_processing.is_(False),
            RecordingSession.occurred_at >= to_utc(d, tz), RecordingSession.occurred_at < to_utc(d + timedelta(days=1), tz))
            .order_by(RecordingSession.occurred_at)).all()
        ids = [x.id for x in sess]
        sums = {x.session_id: x.executive_summary for x in db.scalars(select(ConversationSummary).where(ConversationSummary.session_id.in_(ids)))}
        evs = db.scalars(select(TimelineEvent).where(TimelineEvent.session_id.in_(ids), TimelineEvent.importance >= 0.3)
                         .order_by(TimelineEvent.start_at)).all()
        items = [f"{local(x.occurred_at, tz):%H:%M} {x.title}: {sums.get(x.id, '')[:600]}" for x in sess]
        items += [f"{local(e.start_at, tz):%H:%M} {e.title} — {e.description}" for e in evs]
        try:
            out, by = summarize_day(items, d.isoformat()), "llm"
        except (ProviderNotConfigured, ValueError):
            out, by = {"summary": "", "highlights": [e.title for e in sorted(evs, key=lambda e: -e.importance)[:5]]}, "rule_based"
        row = db.scalar(select(DailySummary).where(DailySummary.user_id == s.owner_id, DailySummary.workspace_id == s.workspace_id,
                                                   DailySummary.date == d))
        if not row:
            row = DailySummary(user_id=s.owner_id, workspace_id=s.workspace_id, date=d)
            db.add(row)
        row.summary, row.highlights, row.source_count, row.generated_by, row.generated_at = out["summary"], out["highlights"], len(sess), by, utcnow()
        db.commit()
    except Exception:  # LLM outage etc.: the day summary just stays as it was
        db.rollback()
        traceback.print_exc()
