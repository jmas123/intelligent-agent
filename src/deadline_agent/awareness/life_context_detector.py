"""Auto-detection of life contexts from tasks and calendar events."""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.models import (
    FileActivity,
    LifeContext,
    Task,
)
from deadline_agent.store.context_repository import LifeContextRepository

USER_TZ = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

# Keywords in task titles / calendar summaries that signal interview activity
_INTERVIEW_SIGNALS = [
    "interview",
    "recruiter",
    "hiring manager",
    "phone screen",
    "technical screen",
    "onsite",
    "on-site",
    "final round",
    "superday",
    "coding challenge",
    "take-home",
    "offer call",
]


def _detect_exams(session: Session, repo: LifeContextRepository) -> LifeContext | None:
    """Detect exam period: 3+ pending exams within a 10-day window."""
    today = date.today()
    lookahead = (today + timedelta(days=21)).isoformat()

    stmt = (
        select(Task)
        .where(Task.type == "exam")
        .where(Task.status == "pending")
        .where(Task.due_date_iso.is_not(None))
        .where(Task.due_date_iso >= today.isoformat())
        .where(Task.due_date_iso <= lookahead)
        .order_by(Task.due_date_iso.asc())
    )
    exams = list(session.scalars(stmt).all())

    if len(exams) < 3:
        return None

    # Sliding window: check if any 10-day window contains 3+ exams
    exam_dates = []
    for e in exams:
        try:
            exam_dates.append((date.fromisoformat(e.due_date_iso[:10]), e.id))
        except (ValueError, TypeError):
            continue

    if len(exam_dates) < 3:
        return None

    # Find the densest 10-day window
    best_cluster: list[tuple[date, int]] = []
    for i in range(len(exam_dates)):
        window_end = exam_dates[i][0] + timedelta(days=10)
        cluster = [(d, eid) for d, eid in exam_dates if exam_dates[i][0] <= d <= window_end]
        if len(cluster) >= 3 and len(cluster) > len(best_cluster):
            best_cluster = cluster

    if len(best_cluster) < 3:
        return None

    # Date range: earliest exam - 7 days prep to latest exam
    earliest = best_cluster[0][0]
    latest = best_cluster[-1][0]
    start = (earliest - timedelta(days=7)).isoformat()
    end = latest.isoformat()
    exam_ids = [eid for _, eid in best_cluster]

    ctx = repo.create({
        "season": "exams",
        "label": f"{len(best_cluster)} exams in {(latest - earliest).days + 1} days",
        "start_date": start,
        "end_date": end,
        "source": "auto",
        "active": True,
        "metadata_json": json.dumps({"exam_count": len(best_cluster), "exam_ids": exam_ids}),
    })
    logger.info("Auto-detected exam period: %s to %s (%d exams)", start, end, len(best_cluster))
    return ctx


def _detect_recruiting(session: Session, repo: LifeContextRepository) -> LifeContext | None:
    """Detect recruiting season: 2+ interview-related tasks in next 14 days."""
    today = date.today()
    lookahead = (today + timedelta(days=14)).isoformat()

    # Check tasks with interview-related titles or interview_prep type
    stmt = (
        select(Task)
        .where(Task.status == "pending")
        .where(Task.due_date_iso.is_not(None))
        .where(Task.due_date_iso >= today.isoformat())
        .where(Task.due_date_iso <= lookahead)
    )
    upcoming_tasks = list(session.scalars(stmt).all())

    interview_tasks = []
    for t in upcoming_tasks:
        if t.type == "interview_prep":
            interview_tasks.append(t)
            continue
        title_lower = t.title.lower()
        if any(signal in title_lower for signal in _INTERVIEW_SIGNALS):
            interview_tasks.append(t)

    if len(interview_tasks) < 2:
        return None

    # Date range: today to 14 days out
    start = today.isoformat()
    end = lookahead
    task_ids = [t.id for t in interview_tasks]

    ctx = repo.create({
        "season": "recruiting",
        "label": f"{len(interview_tasks)} interviews in 2 weeks",
        "start_date": start,
        "end_date": end,
        "source": "auto",
        "active": True,
        "metadata_json": json.dumps({
            "interview_count": len(interview_tasks),
            "task_ids": task_ids,
        }),
    })
    logger.info("Auto-detected recruiting season: %d interview-related tasks", len(interview_tasks))
    return ctx


