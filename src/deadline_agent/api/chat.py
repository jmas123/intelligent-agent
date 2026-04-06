"""Chat API router for Open WebUI integration."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.api.schemas import (
    ActionResponse,
    ApplicationResponse,
    ApproveActionRequest,
    ContextSnapshot,
    DebriefRequest,
    DebriefResponse,
    DigestResponse,
    GmailMessage,
    GmailSearchRequest,
    InsightResponse,
    LifeContextResponse,
    NegotiateRequest,
    NegotiateResponse,
    NegotiationSessionResponse,
    ProposeBlockRequest,
    QueryRequest,
    QueryResponse,
    RecruitingPipelineResponse,
    SemesterRecordResponse,
    SetLifeContextRequest,
    SignalEntry,
    StatusUpdate,
    TaskCountByStatus,
    TaskResponse,
    UpcomingInterview,
    _format_due,
)
from deadline_agent.models import Insight, ProposedAction, Task
from deadline_agent.store.session import get_session

router = APIRouter(tags=["chat"])

VALID_STATUSES = {"pending", "done", "dismissed"}
USER_TZ = ZoneInfo("America/New_York")

DBSession = Annotated[Session, Depends(get_session)]


def _task_to_response(task: Task) -> TaskResponse:
    urgency = "!" * task.urgency_score + f" ({task.urgency_score}/5)"
    return TaskResponse(
        id=task.id,
        title=task.title,
        due=_format_due(task.due_date_iso) if task.due_date_iso else None,
        source=task.source,
        type=task.type,
        course=task.course,
        urgency=urgency,
        status=task.status,
    )


def _today_bounds() -> tuple[str, str]:
    """Return (start_of_today, end_of_today) as UTC ISO strings using user's timezone."""
    now = datetime.now(USER_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()


def _week_end() -> str:
    """Return end of the current week (7 days from start of today) as UTC ISO string."""
    now = datetime.now(USER_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start + timedelta(days=7)).astimezone(UTC).isoformat()


@router.get("/tasks")
def list_tasks(
    session: DBSession,
    status: str | None = None,
    source: str | None = None,
    due_today: bool = False,
    due_this_week: bool = False,
    limit: int = 50,
) -> list[TaskResponse]:
    """List tasks with optional filters."""
    stmt = select(Task).order_by(Task.due_date_iso.asc())
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if source is not None:
        stmt = stmt.where(Task.source == source)
    if due_today:
        start, end = _today_bounds()
        stmt = stmt.where(
            Task.due_date_iso >= start,
            Task.due_date_iso < end,
        )
    elif due_this_week:
        now_iso = datetime.now(UTC).isoformat()
        stmt = stmt.where(
            Task.due_date_iso >= now_iso,
            Task.due_date_iso < _week_end(),
        )
    stmt = stmt.limit(limit)
    tasks = list(session.scalars(stmt).all())
    return [_task_to_response(t) for t in tasks]


@router.get("/tasks/{task_id}")
def get_task(session: DBSession, task_id: int) -> TaskResponse:
    """Get a single task by ID."""
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_response(task)


@router.patch("/tasks/{task_id}/status")
def update_task_status(session: DBSession, task_id: int, body: StatusUpdate) -> TaskResponse:
    """Update a task's status."""
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
        )
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = body.status
    session.commit()
    session.refresh(task)
    return _task_to_response(task)


@router.get("/digest")
async def get_digest(session: DBSession) -> DigestResponse:
    """Get the current morning digest (insight-driven when LLM is available)."""
    from deadline_agent.notifications.digest import generate_digest_v2

    text = await generate_digest_v2(session)
    count = session.scalar(select(func.count(Task.id)).where(Task.status == "pending")) or 0
    return DigestResponse(text=text, task_count=count)


