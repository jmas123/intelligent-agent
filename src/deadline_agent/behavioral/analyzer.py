"""Pattern analysis: extract behavioral patterns from work sessions and file activity."""

import json
import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import (
    BehavioralPattern,
    FileActivity,
    FileTaskLink,
    Task,
    WorkSession,
)
from deadline_agent.reasoning.scheduler import EFFORT_MAP
from deadline_agent.reasoning.state import USER_TZ
from deadline_agent.store.pattern_repository import PatternRepository

logger = logging.getLogger(__name__)

# Canonical task types. Anything else maps to the closest match or is skipped.
CANONICAL_TYPES = {
    "assignment", "exam", "meeting", "reminder", "announcement",
    "recruiting", "project",
    "interview_prep", "networking", "personal",
}

# Map common non-canonical variants to canonical types.
_TYPE_ALIASES: dict[str, str] = {
    "homework": "assignment",
    "discussion": "assignment",
    "lesson": "assignment",
    "assessment": "exam",
    "quiz": "exam",
    "due_date_hint": "assignment",
    "deadline/assignment": "assignment",
    "action item": "reminder",
    "task": "assignment",
    "interview": "interview_prep",
    "coffee chat": "networking",
    "info session": "networking",
}


def _normalize_type(raw: str) -> str | None:
    """Map a raw task type to a canonical type. Returns None if unmappable."""
    lower = raw.lower().strip()
    if lower in CANONICAL_TYPES:
        return lower
    return _TYPE_ALIASES.get(lower)


def _confidence(n: int, threshold: int = 10) -> float:
    """Confidence score: ramps to 1.0 at threshold observations."""
    return min(1.0, n / threshold)


def analyze_effort_accuracy(session: Session) -> list[BehavioralPattern]:
    """Compare estimated vs actual effort per task type for completed tasks."""
    repo = PatternRepository(session)

    # Get completed tasks that have work sessions
    stmt = (
        select(Task)
        .where(Task.status == "done")
        .where(Task.id.in_(select(WorkSession.task_id).distinct()))
    )
    done_tasks = list(session.scalars(stmt).all())

    if not done_tasks:
        return []

    # Accumulate by normalized task type
    by_type: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for task in done_tasks:
        task_type = _normalize_type(task.type)
        if task_type is None:
            continue
        actual = int(
            session.execute(
                select(func.coalesce(func.sum(WorkSession.duration_minutes), 0)).where(
                    WorkSession.task_id == task.id
                )
            ).scalar()
        )
        if actual == 0:
            continue
        base = EFFORT_MAP.get(task_type, 90)
        multiplier = 1.0 + (task.urgency_score - 3) * 0.1
        estimated = max(30, int(base * multiplier))
        by_type[task_type].append((actual, estimated))

    patterns: list[BehavioralPattern] = []
    for task_type, pairs in by_type.items():
        n = len(pairs)
        mean_actual = sum(a for a, _ in pairs) / n
        mean_estimated = sum(e for _, e in pairs) / n
        ratio = mean_actual / mean_estimated if mean_estimated > 0 else 1.0

        p = repo.upsert(
            pattern_type="effort_accuracy",
            pattern_key=task_type,
            value=json.dumps({
                "ratio": round(ratio, 2),
                "mean_actual_min": round(mean_actual, 1),
                "mean_estimated_min": round(mean_estimated, 1),
            }),
            sample_count=n,
            confidence=_confidence(n),
        )
        patterns.append(p)

    return patterns


def _cluster_hours(hour_counts: dict[int, int]) -> list[dict[str, object]]:
    """Group adjacent active hours into windows.

    Returns sorted list of windows: {start_hour, end_hour, total_count, hours}.
    Adjacent means hour N and N+1 both have activity.
    """
    active = sorted(h for h, c in hour_counts.items() if c > 0)
    if not active:
        return []

    clusters: list[list[int]] = []
    current: list[int] = [active[0]]

    for h in active[1:]:
        if h == current[-1] + 1:
            current.append(h)
        else:
            clusters.append(current)
            current = [h]
    clusters.append(current)

    windows: list[dict[str, object]] = []
    for hours in clusters:
        total = sum(hour_counts[h] for h in hours)
        windows.append({
            "start_hour": hours[0],
            "end_hour": hours[-1] + 1,  # exclusive end
            "total_count": total,
            "hours": hours,
        })

    return sorted(windows, key=lambda w: -w["total_count"])  # type: ignore[arg-type]


def _format_hour(h: int) -> str:
    """Format 24h hour as '2 PM', '10 AM', etc."""
    h = h % 24  # handle 24 → 0 (midnight)
    period = "AM" if h < 12 else "PM"
    display = h % 12 or 12
    return f"{display} {period}"


