"""Ask Sapiens: grounded question answering over the caller's permitted records.

Pipeline: parse (dates in the user's timezone, people, projects, intent) -> resolve meeting scope (0 / 1 / many) ->
gather structured records + hybrid-retrieved transcript chunks -> compose a cited answer -> verify citations.

Every statement in an answer comes from a retrieved record and carries an [S#] marker that maps to a ChatCitation.
With an LLM configured the model writes the prose but must cite; if it cites nothing or cites an unknown source,
the deterministic composer is used instead. Nothing is returned that is not backed by a retrieved record."""
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..access import Ctx, visible_session_filter
from ..models import (Commitment, ConversationSummary, ExtractedDecision, ExtractedTask, Insight, Meeting,
                      MemoryEntity, Project, RecordingSession, Speaker)
from . import embeddings, llm as llm_mod, queries, retrieval
from .dates import MONTH_NUM, WEEKDAYS, find_explicit_date
from .stt import ProviderError, ProviderNotConfigured

NUMWORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}
NUMWORDS |= {"twenty": 20, "thirty": 30, "fourteen": 14}
META = set("""meeting meetings call calls discuss discussed discussion discussions happened happen summarize summary summarise
recap show list tell last previous past this week month day days today yesterday did we our us my me give what which who when
where why how about any anyone everyone mention mentioned compare progress important decisions decision tasks task pending
commitments commitment assigned assign decide decided latest recent all project projects made make taken take said say talk talked happen going get got""".split()) | set(MONTH_NUM) | set(WEEKDAYS)
FOLLOWUP = re.compile(r"^(and|what about|how about|also|then|those|that|these|it|they|them|more|why|who|when)\b", re.I)


def tzinfo(name: str):
    try:
        return ZoneInfo(name)
    except Exception:  # unknown name or missing tz database; callers validate names on write, so this is a last resort
        return timezone.utc


def to_utc(d: date, tz) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def local(dt: datetime, tz) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(tz)