@router.get("/context")
def get_context(session: DBSession) -> ContextSnapshot:
    """Build a full context snapshot for LLM grounding."""
    now_iso = datetime.now(UTC).isoformat()
    start_of_today, end_of_today = _today_bounds()
    end_of_week = _week_end()

    # Tasks due today
    due_today_tasks = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= start_of_today)
            .where(Task.due_date_iso < end_of_today)
            .order_by(Task.due_date_iso.asc())
        ).all()
    )

    # Tasks due this week (excluding today)
    due_week_tasks = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= end_of_today)
            .where(Task.due_date_iso < end_of_week)
            .order_by(Task.due_date_iso.asc())
        ).all()
    )

    # Overdue tasks
    overdue_tasks = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso < start_of_today)
            .where(Task.due_date_iso.is_not(None))
            .order_by(Task.due_date_iso.asc())
        ).all()
    )

    # Counts by status
    counts: dict[str, int] = {"pending": 0, "done": 0, "dismissed": 0}
    rows = session.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all()
    for row_status, count in rows:
        if row_status in counts:
            counts[row_status] = count

    # Build proactive alerts
    alerts: list[dict[str, object]] = []
    if due_today_tasks:
        alerts.append(
            {
                "type": "due_today",
                "message": f"You have {len(due_today_tasks)} task(s) due today",
                "task_ids": [t.id for t in due_today_tasks],
            }
        )
    if overdue_tasks:
        alerts.append(
            {
                "type": "overdue",
                "message": f"You have {len(overdue_tasks)} overdue task(s)",
                "task_ids": [t.id for t in overdue_tasks],
            }
        )

    # Active insights
    from deadline_agent.store.insight_repository import InsightRepository

    insight_repo = InsightRepository(session)
    active_insights = insight_repo.list_active(limit=5)
    insight_responses = [_insight_to_response(i) for i in active_insights]

    # Active life contexts
    from deadline_agent.store.context_repository import LifeContextRepository

    context_repo = LifeContextRepository(session)
    active_contexts = context_repo.get_active()
    context_responses = [
        LifeContextResponse(
            id=c.id,
            season=c.season,
            label=c.label,
            start_date=c.start_date,
            end_date=c.end_date,
            source=c.source,
            active=c.active,
        )
        for c in active_contexts
    ]

    return ContextSnapshot(
        now=now_iso,
        tasks_due_today=[_task_to_response(t) for t in due_today_tasks],
        tasks_due_this_week=[_task_to_response(t) for t in due_week_tasks],
        overdue_tasks=[_task_to_response(t) for t in overdue_tasks],
        task_count_by_status=TaskCountByStatus(**counts),
        proactive_alerts=alerts,
        insights=insight_responses,
        life_contexts=context_responses,
    )


def _insight_to_response(insight: Insight) -> InsightResponse:
    import json

    return InsightResponse(
        id=insight.id,
        type=insight.type,
        content=insight.content,
        related_task_ids=json.loads(insight.related_task_ids),
        dismissed=insight.dismissed,
        created_at=insight.created_at.isoformat(),
    )


@router.get("/insights")
def list_insights(session: DBSession, limit: int = 10) -> list[InsightResponse]:
    """List active insights."""
    from deadline_agent.store.insight_repository import InsightRepository

    repo = InsightRepository(session)
    return [_insight_to_response(i) for i in repo.list_active(limit=limit)]


