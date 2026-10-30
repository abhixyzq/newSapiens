"""Report compilation (data) and PDF rendering. Content is plain JSON stored on the report so a user can edit/approve
it before download; the PDF is always rendered from the stored (possibly edited) content.

Labels on every item:  observed / confirmed (human-verified) / ai (AI-extracted, confidence >= 0.7, unreviewed) /
uncertain (low confidence). Nothing in a report is invented: sections are built only from stored records, and empty
sections are omitted."""
import io
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import Ctx, visible_session_filter
from ..config import get_settings
from ..db import utcnow
from ..models import (AudioAsset, Commitment, ConversationSummary, ExtractedDecision, ExtractedTask, Insight,
                      RecordingSession, Speaker, TranscriptionSegment)
from . import queries
from .rag import local, to_utc, tzinfo

LABELS = {"observed": "OBSERVED", "confirmed": "CONFIRMED", "ai": "AI-DETECTED", "uncertain": "NEEDS REVIEW"}
TABLE_LABELS = {"observed": "Observed", "confirmed": "Confirmed", "ai": "AI", "uncertain": "Review"}  # short forms for narrow columns


def _label(conf: float, confirmed: bool) -> str:
    return "confirmed" if confirmed else ("ai" if conf >= 0.7 else "uncertain")


def _fmt_dur(sec) -> str:
    if not sec:
        return "—"
    m = int(round(sec / 60))
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{max(m, 1)}m"


class _Refs:
    def __init__(self, db, tz):
        self.db, self.tz, self.items, self.idx = db, tz, [], {}

    def add(self, s: RecordingSession, segment_ids, start_ms, rtype, rid) -> str:
        k = (rtype, str(rid))
        if k in self.idx:
            return self.idx[k]
        ref = f"R{len(self.items) + 1}"
        excerpt = ""
        if segment_ids:
            try:
                seg = self.db.get(TranscriptionSegment, uuid.UUID(str(segment_ids[0])))
                excerpt = seg.text[:300] if seg else ""
            except ValueError:
                pass
        self.items.append({"ref": ref, "session_id": str(s.id), "session": s.title,
                           "when": local(s.occurred_at, self.tz).strftime("%d %b %Y %H:%M"), "start_ms": start_ms or 0,
                           "record_type": rtype, "record_id": str(rid), "segment_id": str(segment_ids[0]) if segment_ids else None,
                           "excerpt": excerpt})
        self.idx[k] = ref
        return ref