def analyze_peak_hours(session: Session) -> list[BehavioralPattern]:
    """Cluster file activity into productive windows, ranked by intensity."""
    repo = PatternRepository(session)

    timestamps = list(
        session.scalars(select(FileActivity.modified_at).order_by(FileActivity.modified_at))
    )
    if not timestamps:
        return []

    # Count by hour in user timezone
    hour_counts: dict[int, int] = defaultdict(int)
    for ts in timestamps:
        if ts.tzinfo is None:
            local = ts
        else:
            local = ts.astimezone(USER_TZ)
        hour_counts[local.hour] += 1

    total = sum(hour_counts.values())
    if total == 0:
        return []

    # Cluster adjacent hours into windows
    windows = _cluster_hours(hour_counts)
    if not windows:
        return []

    # Store each window as a pattern, ranked primary/secondary
    patterns: list[BehavioralPattern] = []
    for i, window in enumerate(windows):
        start_h = window["start_hour"]
        end_h = window["end_hour"]
        count = window["total_count"]
        rank = "primary" if i == 0 else "secondary"

        # Key: "19-21" for a 7-9 PM window
        key = f"{start_h}-{end_h}"
        label_start = _format_hour(int(start_h))
        label_end = _format_hour(int(end_h))

        p = repo.upsert(
            pattern_type="peak_hours",
            pattern_key=key,
            value=json.dumps({
                "start_hour": start_h,
                "end_hour": end_h,
                "total_count": count,
                "rank": rank,
                "label": f"{label_start}\u2013{label_end}",
                "pct_of_total": round(int(count) / total * 100, 1),
            }),
            sample_count=int(count),
            confidence=_confidence(total, threshold=50),
        )
        patterns.append(p)

    return patterns


