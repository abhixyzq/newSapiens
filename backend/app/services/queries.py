"""Shared permission-scoped queries for derived records (used by REST routers, RAG and reports)."""
from sqlalchemy import and_, or_, select

from ..access import Ctx, visible_session_filter
from ..models import (Commitment, ExtractedDecision, ExtractedTask, Insight, RecordingSession)


def _vis_ids(ctx: Ctx):
    return select(RecordingSession.id).where(visible_session_filter(ctx), RecordingSession.exclude_from_processing.is_(False))


def decisions_q(ctx: Ctx):
    return select(ExtractedDecision).where(ExtractedDecision.workspace_id == ctx.workspace.id,
                                           ExtractedDecision.session_id.in_(_vis_ids(ctx)),
                                           ExtractedDecision.confirmation_status != "rejected")


def tasks_q(ctx: Ctx):
    """AI tasks inherit session visibility; manual (session-less) tasks are private to creator unless shared."""
    return select(ExtractedTask).where(
        ExtractedTask.workspace_id == ctx.workspace.id, ExtractedTask.deleted_at.is_(None),
        or_(ExtractedTask.session_id.in_(_vis_ids(ctx)),
            and_(ExtractedTask.session_id.is_(None),
                 or_(ExtractedTask.owner_id == ctx.user.id, ExtractedTask.visibility == "shared"))))


def commitments_q(ctx: Ctx):
    return select(Commitment).where(Commitment.workspace_id == ctx.workspace.id,
                                    Commitment.session_id.in_(_vis_ids(ctx)), Commitment.status != "cancelled")


def insights_q(ctx: Ctx):
    return select(Insight).where(Insight.workspace_id == ctx.workspace.id, Insight.session_id.in_(_vis_ids(ctx)),
                                 Insight.status != "dismissed")


OPEN_TASK = ("detected", "needs_confirmation", "pending", "in_progress")
