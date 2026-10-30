"""Single authorization policy. Every read path (REST, RAG retrieval, reports) goes through here.

Rules
- Tenant boundary = workspace. A user only ever sees workspaces they are a member of.
- Organization membership grants NO access to other people's content.
- Content has visibility `private` (owner only) or `shared` (members of that workspace, any role).
  Team- vs organization-shared is the workspace the session lives in (team workspace vs org workspace).
- Writing requires role >= member. Viewers are read-only.
"""
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .models import (OrganizationMember, RecordingSession, User, Workspace, WorkspaceMember)

RANK = {"viewer": 0, "member": 1, "manager": 2, "admin": 3, "owner": 4}


@dataclass
class Ctx:
    user: User
    workspace: Workspace
    role: str

    def need(self, min_role: str):
        if RANK[self.role] < RANK[min_role]:
            raise HTTPException(403, f"Requires role {min_role}")


def workspace_role(db: Session, user_id, ws_id) -> Optional[str]:
    m = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == ws_id, WorkspaceMember.user_id == user_id))
    return m.role if m else None


def org_role(db: Session, user_id, org_id) -> Optional[str]:
    m = db.scalar(select(OrganizationMember).where(
        OrganizationMember.organization_id == org_id, OrganizationMember.user_id == user_id))
    return m.role if m else None


def visible_session_filter(ctx: Ctx):
    """SQL predicate over RecordingSession rows the caller may read."""
    return and_(
        RecordingSession.workspace_id == ctx.workspace.id,
        RecordingSession.deleted_at.is_(None),
        or_(RecordingSession.owner_id == ctx.user.id, RecordingSession.visibility == "shared"),
    )


def visible_session_ids(db: Session, ctx: Ctx, include_excluded=False):
    q = select(RecordingSession.id).where(visible_session_filter(ctx))
    if not include_excluded:
        q = q.where(RecordingSession.exclude_from_processing.is_(False))
    return q


def get_visible_session(db: Session, ctx: Ctx, session_id) -> RecordingSession:
    try:
        sid = uuid.UUID(str(session_id))
    except ValueError:
        raise HTTPException(404, "Not found")
    s = db.scalar(select(RecordingSession).where(RecordingSession.id == sid, visible_session_filter(ctx)))
    if not s:  # 404, not 403: do not reveal existence across scopes
        raise HTTPException(404, "Not found")
    return s


def get_owned_session(db: Session, ctx: Ctx, session_id) -> RecordingSession:
    s = get_visible_session(db, ctx, session_id)
    if s.owner_id != ctx.user.id and RANK[ctx.role] < RANK["admin"]:
        raise HTTPException(403, "Only the owner can modify this session")
    if s.owner_id != ctx.user.id and s.visibility != "shared":
        raise HTTPException(404, "Not found")
    return s
