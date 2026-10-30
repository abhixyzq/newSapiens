"""Conversation understanding. Two engines producing the same structure:

* LLM engine  — structured JSON, every item must cite segment indexes that exist; uncited items are dropped.
* Rule-based  — regex/heuristic engine (EN + basic Hinglish). Conservative, lower confidence, everything is
                flagged for human review. Used when no LLM is configured or the LLM output is unusable.

Invariant: an item is only created if it points at real transcript segments. Task owners are only set with
evidence (explicit name next to the verb, or first person with a known speaker)."""
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..config import get_settings
from . import llm as llm_mod
from .dates import parse_due
from .embeddings import tokens
from .stt import ProviderNotConfigured


@dataclass
class Seg:
    id: str
    idx: int
    start_ms: int
    speaker: Optional[str]
    text: str


NOT_NAMES = set("""we i you he she it they this that there these those please let then so and but also now ok okay
yes no well first next today tomorrow maybe if when once after before the a an our their his her my your someone
anyone everyone nobody team can could should would will just""".split())

MODALS = r"(?:will|shall|to|should|needs? to|has to|have to|is going to|are going to|can you|could you|would you|will you|please|you'll|you will)"
RE_NAME_TASK = re.compile(rf"^(?:ok(?:ay)?[, ]+|so[, ]+|and[, ]+)?(?P<name>[A-Z][a-z]{{1,20}})\s*,?\s+{MODALS}\s+(?P<what>.{{4,}})$")
RE_ASSIGN = re.compile(r"\bassign(?:ed|ing)?\s+(?:this|that|it)?\s*to\s+(?P<name>[A-Z][a-z]{1,20})\b[:,]?\s*(?P<what>.*)", re.I)
RE_ACTION = re.compile(r"\b(?:action item|todo|to-do|action point)\s*[:\-–]?\s*(?P<what>.{4,})", re.I)
RE_FIRST = re.compile(r"^(?:ok(?:ay)?[, ]+|so[, ]+|and[, ]+|yes[, ]+|sure[, ]+)?(?:I|I'll|I will|I shall|I am going to|I'm going to|let me|I need to|I have to|I can|I promise to)\b[' ]*(?P<what>.{4,})$", re.I)
RE_WE_NEED = re.compile(r"^(?:ok(?:ay)?[, ]+|so[, ]+)?(?:we|someone|somebody|team)\s+(?:need to|needs to|should|must|have to|has to|ought to)\s+(?P<what>.{4,})$", re.I)
RE_HINGLISH = re.compile(r"^(?:(?P<name>[A-Z][a-z]{1,20})\s+ko\s+)?(?P<what>.{4,}?)\s+(?:karna|karni|bhejna|dena|banana|bhej dena|kar dena|karwana)\s+(?:hai|hoga|h)\b", re.I)
RE_DECISION = re.compile(r"\b(we(?:'ve| have)? decided|decision (?:is|was)|decided to|let'?s go with|we(?:'ll| will) go with|"
                         r"we(?:'ll| will) proceed with|agreed (?:to|that|on)|we agreed|finali[sz]ed|it'?s settled|"
                         r"final decision|approved|tay hua|final kar diya|decide kiya)\b", re.I)
RE_RISK = re.compile(r"\b(blocker|blocked|blocking|risk|delay(?:ed)?|behind schedule|dependency|depends on|waiting (?:on|for)|"
                     r"can'?t proceed|cannot proceed|not possible|missed the deadline|slipping|stuck|issue with|problem with|"
                     r"concern(?:ed)? about)\b", re.I)
RE_IDEA = re.compile(r"\b(idea|what if we|we could|we might|how about|suggestion|suggest|maybe we can|it would be nice|"
                     r"worth trying|let'?s explore|consider)\b", re.I)