def fmt_day(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


@dataclass
class Plan:
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    start_d: Optional[date] = None
    end_d: Optional[date] = None  # exclusive
    label: str = ""
    single_day: Optional[date] = None
    people: list = field(default_factory=list)
    self_ref: bool = False
    project_id: Optional[str] = None
    project_name: str = ""
    intents: set = field(default_factory=set)
    terms: str = ""
    session_id: Optional[str] = None
    compare: bool = False
    explicit_scope: bool = False

    def dump(self) -> dict:
        return {"start_d": self.start_d.isoformat() if self.start_d else None,
                "end_d": self.end_d.isoformat() if self.end_d else None, "label": self.label,
                "single_day": self.single_day.isoformat() if self.single_day else None, "people": self.people,
                "self_ref": self.self_ref, "project_id": self.project_id, "project_name": self.project_name,
                "intents": sorted(self.intents), "terms": self.terms, "session_id": self.session_id}

    @staticmethod
    def load(d: dict, tz) -> "Plan":
        p = Plan(label=d.get("label", ""), people=d.get("people", []), self_ref=d.get("self_ref", False),
                 project_id=d.get("project_id"), project_name=d.get("project_name", ""), intents=set(d.get("intents", [])),
                 terms=d.get("terms", ""), session_id=d.get("session_id"))
        for k in ("start_d", "end_d", "single_day"):
            if d.get(k):
                setattr(p, k, date.fromisoformat(d[k]))
        p.start = to_utc(p.start_d, tz) if p.start_d else None
        p.end = to_utc(p.end_d, tz) if p.end_d else None
        return p


def _period_label(a: date, b: date) -> str:
    last = b - timedelta(days=1)
    return f"{a.day} {a.strftime('%b')} – {last.day} {last.strftime('%b %Y')}"


def parse_dates(q: str, today: date) -> tuple[Optional[date], Optional[date], str, Optional[date]]:
    t = q.lower()
    monday = today - timedelta(days=today.weekday())
    if "yesterday" in t:
        d = today - timedelta(days=1)
        return d, d + timedelta(days=1), f"yesterday ({fmt_day(d)})", d
    if re.search(r"\btoday\b|\bthis morning\b|\bthis afternoon\b", t):
        return today, today + timedelta(days=1), f"today ({fmt_day(today)})", today
    if re.search(r"\b(last|previous|past)\s+(\d+|[a-z]+)\s+days\b", t):
        m = re.search(r"\b(last|previous|past)\s+(\d+|[a-z]+)\s+days\b", t)
        n = int(m[2]) if m[2].isdigit() else NUMWORDS.get(m[2])
        if n:
            return today - timedelta(days=n - 1), today + timedelta(days=1), f"last {n} days ({_period_label(today - timedelta(days=n - 1), today + timedelta(days=1))})", None
    if re.search(r"\b(last|previous|past)\s+week\b", t):
        a = monday - timedelta(days=7)
        return a, monday, f"last week ({_period_label(a, monday)})", None
    if re.search(r"\bthis week\b", t):
        return monday, today + timedelta(days=1), f"this week ({_period_label(monday, today + timedelta(days=1))})", None
    if re.search(r"\b(last|previous|past)\s+month\b", t):
        first = today.replace(day=1)
        a = (first - timedelta(days=1)).replace(day=1)
        return a, first, f"last month ({a.strftime('%B %Y')})", None
    if re.search(r"\bthis month\b", t):
        a = today.replace(day=1)
        return a, today + timedelta(days=1), f"this month ({a.strftime('%B %Y')})", None
    if m := re.search(r"\b(?:last|previous)\s+(" + "|".join(WEEKDAYS) + r")\b", t):
        wd = WEEKDAYS.index(m[1])
        d = today - timedelta(days=((today.weekday() - wd) % 7) or 7)
        return d, d + timedelta(days=1), fmt_day(d), d
    if ex := find_explicit_date(q, today, prefer="past"):
        d = ex[0]
        return d, d + timedelta(days=1), fmt_day(d), d
    return None, None, "", None


def parse_query(db: Session, ctx: Ctx, text: str, prev: Optional[Plan]) -> Plan:
    tz = tzinfo(ctx.user.timezone)
    today = datetime.now(tz).date()
    p = Plan()
    p.start_d, p.end_d, p.label, p.single_day = parse_dates(text, today)
    t = text.lower()
    if "compare" in t or re.search(r"\bvs\.?\b|\bversus\b", t):
        p.compare = True
    # people / projects known to this workspace
    ws = ctx.workspace.id
    names = {e.name: e.name for e in db.scalars(select(MemoryEntity).where(MemoryEntity.workspace_id == ws, MemoryEntity.type == "person"))}
    for e in db.scalars(select(MemoryEntity).where(MemoryEntity.workspace_id == ws, MemoryEntity.type == "person")):
        for a in e.aliases or []:
            names[a] = e.name
    for n in sorted(names, key=len, reverse=True):
        if re.search(rf"\b{re.escape(n.lower())}\b", t):
            if names[n] not in p.people:
                p.people.append(names[n])
    for pr in db.scalars(select(Project).where(Project.workspace_id == ws, Project.deleted_at.is_(None))):
        for n in [pr.name, *(pr.aliases or [])]:
            if n and re.search(rf"\b{re.escape(n.lower())}\b", t):
                p.project_id, p.project_name = str(pr.id), pr.name
    if re.search(r"\b(decid|decision|agreed)", t):
        p.intents.add("decisions")
    if re.search(r"\b(task|tasks|assigned|assign|to-?do|action items?)\b", t):
        p.intents.add("tasks")
    if re.search(r"\b(pending|outstanding|open|overdue|unfinished|commitments?|promise[sd]?)\b", t):
        p.intents.add("pending")
    if re.search(r"\b(commitments?|promise[sd]?|follow[- ]?ups?)\b", t):
        p.intents.add("commitments")
    if re.search(r"\b(risks?|blockers?|blocked|problems?)\b", t):
        p.intents.add("risks")
    if re.search(r"\b(ideas?|suggestions?)\b", t):
        p.intents.add("ideas")
    if re.search(r"\b(summari[sz]e|summary|recap|what happened|overview)\b", t):
        p.intents.add("summary")
    if re.search(r"\bwhen did we (last )?(discuss|talk|mention)|\blast (discussed|time)\b", t):
        p.intents.add("last_discussed")
    if re.search(r"\b(meeting|meetings|call|standup|stand-up|sync)\b", t):
        p.intents.add("meeting")
    if re.search(r"\b(deadline|due)\b", t):
        p.intents.add("deadline")
    if re.search(r"\b(i|my|me|mine)\b", t) and ({"commitments", "pending", "tasks"} & p.intents):
        p.self_ref = True
        if not p.people:
            p.people = [ctx.user.name.split()[0]]
    drop = {w for pe in p.people for w in embeddings.tokens(pe)} | set(embeddings.tokens(p.project_name))
    toks = [w for w in embeddings.tokens(text) if w not in META and not w.isdigit() and w not in drop]
    p.terms = " ".join(toks)
    if not p.intents and not p.terms and (p.people or p.project_id):
        p.intents.add("summary")
    p.explicit_scope = bool(p.start_d or p.people or p.project_id)
    # follow-up: inherit scope from the previous turn when this one adds none
    if prev and not p.explicit_scope and (FOLLOWUP.match(text.strip()) or len(text.split()) <= 6):
        p.start_d, p.end_d, p.label, p.single_day = prev.start_d, prev.end_d, prev.label, prev.single_day
        p.people, p.project_id, p.project_name = prev.people, prev.project_id, prev.project_name
        p.self_ref = prev.self_ref
        if not p.intents:
            p.intents = set(prev.intents)
        p.session_id = prev.session_id
    p.start = to_utc(p.start_d, tz) if p.start_d else None
    p.end = to_utc(p.end_d, tz) if p.end_d else None
    return p


# ---------------------------------------------------------------------------------------- evidence
@dataclass
class Ev:
    ref: str
    type: str
    session_id: Optional[uuid.UUID]
    record_id: Optional[uuid.UUID]
    segment_id: Optional[uuid.UUID]
    start_ms: Optional[int]
    label: str
    text: str


class Evidence:
    def __init__(self, tz):
        self.items: list[Ev] = []
        self.tz = tz
        self._seen: dict = {}
        self._sessions: dict = {}

    def session_label(self, s: RecordingSession) -> str:
        return f"{s.title} · {local(s.occurred_at, self.tz).strftime('%d %b %Y, %H:%M')}"

    def add(self, type_, s: RecordingSession, record_id, segment_ids, start_ms, text) -> str:
        key = (type_, str(record_id))
        if key in self._seen:
            return self._seen[key]
        ref = f"S{len(self.items) + 1}"
        seg = None
        if segment_ids:
            try:
                seg = uuid.UUID(str(segment_ids[0]))
            except ValueError:
                seg = None
        self.items.append(Ev(ref, type_, s.id, record_id, seg, start_ms, self.session_label(s), text[:600]))
        self._seen[key] = ref
        return ref


def _eligible_sessions(db: Session, ctx: Ctx, plan: Plan):
    q = select(RecordingSession).where(visible_session_filter(ctx), RecordingSession.exclude_from_processing.is_(False),
                                       RecordingSession.status.in_(["ready"]))
    if plan.start:
        q = q.where(RecordingSession.occurred_at >= plan.start, RecordingSession.occurred_at < plan.end)
    if plan.project_id:
        q = q.where(RecordingSession.project_id == uuid.UUID(plan.project_id))
    sessions = db.scalars(q.order_by(RecordingSession.occurred_at)).all()
    if plan.people and not plan.self_ref:
        want = {p.lower() for p in plan.people}
        keep = []
        for s in sessions:
            sp = {x.name.lower() for x in db.scalars(select(Speaker).where(Speaker.session_id == s.id))}
            if sp & want:
                keep.append(s)
        sessions = keep or sessions  # a person may be mentioned without being a speaker; refine downstream
    return sessions


def _person_match(col, plan: Plan):
    from sqlalchemy import or_
    return or_(*[col.ilike(f"%{p}%") for p in plan.people])


# ---------------------------------------------------------------------------------------- composition
def _src(s: RecordingSession, tz) -> str:
    return f"{s.title}, {local(s.occurred_at, tz).strftime('%d %b')}"


def _due(t) -> str:
    if t.due_date:
        return f" · due {t.due_date.strftime('%a %d %b %Y')}"
    return f" · deadline mentioned: “{t.due_text}”" if t.due_text else ""


def _flag(conf: float, status: str = "") -> str:
    return " _(needs review)_" if conf < 0.7 or status in ("needs_review", "needs_confirmation") else ""


def compose_meeting(db: Session, ctx: Ctx, s: RecordingSession, plan: Plan, ev: Evidence) -> str:
    tz = ev.tz
    sm = db.scalar(select(ConversationSummary).where(ConversationSummary.session_id == s.id))
    people = [sp.name for sp in db.scalars(select(Speaker).where(Speaker.session_id == s.id))]
    out = [f"**{'Meeting' if s.kind == 'meeting' else 'Conversation'}: {s.title}** — {local(s.occurred_at, tz).strftime('%d %B %Y, %H:%M')}"]
    if people:
        out.append(f"Participants (speakers in transcript): {', '.join(people)}")
    if sm and sm.executive_summary:
        ref = ev.add("summary", s, sm.id, (sm.key_statements[0]["segment_ids"] if sm.key_statements else []), 0, sm.executive_summary)
        out.append(f"\n**Summary** ({'extractive' if sm.generated_by.startswith('extractive') else sm.generated_by.split(' ')[0]}): {sm.executive_summary} [{ref}]")
        if sm.topics:
            out.append(f"**Main topics:** {', '.join(sm.topics)}")
    decs = db.scalars(select(ExtractedDecision).where(ExtractedDecision.session_id == s.id,
                      ExtractedDecision.confirmation_status != "rejected").order_by(ExtractedDecision.timestamp_ms)).all()
    out.append("\n**Decisions**")
    out += [f"- {d.text} [{ev.add('decision', s, d.id, d.segment_ids, d.timestamp_ms, d.text)}]"
            f"{' _(confirmed)_' if d.confirmation_status == 'confirmed' else _flag(d.confidence, d.confirmation_status)}" for d in decs] \
        or ["- No decisions were detected in this recording."]
    tasks = db.scalars(select(ExtractedTask).where(ExtractedTask.session_id == s.id, ExtractedTask.deleted_at.is_(None),
                       ExtractedTask.status != "cancelled").order_by(ExtractedTask.timestamp_ms)).all()
    out.append("\n**Tasks**")
    out += [f"- {t.title} — {'Assigned to ' + t.assignee_name if t.assignee_name else 'no owner identified'}{_due(t)} "
            f"[{ev.add('task', s, t.id, t.segment_ids, t.timestamp_ms, t.title)}]{_flag(t.confidence, t.status)}" for t in tasks] \
        or ["- No tasks were detected in this recording."]
    cms = db.scalars(select(Commitment).where(Commitment.session_id == s.id, Commitment.status != "cancelled")
                     .order_by(Commitment.timestamp_ms)).all()
    if cms:
        out.append("\n**Commitments / follow-ups**")
        out += [f"- {c.person_name or 'Unknown speaker'}: {c.text} [{ev.add('commitment', s, c.id, c.segment_ids, c.timestamp_ms, c.text)}]{_flag(c.confidence)}" for c in cms]
    ins = db.scalars(select(Insight).where(Insight.session_id == s.id, Insight.status != "dismissed").order_by(Insight.timestamp_ms)).all()
    for kind, title in (("risk", "Risks and blockers"), ("idea", "Ideas"), ("question", "Open questions (not verified as unresolved)")):
        rows = [i for i in ins if i.kind == kind]
        if rows:
            out.append(f"\n**{title}**")
            out += [f"- {i.text} [{ev.add('insight', s, i.id, i.segment_ids, i.timestamp_ms, i.text)}]" for i in rows]
    open_t = [t for t in tasks if t.status in queries.OPEN_TASK]
    if open_t or cms:
        out.append(f"\n**Pending:** {len(open_t)} open task(s), {len([c for c in cms if c.status == 'open'])} open commitment(s).")
    if plan.terms:  # topic-specific passages
        hits = retrieval.hybrid_search(db, ctx, plan.terms, k=3, session_ids=[s.id])
        if hits:
            out.append("\n**Relevant passages**")
            for h in hits:
                c = h.chunk
                ref = ev.add("transcript", s, c.id, c.segment_ids, c.start_ms, c.text)
                out.append(f"- “{c.text[:220].replace(chr(10), ' / ')}” [{ref}]")
    return "\n".join(out)


def _group(rows, get_session, tz):
    out: dict = {}
    for r in rows:
        s = get_session(r)
        out.setdefault(s.id, (s, []))[1].append(r)
    return sorted(out.values(), key=lambda x: x[0].occurred_at)


def compose_general(db: Session, ctx: Ctx, plan: Plan, sessions, ev: Evidence) -> tuple[str, bool]:
    tz = ev.tz
    sid_map = {s.id: s for s in sessions}
    sids = list(sid_map)
    out, found = [], False
    scope = plan.label or "all time"
    want_all = not (plan.intents & {"decisions", "tasks", "pending", "commitments", "risks", "ideas"})

    def sess_of(r):
        return sid_map[r.session_id]

    if "decisions" in plan.intents or (want_all and "summary" not in plan.intents and not plan.terms):
        q = select(ExtractedDecision).where(ExtractedDecision.session_id.in_(sids), ExtractedDecision.confirmation_status != "rejected")
        if plan.terms:
            from sqlalchemy import or_
            q = q.where(or_(*[ExtractedDecision.text.ilike(f"%{w}%") for w in plan.terms.split()]))
        rows = db.scalars(q) .all() if sids else []
        if rows:
            found = True
            out.append(f"**Decisions** — {scope}")
            for s, rs in _group(rows, sess_of, tz):
                out.append(f"_{_src(s, tz)}_")
                out += [f"- {d.text} [{ev.add('decision', s, d.id, d.segment_ids, d.timestamp_ms, d.text)}]"
                        f"{' _(confirmed)_' if d.confirmation_status == 'confirmed' else _flag(d.confidence, d.confirmation_status)}" for d in rs]
    if plan.intents & {"tasks", "pending"} or (want_all and not plan.terms):
        q = queries.tasks_q(ctx).where(ExtractedTask.status != "cancelled")
        if "pending" in plan.intents:
            q = q.where(ExtractedTask.status.in_(queries.OPEN_TASK))
        if plan.people:
            q = q.where(_person_match(ExtractedTask.assignee_name, plan))
        if plan.project_id:
            q = q.where(ExtractedTask.project_id == uuid.UUID(plan.project_id))
        if plan.start:
            q = q.where((ExtractedTask.session_id.in_(sids)) | ((ExtractedTask.session_id.is_(None)) &
                        (ExtractedTask.created_at >= plan.start) & (ExtractedTask.created_at < plan.end)))
        if plan.terms and "tasks" not in plan.intents and "pending" not in plan.intents:
            q = q.where(False)
        rows = db.scalars(q.order_by(ExtractedTask.created_at)).all()
        if rows:
            found = True
            out.append(f"\n**{'Pending tasks' if 'pending' in plan.intents else 'Tasks'}**"
                       f"{' for ' + ', '.join(plan.people) if plan.people else ''} — {scope}")
            for t in rows:
                s = sid_map.get(t.session_id) or (db.get(RecordingSession, t.session_id) if t.session_id else None)
                ref = ev.add("task", s, t.id, t.segment_ids, t.timestamp_ms, t.title) if s else None
                out.append(f"- {t.title} — {'Assigned to ' + t.assignee_name if t.assignee_name else 'no owner identified'}{_due(t)}"
                           f" · status: {t.status.replace('_', ' ')}{' [' + ref + ']' if ref else ' (manually created)'}{_flag(t.confidence, t.status) if t.source == 'ai' else ''}")
    if plan.intents & {"commitments", "pending"}:
        q = queries.commitments_q(ctx).where(Commitment.status == "open")
        if plan.people:
            q = q.where(_person_match(Commitment.person_name, plan))
        if sids and (plan.start or plan.project_id):
            q = q.where(Commitment.session_id.in_(sids))
        rows = db.scalars(q.order_by(Commitment.created_at)).all()
        if rows:
            found = True
            out.append(f"\n**Open commitments**{' — ' + ', '.join(plan.people) if plan.people else ''}"
                       f"{' (reading “I/my” as ' + plan.people[0] + ')' if plan.self_ref else ''}")
            for c in rows:
                s = sid_map.get(c.session_id) or db.get(RecordingSession, c.session_id)
                out.append(f"- {c.person_name or 'Unknown speaker'}: {c.text}{_due(c)} [{ev.add('commitment', s, c.id, c.segment_ids, c.timestamp_ms, c.text)}]{_flag(c.confidence)}")
    for kind, intent, title in (("risk", "risks", "Risks and blockers"), ("idea", "ideas", "Ideas")):
        if intent in plan.intents:
            rows = db.scalars(select(Insight).where(Insight.session_id.in_(sids), Insight.kind == kind, Insight.status != "dismissed")).all() if sids else []
            if rows:
                found = True
                out.append(f"\n**{title}** — {scope}")
                out += [f"- {i.text} [{ev.add('insight', sess_of(i), i.id, i.segment_ids, i.timestamp_ms, i.text)}]" for i in rows]
    if "summary" in plan.intents and sessions:
        found = True
        out.append(f"**Summary of {len(sessions)} recording(s)** — {scope}")
        for s in sessions[-15:]:
            sm = db.scalar(select(ConversationSummary).where(ConversationSummary.session_id == s.id))
            if sm and sm.executive_summary:
                out.append(f"- **{_src(s, tz)}** ({s.kind}): {sm.executive_summary[:400]} [{ev.add('summary', s, sm.id, [], 0, sm.executive_summary)}]")
    if plan.terms or "last_discussed" in plan.intents:
        hits = retrieval.hybrid_search(db, ctx, plan.terms or "", k=6, start=plan.start, end=plan.end,
                                       project_id=uuid.UUID(plan.project_id) if plan.project_id else None,
                                       person=plan.people[0] if plan.people and not plan.self_ref else None)
        hits = [h for h in hits if h.chunk.session_id in sid_map or not sids] if (plan.start or plan.project_id) else hits
        if "last_discussed" in plan.intents and hits:
            hits.sort(key=lambda h: h.chunk.occurred_at, reverse=True)
            h = hits[0]
            s = db.get(RecordingSession, h.chunk.session_id)
            out.append(f"The most recent mention I can find was on **{local(s.occurred_at, tz).strftime('%d %B %Y')}** in “{s.title}”.")
            hits = hits[:3]
        if hits:
            found = True
            out.append(f"\n**Relevant passages** — {scope}")
            for h in hits:
                c = h.chunk
                s = db.get(RecordingSession, c.session_id)
                ref = ev.add("transcript", s, c.id, c.segment_ids, c.start_ms, c.text)
                out.append(f"- _{_src(s, tz)}_: “{c.text[:260].replace(chr(10), ' / ')}” [{ref}]")
    return "\n".join(out).strip(), found


def compare_weeks(db: Session, ctx: Ctx, tz) -> str:
    today = datetime.now(tz).date()
    mon = today - timedelta(days=today.weekday())
    periods = [("This week", mon, today + timedelta(days=1)), ("Last week", mon - timedelta(days=7), mon)]
    rows = []
    for name, a, b in periods:
        s0, s1 = to_utc(a, tz), to_utc(b, tz)
        ss = db.scalars(select(RecordingSession).where(visible_session_filter(ctx), RecordingSession.exclude_from_processing.is_(False),
                        RecordingSession.occurred_at >= s0, RecordingSession.occurred_at < s1)).all()
        ids = [s.id for s in ss]
        dur = sum(s.duration_seconds or 0 for s in ss) / 3600
        nd = db.scalar(select(func.count()).select_from(ExtractedDecision).where(ExtractedDecision.session_id.in_(ids))) if ids else 0
        nt = db.scalar(select(func.count()).select_from(ExtractedTask).where(ExtractedTask.session_id.in_(ids), ExtractedTask.deleted_at.is_(None))) if ids else 0
        done = db.scalar(select(func.count()).select_from(ExtractedTask).where(ExtractedTask.workspace_id == ctx.workspace.id,
                         ExtractedTask.completed_at >= s0, ExtractedTask.completed_at < s1))
        rows.append((name, _period_label(a, b), len(ss), sum(1 for s in ss if s.kind == "meeting"), round(dur, 1), nd, nt, done))
    out = ["**Week-over-week comparison** (counts computed directly from stored records; extraction counts are AI-detected, not verified)",
           "", "| Period | Dates | Recordings | Meetings | Hours | Decisions | Tasks detected | Tasks completed |", "|---|---|---|---|---|---|---|---|"]
    out += [f"| {' | '.join(str(x) for x in r)} |" for r in rows]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------- orchestration
def _time_hint(text: str):
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text.lower())
    if m:
        h = int(m[1]) % 12 + (12 if m[3] == "pm" else 0)
        return h, int(m[2] or 0)
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    return (int(m[1]), int(m[2])) if m else None


