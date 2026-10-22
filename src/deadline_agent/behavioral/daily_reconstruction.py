"""Daily auto-reconstruction: infer a timeline of what you did from signals."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import (
    DailyReconstruction,
    FileActivity,
    FileTaskLink,
    Task,
    WorkSession,
)
from deadline_agent.store.reconstruction_repository import ReconstructionRepository

logger = logging.getLogger(__name__)

USER_TZ = ZoneInfo("America/New_York")


async def reconstruct_day(
    session: Session, target_date: date | None = None
) -> DailyReconstruction:
    """Build an inferred timeline for a given day from file activity, calendar, and work sessions.

    Args:
        session: SQLAlchemy session.
        target_date: Date to reconstruct. Defaults to today.

    Returns:
        The persisted DailyReconstruction record.
    """
    if target_date is None:
        target_date = datetime.now(USER_TZ).date()

    date_str = target_date.isoformat()

    # Compute day boundaries in UTC
    day_start_local = datetime(
        target_date.year, target_date.month, target_date.day, tzinfo=USER_TZ
    )
    day_end_local = day_start_local + timedelta(days=1)
    day_start_utc = day_start_local.astimezone(UTC)
    day_end_utc = day_end_local.astimezone(UTC)

    # 1. Gather signals
    file_activities = _query_file_activities(session, day_start_utc, day_end_utc)
    work_sessions = _query_work_sessions(session, day_start_utc, day_end_utc)
    calendar_events = await _fetch_calendar_events(day_start_local, day_end_local)

    # 2. Merge into timeline segments
    segments = _merge_into_timeline(
        file_activities, work_sessions, calendar_events, day_start_local, day_end_local
    )

    # 3. Compute confidence for each segment
    for seg in segments:
        seg["confidence"] = _compute_confidence(seg.get("confidence_sources", {}))

    # 4. Overall confidence (duration-weighted mean)
    total_dur = sum(_segment_duration_minutes(s) for s in segments)
    if total_dur > 0:
        overall = sum(
            s["confidence"] * _segment_duration_minutes(s) for s in segments
        ) / total_dur
    else:
        overall = 0.0

    # 5. Generate narrative
    narrative = _generate_heuristic_narrative(segments)

    # 6. Persist
    repo = ReconstructionRepository(session)
    rec = repo.upsert(
        date=date_str,
        timeline_json=json.dumps(segments, default=str),
        overall_confidence=round(overall, 2),
        narrative=narrative,
    )

    # 7. Emit event
    from deadline_agent.events import DAILY_RECONSTRUCTION_COMPLETE, Event, event_bus

    await event_bus.emit(
        Event(
            type=DAILY_RECONSTRUCTION_COMPLETE,
            payload={"date": date_str, "segment_count": len(segments), "confidence": round(overall, 2)},
        )
    )

    logger.info(
        "Daily reconstruction for %s: %d segments, %.0f%% confidence",
        date_str,
        len(segments),
        overall * 100,
    )
    return rec


# --- Signal queries ---


def _query_file_activities(
    session: Session, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Get file activities with their task links for the time window."""
    stmt = (
        select(FileActivity)
        .where(FileActivity.modified_at >= start)
        .where(FileActivity.modified_at < end)
        .order_by(FileActivity.modified_at.asc())
    )
    activities = list(session.scalars(stmt).all())

    result = []
    for fa in activities:
        # Find best task link
        link_stmt = (
            select(FileTaskLink, Task)
            .join(Task, FileTaskLink.task_id == Task.id)
            .where(FileTaskLink.file_activity_id == fa.id)
            .order_by(FileTaskLink.confidence.desc())
            .limit(1)
        )
        link_row = session.execute(link_stmt).first()

        result.append(
            {
                "timestamp": fa.modified_at,
                "path": fa.path,
                "filename": fa.filename,
                "task_id": link_row[1].id if link_row else None,
                "task_title": link_row[1].title if link_row else None,
                "link_confidence": link_row[0].confidence if link_row else 0.0,
                "life_track": fa.life_track,
            }
        )
    return result


