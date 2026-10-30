import uuid
from datetime import timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import RANK, org_role, workspace_role
from ..db import get_db, utcnow
from ..deps import audit, client_ip, current_user
from ..models import (AuditLog, Invitation, Organization, OrganizationMember, User, Workspace, WorkspaceMember)
from ..security import hash_token, new_token

router = APIRouter(prefix="/api", tags=["organizations"])
Role = Literal["admin", "manager", "member", "viewer"]


def _org(db: Session, user: User, org_id, min_role="viewer") -> tuple[Organization, str]:
    try:
        oid = uuid.UUID(str(org_id))
    except ValueError:
        raise HTTPException(404, "Not found")
    o = db.get(Organization, oid)
    role = org_role(db, user.id, oid) if o and not o.deleted_at else None
    if not role:
        raise HTTPException(404, "Not found")  # non-members cannot learn the org exists
    if RANK[role] < RANK[min_role]:
        raise HTTPException(403, f"Requires role {min_role}")
    return o, role


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/organizations", status_code=201)
def create_org(body: OrgIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    o = Organization(name=body.name.strip(), owner_id=user.id)
    db.add(o)
    db.flush()
    db.add(OrganizationMember(organization_id=o.id, user_id=user.id, role="owner"))
    ws = Workspace(name=o.name, type="organization", organization_id=o.id, timezone=user.timezone)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    audit(db, user=user, ws=ws, org_id=o.id, action="org.create", rtype="organization", rid=o.id, ip=client_ip(request))
    db.commit()
    return {"id": str(o.id), "name": o.name, "workspace_id": str(ws.id), "role": "owner"}


@router.get("/organizations")
def list_orgs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Organization, OrganizationMember.role).join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
                      .where(OrganizationMember.user_id == user.id, Organization.deleted_at.is_(None))).all()
    out = []
    for o, r in rows:
        wss = db.execute(select(Workspace, WorkspaceMember.role).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                         .where(Workspace.organization_id == o.id, WorkspaceMember.user_id == user.id, Workspace.deleted_at.is_(None))).all()
        out.append({"id": str(o.id), "name": o.name, "role": r,
                    "workspaces": [{"id": str(w.id), "name": w.name, "type": w.type, "role": wr} for w, wr in wss]})
    return out


class InviteIn(BaseModel):
    email: EmailStr
    role: Role = "member"


@router.post("/organizations/{org_id}/invitations", status_code=201)
def invite(org_id: str, body: InviteIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    o, _ = _org(db, user, org_id, "admin")
    raw, h = new_token()
    inv = Invitation(organization_id=o.id, email=body.email.lower(), role=body.role, token_hash=h, invited_by=user.id,
                     expires_at=utcnow() + timedelta(days=7))
    db.add(inv)
    audit(db, user=user, org_id=o.id, action="org.invite", rtype="invitation", rid=inv.id, details={"email": body.email, "role": body.role}, ip=client_ip(request))
    db.commit()
    # No email transport is configured in this build: the admin shares this one-time token with the invitee.
    return {"id": str(inv.id), "email": inv.email, "role": inv.role, "expires_at": inv.expires_at.isoformat(), "token": raw,
            "note": "Email delivery is not configured; share this token with the invitee securely."}


class AcceptIn(BaseModel):
    token: str


@router.post("/invitations/accept")
def accept(body: AcceptIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(body.token)))
    if not inv or inv.accepted_at or inv.expires_at < utcnow() or inv.email != user.email:
        raise HTTPException(400, "Invalid, expired or already-used invitation (it must match your account email)")
    if not org_role(db, user.id, inv.organization_id):
        db.add(OrganizationMember(organization_id=inv.organization_id, user_id=user.id, role=inv.role))
        org_ws = db.scalar(select(Workspace).where(Workspace.organization_id == inv.organization_id, Workspace.type == "organization"))
        db.add(WorkspaceMember(workspace_id=org_ws.id, user_id=user.id, role=inv.role))
    inv.accepted_at = utcnow()
    audit(db, user=user, org_id=inv.organization_id, action="org.invite_accepted", rtype="invitation", rid=inv.id, ip=client_ip(request))
    db.commit()
    return {"organization_id": str(inv.organization_id), "role": inv.role}


