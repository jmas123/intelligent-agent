"""Pydantic response models for the chat API."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

EST = ZoneInfo("America/New_York")


class TaskResponse(BaseModel):
    """Serialized task for API responses."""

    id: int
    title: str
    due: str | None = None
    source: str
    type: str
    course: str | None
    urgency: str | None = None
    status: str

    model_config = {"from_attributes": True}


class StatusUpdate(BaseModel):
    """Request body for status updates."""

    status: str


class TaskCountByStatus(BaseModel):
    """Task counts grouped by status."""

    pending: int = 0
    done: int = 0
    dismissed: int = 0


class InsightResponse(BaseModel):
    """Serialized insight for API responses."""

    id: int
    type: str
    content: str
    related_task_ids: list[int]
    dismissed: bool
    created_at: str

    model_config = {"from_attributes": True}


class ContextSnapshot(BaseModel):
    """Aggregated state snapshot for LLM grounding."""

    now: str
    tasks_due_today: list[TaskResponse]
    tasks_due_this_week: list[TaskResponse]
    overdue_tasks: list[TaskResponse]
    task_count_by_status: TaskCountByStatus
    proactive_alerts: list[dict[str, object]]
    insights: list[InsightResponse] = []
    life_contexts: list[LifeContextResponse] = []


class DigestResponse(BaseModel):
    """Digest text response."""

    text: str | None
    task_count: int


class QueryRequest(BaseModel):
    """Request body for natural language queries."""

    question: str


class QueryResponse(BaseModel):
    """Response for natural language queries."""

    answer: str


class LifeContextResponse(BaseModel):
    """Serialized life context for API responses."""

    id: int
    season: str
    label: str | None
    start_date: str
    end_date: str
    source: str
    active: bool

    model_config = {"from_attributes": True}


class SetLifeContextRequest(BaseModel):
    """Request body for setting a manual life context."""

    season: str
    start_date: str
    end_date: str
    label: str | None = None


class ActionResponse(BaseModel):
    """Serialized proposed action for API responses."""

    id: int
    type: str
    status: str
    task_id: int | None
    title: str
    payload: str
    execution_error: str | None
    created_at: str
    approved_at: str | None
    executed_at: str | None

    model_config = {"from_attributes": True}


class ApproveActionRequest(BaseModel):
    """Optional overrides when approving a calendar block."""

    start_iso: str | None = None
    duration_minutes: int | None = None


class ProposeBlockRequest(BaseModel):
    """Request to propose a calendar time block."""

    task_id: int
    start_iso: str
    end_iso: str


class NegotiateRequest(BaseModel):
    """Request body for multi-turn scheduling negotiation."""

    session_id: int | None = None  # None = start new session
    message: str


class NegotiateResponse(BaseModel):
    """Response from negotiation endpoint."""

    session_id: int
    reply: str
    proposed_actions: list[dict[str, object]]
    status: str  # active | resolved | abandoned


class NegotiationSessionResponse(BaseModel):
    """Full negotiation session for API/widget display."""

    id: int
    status: str
    trigger: str
    original_action: ActionResponse | None
    alternatives: list[dict[str, object]]
    created_at: str
    updated_at: str


class DebriefRequest(BaseModel):
    """Request body for semester debrief generation."""

    term: str | None = None
    start: str | None = None  # YYYY-MM-DD
    end: str | None = None  # YYYY-MM-DD


class DebriefResponse(BaseModel):
    """Semester debrief result."""

    record_id: int
    term_name: str
    debrief_text: str
    stats: dict[str, object]


class SemesterRecordResponse(BaseModel):
    """Stored semester record for listing."""

    id: int
    term_name: str
    start_date: str
    end_date: str
    tasks_completed: int
    tasks_slipped: int
    total_work_minutes: int
    debrief_text: str
    created_at: str


class GmailSearchRequest(BaseModel):
    """Request body for Gmail search."""

    query: str
    max_results: int = 10


class GmailMessage(BaseModel):
    """A single Gmail message result."""

    id: str
    subject: str
    sender: str
    snippet: str
    date: str


class SignalEntry(BaseModel):
    """A single signal in the recruiting audit trail."""

    type: str
    date: str
    summary: str


class ApplicationResponse(BaseModel):
    """A single recruiting application."""

    id: int
    company_name: str
    status: str
    applied_at: str | None
    days_since: int | None
    staleness: str  # green | yellow | orange | red
    last_signal_at: str
    signal_count: int
    signals: list[SignalEntry] = []


class UpcomingInterview(BaseModel):
    """An upcoming interview event."""

    task_id: int
    title: str
    company_name: str | None
    due: str | None


class RecruitingPipelineResponse(BaseModel):
    """Full recruiting pipeline view."""

    applications: list[ApplicationResponse]
    upcoming_interviews: list[UpcomingInterview]
    summary: dict[str, int]


def _format_due(due_str: str) -> str:
    """Convert ISO date to a human-friendly relative label in EST."""
    try:
        due = datetime.fromisoformat(due_str)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        due_est = due.astimezone(EST)
    except (ValueError, TypeError):
        return due_str

    now_est = datetime.now(EST)
    delta_days = (due_est.date() - now_est.date()).days
    time_str = due_est.strftime("%I:%M %p %Z").lstrip("0")

    if delta_days < 0:
        return f"OVERDUE ({abs(delta_days)}d ago)"
    if delta_days == 0:
        return f"today at {time_str}"
    if delta_days == 1:
        return f"tomorrow at {time_str}"
    if delta_days < 7:
        return f"{due_est.strftime('%A')} at {time_str}"
    return f"{due_est.strftime('%b %d, %Y')} at {time_str}"