def _detect_light_week(session: Session, repo: LifeContextRepository) -> LifeContext | None:
    """Detect light week: <=2 pending tasks due in next 7 days, no exams."""
    today = date.today()
    week_end = (today + timedelta(days=7)).isoformat()

    stmt = (
        select(Task)
        .where(Task.status == "pending")
        .where(Task.due_date_iso.is_not(None))
        .where(Task.due_date_iso >= today.isoformat())
        .where(Task.due_date_iso <= week_end)
    )
    upcoming = list(session.scalars(stmt).all())

    if len(upcoming) > 2:
        return None

    # No exams this week
    if any(t.type == "exam" for t in upcoming):
        return None

    start = today.isoformat()
    end = week_end

    ctx = repo.create({
        "season": "light_week",
        "label": f"{len(upcoming)} task(s) this week",
        "start_date": start,
        "end_date": end,
        "source": "auto",
        "active": True,
    })
    logger.info("Auto-detected light week: %d tasks due", len(upcoming))
    return ctx


def _detect_burnout(
    session: Session, repo: LifeContextRepository
) -> LifeContext | None:
    """Detect burnout risk: 3+ late-night sessions (1-5 AM) in past 7 days."""
    today = date.today()
    seven_days_ago = datetime.now(UTC) - timedelta(days=7)

    # Count file activity events during late-night hours (1-5 AM local)
    stmt = (
        select(FileActivity.modified_at)
        .where(FileActivity.created_at >= seven_days_ago)
    )
    timestamps = list(session.scalars(stmt).all())

    late_night_count = 0
    late_night_dates: set[str] = set()
    for ts in timestamps:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        local = ts.astimezone(USER_TZ)
        if 1 <= local.hour <= 4:
            late_night_count += 1
            late_night_dates.add(local.strftime("%Y-%m-%d"))

    # Need late-night activity on 3+ distinct days
    if len(late_night_dates) < 3:
        return None

    # Count zero-activity days in the past 7
    stmt_daily = (
        select(
            func.date(FileActivity.created_at).label("day"),
        )
        .where(FileActivity.created_at >= seven_days_ago)
        .group_by(func.date(FileActivity.created_at))
    )
    active_days = {str(row[0]) for row in session.execute(stmt_daily).all()}
    zero_days = 7 - len(active_days)

    start = today.isoformat()
    end = (today + timedelta(days=7)).isoformat()

    ctx = repo.create(
        {
            "season": "burnout",
            "label": (
                f"{len(late_night_dates)} late nights, "
                f"{zero_days} zero-activity days this week"
            ),
            "start_date": start,
            "end_date": end,
            "source": "auto",
            "active": True,
            "metadata_json": json.dumps(
                {
                    "late_night_days": len(late_night_dates),
                    "late_night_events": late_night_count,
                    "zero_activity_days": zero_days,
                }
            ),
        }
    )
    logger.info(
        "Auto-detected burnout risk: %d late nights, %d zero days",
        len(late_night_dates),
        zero_days,
    )
    return ctx


def _detect_crunch_week(
    session: Session, repo: LifeContextRepository
) -> LifeContext | None:
    """Detect crunch week: 5+ pending tasks due in next 7 days."""
    today = date.today()
    week_end = (today + timedelta(days=7)).isoformat()

    stmt = (
        select(func.count(Task.id))
        .where(Task.status == "pending")
        .where(Task.due_date_iso.is_not(None))
        .where(Task.due_date_iso >= today.isoformat())
        .where(Task.due_date_iso <= week_end)
    )
    count = session.execute(stmt).scalar() or 0

    if count < 5:
        return None

    start = today.isoformat()
    end = week_end

    ctx = repo.create(
        {
            "season": "crunch_week",
            "label": f"{count} tasks due this week",
            "start_date": start,
            "end_date": end,
            "source": "auto",
            "active": True,
            "metadata_json": json.dumps({"task_count": count}),
        }
    )
    logger.info("Auto-detected crunch week: %d tasks due", count)
    return ctx


def detect_life_contexts(session: Session) -> list[LifeContext]:
    """Run all heuristics and create/update auto-detected LifeContext records.

    Clears stale auto-detected contexts before re-detecting.
    Skips detection for seasons that have an active manual override.
    """
    repo = LifeContextRepository(session)

    # Clear previous auto-detected contexts
    cleared = repo.clear_auto()
    if cleared:
        logger.debug("Cleared %d stale auto-detected contexts", cleared)

    detected: list[LifeContext] = []

    # Detect each season, skipping if manual override exists
    for season, detector in [
        ("exams", _detect_exams),
        ("recruiting", _detect_recruiting),
        ("light_week", _detect_light_week),
        ("burnout", _detect_burnout),
        ("crunch_week", _detect_crunch_week),
    ]:
        if repo.has_active_manual(season):
            logger.debug("Skipping %s auto-detection: manual override active", season)
            continue
        try:
            ctx = detector(session, repo)
            if ctx is not None:
                detected.append(ctx)
        except Exception:
            logger.exception("Life context detection failed: %s", season)

    return detected