@router.post("/insights/{insight_id}/dismiss")
def dismiss_insight(session: DBSession, insight_id: int) -> InsightResponse:
    """Dismiss an insight."""
    from deadline_agent.store.insight_repository import InsightRepository

    repo = InsightRepository(session)
    result = repo.dismiss(insight_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Insight not found")
    return _insight_to_response(result)


@router.post("/query")
async def query(session: DBSession, body: QueryRequest) -> QueryResponse:
    """Answer a natural language question with full state context."""
    from pathlib import Path
    Path.home().joinpath(".deadline-agent", "query_debug.log").write_text(
        f"HIT /query endpoint with: {body.question!r}\n"
    )
    from deadline_agent.reasoning.engine import answer_query

    answer = await answer_query(session, body.question)
    return QueryResponse(answer=answer)


def _action_to_response(action: ProposedAction) -> ActionResponse:
    return ActionResponse(
        id=action.id,
        type=action.type,
        status=action.status,
        task_id=action.task_id,
        title=action.title,
        payload=action.payload,
        execution_error=action.execution_error,
        created_at=action.created_at.isoformat(),
        approved_at=action.approved_at.isoformat() if action.approved_at else None,
        executed_at=action.executed_at.isoformat() if action.executed_at else None,
    )


@router.get("/actions")
def list_actions(session: DBSession, limit: int = 20) -> list[ActionResponse]:
    """List pending proposed actions."""
    from deadline_agent.store.action_repository import ActionRepository

    repo = ActionRepository(session)
    return [_action_to_response(a) for a in repo.list_pending(limit=limit)]


@router.post("/actions/{action_id}/approve")
async def approve_action(
    session: DBSession,
    action_id: int,
    body: ApproveActionRequest | None = None,
) -> ActionResponse:
    """Approve and execute a proposed action.

    For calendar_block actions, optional start_iso and duration_minutes
    override the proposed time. A conflict check prevents double-booking.
    """
    import json

    from deadline_agent.actions.executor import execute_action
    from deadline_agent.store.action_repository import ActionRepository

    repo = ActionRepository(session)
    action = repo.get(action_id)
    if action is None or action.status != "proposed":
        raise HTTPException(status_code=404, detail="Action not found or not pending")

    # Apply user edits to calendar_block payload
    if action.type == "calendar_block" and body and (body.start_iso or body.duration_minutes):
        payload = json.loads(action.payload)
        if body.start_iso:
            payload["start_iso"] = body.start_iso
        start_dt = datetime.fromisoformat(payload["start_iso"])
        duration = body.duration_minutes or int(
            (datetime.fromisoformat(payload["end_iso"]) - start_dt).total_seconds() / 60
        )
        end_dt = start_dt + timedelta(minutes=duration)
        payload["end_iso"] = end_dt.isoformat()
        action.payload = json.dumps(payload)

    # Conflict check for calendar blocks — on conflict, create negotiation session
    if action.type == "calendar_block":
        payload = json.loads(action.payload)
        try:
            from deadline_agent.reasoning.calendar_gaps import fetch_free_busy

            busy = await fetch_free_busy(payload["start_iso"], payload["end_iso"])
            if busy:
                try:
                    from deadline_agent.reasoning.negotiation import (
                        create_conflict_session,
                    )

                    session_data = await create_conflict_session(
                        session, action, payload["start_iso"], payload["end_iso"]
                    )
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "Time slot already booked",
                            "negotiation_session_id": session_data["session_id"],
                            "alternatives": session_data["alternatives"],
                        },
                    )
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "Time slot already booked",
                            "alternatives": [],
                        },
                    ) from None
        except HTTPException:
            raise
        except Exception:
            pass  # Calendar API unavailable — proceed without check

    action = repo.approve(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found or not pending")
    await execute_action(session, action)
    session.refresh(action)
    return _action_to_response(action)


@router.post("/actions/{action_id}/reject")
def reject_action(session: DBSession, action_id: int) -> ActionResponse:
    """Reject a proposed action."""
    from deadline_agent.store.action_repository import ActionRepository

    repo = ActionRepository(session)
    action = repo.reject(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found or not pending")
    return _action_to_response(action)


@router.post("/actions/propose-block")
def propose_block(session: DBSession, body: ProposeBlockRequest) -> ActionResponse:
    """Propose a calendar time block for a task."""
    from deadline_agent.actions.proposer import propose_time_block

    task = session.get(Task, body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    action = propose_time_block(session, task, body.start_iso, body.end_iso)
    return _action_to_response(action)


@router.get("/calendar-gaps")
async def get_calendar_gaps() -> list[dict[str, object]]:
    """Return free calendar windows in the next 7 days."""
    from datetime import UTC, timedelta

    from deadline_agent.reasoning.calendar_gaps import fetch_free_busy, find_gaps

    now = datetime.now(UTC)
    start = now.isoformat()
    end = (now + timedelta(days=7)).isoformat()
    busy = await fetch_free_busy(start, end)
    return find_gaps(busy, start, end)


@router.post("/propose-schedule")
async def propose_schedule(session: DBSession) -> list[ActionResponse]:
    """Run smart scheduler and return proposed time blocks."""
    from deadline_agent.reasoning.scheduler import propose_smart_schedule
    from deadline_agent.reasoning.state import build_unified_context

    ctx = await build_unified_context(session)
    proposals = propose_smart_schedule(ctx, session)
    return [_action_to_response(p) for p in proposals]


@router.get("/weekly-review")
async def get_weekly_review(session: DBSession) -> dict[str, str]:
    """Get the weekly review summary."""
    from deadline_agent.reasoning.weekly_review import generate_weekly_review

    review = await generate_weekly_review(session)
    return {"review": review}


@router.post("/analyze")
def run_analysis(session: DBSession) -> dict[str, object]:
    """Run behavioral analysis: infer work sessions and detect patterns."""
    from deadline_agent.behavioral.analyzer import run_all_analyses
    from deadline_agent.behavioral.session_inference import infer_sessions

    sessions = infer_sessions(session)
    patterns = run_all_analyses(session)
    return {
        "sessions_inferred": len(sessions),
        "patterns_updated": len(patterns),
    }


@router.get("/life-contexts")
def list_life_contexts(session: DBSession) -> list[LifeContextResponse]:
    """List active life contexts."""
    from deadline_agent.store.context_repository import LifeContextRepository

    repo = LifeContextRepository(session)
    return [
        LifeContextResponse(
            id=c.id,
            season=c.season,
            label=c.label,
            start_date=c.start_date,
            end_date=c.end_date,
            source=c.source,
            active=c.active,
        )
        for c in repo.get_active()
    ]


@router.post("/life-contexts")
def set_life_context(session: DBSession, body: SetLifeContextRequest) -> LifeContextResponse:
    """Set a manual life context."""
    valid_seasons = {"recruiting", "exams", "light_week", "default"}
    if body.season not in valid_seasons:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid season. Must be one of: {', '.join(sorted(valid_seasons))}",
        )
    from deadline_agent.store.context_repository import LifeContextRepository

    repo = LifeContextRepository(session)
    ctx = repo.set_manual(body.season, body.start_date, body.end_date, body.label)
    return LifeContextResponse(
        id=ctx.id,
        season=ctx.season,
        label=ctx.label,
        start_date=ctx.start_date,
        end_date=ctx.end_date,
        source=ctx.source,
        active=ctx.active,
    )


@router.post("/life-contexts/{context_id}/deactivate")
def deactivate_life_context(session: DBSession, context_id: int) -> LifeContextResponse:
    """Deactivate a life context."""
    from deadline_agent.store.context_repository import LifeContextRepository

    repo = LifeContextRepository(session)
    ctx = repo.deactivate(context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Life context not found")
    return LifeContextResponse(
        id=ctx.id,
        season=ctx.season,
        label=ctx.label,
        start_date=ctx.start_date,
        end_date=ctx.end_date,
        source=ctx.source,
        active=ctx.active,
    )


@router.get("/patterns")
def list_patterns(session: DBSession) -> list[dict[str, object]]:
    """List learned behavioral patterns (only confident ones)."""
    from deadline_agent.config import settings
    from deadline_agent.reasoning.state import _format_pattern
    from deadline_agent.store.pattern_repository import PatternRepository

    repo = PatternRepository(session)
    patterns = [
        p for p in repo.get_all()
        if p.confidence >= settings.min_pattern_confidence
    ]
    return [
        {
            "id": p.id,
            "pattern_type": p.pattern_type,
            "pattern_key": p.pattern_key,
            "value": p.value,
            "sample_count": p.sample_count,
            "confidence": p.confidence,
            "updated_at": p.updated_at.isoformat() if p.updated_at else "",
            "description": _format_pattern(p),
        }
        for p in patterns
    ]


@router.post("/negotiate")
async def negotiate(session: DBSession, body: NegotiateRequest) -> NegotiateResponse:
    """Multi-turn scheduling negotiation.

    Without session_id: creates a new user-initiated session.
    With session_id: continues an existing negotiation.
    """
    from deadline_agent.reasoning.negotiation import handle_negotiation_turn

    result = await handle_negotiation_turn(session, body.session_id, body.message)
    return NegotiateResponse(**result)


@router.get("/negotiations")
def list_negotiations(
    session: DBSession, status: str = "active", limit: int = 10
) -> list[NegotiationSessionResponse]:
    """List negotiation sessions, defaults to active ones."""
    import json as _json

    from deadline_agent.store.negotiation_repository import NegotiationRepository

    repo = NegotiationRepository(session)
    if status == "active":
        sessions_list = repo.list_active(limit=limit)
    else:
        from sqlalchemy import select as _sel

        from deadline_agent.models import NegotiationSession

        stmt = (
            _sel(NegotiationSession)
            .where(NegotiationSession.status == status)
            .order_by(NegotiationSession.created_at.desc())
            .limit(limit)
        )
        sessions_list = list(session.scalars(stmt).all())

    results: list[NegotiationSessionResponse] = []
    for neg in sessions_list:
        orig_action = None
        if neg.original_action_id:
            from deadline_agent.store.action_repository import ActionRepository

            action_repo = ActionRepository(session)
            orig = action_repo.get(neg.original_action_id)
            if orig:
                orig_action = _action_to_response(orig)

        results.append(
            NegotiationSessionResponse(
                id=neg.id,
                status=neg.status,
                trigger=neg.trigger,
                original_action=orig_action,
                alternatives=_json.loads(neg.proposed_alternatives),
                created_at=neg.created_at.isoformat(),
                updated_at=neg.updated_at.isoformat(),
            )
        )
    return results


@router.get("/negotiations/{session_id}")
def get_negotiation(session: DBSession, session_id: int) -> NegotiationSessionResponse:
    """Get a single negotiation session with its alternatives."""
    import json as _json

    from deadline_agent.store.negotiation_repository import NegotiationRepository

    repo = NegotiationRepository(session)
    neg = repo.get(session_id)
    if neg is None:
        raise HTTPException(status_code=404, detail="Negotiation session not found")

    orig_action = None
    if neg.original_action_id:
        from deadline_agent.store.action_repository import ActionRepository

        action_repo = ActionRepository(session)
        orig = action_repo.get(neg.original_action_id)
        if orig:
            orig_action = _action_to_response(orig)

    return NegotiationSessionResponse(
        id=neg.id,
        status=neg.status,
        trigger=neg.trigger,
        original_action=orig_action,
        alternatives=_json.loads(neg.proposed_alternatives),
        created_at=neg.created_at.isoformat(),
        updated_at=neg.updated_at.isoformat(),
    )


@router.post("/debrief")
async def generate_debrief(
    session: DBSession, body: DebriefRequest
) -> DebriefResponse:
    """Generate an end-of-semester debrief with analytics."""
    from deadline_agent.reasoning.semester_debrief import (
        generate_semester_debrief,
    )

    now = datetime.now(UTC)
    end = body.end or now.strftime("%Y-%m-%d")
    if body.start:
        start = body.start
    else:
        start_dt = now - timedelta(days=120)
        start = start_dt.strftime("%Y-%m-%d")
    if body.term:
        term = body.term
    else:
        month = now.month
        year = now.year
        if month <= 5:
            term = f"Winter {year}"
        elif month <= 8:
            term = f"Summer {year}"
        else:
            term = f"Fall {year}"

    result = await generate_semester_debrief(session, start, end, term)
    return DebriefResponse(
        record_id=result["record_id"],
        term_name=term,
        debrief_text=result["debrief_text"],
        stats=result["stats"],
    )


@router.get("/debrief")
def list_debriefs(session: DBSession) -> list[SemesterRecordResponse]:
    """List all stored semester debriefs."""
    from deadline_agent.store.semester_repository import (
        SemesterRecordRepository,
    )

    repo = SemesterRecordRepository(session)
    records = repo.list_all()
    return [
        SemesterRecordResponse(
            id=r.id,
            term_name=r.term_name,
            start_date=r.start_date,
            end_date=r.end_date,
            tasks_completed=r.tasks_completed,
            tasks_slipped=r.tasks_slipped,
            total_work_minutes=r.total_work_minutes,
            debrief_text=r.debrief_text,
            created_at=r.created_at.isoformat(),
        )
        for r in records
    ]


@router.get("/debrief/{record_id}")
def get_debrief(session: DBSession, record_id: int) -> SemesterRecordResponse:
    """Get a single semester debrief by ID."""
    from deadline_agent.store.semester_repository import (
        SemesterRecordRepository,
    )

    repo = SemesterRecordRepository(session)
    r = repo.get(record_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Debrief not found")
    return SemesterRecordResponse(
        id=r.id,
        term_name=r.term_name,
        start_date=r.start_date,
        end_date=r.end_date,
        tasks_completed=r.tasks_completed,
        tasks_slipped=r.tasks_slipped,
        total_work_minutes=r.total_work_minutes,
        debrief_text=r.debrief_text,
        created_at=r.created_at.isoformat(),
    )


@router.post("/search-gmail")
async def search_gmail(body: GmailSearchRequest) -> list[GmailMessage]:
    """Search Gmail messages. Returns metadata and snippets, never raw content."""
    import httpx

    from deadline_agent.auth import TokenManager

    tm = TokenManager()
    token = await tm.get_valid_token()
    if not token:
        raise HTTPException(status_code=503, detail="No valid Google token")

    headers = {"Authorization": f"Bearer {token}"}
    api = "https://gmail.googleapis.com/gmail/v1"

    async with httpx.AsyncClient() as client:
        # Search for messages
        resp = await client.get(
            f"{api}/users/me/messages",
            params={"q": body.query, "maxResults": body.max_results},
            headers=headers,
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Gmail API error: {resp.text}")

        message_ids = [m["id"] for m in resp.json().get("messages", [])]

        # Fetch metadata for each message
        results: list[GmailMessage] = []
        for msg_id in message_ids:
            msg_resp = await client.get(
                f"{api}/users/me/messages/{msg_id}",
                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                headers=headers,
                timeout=10.0,
            )
            if msg_resp.status_code != 200:
                continue

            msg_data = msg_resp.json()
            header_list = msg_data.get("payload", {}).get("headers", [])
            header_map = {h["name"].lower(): h["value"] for h in header_list}

            results.append(
                GmailMessage(
                    id=msg_id,
                    subject=header_map.get("subject", ""),
                    sender=header_map.get("from", ""),
                    snippet=msg_data.get("snippet", ""),
                    date=header_map.get("date", ""),
                )
            )

    return results


@router.get("/recruiting-pipeline")
def get_recruiting_pipeline(session: DBSession) -> RecruitingPipelineResponse:
    """Get the full recruiting pipeline view."""
    import json as _json

    from deadline_agent.awareness.recruiting_extractor import (
        extract_company_from_calendar,
    )
    from deadline_agent.store.recruiting_repository import RecruitingRepository

    repo = RecruitingRepository(session)
    apps = repo.list_all()
    now = datetime.now(UTC)

    app_responses: list[ApplicationResponse] = []
    summary: dict[str, int] = {s: 0 for s in ["applied", "response", "interview", "offer", "closed"]}

    for app in apps:
        days_since: int | None = None
        if app.applied_at:
            applied = app.applied_at
            if applied.tzinfo is None:
                applied = applied.replace(tzinfo=UTC)
            days_since = (now - applied).days

        if days_since is None:
            staleness = "green"
        elif days_since < 7:
            staleness = "green"
        elif days_since < 14:
            staleness = "yellow"
        elif days_since < 30:
            staleness = "orange"
        else:
            staleness = "red"

        raw_signals = _json.loads(app.signals_json)
        signal_entries = [
            SignalEntry(
                type=s.get("type", ""),
                date=s.get("date", ""),
                summary=s.get("summary", ""),
            )
            for s in raw_signals
        ]
        app_responses.append(
            ApplicationResponse(
                id=app.id,
                company_name=app.company_name,
                status=app.status,
                applied_at=app.applied_at.isoformat() if app.applied_at else None,
                days_since=days_since,
                staleness=staleness,
                last_signal_at=app.last_signal_at.isoformat(),
                signal_count=len(raw_signals),
                signals=signal_entries,
            )
        )
        if app.status in summary:
            summary[app.status] += 1

    # Upcoming interviews: tasks with type=interview_prep, due in the future
    interview_tasks = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.type == "interview_prep")
            .where(Task.due_date_iso >= now.isoformat())
            .order_by(Task.due_date_iso.asc())
            .limit(10)
        ).all()
    )

    upcoming: list[UpcomingInterview] = []
    for t in interview_tasks:
        company = extract_company_from_calendar(t.title)
        upcoming.append(
            UpcomingInterview(
                task_id=t.id,
                title=t.title,
                company_name=company,
                due=_format_due(t.due_date_iso) if t.due_date_iso else None,
            )
        )

    return RecruitingPipelineResponse(
        applications=app_responses,
        upcoming_interviews=upcoming,
        summary=summary,
    )


@router.post("/recruiting-pipeline/refresh")
async def refresh_recruiting_pipeline(session: DBSession) -> dict[str, int]:
    """Backfill recruiting applications from existing file activity and Gmail."""
    from deadline_agent.awareness.recruiting_extractor import (
        extract_company_from_email,
        extract_company_from_filename,
        has_recruiting_signal,
        infer_status,
    )
    from deadline_agent.models import FileActivity
    from deadline_agent.store.recruiting_repository import RecruitingRepository

    repo = RecruitingRepository(session)
    created = 0

    # Scan existing recruiting file activity
    file_activities = list(
        session.scalars(
            select(FileActivity).where(FileActivity.life_track == "recruiting")
        ).all()
    )

    for fa in file_activities:
        company = extract_company_from_filename(fa.filename)
        if company:
            repo.upsert_application(
                company_name=company,
                source="file",
                signal={"type": "file", "date": fa.modified_at.isoformat(), "summary": fa.filename},
                applied_at=fa.modified_at,
            )
            created += 1

    # Search Gmail for recruiting emails
    email_scanned = 0
    try:
        import httpx

        from deadline_agent.auth import TokenManager

        tm = TokenManager()
        token = await tm.get_valid_token()
        if token:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    params={
                        "q": "subject:(interview OR application OR offer OR recruiter) newer_than:90d",
                        "maxResults": 50,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    for msg in resp.json().get("messages", []):
                        mr = await client.get(
                            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=10.0,
                        )
                        if mr.status_code != 200:
                            continue
                        md = mr.json()
                        hdrs = {h["name"].lower(): h["value"] for h in md.get("payload", {}).get("headers", [])}
                        subject = hdrs.get("subject", "")
                        sender = hdrs.get("from", "")
                        snippet = md.get("snippet", "")
                        text = f"{subject} {snippet}"

                        if has_recruiting_signal(text):
                            company = extract_company_from_email(sender, subject, snippet)
                            if company:
                                app = repo.upsert_application(
                                    company_name=company,
                                    source="email",
                                    signal={"type": "email", "date": hdrs.get("date", ""), "summary": subject[:100]},
                                )
                                new_status = infer_status(app.status, text)
                                if new_status != app.status:
                                    repo.advance_status(app.id, new_status, signal={
                                        "type": "status_change",
                                        "date": hdrs.get("date", ""),
                                        "summary": f"{app.status} → {new_status}",
                                    })
                                email_scanned += 1
    except Exception:
        pass  # Gmail unavailable — still return file results

    return {"applications_found": len(repo.list_all()), "files_scanned": len(file_activities), "emails_scanned": email_scanned}