@router.get("/organizations/{org_id}/members")
def members(org_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    o, _ = _org(db, user, org_id)
    rows = db.execute(select(User, OrganizationMember.role).join(OrganizationMember, OrganizationMember.user_id == User.id)
                      .where(OrganizationMember.organization_id == o.id)).all()
    return [{"user_id": str(u.id), "name": u.name, "email": u.email, "role": r} for u, r in rows]


class RoleIn(BaseModel):
    role: Role


@router.patch("/organizations/{org_id}/members/{user_id}")
def set_role(org_id: str, user_id: str, body: RoleIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    o, _ = _org(db, user, org_id, "admin")
    m = db.scalar(select(OrganizationMember).where(OrganizationMember.organization_id == o.id, OrganizationMember.user_id == uuid.UUID(user_id)))
    if not m or m.role == "owner":
        raise HTTPException(404, "Member not found (the owner's role cannot be changed)")
    m.role = body.role
    for wm in db.scalars(select(WorkspaceMember).join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                         .where(Workspace.organization_id == o.id, Workspace.type == "organization", WorkspaceMember.user_id == m.user_id)):
        wm.role = body.role
    audit(db, user=user, org_id=o.id, action="org.role_change", rtype="user", rid=user_id, details={"role": body.role}, ip=client_ip(request))
    db.commit()
    return {"user_id": user_id, "role": body.role}


@router.delete("/organizations/{org_id}/members/{user_id}")
def remove_member(org_id: str, user_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    o, _ = _org(db, user, org_id, "admin")
    uid = uuid.UUID(user_id)
    m = db.scalar(select(OrganizationMember).where(OrganizationMember.organization_id == o.id, OrganizationMember.user_id == uid))
    if not m or m.role == "owner":
        raise HTTPException(404, "Member not found (the owner cannot be removed)")
    db.delete(m)
    for wm in db.scalars(select(WorkspaceMember).join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                         .where(Workspace.organization_id == o.id, WorkspaceMember.user_id == uid)):
        db.delete(wm)
    audit(db, user=user, org_id=o.id, action="org.member_removed", rtype="user", rid=user_id, ip=client_ip(request))
    db.commit()
    return {"removed": user_id}


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    member_ids: list[str] = []


@router.post("/organizations/{org_id}/teams", status_code=201)
def create_team(org_id: str, body: TeamIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A team is its own workspace: its shared content is visible to team members only, not the whole org."""
    o, _ = _org(db, user, org_id, "manager")
    ws = Workspace(name=body.name.strip(), type="team", organization_id=o.id, timezone=user.timezone)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="admin" if org_role(db, user.id, o.id) != "owner" else "owner"))
    for mid in set(body.member_ids) - {str(user.id)}:
        if org_role(db, uuid.UUID(mid), o.id):
            db.add(WorkspaceMember(workspace_id=ws.id, user_id=uuid.UUID(mid), role="member"))
    audit(db, user=user, ws=ws, org_id=o.id, action="team.create", rtype="workspace", rid=ws.id, ip=client_ip(request))
    db.commit()
    return {"id": str(ws.id), "name": ws.name, "type": "team"}


@router.get("/organizations/{org_id}/audit")
def audit_log(org_id: str, limit: int = 100, offset: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db)):
    o, _ = _org(db, user, org_id, "admin")
    rows = db.scalars(select(AuditLog).where(AuditLog.organization_id == o.id).order_by(AuditLog.created_at.desc())
                      .limit(min(limit, 500)).offset(offset)).all()
    return [{"at": r.created_at.isoformat(), "user_id": str(r.user_id) if r.user_id else None, "action": r.action,
             "resource_type": r.resource_type, "resource_id": r.resource_id, "details": r.details} for r in rows]


class WsSettings(BaseModel):
    name: Optional[str] = None
    report_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: Optional[str] = None
    auto_daily_report: Optional[bool] = None
    audio_retention_days: Optional[int] = Field(default=None, ge=1, le=3650)


@router.patch("/workspaces/{ws_id}")
def ws_settings(ws_id: str, body: WsSettings, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = db.get(Workspace, uuid.UUID(ws_id))
    role = workspace_role(db, user.id, ws.id) if ws else None
    if not role:
        raise HTTPException(404, "Not found")
    if RANK[role] < RANK["admin"]:
        raise HTTPException(403, "Requires role admin")
    if body.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(body.timezone)
        except Exception:
            raise HTTPException(422, "Unknown timezone")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(ws, k, v)
    audit(db, user=user, ws=ws, action="workspace.settings", rtype="workspace", rid=ws.id, details=body.model_dump(exclude_none=True), ip=client_ip(request))
    db.commit()
    return {"id": str(ws.id), "report_time": ws.report_time, "timezone": ws.timezone, "auto_daily_report": ws.auto_daily_report,
            "audio_retention_days": ws.audio_retention_days, "name": ws.name}
