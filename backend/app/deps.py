import hmac
import time
import uuid
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import RANK, Ctx, workspace_role
from .config import get_settings
from .db import get_db
from .models import AuditLog, User, Workspace
from .security import COOKIE, decode_token


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:]
    uid = decode_token(token) if token else None
    user = None
    if uid:
        try:
            user = db.get(User, uuid.UUID(uid))
        except ValueError:
            user = None
    if not user or not user.is_active or user.deleted_at:
        raise HTTPException(401, "Not authenticated")
    return user


def require_api_token(authorization: str = Header(default="")):
    """Optional static bearer token (API_TOKEN). Empty = endpoint is open (single-user / private network)."""
    expected = get_settings().api_token
    if not expected:
        return
    if not hmac.compare_digest(authorization.removeprefix("Bearer ").encode(), expected.encode()):
        raise HTTPException(401, "Invalid token")


def get_ctx(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user),
            x_workspace_id: Optional[str] = Header(default=None)) -> Ctx:
    if x_workspace_id:
        try:
            ws = db.get(Workspace, uuid.UUID(x_workspace_id))
        except ValueError:
            ws = None
        role = workspace_role(db, user.id, ws.id) if ws and not ws.deleted_at else None
        if not ws or not role:
            raise HTTPException(403, "Not a member of this workspace")
    else:
        ws = db.scalar(select(Workspace).where(Workspace.owner_user_id == user.id,
                                               Workspace.type == "individual", Workspace.deleted_at.is_(None)))
        if not ws:
            raise HTTPException(409, "No personal workspace")
        role = "owner"
    return Ctx(user=user, workspace=ws, role=role)


def require(min_role: str):
    def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        if RANK[ctx.role] < RANK[min_role]:
            raise HTTPException(403, f"Requires role {min_role}")
        return ctx
    return dep


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def audit(db: Session, *, user=None, ws=None, org_id=None, action: str, rtype="", rid=None, details=None, ip=None):
    db.add(AuditLog(user_id=user.id if user else None, workspace_id=ws.id if ws else None,
                    organization_id=org_id or (ws.organization_id if ws else None), action=action,
                    resource_type=rtype, resource_id=str(rid) if rid else None, details=details or {}, ip=ip))


# ---- naive in-process sliding-window rate limiter (swap for Redis when running >1 process)
_hits: dict[str, deque] = defaultdict(deque)


def _check(key: str, limit: int, window=60):
    now = time.time()
    q = _hits[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests", headers={"Retry-After": str(window)})
    q.append(now)


def rate_limit_global(request: Request):
    _check(f"g:{client_ip(request)}", get_settings().rate_limit_per_minute)


def rate_limit_auth(request: Request):
    _check(f"a:{client_ip(request)}", get_settings().auth_rate_limit_per_minute)