def analyze_lead_time(session: Session) -> list[BehavioralPattern]:
    """Track time between task creation and first linked file activity, per task type."""
    repo = PatternRepository(session)

    # Tasks with at least one linked file activity
    stmt = (
        select(Task)
        .where(Task.id.in_(select(FileTaskLink.task_id).distinct()))
    )
    tasks = list(session.scalars(stmt).all())

    if not tasks:
        return []

    by_type: dict[str, list[float]] = defaultdict(list)
    for task in tasks:
        task_type = _normalize_type(task.type)
        if task_type is None:
            continue
        # Find earliest linked file activity
        earliest_stmt = (
            select(FileActivity.modified_at)
            .join(FileTaskLink, FileTaskLink.file_activity_id == FileActivity.id)
            .where(FileTaskLink.task_id == task.id)
            .order_by(FileActivity.modified_at.asc())
            .limit(1)
        )
        first_activity = session.execute(earliest_stmt).scalar()
        if first_activity is None:
            continue

        lead_hours = (first_activity - task.created_at).total_seconds() / 3600
        if lead_hours < 0:
            lead_hours = 0
        by_type[task_type].append(lead_hours)

    patterns: list[BehavioralPattern] = []
    for task_type, hours_list in by_type.items():
        n = len(hours_list)
        mean_hours = sum(hours_list) / n
        sorted_hours = sorted(hours_list)
        median_hours = sorted_hours[n // 2]

        p = repo.upsert(
            pattern_type="lead_time",
            pattern_key=task_type,
            value=json.dumps({
                "mean_hours": round(mean_hours, 1),
                "median_hours": round(median_hours, 1),
            }),
            sample_count=n,
            confidence=_confidence(n),
        )
        patterns.append(p)

    return patterns


def analyze_session_duration(session: Session) -> list[BehavioralPattern]:
    """Average work session duration per task type."""
    repo = PatternRepository(session)

    stmt = (
        select(Task.type, WorkSession.duration_minutes)
        .join(WorkSession, WorkSession.task_id == Task.id)
    )
    rows = session.execute(stmt).all()

    if not rows:
        return []

    by_type: dict[str, list[int]] = defaultdict(list)
    for raw_type, duration in rows:
        task_type = _normalize_type(raw_type)
        if task_type is None:
            continue
        by_type[task_type].append(duration)

    patterns: list[BehavioralPattern] = []
    for task_type, durations in by_type.items():
        n = len(durations)
        mean_dur = sum(durations) / n

        p = repo.upsert(
            pattern_type="session_duration",
            pattern_key=task_type,
            value=json.dumps({
                "mean_minutes": round(mean_dur, 1),
                "total_sessions": n,
            }),
            sample_count=n,
            confidence=_confidence(n),
        )
        patterns.append(p)

    return patterns


def analyze_procrastination(session: Session) -> list[BehavioralPattern]:
    """Days before deadline that work starts, per task type."""
    repo = PatternRepository(session)

    # Tasks with due dates and at least one work session
    stmt = (
        select(Task)
        .where(Task.due_date_iso.is_not(None))
        .where(Task.id.in_(select(WorkSession.task_id).distinct()))
    )
    tasks = list(session.scalars(stmt).all())

    if not tasks:
        return []

    by_type: dict[str, list[float]] = defaultdict(list)
    for task in tasks:
        task_type = _normalize_type(task.type)
        if task_type is None:
            continue
        # Earliest work session
        earliest_stmt = (
            select(WorkSession.started_at)
            .where(WorkSession.task_id == task.id)
            .order_by(WorkSession.started_at.asc())
            .limit(1)
        )
        first_session = session.execute(earliest_stmt).scalar()
        if first_session is None or task.due_date_iso is None:
            continue

        try:
            due = datetime.fromisoformat(task.due_date_iso)
        except ValueError:
            continue

        # Days before deadline that work started (positive = ahead, negative = late)
        days_before = (due - first_session).total_seconds() / 86400
        by_type[task_type].append(days_before)

    patterns: list[BehavioralPattern] = []
    for task_type, days_list in by_type.items():
        n = len(days_list)
        mean_days = sum(days_list) / n

        p = repo.upsert(
            pattern_type="procrastination",
            pattern_key=task_type,
            value=json.dumps({
                "mean_days_before_deadline": round(mean_days, 1),
            }),
            sample_count=n,
            confidence=_confidence(n),
        )
        patterns.append(p)

    return patterns


def analyze_work_by_time(session: Session) -> list[BehavioralPattern]:
    """Cross-reference task types and life tracks with time-of-day."""
    repo = PatternRepository(session)

    # Build {category: {hour: count}} from two sources:

    type_hours: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    # Source 1: Task-linked activities → use normalized task type
    stmt = (
        select(Task.type, FileActivity.modified_at)
        .join(FileTaskLink, FileTaskLink.task_id == Task.id)
        .join(FileActivity, FileTaskLink.file_activity_id == FileActivity.id)
    )
    for raw_type, modified_at in session.execute(stmt).all():
        task_type = _normalize_type(raw_type)
        if task_type is None:
            continue
        if modified_at.tzinfo is None:
            local = modified_at
        else:
            local = modified_at.astimezone(USER_TZ)
        type_hours[task_type][local.hour] += 1

    # Source 2: Life-track tagged activities (not linked to any task)
    lt_stmt = (
        select(FileActivity.life_track, FileActivity.modified_at)
        .where(FileActivity.life_track.is_not(None))
        .where(
            ~FileActivity.id.in_(select(FileTaskLink.file_activity_id))
        )
    )
    for track, modified_at in session.execute(lt_stmt).all():
        if modified_at.tzinfo is None:
            local = modified_at
        else:
            local = modified_at.astimezone(USER_TZ)
        type_hours[track][local.hour] += 1

    patterns: list[BehavioralPattern] = []
    for task_type, hour_counts in type_hours.items():
        total = sum(hour_counts.values())
        if total < 3:
            continue

        # Find the primary window using the existing clustering helper
        windows = _cluster_hours(hour_counts)
        if not windows:
            continue

        primary = windows[0]  # highest activity window
        start_h = primary["start_hour"]
        end_h = primary["end_hour"]
        count = primary["total_count"]
        label = f"{_format_hour(int(start_h))}\u2013{_format_hour(int(end_h))}"

        p = repo.upsert(
            pattern_type="work_by_time",
            pattern_key=task_type,
            value=json.dumps({
                "primary_window": label,
                "start_hour": start_h,
                "end_hour": end_h,
                "activity_count": int(count),
                "total_activity": total,
            }),
            sample_count=total,
            confidence=_confidence(total),
        )
        patterns.append(p)

    return patterns


def run_all_analyses(session: Session) -> list[BehavioralPattern]:
    """Run all pattern analyses and upsert results. Returns all updated patterns."""
    all_patterns: list[BehavioralPattern] = []

    # Phase 18: Focus quality analyses
    from deadline_agent.awareness.focus_quality import (
        analyze_focus_quality,
        analyze_session_fragmentation,
    )

    # Phase 24: Task affect + energy proxy
    from deadline_agent.awareness.task_affect import (
        classify_task_affect,
        compute_energy_proxy,
    )

    for analyze_fn in [
        analyze_effort_accuracy,
        analyze_peak_hours,
        analyze_lead_time,
        analyze_session_duration,
        analyze_procrastination,
        analyze_work_by_time,
        analyze_focus_quality,
        analyze_session_fragmentation,
        classify_task_affect,
        compute_energy_proxy,
    ]:
        try:
            patterns = analyze_fn(session)
            all_patterns.extend(patterns)
        except Exception:
            logger.exception("Pattern analysis failed: %s", analyze_fn.__name__)

    # Phase 25: Recruiting intelligence
    if settings.recruiting_analytics_enabled:
        try:
            from deadline_agent.behavioral.recruiting_analyzer import run_recruiting_analyses

            recruiting_patterns = run_recruiting_analyses(session)
            all_patterns.extend(recruiting_patterns)
        except Exception:
            logger.exception("Recruiting analysis failed")

    logger.info("Pattern analysis complete: %d pattern(s) updated", len(all_patterns))

    # Run life context auto-detection
    try:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts

        contexts = detect_life_contexts(session)
        if contexts:
            logger.info("Auto-detected %d life context(s)", len(contexts))
    except Exception:
        logger.exception("Life context detection failed")

    return all_patterns