def _query_work_sessions(
    session: Session, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Get work sessions overlapping the time window."""
    stmt = (
        select(WorkSession, Task)
        .join(Task, WorkSession.task_id == Task.id)
        .where(WorkSession.started_at < end)
        .where(WorkSession.ended_at > start)
        .order_by(WorkSession.started_at.asc())
    )
    rows = session.execute(stmt).all()
    return [
        {
            "start": ws.started_at,
            "end": ws.ended_at,
            "duration_minutes": ws.duration_minutes,
            "task_id": task.id,
            "task_title": task.title,
            "file_count": ws.file_activity_count,
        }
        for ws, task in rows
    ]


async def _fetch_calendar_events(
    start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Fetch calendar events for the time window. Returns [] on failure."""
    try:
        from deadline_agent.reasoning.calendar_gaps import fetch_events

        events = await fetch_events(start.isoformat(), end.isoformat())
        return events or []
    except Exception:
        logger.debug("Calendar fetch failed, continuing without calendar data")
        return []


# --- Timeline merging ---


def _merge_into_timeline(
    file_activities: list[dict],
    work_sessions: list[dict],
    calendar_events: list[dict],
    day_start: datetime,
    day_end: datetime,
) -> list[dict[str, Any]]:
    """Merge raw signals into a list of timeline segments."""
    segments: list[dict[str, Any]] = []

    # Calendar events → meeting segments (highest priority, confidence 1.0)
    for ev in calendar_events:
        start_str = ev.get("start", "")
        end_str = ev.get("end", "")
        if not start_str or "T" not in start_str:
            continue  # skip all-day events
        try:
            ev_start = datetime.fromisoformat(start_str)
            ev_end = datetime.fromisoformat(end_str) if end_str else ev_start + timedelta(hours=1)
        except (ValueError, TypeError):
            continue

        segments.append(
            {
                "start_time": ev_start.isoformat(),
                "end_time": ev_end.isoformat(),
                "activity_type": "meeting",
                "task_id": None,
                "task_title": ev.get("summary", "Meeting"),
                "description": ev.get("summary", "Calendar event"),
                "confidence": 1.0,
                "confidence_sources": {"calendar_match": True},
                "confirmed": None,
            }
        )

    # Work sessions → work segments
    for ws in work_sessions:
        # Skip if entirely overlapped by a calendar event
        ws_start = ws["start"] if isinstance(ws["start"], datetime) else datetime.fromisoformat(str(ws["start"]))
        ws_end = ws["end"] if isinstance(ws["end"], datetime) else datetime.fromisoformat(str(ws["end"]))

        if _overlaps_meeting(ws_start, ws_end, segments):
            continue

        file_count = ws.get("file_count", 0)
        segments.append(
            {
                "start_time": ws_start.isoformat(),
                "end_time": ws_end.isoformat(),
                "activity_type": "work",
                "task_id": ws["task_id"],
                "task_title": ws["task_title"],
                "description": f"Worked on {ws['task_title']} ({file_count} file edits)",
                "confidence_sources": {
                    "file_count": file_count,
                    "calendar_match": False,
                    "link_confidence": 0.8,  # work sessions are task-linked by definition
                    "duration_minutes": ws["duration_minutes"],
                },
                "confirmed": None,
            }
        )

    # Unlinked file activity clusters → work segments with lower confidence
    unlinked = [fa for fa in file_activities if fa["task_id"] is None]
    if unlinked:
        clusters = _cluster_activities(unlinked, gap_minutes=30)
        for cluster in clusters:
            c_start = cluster[0]["timestamp"]
            c_end = cluster[-1]["timestamp"]
            if isinstance(c_start, str):
                c_start = datetime.fromisoformat(c_start)
            if isinstance(c_end, str):
                c_end = datetime.fromisoformat(c_end)

            if _overlaps_existing(c_start, c_end, segments):
                continue

            segments.append(
                {
                    "start_time": c_start.isoformat(),
                    "end_time": (c_end + timedelta(minutes=5)).isoformat(),
                    "activity_type": "work",
                    "task_id": None,
                    "task_title": None,
                    "description": f"File activity ({len(cluster)} edits, unlinked)",
                    "confidence_sources": {
                        "file_count": len(cluster),
                        "calendar_match": False,
                        "link_confidence": 0.0,
                        "duration_minutes": max(1, int((c_end - c_start).total_seconds() / 60)),
                    },
                    "confirmed": None,
                }
            )

    # Sort segments by start time
    segments.sort(key=lambda s: s["start_time"])

    # Fill gaps with idle segments
    segments = _fill_gaps(segments, day_start, day_end)

    return segments


def _overlaps_meeting(start: datetime, end: datetime, segments: list[dict]) -> bool:
    """Check if a time range overlaps any meeting segment."""
    for seg in segments:
        if seg["activity_type"] != "meeting":
            continue
        seg_start = datetime.fromisoformat(seg["start_time"])
        seg_end = datetime.fromisoformat(seg["end_time"])
        if start < seg_end and end > seg_start:
            return True
    return False


def _overlaps_existing(start: datetime, end: datetime, segments: list[dict]) -> bool:
    """Check if a time range substantially overlaps any existing segment."""
    for seg in segments:
        seg_start = datetime.fromisoformat(seg["start_time"])
        seg_end = datetime.fromisoformat(seg["end_time"])
        if start < seg_end and end > seg_start:
            return True
    return False


def _cluster_activities(
    activities: list[dict], gap_minutes: int = 30
) -> list[list[dict]]:
    """Cluster activities by time gap."""
    if not activities:
        return []

    clusters: list[list[dict]] = [[activities[0]]]
    for fa in activities[1:]:
        ts = fa["timestamp"]
        prev_ts = clusters[-1][-1]["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if isinstance(prev_ts, str):
            prev_ts = datetime.fromisoformat(prev_ts)
        gap = (ts - prev_ts).total_seconds() / 60
        if gap > gap_minutes:
            clusters.append([fa])
        else:
            clusters[-1].append(fa)
    return clusters


def _fill_gaps(
    segments: list[dict], day_start: datetime, day_end: datetime
) -> list[dict]:
    """Fill gaps > 30 min between segments with idle segments."""
    if not segments:
        return []

    filled: list[dict] = []
    prev_end = day_start

    for seg in segments:
        seg_start = datetime.fromisoformat(seg["start_time"])
        if seg_start.tzinfo is None:
            seg_start = seg_start.replace(tzinfo=USER_TZ)

        gap_minutes = (seg_start - prev_end).total_seconds() / 60
        if gap_minutes > 30:
            filled.append(
                {
                    "start_time": prev_end.isoformat(),
                    "end_time": seg_start.isoformat(),
                    "activity_type": "idle",
                    "task_id": None,
                    "task_title": None,
                    "description": f"No detected activity ({int(gap_minutes)} min)",
                    "confidence": 0.9,
                    "confidence_sources": {"inferred_idle": True},
                    "confirmed": None,
                }
            )

        filled.append(seg)
        seg_end = datetime.fromisoformat(seg["end_time"])
        if seg_end.tzinfo is None:
            seg_end = seg_end.replace(tzinfo=USER_TZ)
        prev_end = seg_end

    return filled


# --- Confidence ---


def _compute_confidence(sources: dict[str, Any]) -> float:
    """Compute segment confidence from its sources."""
    score = 0.0
    if sources.get("calendar_match"):
        return 1.0  # calendar events are fully trusted
    if sources.get("inferred_idle"):
        return 0.9  # absence of signal is fairly reliable
    if sources.get("file_count", 0) >= 5:
        score += 0.3
    elif sources.get("file_count", 0) >= 1:
        score += 0.15
    if sources.get("link_confidence", 0) > 0.7:
        score += 0.3
    elif sources.get("link_confidence", 0) > 0.3:
        score += 0.15
    if sources.get("duration_minutes", 0) > 30:
        score += 0.2
    elif sources.get("duration_minutes", 0) > 10:
        score += 0.1
    # Base confidence for any detected activity
    score += 0.1
    return min(1.0, round(score, 2))


def _segment_duration_minutes(seg: dict) -> float:
    """Compute duration of a segment in minutes."""
    try:
        start = datetime.fromisoformat(seg["start_time"])
        end = datetime.fromisoformat(seg["end_time"])
        return max(0, (end - start).total_seconds() / 60)
    except (KeyError, ValueError):
        return 0


# --- Narrative ---


def _generate_heuristic_narrative(segments: list[dict]) -> str:
    """Generate a plain-text summary of the day's timeline."""
    if not segments:
        return "No activity detected today."

    work_segs = [s for s in segments if s["activity_type"] == "work"]
    meeting_segs = [s for s in segments if s["activity_type"] == "meeting"]
    idle_segs = [s for s in segments if s["activity_type"] == "idle"]

    work_minutes = sum(_segment_duration_minutes(s) for s in work_segs)
    meeting_minutes = sum(_segment_duration_minutes(s) for s in meeting_segs)
    idle_minutes = sum(_segment_duration_minutes(s) for s in idle_segs)

    # Unique tasks worked on
    tasks = {s["task_title"] for s in work_segs if s.get("task_title")}
    unlinked = sum(1 for s in work_segs if not s.get("task_title"))

    parts: list[str] = []

    if work_minutes > 0:
        task_str = ", ".join(sorted(tasks)[:3]) if tasks else "unlinked files"
        if len(tasks) > 3:
            task_str += f" +{len(tasks) - 3} more"
        if unlinked:
            task_str += f" (+{unlinked} unlinked session{'s' if unlinked > 1 else ''})"
        parts.append(f"{_fmt_duration(work_minutes)} of work on {task_str}")

    if meeting_minutes > 0:
        meeting_names = [s.get("task_title", "meeting") for s in meeting_segs[:2]]
        parts.append(
            f"{len(meeting_segs)} meeting{'s' if len(meeting_segs) > 1 else ''} "
            f"({_fmt_duration(meeting_minutes)}): {', '.join(meeting_names)}"
        )

    if idle_minutes > 60:
        parts.append(f"{_fmt_duration(idle_minutes)} idle")

    if not parts:
        return "Minimal activity detected today."

    return "Today: " + ". ".join(parts) + "."


def _fmt_duration(minutes: float) -> str:
    """Format minutes into a readable duration string."""
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    if hours > 0 and mins > 0:
        return f"{hours}h {mins}m"
    if hours > 0:
        return f"{hours}h"
    return f"{mins}m"