def compile_report(db: Session, ctx: Ctx, period_type: str, start_d: date, end_d: date, *, shared_only=False) -> dict:
    """[start_d, end_d) in the user's timezone."""
    tz = tzinfo(ctx.user.timezone)
    s0, s1 = to_utc(start_d, tz), to_utc(end_d, tz)
    q = select(RecordingSession).where(visible_session_filter(ctx), RecordingSession.occurred_at >= s0,
                                       RecordingSession.occurred_at < s1).order_by(RecordingSession.occurred_at)
    if shared_only:
        q = q.where(RecordingSession.visibility == "shared")
    all_sessions = db.scalars(q).all()
    excluded = [s for s in all_sessions if s.exclude_from_processing]
    sessions = [s for s in all_sessions if not s.exclude_from_processing]
    ids = [s.id for s in sessions]
    by_id = {s.id: s for s in sessions}
    refs = _Refs(db, tz)
    nid = iter(range(1, 10_000))

    def item(**kw):
        return {"id": f"i{next(nid)}", **kw}

    decs = db.scalars(select(ExtractedDecision).where(ExtractedDecision.session_id.in_(ids), ExtractedDecision.confirmation_status != "rejected")
                      .order_by(ExtractedDecision.created_at)).all() if ids else []
    tasks = db.scalars(select(ExtractedTask).where(ExtractedTask.session_id.in_(ids), ExtractedTask.deleted_at.is_(None),
                                                   ExtractedTask.status != "cancelled")).all() if ids else []
    cms = db.scalars(select(Commitment).where(Commitment.session_id.in_(ids), Commitment.status != "cancelled")).all() if ids else []
    ins = db.scalars(select(Insight).where(Insight.session_id.in_(ids), Insight.status != "dismissed")).all() if ids else []
    sums = {x.session_id: x for x in db.scalars(select(ConversationSummary).where(ConversationSummary.session_id.in_(ids)))} if ids else {}
    has_audio = {a for a in db.scalars(select(AudioAsset.session_id).where(AudioAsset.session_id.in_(ids), AudioAsset.deleted_at.is_(None)))} if ids else set()

    meetings = [s for s in sessions if s.kind == "meeting"]
    total_sec = sum(s.duration_seconds or 0 for s in sessions)
    sec = {}

    # --- executive summary
    paras = [item(text=f"{len(sessions)} recording(s) captured, including {len(meetings)} meeting(s); total recorded time {_fmt_dur(total_sec)}.", label="observed"),
             item(text=f"AI-detected from transcripts: {len(decs)} decision(s), {len(tasks)} task(s), {len(cms)} commitment(s), "
                       f"{sum(1 for i in ins if i.kind == 'risk')} risk(s). Items marked NEEDS REVIEW or AI-DETECTED have not been verified by a person.", label="observed")]
    for d in sorted(decs, key=lambda d: -d.confidence)[:3]:
        s = by_id[d.session_id]
        paras.append(item(text=f"Decision: {d.text}", label=_label(d.confidence, d.confirmation_status == "confirmed"),
                          ref=refs.add(s, d.segment_ids, d.timestamp_ms, "decision", d.id)))
    pending_unowned = [t for t in tasks if t.status in queries.OPEN_TASK and not t.assignee_name]
    if pending_unowned:
        paras.append(item(text=f"{len(pending_unowned)} open task(s) have no identified owner.", label="observed"))
    if excluded:
        paras.append(item(text=f"{len(excluded)} recording(s) were marked as sensitive and are excluded from this report.", label="observed"))
    sec["executive_summary"] = paras

    # --- timeline
    sec["timeline"] = [item(time=local(s.occurred_at, tz).strftime("%d %b %H:%M") if period_type != "daily" else local(s.occurred_at, tz).strftime("%H:%M"),
                            title=s.title, kind=s.kind, duration=_fmt_dur(s.duration_seconds),
                            participants=[sp.name for sp in db.scalars(select(Speaker).where(Speaker.session_id == s.id))],
                            summary=(sums[s.id].executive_summary[:300] if s.id in sums else ""), status=s.status,
                            label="observed", session_id=str(s.id)) for s in sessions]

    # --- meetings & discussions
    sec["meetings"] = []
    for s in sessions:
        sm = sums.get(s.id)
        if sm:
            sec["meetings"].append(item(title=s.title, kind=s.kind, when=local(s.occurred_at, tz).strftime("%d %b %H:%M"),
                                        summary=sm.executive_summary, topics=sm.topics or [],
                                        label="ai", summary_method=sm.generated_by, session_id=str(s.id)))

    def src(s):
        return {"session": s.title, "when": local(s.occurred_at, tz).strftime("%d %b %H:%M"), "session_id": str(s.id)}

    sec["decisions"] = [item(text=d.text, **src(by_id[d.session_id]), label=_label(d.confidence, d.confirmation_status == "confirmed"),
                             confidence=round(d.confidence, 2), ref=refs.add(by_id[d.session_id], d.segment_ids, d.timestamp_ms, "decision", d.id)) for d in decs]
    sec["tasks"] = [item(text=t.title, owner=t.assignee_name or "Unassigned", owner_basis=t.assignee_evidence,
                         due=t.due_date.isoformat() if t.due_date else (f"“{t.due_text}” (not a fixed date)" if t.due_text else ""),
                         priority=t.priority, status=t.status.replace("_", " "), **src(by_id[t.session_id]),
                         label=_label(t.confidence, t.status in ("pending", "in_progress", "completed") or t.user_modified),
                         confidence=round(t.confidence, 2), ref=refs.add(by_id[t.session_id], t.segment_ids, t.timestamp_ms, "task", t.id)) for t in tasks]
    sec["commitments"] = [item(text=c.text, person=c.person_name or "Unknown speaker", status=c.status,
                               due=c.due_date.isoformat() if c.due_date else (c.due_text or ""), **src(by_id[c.session_id]),
                               label=_label(c.confidence, not c.needs_review), confidence=round(c.confidence, 2),
                               ref=refs.add(by_id[c.session_id], c.segment_ids, c.timestamp_ms, "commitment", c.id)) for c in cms]
    for kind, key in (("risk", "risks"), ("idea", "ideas"), ("question", "questions")):
        sec[key] = [item(text=i.text, **src(by_id[i.session_id]), label=_label(i.confidence, not i.needs_review), confidence=round(i.confidence, 2),
                         ref=refs.add(by_id[i.session_id], i.segment_ids, i.timestamp_ms, "insight", i.id)) for i in ins if i.kind == kind]

    # --- insights derived from counts (no scoring, no judgement)
    obs = []
    if tasks:
        un = sum(1 for t in tasks if not t.assignee_name)
        obs.append(f"{un} of {len(tasks)} detected task(s) have no identified owner.") if un else None
    review = sum(1 for d in decs if d.confirmation_status == "needs_review") + sum(1 for t in tasks if t.status in ("needs_confirmation", "detected")) + sum(1 for c in cms if c.needs_review)
    if review:
        obs.append(f"{review} AI-detected item(s) are awaiting review.")
    mids = {s.id for s in meetings}
    quiet = [s for s in meetings if not any(d.session_id == s.id for d in decs)]
    if meetings and quiet:
        obs.append(f"{len(quiet)} of {len(meetings)} meeting(s) have no decision detected (may reflect extraction limits, not absence).")
    openq = [i for i in ins if i.kind == "question"]
    if openq:
        obs.append(f"{len(openq)} question(s) were asked that may be unanswered; this is not verified.")
    sec["ai_insights"] = [item(text=t, label="observed") for t in obs]

    # --- tomorrow's priorities: rule-based on due dates/priority only
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)
    pr = []
    open_q = queries.tasks_q(ctx).where(ExtractedTask.status.in_(queries.OPEN_TASK))
    for t in db.scalars(open_q):
        reason = None
        if t.due_date and t.due_date < today:
            reason, rank = f"overdue since {t.due_date.isoformat()}", 0
        elif t.due_date and t.due_date <= tomorrow:
            reason, rank = f"due {t.due_date.isoformat()}", 1
        elif t.priority == "high":
            reason, rank = "high priority", 2
        if reason:
            pr.append((rank, item(text=t.title, owner=t.assignee_name or "Unassigned", reason=reason, label=_label(t.confidence, t.status in ("pending", "in_progress")))))
    sec["priorities"] = [x for _, x in sorted(pr, key=lambda p: p[0])][:8]

    sections = {k: v for k, v in sec.items() if v}
    sections.setdefault("executive_summary", sec["executive_summary"])
    if not sessions:
        sections = {"executive_summary": [item(text="No recordings were captured in this period.", label="observed")]}
    names = {"daily": "Daily Intelligence Report", "weekly": "Weekly Intelligence Report", "monthly": "Monthly Intelligence Report",
             "organization": "Organization Report"}
    last = end_d - timedelta(days=1)
    return {
        "title": names.get(period_type, "Report"), "workspace": ctx.workspace.name, "author": ctx.user.name,
        "period": {"type": period_type, "start": start_d.isoformat(), "end": last.isoformat(),
                   "label": start_d.strftime("%d %B %Y") if start_d == last else f"{start_d.strftime('%d %b %Y')} – {last.strftime('%d %b %Y')}"},
        "timezone": ctx.user.timezone, "generated_at": utcnow().isoformat(), "notes": "",
        "sections": sections, "audio_available": [str(i) for i in has_audio],
        "appendix": {"sources": refs.items, "confidence_notes": [
            "OBSERVED: counted or read directly from stored records (times, durations, counts).",
            "CONFIRMED: reviewed or set by a person.",
            "AI-DETECTED: extracted automatically with confidence of 0.70 or higher; not yet reviewed.",
            "NEEDS REVIEW: extracted with low confidence or lacking evidence (e.g. no clearly named task owner).",
            "Summaries marked 'extractive' are selected transcript sentences, not abstractive AI summaries.",
            "Transcripts may contain recognition errors; check the cited timestamp before relying on an item."]},
    }


