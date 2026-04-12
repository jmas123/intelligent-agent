"""Compressed history, longitudinal context, and workload spike prediction.

Provides temporal continuity for LLM reasoning by building context from
WeeklySnapshot and SemesterRecord history.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.models import (
    BehavioralPattern,
    LifeContext,
    SemesterRecord,
    Task,
    WeeklySnapshot,
)

logger = logging.getLogger(__name__)


def build_compressed_history(session: Session, weeks: int = 4) -> str:
    """Build compressed narrative from recent WeeklySnapshots.

    Each week is compressed to 1-2 lines with key stats and narrative excerpt.
    Total output is capped to keep prompt budget manageable.
    """
    from deadline_agent.store.snapshot_repository import WeeklySnapshotRepository

    repo = WeeklySnapshotRepository(session)
    snapshots = repo.get_recent(limit=weeks)

    if not snapshots:
        return ""

    lines: list[str] = []
    for snap in snapshots:
        work_hours = round(snap.total_work_minutes / 60, 1) if snap.total_work_minutes else 0
        week_label = f"Week of {snap.week_start}"
        if snap.semester_week_number is not None:
            week_label += f" (wk {snap.semester_week_number})"

        stats = f"{snap.tasks_completed} done, {snap.tasks_slipped} slipped, {work_hours}h worked"

        # Add life context if present
        try:
            contexts = json.loads(snap.life_contexts_json)
            if contexts:
                seasons = [c.get("season", "") for c in contexts if c.get("season")]
                if seasons:
                    stats += f". {', '.join(s.title() for s in seasons)} active"
        except (json.JSONDecodeError, TypeError):
            pass

        # Truncate narrative excerpt
        narrative = snap.narrative.strip()
        if narrative:
            excerpt = narrative[:120].rsplit(" ", 1)[0] if len(narrative) > 120 else narrative
            if len(narrative) > 120:
                excerpt += "..."
            lines.append(f"  {week_label}: {stats}. \"{excerpt}\"")
        else:
            lines.append(f"  {week_label}: {stats}")

    return "\n".join(lines)


def build_longitudinal_context(session: Session) -> str:
    """Build cross-semester pattern context from SemesterRecord analytics.

    Compares current behavioral patterns to historical semester data for
    cross-semester pattern detection.
    """
    from deadline_agent.store.pattern_repository import PatternRepository
    from deadline_agent.store.semester_repository import SemesterRecordRepository

    semester_repo = SemesterRecordRepository(session)
    records = semester_repo.list_all(limit=3)

    if not records:
        return ""

    pattern_repo = PatternRepository(session)
    current_patterns = {
        (p.pattern_type, p.pattern_key): p
        for p in pattern_repo.get_all()
        if p.confidence >= 0.3
    }

    lines: list[str] = []

    for record in records:
        try:
            analytics = json.loads(record.analytics_json)
        except (json.JSONDecodeError, TypeError):
            continue

        term = record.term_name

        # Compare effort accuracy
        effort_by_type = analytics.get("effort_accuracy_by_type", {})
        for task_type, data in effort_by_type.items():
            past_ratio = data.get("ratio", 1.0)
            current_p = current_patterns.get(("effort_accuracy", task_type))
            if current_p:
                try:
                    current_ratio = json.loads(current_p.value).get("ratio", 1.0)
                except (json.JSONDecodeError, TypeError):
                    continue
                if abs(past_ratio - current_ratio) > 0.2:
                    direction = "improving" if current_ratio < past_ratio else "worsening"
                    lines.append(
                        f"  {term}: {task_type} effort accuracy was {past_ratio:.1f}x, "
                        f"now {current_ratio:.1f}x ({direction})"
                    )

        # Compare procrastination trend
        proc_trend = analytics.get("procrastination_trend", {})
        trend_dir = proc_trend.get("trend")
        if trend_dir and trend_dir != "stable":
            first_half = proc_trend.get("first_half_avg_days", 0)
            second_half = proc_trend.get("second_half_avg_days", 0)
            lines.append(
                f"  {term}: procrastination trend was {trend_dir} "
                f"({first_half:.1f}d → {second_half:.1f}d lead time)"
            )

        # Workload distribution for week comparison
        workload = analytics.get("workload_distribution", [])
        if workload:
            peak_week = max(workload, key=lambda w: w.get("tasks_completed", 0))
            peak_count = peak_week.get("tasks_completed", 0)
            peak_num = peak_week.get("week", "?")
            if peak_count >= 6:
                lines.append(
                    f"  {term}: heaviest week was week {peak_num} "
                    f"with {peak_count} tasks completed"
                )

    return "\n".join(lines) if lines else ""


def _get_current_semester_week(session: Session) -> int | None:
    """Determine current week number within the active semester."""
    from deadline_agent.store.semester_repository import SemesterRecordRepository

    repo = SemesterRecordRepository(session)
    records = repo.list_all(limit=1)
    if not records:
        return None

    record = records[0]
    try:
        start = datetime.fromisoformat(record.start_date)
        now = datetime.now(UTC).replace(tzinfo=None)
        if now < start:
            return None
        delta = now - start
        return (delta.days // 7) + 1
    except (ValueError, TypeError):
        return None


def predict_workload_spikes(
    session: Session,
    current_week_number: int | None,
    upcoming_tasks: list[Task],
    life_contexts: list[LifeContext],
) -> list[str]:
    """Predict workload spikes by comparing current state to historical patterns.

    Checks WeeklySnapshot history and SemesterRecord workload_distribution
    to warn about historically bad weeks.
    """
    if current_week_number is None:
        return []

    from deadline_agent.store.semester_repository import SemesterRecordRepository
    from deadline_agent.store.snapshot_repository import WeeklySnapshotRepository

    predictions: list[str] = []

    # Check SemesterRecord workload_distribution for same week number
    semester_repo = SemesterRecordRepository(session)
    records = semester_repo.list_all(limit=3)

    for record in records:
        try:
            analytics = json.loads(record.analytics_json)
        except (json.JSONDecodeError, TypeError):
            continue

        workload = analytics.get("workload_distribution", [])
        for week_data in workload:
            week_num = week_data.get("week")
            if week_num is None:
                continue
            # Check if current week or next week matches a historically heavy week
            if abs(week_num - current_week_number) <= 1:
                past_count = week_data.get("tasks_completed", 0)
                if past_count >= 6:
                    current_upcoming = len(upcoming_tasks)
                    context_note = ""
                    if life_contexts:
                        active_seasons = [c.season for c in life_contexts if c.season != "default"]
                        if active_seasons:
                            context_note = f" with {', '.join(active_seasons)} active"

                    predictions.append(
                        f"Week {week_num} in {record.term_name} had {past_count} tasks. "
                        f"You're in week {current_week_number} now{context_note} "
                        f"with {current_upcoming} tasks upcoming."
                    )
                    break  # One prediction per semester is enough

    # Check WeeklySnapshot history for patterns
    snapshot_repo = WeeklySnapshotRepository(session)
    recent = snapshot_repo.get_recent(limit=4)
    if len(recent) >= 2:
        slip_trend = [s.tasks_slipped for s in recent]
        if all(s > 0 for s in slip_trend[:2]):
            predictions.append(
                f"You've slipped tasks {len([s for s in slip_trend if s > 0])} "
                f"of the last {len(slip_trend)} weeks — workload may be unsustainable."
            )

    return predictions