RE_PRIORITY_HI = re.compile(r"\b(urgent|asap|critical|high priority|top priority|immediately|right away|p0|p1)\b", re.I)
RE_PRIORITY_LO = re.compile(r"\b(low priority|no rush|whenever|nice to have|when you get time)\b", re.I)
RE_HEDGE = re.compile(r"\b(maybe|might|perhaps|if we|if you|possibly|probably|not sure)\b", re.I)
RE_LEAD = re.compile(r"^(?:ok(?:ay)?|so|and|yes|sure|well|right|alright)[, ]+", re.I)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 3]


def _clean_what(w: str) -> str:
    w = re.sub(r"\s+", " ", w).strip(" .,;:!-–")
    w = re.sub(r"\s+(?:by|before|until|till|on|due)\s+(?:this |next |coming )?"
               r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|eod|end of (?:the )?(?:day|week|month)|"
               r"\d{1,2}(?:st|nd|rd|th)?\s+\w+|\w+\s+\d{1,2}(?:st|nd|rd|th)?)\s*$", "", w, flags=re.I)
    return (w[:1].upper() + w[1:])[:240]


def _priority(s: str) -> str:
    return "high" if RE_PRIORITY_HI.search(s) else "low" if RE_PRIORITY_LO.search(s) else "medium"


def _project_for(text: str, projects: list[dict]) -> Optional[str]:
    low = text.lower()
    for p in projects:
        for n in [p["name"], *p.get("aliases", [])]:
            if n and re.search(rf"\b{re.escape(n.lower())}\b", low):
                return p["id"]
    return None


def rule_based(segs: list[Seg], ref: date, projects: list[dict]) -> dict:
    decisions, tasks, commitments, insights = [], [], [], []
    seen = set()

    def once(kind, text):
        k = (kind, re.sub(r"\W+", " ", text.lower()).strip())
        if k in seen:
            return False
        seen.add(k)
        return True

    for sg in segs:
        for sent in sentences(sg.text):
            core = RE_LEAD.sub("", sent).strip()
            due_date, due_text = parse_due(sent, ref)
            hedged = bool(RE_HEDGE.search(sent)) or sent.endswith("?")
            proj = _project_for(sent, projects)

            if RE_DECISION.search(sent) and not sent.endswith("?") and once("d", sent):
                decisions.append({"text": sent[:400], "context": "", "segment_ids": [sg.id], "timestamp_ms": sg.start_ms,
                                  "participants": [sg.speaker] if sg.speaker else [], "confidence": 0.55 if hedged else 0.7,
                                  "project_id": proj})

            task = None
            if sent.endswith("?") and not re.match(r"^(can|could|would|will)\s+you", core, re.I):
                task = None
            elif m := RE_NAME_TASK.match(core):
                nm = m["name"]
                if nm.lower() not in NOT_NAMES:
                    task = dict(title=_clean_what(m["what"]), assignee=nm, evidence="explicit_name",
                                confidence=0.75 - (0.25 if hedged else 0))
            if not task and (m := RE_ASSIGN.search(sent)):
                what = m["what"] or sent
                task = dict(title=_clean_what(what), assignee=m["name"], evidence="explicit_name", confidence=0.7)
            if not task and (m := RE_ACTION.search(sent)):
                task = dict(title=_clean_what(m["what"]), assignee=None, evidence="none", confidence=0.65)
            if not task and (m := RE_HINGLISH.match(core)):
                nm = m["name"] if m["name"] and m["name"].lower() not in NOT_NAMES else None
                task = dict(title=_clean_what(m["what"]), assignee=nm, evidence="explicit_name" if nm else "none",
                            confidence=0.55)
            if not task and (m := RE_WE_NEED.match(core)):
                task = dict(title=_clean_what(m["what"]), assignee=None, evidence="none", confidence=0.45)

            if task and len(task["title"]) >= 4 and once("t", task["title"]):
                conf = task["confidence"] + (0.05 if due_date else 0)
                tasks.append({"title": task["title"], "description": sent[:400], "assignee_name": task["assignee"],
                              "assignee_evidence": task["evidence"], "due_date": due_date, "due_text": due_text,
                              "priority": _priority(sent), "segment_ids": [sg.id], "timestamp_ms": sg.start_ms,
                              "confidence": round(max(0.2, min(conf, 0.9)), 2), "project_id": proj})
                continue

            if not sent.endswith("?") and (m := RE_FIRST.match(core)) and once("c", sent):
                commitments.append({"person_name": sg.speaker, "text": sent[:400], "kind": "promise",
                                    "due_date": due_date, "due_text": due_text, "segment_ids": [sg.id],
                                    "timestamp_ms": sg.start_ms, "confidence": 0.6 if sg.speaker else 0.4})
                continue

            if RE_RISK.search(sent) and once("r", sent):
                insights.append({"kind": "risk", "text": sent[:400], "segment_ids": [sg.id], "timestamp_ms": sg.start_ms,
                                 "confidence": 0.5})
            elif RE_IDEA.search(sent) and not sent.endswith("?") and once("i", sent):
                insights.append({"kind": "idea", "text": sent[:400], "segment_ids": [sg.id], "timestamp_ms": sg.start_ms,
                                 "confidence": 0.45})
            elif sent.endswith("?") and len(sent.split()) >= 5 and once("q", sent):
                insights.append({"kind": "question", "text": sent[:400], "segment_ids": [sg.id],
                                 "timestamp_ms": sg.start_ms, "confidence": 0.4})
    return {"summary": _extractive_summary(segs, decisions, tasks), "decisions": decisions, "tasks": tasks,
            "commitments": commitments, "insights": insights, "engine": "rule_based"}


