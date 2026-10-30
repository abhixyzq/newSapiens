import re
from datetime import date, timedelta
from typing import Optional

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)} | {"sept": 9}
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS)} | MONTH_ABBR
_MON = "|".join(sorted(MONTH_NUM, key=len, reverse=True))
_DAY_MON = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?({_MON})\b(?:,?\s*(\d{{4}}))?", re.I)
_MON_DAY = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s*(\d{{4}}))?", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")


def _mk(y, m, d) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_explicit_date(text: str, ref: date, *, prefer: str = "future") -> Optional[tuple[date, str]]:
    """First explicit calendar date in text. Year-less dates resolve to the nearest occurrence
    in the `prefer` direction ('future' for deadlines, 'past' for recalling history)."""
    def resolve(m, d, y):
        if y:
            return _mk(y, m, d)
        cands = [c for c in (_mk(ref.year + k, m, d) for k in (-1, 0, 1)) if c]
        if prefer == "past":
            past = [c for c in cands if c <= ref]
            return max(past) if past else None
        fut = [c for c in cands if c >= ref]
        return min(fut) if fut else None

    if mt := _ISO.search(text):
        d = _mk(int(mt[1]), int(mt[2]), int(mt[3]))
        if d:
            return d, mt[0]
    if mt := _DAY_MON.search(text):
        d = resolve(MONTH_NUM[mt[2].lower()], int(mt[1]), int(mt[3]) if mt[3] else None)
        if d:
            return d, mt[0]
    if mt := _MON_DAY.search(text):
        d = resolve(MONTH_NUM[mt[1].lower()], int(mt[2]), int(mt[3]) if mt[3] else None)
        if d:
            return d, mt[0]
    if mt := _SLASH.search(text):  # day/month (Indian/UK convention), optional year
        y = int(mt[3]) if mt[3] else None
        if y and y < 100:
            y += 2000
        d = resolve(int(mt[2]), int(mt[1]), y)
        if d:
            return d, mt[0]
    return None


def next_weekday(ref: date, wd: int, *, strictly_after=True) -> date:
    delta = (wd - ref.weekday()) % 7
    if delta == 0 and strictly_after:
        delta = 7
    return ref + timedelta(days=delta)


def parse_due(text: str, ref: date) -> tuple[Optional[date], Optional[str]]:
    """Deadline phrase -> (date or None, phrase). Vague phrases ('next week', 'soon') keep the text but no date,
    so we never invent a precise deadline."""
    t = text.lower()
    m = re.search(r"\b(?:by|before|until|till|on|due|deadline(?: is)?|latest)\s+(?:this\s+|next\s+|coming\s+)?"
                  r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        wd = WEEKDAYS.index(m[1])
        nxt = "next " + m[1] in t
        d = next_weekday(ref, wd)
        if nxt and d - ref < timedelta(days=7) and wd > ref.weekday():
            d += timedelta(days=7)
        return d, m[0].strip()
    if re.search(r"\btomorrow\b", t):
        return ref + timedelta(days=1), "tomorrow"
    if re.search(r"\b(today|tonight|eod|end of (the )?day|cob)\b", t):
        return ref, re.search(r"\b(today|tonight|eod|end of (the )?day|cob)\b", t)[0]
    if re.search(r"\bend of (the )?week\b", t):
        return next_weekday(ref, 4, strictly_after=False), "end of week"
    if re.search(r"\bend of (the )?month\b", t):
        nm = date(ref.year + (ref.month == 12), ref.month % 12 + 1, 1)
        return nm - timedelta(days=1), "end of month"
    ex = find_explicit_date(t, ref, prefer="future")
    if ex and re.search(r"\b(by|before|until|till|on|due|deadline)\b", t):
        return ex
    if m := re.search(r"\b(next week|next month|this week|this month|soon|asap|next quarter|by the end of the year)\b", t):
        return None, m[0]
    return None, None