def pick_option(text: str, options: list[dict], tz) -> Optional[dict]:
    t = text.lower().strip()
    ords = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": -1}
    if t.isdigit() and 1 <= int(t) <= len(options):
        return options[int(t) - 1]
    for w, i in ords.items():
        if re.search(rf"\b{w}\b", t):
            return options[i]
    hint = _time_hint(t)
    if hint:
        for o in options:
            lt = local(datetime.fromisoformat(o["start"]), tz)
            if (lt.hour, lt.minute) == hint or (lt.hour == hint[0] and hint[1] == 0):
                return o
    for o in options:
        words = [w for w in embeddings.tokens(o["title"]) if len(w) > 2]
        if words and sum(w in t for w in words) >= max(1, len(words) // 2):
            return o
    return None


def answer(db: Session, ctx: Ctx, question: str, prev_meta: Optional[dict]) -> dict:
    tz = tzinfo(ctx.user.timezone)
    prev_plan = Plan.load(prev_meta["plan"], tz) if prev_meta and prev_meta.get("plan") else None
    ev = Evidence(tz)

    # 1. answer to an earlier "which meeting?" question
    if prev_meta and prev_meta.get("clarify") and prev_plan:
        opt = pick_option(question, prev_meta["clarify"]["options"], tz)
        if opt:
            plan = prev_plan
            plan.session_id = opt["id"]
            plan.intents.discard("meeting_choice")
        else:
            plan = parse_query(db, ctx, question, prev_plan)
    else:
        plan = parse_query(db, ctx, question, prev_plan)

    meta = {"plan": plan.dump(), "found": False, "mode": "deterministic"}
    scope = plan.label or "the available history"

    if plan.compare and re.search(r"week", question.lower()):
        text = compare_weeks(db, ctx, tz)
        meta["found"] = True
        return {"content": text, "evidence": ev.items, "meta": meta}

    # 2. a specific meeting?
    if not plan.session_id and plan.single_day and ("meeting" in plan.intents or "summary" in plan.intents and "meeting" in question.lower()):
        q = (select(RecordingSession, Meeting).join(Meeting, Meeting.session_id == RecordingSession.id)
             .where(visible_session_filter(ctx), RecordingSession.exclude_from_processing.is_(False), Meeting.deleted_at.is_(None),
                    RecordingSession.occurred_at >= plan.start, RecordingSession.occurred_at < plan.end)
             .order_by(RecordingSession.occurred_at))
        if plan.project_id:
            q = q.where(RecordingSession.project_id == uuid.UUID(plan.project_id))
        found_m = [r[0] for r in db.execute(q).all()]
        if plan.people:
            narrowed = [s for s in found_m if {x.name.lower() for x in db.scalars(select(Speaker).where(Speaker.session_id == s.id))} & {p.lower() for p in plan.people}]
            found_m = narrowed or found_m
        if not found_m:
            others = db.scalars(select(RecordingSession).where(visible_session_filter(ctx), RecordingSession.exclude_from_processing.is_(False),
                                RecordingSession.occurred_at >= plan.start, RecordingSession.occurred_at < plan.end)).all()
            msg = f"I couldn't find any meeting on **{fmt_day(plan.single_day)}** in the recordings you have access to."
            if others:
                msg += " I did find other recordings that day that are not marked as meetings: " + \
                       "; ".join(f"“{o.title}” ({local(o.occurred_at, tz).strftime('%H:%M')})" for o in others) + \
                       ". Ask about one of those by name, or mark it as a meeting."
            return {"content": msg, "evidence": [], "meta": meta}
        if len(found_m) > 1:
            opts = [{"id": str(s.id), "title": s.title, "start": s.occurred_at.isoformat()} for s in found_m]
            lines = [f"There are **{len(found_m)} meetings** on {fmt_day(plan.single_day)}. Which one do you mean?"]
            lines += [f"{i + 1}. {o['title']} — {local(datetime.fromisoformat(o['start']), tz).strftime('%H:%M')}" for i, o in enumerate(opts)]
            meta["clarify"] = {"options": opts}
            meta["found"] = True
            return {"content": "\n".join(lines), "evidence": [], "meta": meta}
        plan.session_id = str(found_m[0].id)
        meta["plan"] = plan.dump()

    if plan.session_id:
        s = db.scalar(select(RecordingSession).where(RecordingSession.id == uuid.UUID(plan.session_id), visible_session_filter(ctx),
                                                     RecordingSession.exclude_from_processing.is_(False)))
        if not s:
            return {"content": "I couldn't find that recording in what you have access to.", "evidence": [], "meta": meta}
        text = compose_meeting(db, ctx, s, plan, ev)
        meta["found"] = True
        return finalize(question, text, ev, meta, scope)

    # 3. general retrieval
    sessions = _eligible_sessions(db, ctx, plan)
    if not (plan.terms or plan.intents or plan.start or plan.people or plan.project_id):
        return {"content": "Ask me about your recorded conversations, for example: “What did we decide about pricing?”, "
                           "“Show all pending commitments”, or “What happened in the meeting on 25 January?”.",
                "evidence": [], "meta": meta}
    text, found = compose_general(db, ctx, plan, sessions, ev)
    if not found:
        bits = [x for x in [plan.label, ', '.join(plan.people), plan.project_name, f"“{plan.terms}”" if plan.terms else ""] if x]
        return {"content": f"I couldn't find anything matching {' · '.join(bits) or 'that'} in the recordings, tasks and decisions "
                           f"you have access to. I won't guess — try a wider date range or different keywords.",
                "evidence": [], "meta": meta}
    meta["found"] = True
    return finalize(question, text, ev, meta, scope)


def finalize(question: str, text: str, ev: Evidence, meta: dict, scope: str) -> dict:
    """Optionally let an LLM rewrite the composed answer from evidence only; accept it only if citations verify."""
    if llm_mod.llm_enabled() and ev.items:
        try:
            blocks = "\n".join(f"[{e.ref}] ({e.type}; {e.label}) {e.text}" for e in ev.items[:40])
            out = llm_mod.get_llm().complete(
                "You answer questions about a user's recorded work conversations using ONLY the numbered evidence. "
                "Cite every claim with [S#]. If evidence is insufficient say so; never add facts, names, dates or tasks that are "
                "not in the evidence. Mark AI-detected items that need review as unconfirmed. Use concise markdown.",
                f"QUESTION: {question}\nSCOPE: {scope}\nEVIDENCE:\n{blocks}", max_tokens=1500)
            refs = set(re.findall(r"\[(S\d+)\]", out))
            valid = {e.ref for e in ev.items}
            if refs and refs <= valid:
                meta["mode"] = f"llm:{llm_mod.get_llm().name}"
                text = out
        except (ProviderError, ProviderNotConfigured, Exception):
            pass
    used = set(re.findall(r"\[(S\d+)\]", text))
    ev.items = [e for e in ev.items if e.ref in used]
    return {"content": text, "evidence": ev.items, "meta": meta}
