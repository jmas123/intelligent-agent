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


class WeeklySnapshot(Base):
    """Persistent weekly summary for longitudinal reasoning."""

    __tablename__ = "weekly_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[str]  # ISO date (Monday of the week)
    week_end: Mapped[str]  # ISO date (Sunday)
    tasks_completed: Mapped[int] = mapped_column(default=0)
    tasks_slipped: Mapped[int] = mapped_column(default=0)
    tasks_upcoming: Mapped[int] = mapped_column(default=0)
    total_work_minutes: Mapped[int] = mapped_column(default=0)
    stats_json: Mapped[str] = mapped_column(default="{}")  # JSON: by_course breakdowns
    narrative: Mapped[str] = mapped_column(default="")  # LLM-generated weekly summary
    life_contexts_json: Mapped[str] = mapped_column(default="[]")  # active contexts that week
    health_signals_json: Mapped[str] = mapped_column(default="{}")  # late nights, zero days
    semester_week_number: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


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
    # Phase 25: recruiting intelligence fields
    company_tier: Mapped[str | None] = mapped_column(default=None)  # big_tech | mid_cap | startup_early | startup_growth | finance | other
    role_type: Mapped[str | None] = mapped_column(default=None)  # swe | data | pm | infra | ml | other
    resume_variant: Mapped[str | None] = mapped_column(default=None)  # filename stem of resume used
    application_method: Mapped[str | None] = mapped_column(default=None)  # direct | referral | aggregator | career_fair
    applied_day_of_week: Mapped[int | None] = mapped_column(default=None)  # 0=Mon..6=Sun
    applied_hour: Mapped[int | None] = mapped_column(default=None)  # hour in local time
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ExtractionLog(Base):
    """Opt-in training data: input prompt → extracted JSON for LoRA fine-tuning."""

    __tablename__ = "extraction_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    input_text: Mapped[str]  # prompt sent to LLM
    output_json: Mapped[str]  # ExtractedTask JSON
    model_used: Mapped[str]  # model tag that produced this
    confidence: Mapped[float]
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), default=None)
    accepted: Mapped[bool] = mapped_column(default=True)  # flipped to False when task dismissed
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DigestLog(Base):
    """Opt-in training data: state prompt → LLM output for LoRA fine-tuning."""

    __tablename__ = "digest_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    input_prompt: Mapped[str]  # state snapshot text
    output_text: Mapped[str]  # LLM-generated output
    model_used: Mapped[str]
    prompt_type: Mapped[str]  # "digest" | "insight" | "query"
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class IdentityDocument(Base):
    """Durable 'about you' document. Single row, evolves over time via version increments."""

    __tablename__ = "identity_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    document_json: Mapped[str] = mapped_column(default="{}")  # structured identity data
    document_markdown: Mapped[str] = mapped_column(default="")  # portable rendering
    last_synthesis_at: Mapped[datetime] = mapped_column(server_default=func.now())
    synthesis_sources_json: Mapped[str] = mapped_column(default="{}")  # what data was consumed
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class KnowledgeEntity(Base):
    """Entity in the personal knowledge graph (person, company, course, project)."""

    __tablename__ = "knowledge_entities"
    __table_args__ = (UniqueConstraint("entity_type", "normalized_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str]  # person | company | course | project | institution
    name: Mapped[str]  # display name
    normalized_name: Mapped[str]  # lowercase dedup key
    metadata_json: Mapped[str] = mapped_column(default="{}")  # role, notes, etc.
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    mention_count: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Goal(Base):
    """User-stated intention for tracking behavior gaps."""

    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str]
    category: Mapped[str]  # academic | recruiting | health | social | personal
    target_metric: Mapped[str | None] = mapped_column(default=None)  # e.g. "4h/week on CS 301"
    status: Mapped[str] = mapped_column(default="active")  # active | achieved | abandoned
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Decision(Base):
    """Logged decision with optional outcome tracking."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str]
    alternatives_considered: Mapped[str] = mapped_column(default="[]")  # JSON list[str]
    chosen_option: Mapped[str]
    context_json: Mapped[str] = mapped_column(default="{}")  # ambient state at decision time
    outcome: Mapped[str | None] = mapped_column(default=None)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Relationship(Base):
    """Tracked interpersonal relationship with trend detection."""

    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("person_normalized", "channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person: Mapped[str]  # display name
    person_normalized: Mapped[str]  # lowercase dedup key
    channel: Mapped[str]  # email | calendar
    interaction_count: Mapped[int] = mapped_column(default=0)
    last_interaction_at: Mapped[datetime | None] = mapped_column(default=None)
    avg_response_time_hours: Mapped[float | None] = mapped_column(default=None)
    trend: Mapped[str] = mapped_column(default="stable")  # growing | stable | atrophying
    energy_signal: Mapped[str | None] = mapped_column(default=None)  # positive | neutral | draining
    metadata_json: Mapped[str] = mapped_column(default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class EntityRelationship(Base):
    """Relationship between two knowledge entities."""

    __tablename__ = "entity_relationships"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "relationship_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_entity_id: Mapped[int] = mapped_column(ForeignKey("knowledge_entities.id"))
    target_entity_id: Mapped[int] = mapped_column(ForeignKey("knowledge_entities.id"))
    relationship_type: Mapped[str]  # teaches | works_at | enrolled_in | recruiter_for
    confidence: Mapped[float] = mapped_column(default=0.5)
    evidence_json: Mapped[str] = mapped_column(default="[]")  # JSON list of evidence snippets
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
