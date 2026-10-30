from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import RANK
from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import audit, client_ip, current_user, rate_limit_auth
from ..models import (AudioAsset, ChatSession, DailyReport, Organization, OrganizationMember, RecordingSession, User, Workspace,
                      WorkspaceMember, ExtractedTask)
from ..security import COOKIE, create_token, hash_password, verify_password
from ..services.storage import get_storage

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "UTC"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def user_out(db: Session, u: User) -> dict:
    wss = db.execute(select(Workspace, WorkspaceMember.role).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                     .where(WorkspaceMember.user_id == u.id, Workspace.deleted_at.is_(None))).all()
    orgs = db.execute(select(Organization, OrganizationMember.role).join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
                      .where(OrganizationMember.user_id == u.id, Organization.deleted_at.is_(None))).all()
    return {"id": str(u.id), "email": u.email, "name": u.name, "timezone": u.timezone, "audio_retention_days": u.audio_retention_days,
            "workspaces": [{"id": str(w.id), "name": w.name, "type": w.type, "role": r, "organization_id": str(w.organization_id) if w.organization_id else None,
                            "report_time": w.report_time, "timezone": w.timezone, "auto_daily_report": w.auto_daily_report} for w, r in wss],
            "organizations": [{"id": str(o.id), "name": o.name, "role": r} for o, r in orgs]}


def _set_cookie(resp: Response, token: str):
    s = get_settings()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=s.cookie_secure, max_age=s.access_token_minutes * 60, path="/")


@router.post("/register", status_code=201, dependencies=[Depends(rate_limit_auth)])
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower()
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(body.timezone)
    except Exception:
        body.timezone = "UTC"  # unknown zone from the client: start on UTC; the user can change it in Settings
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    u = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), timezone=body.timezone)
    db.add(u)
    db.flush()
    ws = Workspace(name=f"{u.name}'s workspace", type="individual", owner_user_id=u.id, timezone=body.timezone)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role="owner"))
    audit(db, user=u, ws=ws, action="auth.register", ip=client_ip(request))
    db.commit()
    _set_cookie(response, create_token(u.id))
    return {"user": user_out(db, u), "token": create_token(u.id)}


@router.post("/login", dependencies=[Depends(rate_limit_auth)])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    if not u or u.deleted_at or not u.is_active or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid email or password")
    audit(db, user=u, action="auth.login", ip=client_ip(request))
    db.commit()
    tok = create_token(u.id)
    _set_cookie(response, tok)
    return {"user": user_out(db, u), "token": tok}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return user_out(db, user)


class ProfileIn(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None
    audio_retention_days: Optional[int] = Field(default=None, ge=1, le=3650)
    clear_retention: bool = False


@router.patch("/me")
def update_me(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.name:
        user.name = body.name.strip()
    if body.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(body.timezone)
        except Exception:
            raise HTTPException(422, "Unknown timezone")
        user.timezone = body.timezone
    if body.clear_retention:
        user.audio_retention_days = None
    elif body.audio_retention_days:
        user.audio_retention_days = body.audio_retention_days
    db.commit()
    return user_out(db, user)


@router.get("/export")
def export_my_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Data export (portability): everything the user owns, as JSON. Audio files are exported via signed URLs on sessions."""
    from ..models import (Commitment, ConversationSummary, ExtractedDecision, Insight, TranscriptionSegment)
    sess = db.scalars(select(RecordingSession).where(RecordingSession.owner_id == user.id, RecordingSession.deleted_at.is_(None))).all()
    ids = [s.id for s in sess]

    def rows(M, col):
        return [{c.name: (str(getattr(r, c.name)) if getattr(r, c.name) is not None else None) for c in M.__table__.columns}
                for r in db.scalars(select(M).where(col.in_(ids)))] if ids else []
    return {"user": user_out(db, user), "sessions": [{c.name: str(getattr(s, c.name)) for c in RecordingSession.__table__.columns} for s in sess],
            "segments": rows(TranscriptionSegment, TranscriptionSegment.session_id), "summaries": rows(ConversationSummary, ConversationSummary.session_id),
            "decisions": rows(ExtractedDecision, ExtractedDecision.session_id), "commitments": rows(Commitment, Commitment.session_id),
            "insights": rows(Insight, Insight.session_id), "tasks": [{c.name: str(getattr(t, c.name)) for c in ExtractedTask.__table__.columns}
                                                                     for t in db.scalars(select(ExtractedTask).where(ExtractedTask.owner_id == user.id))]}


class DeleteIn(BaseModel):
    password: str


@router.delete("/me")
def delete_account(body: DeleteIn, request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Irreversibly deletes the account, the personal workspace, all owned recordings (audio included) and derived data.
    Refused while the user still owns an organization (transfer or delete it first)."""
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "Wrong password")
    if db.scalar(select(Organization).where(Organization.owner_id == user.id, Organization.deleted_at.is_(None))):
        raise HTTPException(409, "You own an organization; transfer ownership or delete it first")
    store = get_storage()
    for s in db.scalars(select(RecordingSession).where(RecordingSession.owner_id == user.id)):
        store.delete_prefix(f"{s.workspace_id}/{s.id}")
    audit(db, action="auth.account_deleted", rtype="user", rid=user.id, ip=client_ip(request))
    uid = user.id
    db.delete(user)  # FK ON DELETE CASCADE removes owned rows (sessions, derived data, memberships, chats)
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"deleted": str(uid)}
