"""Focus quality measurement from file activity patterns.

Computes:
- Edit-to-output ratio: many saves with little size change = stuck
- Session fragmentation: unique files / session duration = context switching
- Inter-file-open interval: gaps between distinct file paths
"""

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import FileActivity, WorkSession
from deadline_agent.store.pattern_repository import PatternRepository

logger = logging.getLogger(__name__)


def analyze_focus_quality(session: Session) -> list[tuple[str, str]]:
    """Compute focus quality from file activity size deltas.

    For each file edited in the last 24 hours, compares save count vs net size change.
    High save count with little net change indicates being stuck.

    Returns list of (pattern_key, status) tuples for testing.
    """
    now = datetime.now(UTC)
    since = (now - timedelta(hours=24)).isoformat()

    activities = list(
        session.scalars(
            select(FileActivity)
            .where(FileActivity.created_at >= since)
            .where(FileActivity.event_type == "modified")
            .order_by(FileActivity.modified_at.asc())
        ).all()
    )

    if not activities:
        return []

    # Group by file path: track saves and size changes
    by_file: dict[str, list[FileActivity]] = defaultdict(list)
    for a in activities:
        by_file[a.path].append(a)

    repo = PatternRepository(session)
    results: list[tuple[str, str]] = []

    for path, edits in by_file.items():
        if len(edits) < 3:
            continue  # Need minimum edits to judge

        save_count = len(edits)
        sizes = [e.size_bytes for e in edits]
        net_change = abs(sizes[-1] - sizes[0])
        avg_change_per_save = net_change / save_count if save_count > 0 else 0

        # Stuck threshold: many saves (5+) with < 100 bytes net change per save
        if save_count >= 5 and avg_change_per_save < 100:
            status = "stuck"
        elif save_count >= 3 and avg_change_per_save < 50:
            status = "stuck"
        elif avg_change_per_save > 500:
            status = "productive"
        else:
            status = "normal"

        # Use filename as pattern key
        filename = edits[0].filename
        value = json.dumps({
            "status": status,
            "save_count": save_count,
            "net_change_bytes": net_change,
            "avg_change_per_save": round(avg_change_per_save, 1),
        })

        confidence = min(1.0, save_count / 10)
        repo.upsert("focus_quality", filename, value, save_count, confidence)
        results.append((filename, status))

    return results


def analyze_session_fragmentation(session: Session) -> list[tuple[str, float]]:
    """Compute session fragmentation scores from work sessions.

    Fragmentation = unique_files / session_duration_minutes.
    High values indicate rapid context switching between files.

    Returns list of (period_key, fragmentation_score) tuples.
    """
    now = datetime.now(UTC)
    week_ago = (now - timedelta(days=7)).isoformat()

    work_sessions = list(
        session.scalars(
            select(WorkSession)
            .where(WorkSession.started_at >= week_ago)
            .where(WorkSession.duration_minutes >= 5)
        ).all()
    )

    if not work_sessions:
        return []

    # Compute per-session fragmentation, then aggregate
    total_duration = 0
    total_unique_files = 0
    session_scores: list[float] = []

    for ws in work_sessions:
        if ws.duration_minutes < 5:
            continue

        # Count unique files touched during this session
        activities = list(
            session.scalars(
                select(FileActivity.path)
                .where(FileActivity.modified_at >= ws.started_at)
                .where(FileActivity.modified_at <= ws.ended_at)
            ).all()
        )

        unique_files = len(set(activities))
        if unique_files == 0:
            continue

        score = unique_files / ws.duration_minutes
        session_scores.append(score)
        total_duration += ws.duration_minutes
        total_unique_files += unique_files

    if not session_scores:
        return []

    avg_score = sum(session_scores) / len(session_scores)
    avg_duration = total_duration / len(session_scores)

    repo = PatternRepository(session)
    value = json.dumps({
        "avg_fragmentation": round(avg_score, 4),
        "avg_session_duration_min": round(avg_duration, 1),
        "avg_unique_files_per_session": round(
            total_unique_files / len(session_scores), 1
        ),
        "sessions_analyzed": len(session_scores),
    })

    confidence = min(1.0, len(session_scores) / 10)
    repo.upsert(
        "session_fragmentation", "weekly", value, len(session_scores), confidence
    )

    return [("weekly", avg_score)]


def compute_inter_file_intervals(session: Session) -> dict[str, float]:
    """Compute average time between switching files in the last 24h.

    Returns dict with avg_interval_seconds and switch_count.
    """
    now = datetime.now(UTC)
    since = (now - timedelta(hours=24)).isoformat()

    activities = list(
        session.scalars(
            select(FileActivity)
            .where(FileActivity.created_at >= since)
            .order_by(FileActivity.modified_at.asc())
        ).all()
    )

    if len(activities) < 2:
        return {}

    gaps: list[float] = []
    prev = activities[0]
    for curr in activities[1:]:
        if curr.path != prev.path:
            prev_ts = prev.modified_at
            curr_ts = curr.modified_at
            if prev_ts.tzinfo is None:
                prev_ts = prev_ts.replace(tzinfo=UTC)
            if curr_ts.tzinfo is None:
                curr_ts = curr_ts.replace(tzinfo=UTC)
            gap = (curr_ts - prev_ts).total_seconds()
            if 0 < gap < 7200:  # Ignore gaps > 2 hours (break, not switch)
                gaps.append(gap)
        prev = curr

    if not gaps:
        return {}

    return {
        "avg_interval_seconds": round(sum(gaps) / len(gaps), 1),
        "switch_count": len(gaps),
        "min_interval_seconds": round(min(gaps), 1),
    }
