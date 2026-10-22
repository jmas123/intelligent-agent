"""Pydantic response models for the chat API."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

EST = ZoneInfo("America/New_York")


class CreateTaskRequest(BaseModel):
    """Request body for manually creating a task."""

    title: str
    due_date: str | None = None  # ISO 8601 date or datetime
    type: str = "personal"  # assignment | exam | meeting | personal | project | interview_prep
    course: str | None = None
    urgency: int = 3  # 1-5


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
    affect: str | None = None  # Phase 24: enjoyment | neutral | avoidance | anxiety
    # Phase 27: Priority ranking
    priority_rank: int | None = None  # 1 = highest priority
    priority_score: float | None = None  # 0.0-1.0
    priority_action: str | None = None  # suggested next action
    # Phase 28: Risk assessment
    risk_level: str | None = None  # low | medium | high | critical
    risk_pct: int | None = None  # P(miss) as 0-100
    risk_summary: str | None = None  # human-readable risk line

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


class WeeklySnapshotResponse(BaseModel):
    """Serialized weekly snapshot for API responses."""

    id: int
    week_start: str
    week_end: str
    tasks_completed: int
    tasks_slipped: int
    tasks_upcoming: int
    total_work_minutes: int
    narrative: str
    semester_week_number: int | None
    created_at: str

    model_config = {"from_attributes": True}


class RecoveryDailyBlock(BaseModel):
    """A single scheduled work block in a recovery plan."""

    day: str
    time_window: str
    action: str


class RecoveryPlanResponse(BaseModel):
    """Recovery plan for an at-risk task."""

    task_title: str
    status: str
    days_behind: float
    estimated_hours_remaining: float
    daily_blocks: list[RecoveryDailyBlock]
    tradeoff_note: str


class RecoveryPlansResponse(BaseModel):
    """Response containing recovery plans."""

    plans: list[RecoveryPlanResponse]


class RecoveryPlanRequest(BaseModel):
    """Request body for on-demand recovery plans."""

    task_ids: list[int] = []


class AmbientNotificationResponse(BaseModel):
    """A single ambient notification."""

    title: str
    body: str
    category: str
    priority: str
    timestamp: str


class CurrentModeResponse(BaseModel):
    """Active mode with summary data."""

    mode: str | None
    label: str
    summary: str
    life_contexts: list[LifeContextResponse] = []


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
    # Phase 25 fields
    company_tier: str | None = None
    role_type: str | None = None
    resume_variant: str | None = None


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


# ── Phase 25: Recruiting analytics schemas ───────────────────────────


class ResponseRateEntry(BaseModel):
    """Response rate for a single dimension (tier, method, etc.)."""

    key: str
    total: int
    responded: int
    rate: float


class FitScoreEntry(BaseModel):
    """Fit score for an active application."""

    company: str
    score: float
    rank: int
    matching_factors: list[str] = []


class RecruitingAnalyticsResponse(BaseModel):
    """Full recruiting analytics payload."""

    response_rates: list[ResponseRateEntry] = []
    over_indexing_alerts: list[dict] = []
    tier_gaps: list[dict] = []
    resume_effectiveness: list[ResponseRateEntry] = []
    temporal_patterns: list[dict] = []
    fit_scores: list[FitScoreEntry] = []
    pipeline_stats: dict = {}


# ── Goal schemas ─────────────────────────────────────────────────────


class GoalResponse(BaseModel):
    """Serialized goal for API responses."""

    id: int
    description: str
    category: str
    target_metric: str | None
    status: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class CreateGoalRequest(BaseModel):
    """Request body for creating a goal."""

    description: str
    category: str  # academic | recruiting | health | social | personal
    target_metric: str | None = None


class GoalStatusUpdate(BaseModel):
    """Request body for updating goal status."""

    status: str  # achieved | abandoned


# ── Decision schemas ─────────────────────────────────────────────────


class DecisionResponse(BaseModel):
    """Serialized decision for API responses."""

    id: int
    description: str
    alternatives_considered: list[str]
    chosen_option: str
    context_json: dict
    outcome: str | None
    outcome_recorded_at: str | None
    created_at: str

    model_config = {"from_attributes": True}


class CreateDecisionRequest(BaseModel):
    """Request body for recording a decision."""

    description: str
    chosen_option: str
    alternatives: list[str] | None = None
    context: dict | None = None


class RecordOutcomeRequest(BaseModel):
    """Request body for recording a decision outcome."""

    outcome: str


# ── Relationship schemas ─────────────────────────────────────────────


class RelationshipResponse(BaseModel):
    """Serialized relationship for API responses."""

    id: int
    person: str
    channel: str
    interaction_count: int
    last_interaction_at: str | None
    avg_response_time_hours: float | None
    trend: str
    energy_signal: str | None

    model_config = {"from_attributes": True}


# ── Simulation schemas ──────────────────────────────────────────────


class SimulateRequest(BaseModel):
    """Request body for behavioral simulation."""

    scenario: str


class SimulationImpact(BaseModel):
    """A single projected impact."""

    domain: str
    impact: str
    severity: str


class SimulateResponse(BaseModel):
    """Behavioral simulation result."""

    scenario: str
    confidence: str
    confidence_reason: str
    projected_impacts: list[SimulationImpact]
    weekly_projection: str
    recommendation: str
    data_density: dict[str, int]


class SimulateRequestV2(BaseModel):
    """Request body for enhanced simulation with risk grounding."""

    scenario: str
    session_id: str | None = None


class ScenarioRiskDeltaResponse(BaseModel):
    """Per-task risk delta under a simulated scenario."""

    task_id: int
    task_title: str
    course: str | None
    p_miss_before: float
    p_miss_after: float
    delta: float
    risk_level_before: str
    risk_level_after: str
    hours_available_before: float
    hours_available_after: float


class DropAnalysisResponse(BaseModel):
    """Impact analysis for dropping a task."""

    dropped_task: str
    freed_hours: float
    beneficiaries: list[dict[str, object]]
    net_risk_change: float


class ProjectedScheduleGap(BaseModel):
    """A free window in the projected schedule."""

    start: str
    end: str
    duration_minutes: int


class SimulateResponseV2(BaseModel):
    """Enhanced simulation result with grounded risk deltas."""

    scenario: str
    session_id: str
    confidence: str
    confidence_reason: str
    projected_impacts: list[SimulationImpact]
    weekly_projection: str
    recommendation: str
    data_density: dict[str, int]
    risk_deltas: list[ScenarioRiskDeltaResponse]
    projected_schedule: list[ProjectedScheduleGap]
    drop_analysis: DropAnalysisResponse | None


# ── Task affect schemas ─────────────────────────────────────


class TaskAffectResponse(BaseModel):
    """Inferred affect for a task type."""

    task_type: str
    affect_label: str  # enjoyment | neutral | avoidance | anxiety
    confidence: float
    evidence: dict[str, object]
    intervention: str
    energy_label: str | None = None  # energizing | neutral | draining


# ── Priority ranking schemas ───────────────────────────────────────────


class PriorityItemResponse(BaseModel):
    """A single ranked priority for API responses."""

    rank: int
    task_id: int
    title: str
    course: str | None
    type: str
    time_label: str
    score: float
    risk_if_ignored: str
    suggested_action: str
    reasoning: str


class PrioritiesListResponse(BaseModel):
    """Full priority ranking payload."""

    priorities: list[PriorityItemResponse]
    computed_at: str | None = None


# ── Risk assessment schemas ────────────────────────────────────────────


class TaskRiskResponse(BaseModel):
    """Risk assessment for a single task."""

    task_id: int
    title: str
    course: str | None
    type: str
    time_label: str
    probability_of_missing: float
    probability_pct: int
    hours_needed: float
    hours_available: float
    hours_worked: float
    confidence: float
    confidence_reason: str
    risk_level: str  # low | medium | high | critical
    summary: str


class RiskListResponse(BaseModel):
    """Full risk assessment payload."""

    risks: list[TaskRiskResponse]
    high_risk_count: int = 0
    critical_count: int = 0


class MeetingBriefingResponse(BaseModel):
    """Pre-meeting briefing for an upcoming calendar event."""

    summary: str
    start: str
    minutes_until: int
    briefing: str
    attendee_count: int


# ── Intervention / Brain tab schemas ────────────────────────────────


class InterventionRecordResponse(BaseModel):
    """A single intervention decision (fired or suppressed)."""

    id: int
    task_id: int | None
    risk_level: str
    intervention_tier: str
    category: str
    title: str
    body: str
    fired: bool
    suppression_reason: str | None
    user_acted: bool
    created_at: str

    model_config = {"from_attributes": True}


class InterventionBudget(BaseModel):
    """Current intervention budget usage."""

    fired_today: int
    max_per_day: int
    quiet_hours_active: bool
    quiet_hours_start: str
    quiet_hours_end: str


class InterventionTierStat(BaseModel):
    """Effectiveness stat for a single tier."""

    tier: str
    total: int
    acted: int
    rate: float


class InterventionStatsResponse(BaseModel):
    """Aggregated intervention stats for the Brain tab."""

    budget: InterventionBudget
    effectiveness: list[InterventionTierStat]
    escalations_pending: int


class InterventionEscalationResponse(BaseModel):
    """A pending escalation."""

    id: int
    task_id: int
    current_tier: str
    next_tier: str
    escalate_after: str
    created_at: str

    model_config = {"from_attributes": True}


class DailyReconstructionSegment(BaseModel):
    """A single segment in the daily timeline."""

    start_time: str
    end_time: str
    activity_type: str
    task_id: int | None
    task_title: str | None
    description: str
    confidence: float
    confidence_sources: dict[str, object] | None = None
    confirmed: bool | None


class DailyReconstructionResponse(BaseModel):
    """Daily reconstructed timeline."""

    date: str
    overall_confidence: float
    user_confirmed: bool
    narrative: str
    segments: list[DailyReconstructionSegment]


class MicroConfirmationResponse(BaseModel):
    """A timeline segment needing user confirmation."""

    date: str
    segment_index: int
    task_id: int | None
    task_title: str | None
    start_time: str
    end_time: str
    confidence: float
    confirmed: bool | None


class MicroConfirmationAction(BaseModel):
    """Request body for responding to a micro-confirmation."""

    date: str
    segment_index: int
    confirmed: bool


class SessionBriefingPriority(BaseModel):
    """One ranked priority in the session briefing."""

    task_id: int
    rank: int
    title: str
    course: str | None
    time_label: str
    risk_level: str
    probability_pct: int | None
    suggested_action: str
    why: str


class SessionBriefingTimeBlock(BaseModel):
    """A recommended focus block carved out of available time."""

    task_id: int | None
    title: str
    minutes: int
    rationale: str


class SessionBriefingResponse(BaseModel):
    """On-demand session briefing — Phase 32 structured payload."""

    briefing: str | None  # formatted text (back-compat)
    generated_at: str | None = None
    available_minutes: int = 0
    available_label: str = ""
    life_context: str | None = None
    next_event: dict[str, str] | None = None
    priorities: list[SessionBriefingPriority] = []
    extras: list[str] = []
    time_blocks: list[SessionBriefingTimeBlock] = []
    recommendation: str = ""


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
