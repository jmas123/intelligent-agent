"""Sleep and physical world inference from file activity timestamps.

Infers:
- Sleep window: longest daily gap with no file activity
- Sleep consistency: stdev of sleep start/end times across the week
- All-nighters: days with no gap > 3 hours
"""

import logging
import statistics
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import FileActivity

logger = logging.getLogger(__name__)

USER_TZ = ZoneInfo("America/New_York")


def infer_sleep_signals(
    session: Session, days: int = 7
) -> dict[str, object]:
    """Infer sleep patterns from file activity gaps.

    Returns a dict suitable for merging into health_signals:
    - sleep_window_avg_start: average sleep start time (HH:MM)
    - sleep_window_avg_end: average wake time (HH:MM)
    - sleep_hours_avg: average inferred sleep duration
    - sleep_consistency: "consistent" | "irregular" | "erratic"
    - all_nighter_dates: list of dates with no gap > 3h
    """
    now = datetime.now(UTC)
    since = (now - timedelta(days=days))

    timestamps = list(
        session.scalars(
            select(FileActivity.modified_at)
            .where(FileActivity.created_at >= since.isoformat())
            .order_by(FileActivity.modified_at.asc())
        ).all()
    )

    if len(timestamps) < 5:
        return {}

    # Normalize to user timezone
    local_ts: list[datetime] = []
    for ts in timestamps:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        local_ts.append(ts.astimezone(USER_TZ))

    # Group by date
    by_date: dict[str, list[datetime]] = {}
    for ts in local_ts:
        day_key = ts.strftime("%Y-%m-%d")
        by_date.setdefault(day_key, []).append(ts)

    if len(by_date) < 2:
        return {}

    # For each pair of consecutive days, find the overnight gap
    sorted_dates = sorted(by_date.keys())
    sleep_starts: list[float] = []  # hours past midnight
    sleep_ends: list[float] = []
    sleep_durations: list[float] = []
    all_nighter_dates: list[str] = []

    for i in range(len(sorted_dates) - 1):
        day1 = sorted_dates[i]
        day2 = sorted_dates[i + 1]

        day1_ts = sorted(by_date[day1])
        day2_ts = sorted(by_date[day2])

        if not day1_ts or not day2_ts:
            continue

        # Last activity of day1, first activity of day2
        last_activity = day1_ts[-1]
        first_activity = day2_ts[0]

        gap_hours = (first_activity - last_activity).total_seconds() / 3600

        if gap_hours < 3:
            # Possible all-nighter
            all_nighter_dates.append(day1)
            continue

        if gap_hours > 16:
            # Probably a day with no activity, not a sleep signal
            continue

        # Record sleep window
        sleep_start_hour = last_activity.hour + last_activity.minute / 60
        sleep_end_hour = first_activity.hour + first_activity.minute / 60

        sleep_starts.append(sleep_start_hour)
        sleep_ends.append(sleep_end_hour)
        sleep_durations.append(gap_hours)

    if not sleep_durations:
        result: dict[str, object] = {}
        if all_nighter_dates:
            result["all_nighter_dates"] = all_nighter_dates
        return result

    # Compute averages
    avg_start = statistics.mean(sleep_starts)
    avg_end = statistics.mean(sleep_ends)
    avg_duration = statistics.mean(sleep_durations)

    # Compute consistency from standard deviation
    if len(sleep_starts) >= 3:
        start_stdev = statistics.stdev(sleep_starts)
        end_stdev = statistics.stdev(sleep_ends)
        avg_stdev = (start_stdev + end_stdev) / 2

        if avg_stdev < 1.0:
            consistency = "consistent"
        elif avg_stdev < 2.0:
            consistency = "irregular"
        else:
            consistency = "erratic"
    else:
        consistency = "insufficient_data"

    def _format_hour(h: float) -> str:
        """Convert decimal hour to HH:MM format."""
        hour = int(h) % 24
        minute = int((h % 1) * 60)
        return f"{hour:02d}:{minute:02d}"

    result = {
        "sleep_window_avg_start": _format_hour(avg_start),
        "sleep_window_avg_end": _format_hour(avg_end),
        "sleep_hours_avg": round(avg_duration, 1),
        "sleep_consistency": consistency,
        "sleep_days_analyzed": len(sleep_durations),
    }

    if all_nighter_dates:
        result["all_nighter_dates"] = all_nighter_dates

    return result
