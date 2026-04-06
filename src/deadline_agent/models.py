"""SQLAlchemy models for task storage."""

from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""


class Task(Base):
    """Extracted task/deadline. No raw content stored."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    due_date_iso: Mapped[str | None]
    source: Mapped[str]  # gmail | moodle | gcal | slack | notion
    type: Mapped[str]  # assignment | exam | meeting | reminder | announcement | interview_prep | networking | personal
    course: Mapped[str | None]
    urgency_score: Mapped[int]  # 1-5
    confidence: Mapped[float]  # 0.0-1.0
    raw_hash: Mapped[str] = mapped_column(unique=True)  # sha256 for dedup
    embedding: Mapped[str | None] = mapped_column(default=None)  # JSON-serialized float list
    status: Mapped[str] = mapped_column(default="pending")  # pending | done | dismissed
    alert_24h_sent: Mapped[bool] = mapped_column(default=False)
    alert_2h_sent: Mapped[bool] = mapped_column(default=False)
    no_work_alert_sent: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class FileActivity(Base):
    """Record of file system activity. Only metadata stored, never file content."""

    __tablename__ = "file_activity"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str]  # absolute path
    filename: Mapped[str]  # basename
    directory: Mapped[str]  # parent dir
    size_bytes: Mapped[int]
    modified_at: Mapped[datetime]  # file mtime
    event_type: Mapped[str]  # created | modified | moved
    life_track: Mapped[str | None] = mapped_column(default=None)  # school | recruiting | project
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class FileTaskLink(Base):
    """Association between a file activity event and a task."""

    __tablename__ = "file_task_links"
    __table_args__ = (UniqueConstraint("file_activity_id", "task_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_activity_id: Mapped[int] = mapped_column(ForeignKey("file_activity.id"))
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    confidence: Mapped[float]  # 0.0-1.0
    method: Mapped[str]  # string | embedding | directory
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Insight(Base):
    """LLM-generated proactive suggestion linked to tasks."""

    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str]  # no_progress | workload_spike | overdue_cluster | suggestion
    content: Mapped[str]  # human-readable suggestion text
    related_task_ids: Mapped[str] = mapped_column(default="[]")  # JSON list[int]
    dismissed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ProposedAction(Base):
    """User-approvable action proposed by the reasoning engine."""

    __tablename__ = "proposed_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str]  # calendar_block | gmail_draft
    status: Mapped[str] = mapped_column(default="proposed")
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), default=None)
    title: Mapped[str]  # human-readable summary
    payload: Mapped[str]  # JSON action parameters
    execution_error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(default=None)
    executed_at: Mapped[datetime | None] = mapped_column(default=None)


class WorkSession(Base):
    """Inferred work session for a task, clustered from FileActivity timestamps."""

    __tablename__ = "work_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime]
    duration_minutes: Mapped[int]
    file_activity_count: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LifeContext(Base):
    """Active life context / season. Multiple may overlap."""

    __tablename__ = "life_contexts"

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[str]  # recruiting | exams | light_week | default
    label: Mapped[str | None] = mapped_column(default=None)  # human-friendly label
    start_date: Mapped[str]  # ISO date string (YYYY-MM-DD)
    end_date: Mapped[str]  # ISO date string (YYYY-MM-DD)
    source: Mapped[str]  # manual | auto
    active: Mapped[bool] = mapped_column(default=True)
    metadata_json: Mapped[str | None] = mapped_column(default=None)  # JSON extra info
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BehavioralPattern(Base):
    """Persistent learned behavior. Never deleted by clear_stale()."""

    __tablename__ = "behavioral_patterns"
    __table_args__ = (UniqueConstraint("pattern_type", "pattern_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_type: Mapped[str]  # effort_accuracy | peak_hours | lead_time | session_duration
    pattern_key: Mapped[str]  # e.g. "assignment", "14", "CS101"
    value: Mapped[str]  # JSON-encoded
    sample_count: Mapped[int] = mapped_column(default=0)
    confidence: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class NegotiationSession(Base):
    """Multi-turn scheduling negotiation conversation."""

    __tablename__ = "negotiation_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(default="active")  # active | resolved | abandoned
    trigger: Mapped[str]  # conflict_409 | user_initiated | priority_reschedule
    original_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposed_actions.id"), default=None
    )
    proposed_alternatives: Mapped[str] = mapped_column(default="[]")  # JSON
    conversation_history: Mapped[str] = mapped_column(default="[]")  # JSON
    resolution: Mapped[str | None] = mapped_column(default=None)  # JSON
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class SemesterRecord(Base):
    """End-of-semester debrief snapshot."""

    __tablename__ = "semester_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    term_name: Mapped[str]  # e.g. "Winter 2026"
    start_date: Mapped[str]  # YYYY-MM-DD
    end_date: Mapped[str]  # YYYY-MM-DD
    tasks_completed: Mapped[int] = mapped_column(default=0)
    tasks_slipped: Mapped[int] = mapped_column(default=0)
    total_work_minutes: Mapped[int] = mapped_column(default=0)
    analytics_json: Mapped[str] = mapped_column(default="{}")  # JSON
    debrief_text: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class RecruitingApplication(Base):
    """Tracked job application, auto-populated from files and emails."""

    __tablename__ = "recruiting_applications"
    __table_args__ = (UniqueConstraint("company_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str]  # display name: "Google"
    company_normalized: Mapped[str]  # lowercase dedup key: "google"
    status: Mapped[str] = mapped_column(default="applied")  # applied | response | interview | offer | closed
    applied_at: Mapped[datetime | None] = mapped_column(default=None)  # earliest signal date
    last_signal_at: Mapped[datetime] = mapped_column(server_default=func.now())
    source: Mapped[str] = mapped_column(default="file")  # file | email | calendar
    signals_json: Mapped[str] = mapped_column(default="[]")  # JSON list of {type, date, summary}
    related_task_ids: Mapped[str] = mapped_column(default="[]")  # JSON list[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
