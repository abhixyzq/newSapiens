"""Relational model. Every content row carries workspace_id (tenant boundary).
Content visibility is decided once, on recording_sessions (owner_id + visibility);
all derived rows (segments, decisions, tasks, chunks, ...) inherit it via session_id."""
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, Uuid)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .config import get_settings
from .db import Base, utcnow

try:  # pgvector is only needed on PostgreSQL
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover
    Vector = None


class Embedding(TypeDecorator):
    """pgvector `vector(dim)` on PostgreSQL, JSON list elsewhere (dev/test)."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql" and Vector is not None:
            return dialect.type_descriptor(Vector(get_settings().embedding_dim))
        return dialect.type_descriptor(JSON())


def pk():
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def fk(target: str, *, null=False, ondelete="CASCADE", index=True):
    return mapped_column(Uuid, ForeignKey(target, ondelete=ondelete), nullable=null, index=index)


class TS:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SoftDelete:
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------ identity & tenancy
class User(Base, TS, SoftDelete):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    audio_retention_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # null = keep until deleted


class Organization(Base, TS, SoftDelete):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[uuid.UUID] = fk("users.id", ondelete="RESTRICT")


class OrganizationMember(Base, TS):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)
    id: Mapped[uuid.UUID] = pk()
    organization_id: Mapped[uuid.UUID] = fk("organizations.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    role: Mapped[str] = mapped_column(String(20), default="member")  # owner|admin|manager|member|viewer


class Invitation(Base, TS):
    __tablename__ = "organization_invitations"
    id: Mapped[uuid.UUID] = pk()
    organization_id: Mapped[uuid.UUID] = fk("organizations.id")
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    invited_by: Mapped[uuid.UUID] = fk("users.id")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Workspace(Base, TS, SoftDelete):
    """individual: one owner. organization: org-wide shared space. team: a team/project space inside an org."""
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20), default="individual")
    owner_user_id: Mapped[Optional[uuid.UUID]] = fk("users.id", null=True, ondelete="CASCADE")
    organization_id: Mapped[Optional[uuid.UUID]] = fk("organizations.id", null=True)
    report_time: Mapped[str] = mapped_column(String(5), default="18:00")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    auto_daily_report: Mapped[bool] = mapped_column(Boolean, default=True)
    audio_retention_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class WorkspaceMember(Base, TS):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    role: Mapped[str] = mapped_column(String(20), default="member")


class Project(Base, TS, SoftDelete):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    aliases: Mapped[Any] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[Optional[uuid.UUID]] = fk("users.id", null=True, ondelete="SET NULL")


# ------------------------------------------------------------------ capture
class RecordingSession(Base, TS, SoftDelete):
    __tablename__ = "recording_sessions"
    __table_args__ = (Index("ix_rs_ws_occurred", "workspace_id", "occurred_at"),)
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    title: Mapped[str] = mapped_column(String(300), default="Untitled session")
    kind: Mapped[str] = mapped_column(String(20), default="conversation")  # meeting|conversation|voice_note
    source: Mapped[str] = mapped_column(String(30), default="upload")
    # mic_desktop|mic_mobile|upload|meeting_integration|voice_note|transcript_import
    status: Mapped[str] = mapped_column(String(30), default="created", index=True)
    # created|recording|paused|uploaded|transcribing|awaiting_transcription|analyzing|ready|failed|excluded
    visibility: Mapped[str] = mapped_column(String(20), default="private")  # private|shared
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    project_id: Mapped[Optional[uuid.UUID]] = fk("projects.id", null=True, ondelete="SET NULL")
    exclude_from_processing: Mapped[bool] = mapped_column(Boolean, default=False)  # sensitive discussion
    retention_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[Any] = mapped_column(JSON, default=dict)  # pause log, client info, processing log


class AudioAsset(Base, TS, SoftDelete):
    __tablename__ = "audio_assets"
    __table_args__ = (UniqueConstraint("session_id", "chunk_index"),)
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str] = mapped_column(String(500))
    filename: Mapped[str] = mapped_column(String(300), default="")
    mime: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")


class Speaker(Base, TS):
    __tablename__ = "speakers"
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    label: Mapped[str] = mapped_column(String(100))  # SPEAKER_00 etc. from diarization / transcript
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = fk("memory_entities.id", null=True, ondelete="SET NULL")

    @property
    def name(self) -> str:
        return self.display_name or self.label


class TranscriptionSegment(Base, TS):
    __tablename__ = "transcription_segments"
    __table_args__ = (UniqueConstraint("session_id", "idx"),)
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    idx: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    speaker_id: Mapped[Optional[uuid.UUID]] = fk("speakers.id", null=True, ondelete="SET NULL")
    text: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str] = mapped_column(Text)  # preserved when a user edits `text`
    source_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # as spoken, before translation; null = same as text
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class Meeting(Base, TS, SoftDelete):
    __tablename__ = "meetings"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    title: Mapped[str] = mapped_column(String(300))
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    project_id: Mapped[Optional[uuid.UUID]] = fk("projects.id", null=True, ondelete="SET NULL")


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    id: Mapped[uuid.UUID] = pk()
    meeting_id: Mapped[uuid.UUID] = fk("meetings.id")
    name: Mapped[str] = mapped_column(String(200))
    entity_id: Mapped[Optional[uuid.UUID]] = fk("memory_entities.id", null=True, ondelete="SET NULL")
    speaker_id: Mapped[Optional[uuid.UUID]] = fk("speakers.id", null=True, ondelete="SET NULL")


# ------------------------------------------------------------------ intelligence outputs
class ConversationSummary(Base, TS):
    __tablename__ = "conversation_summaries"
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id", index=True)
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    executive_summary: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[Any] = mapped_column(JSON, default=list)
    key_statements: Mapped[Any] = mapped_column(JSON, default=list)  # [{text, segment_ids}]
    outcomes: Mapped[Any] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(100), default="")  # e.g. "extractive" or "llm:<model>"


class _Extracted(TS):
    """Shared columns for AI-extracted, reviewable records."""
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    segment_ids: Mapped[Any] = mapped_column(JSON, default=list)
    timestamp_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai|manual
    user_modified: Mapped[bool] = mapped_column(Boolean, default=False)


class ExtractedDecision(Base, _Extracted):
    __tablename__ = "extracted_decisions"
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    text: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="")
    participants: Mapped[Any] = mapped_column(JSON, default=list)
    project_id: Mapped[Optional[uuid.UUID]] = fk("projects.id", null=True, ondelete="SET NULL")
    confirmation_status: Mapped[str] = mapped_column(String(20), default="needs_review")
    # needs_review | confirmed | rejected


class ExtractedTask(Base, _Extracted, SoftDelete):
    __tablename__ = "extracted_tasks"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    session_id: Mapped[Optional[uuid.UUID]] = fk("recording_sessions.id", null=True)
    owner_id: Mapped[Optional[uuid.UUID]] = fk("users.id", null=True, ondelete="SET NULL")  # creator / visibility owner
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    assignee_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    assignee_user_id: Mapped[Optional[uuid.UUID]] = fk("users.id", null=True, ondelete="SET NULL")
    assignee_evidence: Mapped[str] = mapped_column(String(30), default="none")  # explicit_name|first_person|manual|none
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_text: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    project_id: Mapped[Optional[uuid.UUID]] = fk("projects.id", null=True, ondelete="SET NULL")
    status: Mapped[str] = mapped_column(String(30), default="needs_confirmation")
    # detected|needs_confirmation|pending|in_progress|completed|cancelled
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    remind_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # reminds the owner only
    history: Mapped[Any] = mapped_column(JSON, default=list)  # status/owner/deadline change log


class Commitment(Base, _Extracted):
    __tablename__ = "commitments"
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    person_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="promise")  # promise|follow_up|responsibility
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_text: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|fulfilled|cancelled
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)


class Insight(Base, _Extracted):
    """Risks/blockers, ideas, and open questions."""
    __tablename__ = "extracted_insights"
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    kind: Mapped[str] = mapped_column(String(20), index=True)  # risk|idea|question
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)


class TimelineEvent(Base, TS):
    """One activity in a session, grouped by the LLM from consecutive transcript lines. start_at/end_at are speech time
    (session occurred_at + evidence segment offsets), never processing time."""
    __tablename__ = "timeline_events"
    __table_args__ = (Index("ix_te_ws_start", "workspace_id", "start_at"),)
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id", index=False)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(20), default="other")  # work|study|personal|meeting|other
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)  # offset into the session, for "open transcript here"
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    segment_ids: Mapped[Any] = mapped_column(JSON, default=list)


class DailySummary(Base, TS):
    """'Your day at a glance' per user, workspace and local date; generated_at is processing time."""
    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", "date"),)
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", index=False)
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id", index=False)
    date: Mapped[date] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text, default="")
    highlights: Mapped[Any] = mapped_column(JSON, default=list)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_by: Mapped[str] = mapped_column(String(100), default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ------------------------------------------------------------------ knowledge / memory
class KnowledgeDocument(Base, TS):
    __tablename__ = "knowledge_documents"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    kind: Mapped[str] = mapped_column(String(20))  # transcript|summary
    title: Mapped[str] = mapped_column(String(300), default="")


class KnowledgeChunk(Base, TS):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_kc_ws_occ", "workspace_id", "occurred_at"),)
    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = fk("knowledge_documents.id")
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    project_id: Mapped[Optional[uuid.UUID]] = fk("projects.id", null=True, ondelete="SET NULL")
    kind: Mapped[str] = mapped_column(String(20), default="transcript")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    speakers: Mapped[Any] = mapped_column(JSON, default=list)
    segment_ids: Mapped[Any] = mapped_column(JSON, default=list)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    embedding: Mapped[Any] = mapped_column(Embedding, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(100), default="")


class MemoryEntity(Base, TS):
    __tablename__ = "memory_entities"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    type: Mapped[str] = mapped_column(String(20), default="person")  # person|team|company|product|project
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[Any] = mapped_column(JSON, default=list)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    attributes: Mapped[Any] = mapped_column(JSON, default=dict)
    project_id: Mapped[Optional[uuid.UUID]] = fk("projects.id", null=True, ondelete="SET NULL")


class EntityRelationship(Base, TS):
    __tablename__ = "entity_relationships"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    from_entity_id: Mapped[uuid.UUID] = fk("memory_entities.id")
    to_entity_id: Mapped[uuid.UUID] = fk("memory_entities.id")
    relation: Mapped[str] = mapped_column(String(50))  # e.g. participated_with, assigned_to
    session_id: Mapped[Optional[uuid.UUID]] = fk("recording_sessions.id", null=True)


# ------------------------------------------------------------------ chat
class ChatSession(Base, TS, SoftDelete):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    title: Mapped[str] = mapped_column(String(300), default="New chat")


class ChatMessage(Base, TS):
    __tablename__ = "chat_messages"
    id: Mapped[uuid.UUID] = pk()
    chat_id: Mapped[uuid.UUID] = fk("chat_sessions.id")
    role: Mapped[str] = mapped_column(String(10))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[Any] = mapped_column(JSON, default=dict)  # plan/filters, clarification options, found flag


class ChatCitation(Base):
    __tablename__ = "chat_citations"
    id: Mapped[uuid.UUID] = pk()
    message_id: Mapped[uuid.UUID] = fk("chat_messages.id")
    ref: Mapped[str] = mapped_column(String(10))  # S1, S2 ...
    session_id: Mapped[Optional[uuid.UUID]] = fk("recording_sessions.id", null=True, ondelete="SET NULL")
    record_type: Mapped[str] = mapped_column(String(20))  # transcript|summary|decision|task|commitment|insight
    record_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    label: Mapped[str] = mapped_column(String(400), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")


# ------------------------------------------------------------------ reports
class DailyReport(Base, TS, SoftDelete):
    __tablename__ = "daily_reports"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    period_type: Mapped[str] = mapped_column(String(20), default="daily")  # daily|weekly|monthly|organization
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|approved|failed
    content: Mapped[Any] = mapped_column(JSON, default=dict)
    shared_with: Mapped[Any] = mapped_column(JSON, default=list)  # [user_id str]
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ReportSource(Base):
    __tablename__ = "report_sources"
    id: Mapped[uuid.UUID] = pk()
    report_id: Mapped[uuid.UUID] = fk("daily_reports.id")
    session_id: Mapped[Optional[uuid.UUID]] = fk("recording_sessions.id", null=True, ondelete="SET NULL")
    record_type: Mapped[str] = mapped_column(String(20))
    record_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


# ------------------------------------------------------------------ platform
class Integration(Base, TS):
    __tablename__ = "integrations"
    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    provider: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    config: Mapped[Any] = mapped_column(JSON, default=dict)  # never store secrets here
    created_by: Mapped[Optional[uuid.UUID]] = fk("users.id", null=True, ondelete="SET NULL")


class ConsentRecord(Base, TS):
    __tablename__ = "consent_records"
    id: Mapped[uuid.UUID] = pk()
    session_id: Mapped[uuid.UUID] = fk("recording_sessions.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    consent_type: Mapped[str] = mapped_column(String(40))  # self_recording|participants_notified|upload_rights
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    statement: Mapped[str] = mapped_column(Text, default="")
    participants_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = pk()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True, index=True)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True, index=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(50), default="")
    resource_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Any] = mapped_column(JSON, default=dict)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class Notification(Base, TS):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id")
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