# -------------------------------------------------------------------------------------------- PDF
SECTION_TITLES = [("executive_summary", "Executive Summary"), ("timeline", "Workday Timeline"), ("meetings", "Meetings and Discussions"),
                  ("decisions", "Important Decisions"), ("tasks", "Tasks and Responsibilities"), ("commitments", "Pending Commitments"),
                  ("risks", "Risks and Blockers"), ("ideas", "Ideas and Opportunities"), ("questions", "Open Questions"),
                  ("ai_insights", "AI Insights"), ("priorities", "Tomorrow's Priorities")]


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    cands = [get_settings().pdf_font_path, "C:/Windows/Fonts/Nirmala.ttc", "C:/Windows/Fonts/segoeui.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"]
    bold = ["", "C:/Windows/Fonts/NirmalaB.ttc", "C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", ""]
    for c, b in zip(cands, bold):
        if c and os.path.exists(c):
            try:
                pdfmetrics.registerFont(TTFont("NS", c))
                pdfmetrics.registerFont(TTFont("NS-B", b if b and os.path.exists(b) else c))
                return "NS", "NS-B", True
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold", False


def render_pdf(content: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, PageTemplate, Paragraph, Spacer, Table,
                                    TableStyle)

    f, fb, uni = _font()
    NAVY, CYAN, SLATE, LIGHT = colors.HexColor("#0B1B3B"), colors.HexColor("#0EA5C6"), colors.HexColor("#475569"), colors.HexColor("#E8EEF6")
    chip = {"observed": "#475569", "confirmed": "#15803D", "ai": "#1D4ED8", "uncertain": "#B45309"}

    def t(x) -> str:
        x = escape(str(x if x is not None else ""))
        return x if uni else x.encode("latin-1", "replace").decode("latin-1")

    st = {
        "h1": ParagraphStyle("h1", fontName=fb, fontSize=22, leading=26, textColor=colors.white),
        "sub": ParagraphStyle("sub", fontName=f, fontSize=10, leading=14, textColor=colors.HexColor("#C7D5EA")),
        "h2": ParagraphStyle("h2", fontName=fb, fontSize=14, leading=18, textColor=NAVY, spaceBefore=14, spaceAfter=6),
        "p": ParagraphStyle("p", fontName=f, fontSize=9.5, leading=13.5, textColor=colors.HexColor("#1E293B")),
        "small": ParagraphStyle("small", fontName=f, fontSize=8, leading=11, textColor=SLATE),
        "cell": ParagraphStyle("cell", fontName=f, fontSize=8.5, leading=11.5),
        "cellb": ParagraphStyle("cellb", fontName=fb, fontSize=8.5, leading=11.5, textColor=colors.white),
    }

    def tag(label):
        return f'<font color="{chip.get(label, "#475569")}" size="7"><b>[{LABELS.get(label, label)}]</b></font>'

    def bullet(it, lead=""):
        ref = f' <font color="#64748B" size="7">[{it["ref"]}]</font>' if it.get("ref") else ""
        return Paragraph(f'{tag(it.get("label", "observed"))} {lead}{t(it["text"])}{ref}', st["p"])

    def table(head, rows, widths):
        data = [[Paragraph(t(h), st["cellb"]) for h in head]] + [[Paragraph(t(c), st["cell"]) for c in r] for r in rows]
        tb = Table(data, colWidths=widths, repeatRows=1)
        tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F8FC")]),
                                ("LINEBELOW", (0, 0), (-1, -1), 0.25, LIGHT), ("TOPPADDING", (0, 0), (-1, -1), 4),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        return tb

    W = A4[0] - 40 * mm
    buf = io.BytesIO()

    def deco(canv, doc):
        canv.saveState()
        canv.setFont(f, 7.5)
        canv.setFillColor(SLATE)
        canv.drawString(20 * mm, 10 * mm, "NEW SAPIENS · AI-detected items are unverified unless marked CONFIRMED")
        canv.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Page {doc.page}")
        canv.restoreState()

    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                          title=content["title"], author="NEW SAPIENS")
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")], onPage=deco)])

    band = Table([[Paragraph(t(content["title"]), st["h1"])], [Paragraph(t(f'{content["period"]["label"]} · {content.get("workspace", "")} · {content["timezone"]}'), st["sub"])],
                  [Paragraph(t(f'Prepared for {content.get("author", "")} · generated {content["generated_at"][:16].replace("T", " ")} UTC'), st["sub"])]],
                 colWidths=[W])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (0, 0), 14),
                              ("BOTTOMPADDING", (0, -1), (-1, -1), 14), ("LINEBELOW", (0, -1), (-1, -1), 3, CYAN)]))
    story = [band, Spacer(1, 6)]
    if content.get("notes"):
        story += [Paragraph("Author's note", st["h2"]), Paragraph(t(content["notes"]), st["p"])]

    S = content["sections"]
    for key, title in SECTION_TITLES:
        rows = S.get(key)
        if not rows:
            continue
        story += [CondPageBreak(60 * mm), Paragraph(t(title), st["h2"])]
        if key == "timeline":
            story.append(table(["Time", "Session", "Participants", "Duration", "Summary"],
                               [[r["time"], f'{r["title"]} ({r["kind"]})', ", ".join(r["participants"]) or "—", r["duration"], r["summary"] or "—"] for r in rows],
                               [20 * mm, 40 * mm, 30 * mm, 16 * mm, W - 106 * mm]))
        elif key == "meetings":
            for r in rows:
                story += [Paragraph(f'<b>{t(r["title"])}</b> — {t(r["when"])} {tag("ai")}', st["p"]),
                          Paragraph(t(r["summary"]), st["p"]),
                          Paragraph(t("Topics: " + ", ".join(r["topics"])) if r["topics"] else "", st["small"]),
                          Paragraph(t(f'Summary method: {r["summary_method"]}'), st["small"]), Spacer(1, 5)]
        elif key == "tasks":
            story.append(table(["Task", "Owner", "Due", "Priority", "Status", "Label", "Src"],
                               [[r["text"], r["owner"] + (" (inferred: speaker)" if r["owner_basis"] == "first_person" else ""), r["due"] or "—", r["priority"], r["status"],
                                 TABLE_LABELS[r["label"]], r.get("ref", "")] for r in rows],
                               [W * .26, W * .14, W * .13, W * .10, W * .11, W * .18, W * .08]))
        elif key == "commitments":
            story.append(table(["Person", "Commitment", "Due", "Status", "Label", "Src"],
                               [[r["person"], r["text"], r["due"] or "—", r["status"], TABLE_LABELS[r["label"]], r.get("ref", "")] for r in rows],
                               [W * .14, W * .38, W * .13, W * .10, W * .18, W * .07]))
        elif key == "priorities":
            for r in rows:
                story.append(Paragraph(f'{tag(r["label"])} {t(r["text"])} — {t(r["owner"])} <font color="#64748B">({t(r["reason"])})</font>', st["p"]))
        else:
            for r in rows:
                story.append(bullet(r))
                story.append(Spacer(1, 2))
    # appendix
    story += [CondPageBreak(70 * mm), Paragraph("Appendix", st["h2"]), Paragraph("Data confidence notes", st["p"])]
    story += [Paragraph("• " + t(n), st["small"]) for n in content["appendix"]["confidence_notes"]]
    srcs = content["appendix"]["sources"]
    if srcs:
        story += [Spacer(1, 6), Paragraph("Source references and transcript excerpts", st["p"]), Spacer(1, 3)]
        story.append(table(["Ref", "Recording", "When", "At", "Transcript excerpt"],
                           [[s["ref"], s["session"], s["when"], f'{s["start_ms"] // 60000:02d}:{(s["start_ms"] // 1000) % 60:02d}', s["excerpt"] or "(no excerpt)"] for s in srcs],
                           [11 * mm, 34 * mm, 26 * mm, 14 * mm, W - 85 * mm]))
    doc.build(story)
    return buf.getvalue()