def _extractive_summary(segs: list[Seg], decisions, tasks) -> dict:
    speakers = {t for s in segs if s.speaker for t in tokens(s.speaker)}
    freq = Counter(t for s in segs for t in tokens(s.text) if t not in speakers and not t.isdigit())
    topics = [w for w, c in freq.most_common(8) if c >= 2][:5] or [w for w, _ in freq.most_common(3)]
    scored = []
    for s in segs:
        for sent in sentences(s.text):
            toks = tokens(sent)
            if len(toks) >= 4:
                scored.append((sum(freq[t] for t in toks) / (len(toks) ** 0.7), s, sent))
    top = sorted(scored, key=lambda x: -x[0])[:3]
    top.sort(key=lambda x: x[1].idx)
    return {
        "executive_summary": " ".join(t[2] for t in top),
        "topics": topics,
        "key_statements": [{"text": t[2], "segment_ids": [t[1].id]} for t in top],
        "outcomes": [d["text"] for d in decisions[:3]] + [t["title"] for t in tasks[:3]],
        "generated_by": "extractive (rule-based; not an abstractive AI summary)",
    }


# ------------------------------------------------------------------------------------------ LLM engine
SYSTEM = """You extract structured work intelligence from a transcript. Rules:
- Use ONLY what is in the transcript. Never invent people, tasks, deadlines or decisions.
- Every item MUST list "evidence": the transcript line numbers it is based on. Drop items you cannot cite.
- Task assignee: set only if a person is explicitly named as responsible, or the speaker says "I will" (assignee_basis "first_person" and use the speaker's name). Otherwise assignee=null, assignee_basis="none".
- deadlines: ISO date (YYYY-MM-DD) only if the date is unambiguous given REFERENCE_DATE, else null and put the phrase in due_text.
- confidence is 0..1; lower it for hedged or ambiguous statements.
Return one JSON object with keys: summary{executive_summary,topics[],key_statements[{text,evidence[]}],outcomes[]},
decisions[{text,context,participants[],evidence[],confidence}],
tasks[{title,description,assignee,assignee_basis,due_date,due_text,priority(low|medium|high),evidence[],confidence}],
commitments[{person,text,kind(promise|follow_up|responsibility),due_date,due_text,evidence[],confidence}],
risks[{text,evidence[],confidence}], ideas[{text,evidence[],confidence}], open_questions[{text,evidence[],confidence}]."""


def _ev(item, segs_by_idx):
    ids = []
    for i in item.get("evidence") or []:
        try:
            s = segs_by_idx.get(int(i))
        except (TypeError, ValueError):
            s = None
        if s:
            ids.append(s)
    return ids


