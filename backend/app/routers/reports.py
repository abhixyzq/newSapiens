import uuid
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..access import Ctx, RANK, workspace_role
from ..db import get_db, utcnow
from ..deps import audit, client_ip, require
from ..models import DailyReport, ReportSource, User
from ..services import reports as rsvc
from ..services.rag import tzinfo

router = APIRouter(prefix="/api/reports", tags=["reports"])


def period_bounds(kind: str, anchor: date) -> tuple[date, date]:
    if kind == "daily":
        return anchor, anchor + timedelta(days=1)
    if kind == "weekly":
        mon = anchor - timedelta(days=anchor.weekday())
        return mon, mon + timedelta(days=7)
    first = anchor.replace(day=1)
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return first, nxt


def generate(db: Session, ctx: Ctx, kind: str, anchor: date, *, auto=False, existing: Optional[DailyReport] = None) -> DailyReport:
    start, end = period_bounds("monthly" if kind == "organization" else kind, anchor)
    if kind == "organization":
        start, end = period_bounds("weekly", anchor)
    content = rsvc.compile_report(db, ctx, kind, start, end, shared_only=(kind == "organization"))
    r = existing or DailyReport(workspace_id=ctx.workspace.id, owner_id=ctx.user.id, period_type=kind, period_start=start, period_end=end - timedelta(days=1))
    r.content, r.title = content, f"{content['title']} — {content['period']['label']}"
    r.timezone, r.generated_at, r.auto_generated, r.status = ctx.user.timezone, utcnow(), auto, "draft"
    if existing:
        r.revision += 1
        r.approved_at = None
    else:
        db.add(r)
    db.flush()
    db.execute(delete(ReportSource).where(ReportSource.report_id == r.id))
    for s in content["appendix"]["sources"]:
        db.add(ReportSource(report_id=r.id, session_id=uuid.UUID(s["session_id"]), record_type=s["record_type"],
                            record_id=uuid.UUID(s["record_id"]), segment_id=uuid.UUID(s["segment_id"]) if s["segment_id"] else None, start_ms=s["start_ms"]))
    return r


def report_out(r: DailyReport, full=False, me: Optional[User] = None) -> dict:
    out = {"id": str(r.id), "title": r.title, "period_type": r.period_type, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
           "status": r.status, "auto_generated": r.auto_generated, "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
           "approved_at": r.approved_at.isoformat() + "Z" if r.approved_at else None, "revision": r.revision, "owner_id": str(r.owner_id),
           "shared_with": r.shared_with or []}
    if full:
        out["content"] = r.content
    return out


def visible_reports(ctx: Ctx):
    cond = or_(DailyReport.owner_id == ctx.user.id, DailyReport.shared_with.like(f'%"{ctx.user.id}"%'))
    return select(DailyReport).where(DailyReport.workspace_id == ctx.workspace.id, DailyReport.deleted_at.is_(None), cond)


def _get(db: Session, ctx: Ctx, rid: str, owner_only=False) -> DailyReport:
    try:
        r = db.scalar(visible_reports(ctx).where(DailyReport.id == uuid.UUID(rid)))
    except ValueError:
        r = None
    if not r or (owner_only and r.owner_id != ctx.user.id):
        raise HTTPException(404, "Not found")
    return r


class GenIn(BaseModel):
    period_type: Literal["daily", "weekly", "monthly", "organization"] = "daily"
    anchor_date: Optional[date] = None   # any day inside the period; default = today in the user's timezone


