"""Absence detection: finds meaningful things that aren't happening.

Computes rolling 4-week baselines for:
- Unscheduled evenings (evenings with no file activity after 6 PM)
- Personal project activity (life_track = "project")
- Non-academic calendar interactions

Generates signals when categories drop to zero for 2+ weeks
or fall 70%+ below baseline.
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.models import FileActivity

logger = logging.getLogger(__name__)

USER_TZ = ZoneInfo("America/New_York")


def detect_absences(session: Session) -> list[str]:
    """Detect meaningful absences from recent activity patterns.

    Returns a list of human-readable absence signals.
    """
    now = datetime.now(UTC)
    signals: list[str] = []

    # Compute weekly buckets for the last 4 weeks
    weeks: list[tuple[datetime, datetime]] = []
    for w in range(4):
        end = now - timedelta(weeks=w)
        start = end - timedelta(weeks=1)
        weeks.append((start, end))

    # 1. Unscheduled evenings (no file activity between 6 PM - midnight)
    evening_counts = _count_evening_activity_by_week(session, weeks)
    _check_absence(
        signals,
        evening_counts,
        "evening without any activity",
        "free evening",
        invert=True,  # We want evenings WITH NO activity
    )

    # 2. Personal project activity
    project_counts = _count_life_track_by_week(session, weeks, "project")
    _check_absence(
        signals,
        project_counts,
        "personal project activity",
        "personal project work",
        invert=False,
    )

    # 3. Recruiting activity (detect if it suddenly stopped)
    recruiting_counts = _count_life_track_by_week(session, weeks, "recruiting")
    _check_recruiting_drop(signals, recruiting_counts)

    return signals


def _count_evening_activity_by_week(
    session: Session, weeks: list[tuple[datetime, datetime]]
) -> list[int]:
    """Count evenings with file activity per week.

    An 'evening' is 6 PM - midnight local time.
    Returns count of evenings WITH activity (not without).
    """
    counts: list[int] = []

    for start, end in weeks:
        # Query file activity in this week
        activities = list(
            session.scalars(
                select(FileActivity.modified_at)
                .where(FileActivity.created_at >= start.isoformat())
                .where(FileActivity.created_at < end.isoformat())
            ).all()
        )

        # Check each evening in the week
        evening_days_with_activity: set[str] = set()
        for ts in activities:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            local = ts.astimezone(USER_TZ)
            if 18 <= local.hour < 24:
                evening_days_with_activity.add(local.strftime("%Y-%m-%d"))

        counts.append(len(evening_days_with_activity))

    return counts


def _count_life_track_by_week(
    session: Session,
    weeks: list[tuple[datetime, datetime]],
    life_track: str,
) -> list[int]:
    """Count file activity events for a given life_track per week."""
    counts: list[int] = []

    for start, end in weeks:
        count = session.scalar(
            select(func.count(FileActivity.id))
            .where(FileActivity.created_at >= start.isoformat())
            .where(FileActivity.created_at < end.isoformat())
            .where(FileActivity.life_track == life_track)
        ) or 0
        counts.append(count)

    return counts


def _check_absence(
    signals: list[str],
    weekly_counts: list[int],
    activity_name: str,
    display_name: str,
    invert: bool = False,
) -> None:
    """Check for meaningful absence in a metric.

    weekly_counts[0] = most recent week, [3] = oldest.
    If invert=True, we're looking for evenings WITHOUT activity (7 - count).
    """
    if len(weekly_counts) < 3:
        return

    if invert:
        # Convert "evenings with activity" to "evenings without activity"
        weekly_counts = [7 - c for c in weekly_counts]

    current = weekly_counts[0]
    baseline_weeks = weekly_counts[1:]

    # Check for zero in current week with non-zero baseline
    baseline_avg = sum(baseline_weeks) / len(baseline_weeks)
    if baseline_avg == 0:
        return  # No baseline to compare against

    # Zero for 2+ consecutive weeks
    if current == 0 and weekly_counts[1] == 0:
        weeks_at_zero = 2
        if len(weekly_counts) > 2 and weekly_counts[2] == 0:
            weeks_at_zero = 3
        signals.append(f"No {display_name} in {weeks_at_zero} weeks")
        return

    # 70%+ drop from baseline
    if baseline_avg > 0 and current < baseline_avg * 0.3:
        drop_pct = int((1 - current / baseline_avg) * 100)
        signals.append(
            f"{display_name.capitalize()} dropped {drop_pct}% vs your baseline"
        )


def _check_recruiting_drop(
    signals: list[str], weekly_counts: list[int]
) -> None:
    """Detect sudden recruiting activity drop (was active, now stopped)."""
    if len(weekly_counts) < 3:
        return

    current = weekly_counts[0]
    prev_avg = sum(weekly_counts[1:]) / len(weekly_counts[1:])

    # Was actively recruiting (avg 5+ events/week) but current week is near zero
    if prev_avg >= 5 and current <= 1:
        signals.append(
            "Recruiting activity dropped to near-zero after active weeks"
        )