def _d(v) -> Optional[date]:
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def llm_extract(segs: list[Seg], ref: date, projects: list[dict]) -> dict:
    model = llm_mod.get_llm()
    body = "\n".join(f"[{s.idx}] {s.speaker or 'Unknown'}: {s.text}" for s in segs)
    out = llm_mod.parse_json(model.complete(SYSTEM, f"REFERENCE_DATE: {ref.isoformat()}\nTRANSCRIPT:\n{body[:150000]}",
                                            json_mode=True, max_tokens=8000))
    by_idx = {s.idx: s for s in segs}
    res = {"decisions": [], "tasks": [], "commitments": [], "insights": [], "engine": f"llm:{model.name}"}

    def common(it):
        ss = _ev(it, by_idx)
        if not ss:
            return None
        try:
            conf = max(0.0, min(1.0, float(it.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        return ss, conf

    for it in out.get("decisions", []):
        if (c := common(it)) and it.get("text"):
            res["decisions"].append({"text": it["text"], "context": it.get("context", ""), "participants": it.get("participants") or [],
                                     "segment_ids": [s.id for s in c[0]], "timestamp_ms": c[0][0].start_ms, "confidence": c[1],
                                     "project_id": _project_for(it["text"], projects)})
    for it in out.get("tasks", []):
        if (c := common(it)) and it.get("title"):
            basis = it.get("assignee_basis", "none")
            who = it.get("assignee") if basis in ("named", "first_person") else None
            res["tasks"].append({"title": it["title"][:240], "description": it.get("description", ""), "assignee_name": who,
                                 "assignee_evidence": {"named": "explicit_name", "first_person": "first_person"}.get(basis, "none") if who else "none",
                                 "due_date": _d(it.get("due_date")), "due_text": it.get("due_text"),
                                 "priority": it.get("priority") if it.get("priority") in ("low", "medium", "high") else "medium",
                                 "segment_ids": [s.id for s in c[0]], "timestamp_ms": c[0][0].start_ms, "confidence": c[1],
                                 "project_id": _project_for(it["title"], projects)})
    for it in out.get("commitments", []):
        if (c := common(it)) and it.get("text"):
            res["commitments"].append({"person_name": it.get("person"), "text": it["text"], "kind": it.get("kind", "promise"),
                                       "due_date": _d(it.get("due_date")), "due_text": it.get("due_text"),
                                       "segment_ids": [s.id for s in c[0]], "timestamp_ms": c[0][0].start_ms, "confidence": c[1]})
    for key, kind in (("risks", "risk"), ("ideas", "idea"), ("open_questions", "question")):
        for it in out.get(key, []):
            if (c := common(it)) and it.get("text"):
                res["insights"].append({"kind": kind, "text": it["text"], "segment_ids": [s.id for s in c[0]],
                                        "timestamp_ms": c[0][0].start_ms, "confidence": c[1]})
    sm = out.get("summary") or {}
    res["summary"] = {"executive_summary": sm.get("executive_summary", ""), "topics": sm.get("topics", [])[:10],
                      "key_statements": [{"text": k["text"], "segment_ids": [s.id for s in _ev(k, by_idx)]}
                                         for k in sm.get("key_statements", []) if k.get("text") and _ev(k, by_idx)],
                      "outcomes": sm.get("outcomes", [])[:10], "generated_by": f"llm:{get_settings().llm_model}"}
    if not res["summary"]["executive_summary"]:
        raise ValueError("LLM returned no summary")
    return res


def extract(segs: list[Seg], ref: date, projects: list[dict]) -> tuple[dict, Optional[str]]:
    """Returns (result, note). Falls back to rules when no LLM is configured or output is unusable.
    ProviderError (transient) propagates so the job runner can retry."""
    if llm_mod.llm_enabled():
        try:
            return llm_extract(segs, ref, projects), None
        except (ProviderNotConfigured, ValueError, KeyError) as e:
            return rule_based(segs, ref, projects), f"LLM unavailable/unusable ({e}); used rule-based extraction"
    return rule_based(segs, ref, projects), None