@router.post("/generate", status_code=201)
def gen(body: GenIn, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    if body.period_type == "organization":
        if ctx.workspace.type == "individual" or RANK[ctx.role] < RANK["manager"]:
            raise HTTPException(403, "Organization reports require manager+ in an organization/team workspace")
    anchor = body.anchor_date or datetime.now(tzinfo(ctx.user.timezone)).date()
    start, _ = period_bounds("weekly" if body.period_type == "organization" else body.period_type, anchor)
    ex = db.scalar(select(DailyReport).where(DailyReport.workspace_id == ctx.workspace.id, DailyReport.owner_id == ctx.user.id,
                                              DailyReport.period_type == body.period_type, DailyReport.period_start == start, DailyReport.deleted_at.is_(None)))
    r = generate(db, ctx, body.period_type, anchor, existing=ex)  # regenerate = new revision of the same report
    audit(db, user=ctx.user, ws=ctx.workspace, action="report.generate", rtype="report", rid=r.id, ip=client_ip(request))
    db.commit()
    return report_out(r, full=True)


@router.get("")
def list_reports(ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db), period_type: Optional[str] = None, limit: int = Query(30, le=100), offset: int = 0):
    q = visible_reports(ctx)
    if period_type:
        q = q.where(DailyReport.period_type == period_type)
    return [report_out(r) for r in db.scalars(q.order_by(DailyReport.period_start.desc(), DailyReport.created_at.desc()).limit(limit).offset(offset))]


@router.get("/{rid}")
def get_report(rid: str, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    return report_out(_get(db, ctx, rid), full=True)


class EditIn(BaseModel):
    notes: Optional[str] = None
    remove_items: list[str] = []
    title: Optional[str] = None


@router.patch("/{rid}")
def edit_report(rid: str, body: EditIn, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    """Authors can add a note and drop items they judge wrong. Edits mark the report as a draft again."""
    r = _get(db, ctx, rid, owner_only=True)
    c = dict(r.content)
    if body.notes is not None:
        c["notes"] = body.notes[:5000]
    if body.remove_items:
        gone = set(body.remove_items)
        c["sections"] = {k: [i for i in v if i.get("id") not in gone] for k, v in c["sections"].items()}
        c["sections"] = {k: v for k, v in c["sections"].items() if v or k == "executive_summary"}
    if body.title:
        r.title = body.title[:300]
    r.content, r.status, r.approved_at = c, "draft", None
    audit(db, user=ctx.user, ws=ctx.workspace, action="report.edit", rtype="report", rid=r.id, ip=client_ip(request))
    db.commit()
    return report_out(r, full=True)


@router.post("/{rid}/approve")
def approve(rid: str, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    r = _get(db, ctx, rid, owner_only=True)
    r.status, r.approved_at = "approved", utcnow()
    audit(db, user=ctx.user, ws=ctx.workspace, action="report.approve", rtype="report", rid=r.id, ip=client_ip(request))
    db.commit()
    return report_out(r)


class ShareIn(BaseModel):
    user_ids: list[uuid.UUID]


@router.post("/{rid}/share")
def share(rid: str, body: ShareIn, request: Request, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    """Share with specific members of this workspace only. Sharing a report never grants access to its source recordings."""
    r = _get(db, ctx, rid, owner_only=True)
    if r.status != "approved":
        raise HTTPException(409, "Approve the report before sharing it")
    ok = [str(u) for u in body.user_ids if workspace_role(db, u, ctx.workspace.id)]
    if len(ok) != len(set(map(str, body.user_ids))):
        raise HTTPException(422, "All recipients must be members of this workspace")
    r.shared_with = sorted(set(ok))
    audit(db, user=ctx.user, ws=ctx.workspace, action="report.share", rtype="report", rid=r.id, details={"with": r.shared_with}, ip=client_ip(request))
    db.commit()
    return report_out(r)


@router.get("/{rid}/download")
def download(rid: str, request: Request, ctx: Ctx = Depends(require("viewer")), db: Session = Depends(get_db)):
    r = _get(db, ctx, rid)
    pdf = rsvc.render_pdf({**r.content, "title": r.content["title"]})
    audit(db, user=ctx.user, ws=ctx.workspace, action="report.download", rtype="report", rid=r.id, ip=client_ip(request))
    db.commit()
    fn = f"newsapiens-{r.period_type}-{r.period_start.isoformat()}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@router.delete("/{rid}")
def delete_report(rid: str, ctx: Ctx = Depends(require("member")), db: Session = Depends(get_db)):
    r = _get(db, ctx, rid, owner_only=True)
    r.deleted_at = utcnow()
    db.commit()
    return {"deleted": rid}
