"""Infer WorkSessions from FileActivity timestamps linked to tasks."""

import logging
from itertools import groupby

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import FileActivity, FileTaskLink, WorkSession
from deadline_agent.store.session_repository import WorkSessionRepository

logger = logging.getLogger(__name__)

MIN_SESSION_MINUTES = 5


def infer_sessions(session: Session) -> list[WorkSession]:
    """Cluster FileActivity records into WorkSessions grouped by task_id.

    Algorithm:
    1. Join FileTaskLink -> FileActivity, ordered by (task_id, modified_at).
    2. Group consecutive activities by task_id.
    3. Split into sessions where the gap exceeds session_gap_minutes.
    4. Skip sessions shorter than MIN_SESSION_MINUTES.
    5. Deduplicate against existing sessions.

    Returns newly created WorkSession records.
    """
    gap_minutes = settings.session_gap_minutes
    repo = WorkSessionRepository(session)

    # Fetch all linked file activities ordered for clustering
    stmt = (
        select(FileTaskLink.task_id, FileActivity.modified_at)
        .join(FileActivity, FileTaskLink.file_activity_id == FileActivity.id)
        .order_by(FileTaskLink.task_id, FileActivity.modified_at.asc())
    )
    rows = session.execute(stmt).all()

    if not rows:
        return []

    created: list[WorkSession] = []

    for task_id, group in groupby(rows, key=lambda r: r[0]):
        timestamps = [r[1] for r in group]
        if not timestamps:
            continue

        # Split into sessions based on gap threshold
        cluster_start = timestamps[0]
        cluster_end = timestamps[0]
        cluster_count = 1

        for ts in timestamps[1:]:
            gap = (ts - cluster_end).total_seconds() / 60
            if gap > gap_minutes:
                # Flush current cluster
                ws = _maybe_create_session(
                    repo, task_id, cluster_start, cluster_end, cluster_count
                )
                if ws is not None:
                    created.append(ws)
                # Start new cluster
                cluster_start = ts
                cluster_end = ts
                cluster_count = 1
            else:
                cluster_end = ts
                cluster_count += 1

        # Flush final cluster
        ws = _maybe_create_session(repo, task_id, cluster_start, cluster_end, cluster_count)
        if ws is not None:
            created.append(ws)

    logger.info("Inferred %d new work session(s)", len(created))
    return created


def _maybe_create_session(
    repo: WorkSessionRepository,
    task_id: int,
    started_at: object,
    ended_at: object,
    count: int,
) -> WorkSession | None:
    """Create a session if it meets duration threshold and isn't a duplicate."""
    from datetime import datetime

    start = (
        started_at if isinstance(started_at, datetime)
        else datetime.fromisoformat(str(started_at))
    )
    end = (
        ended_at if isinstance(ended_at, datetime)
        else datetime.fromisoformat(str(ended_at))
    )

    duration = int((end - start).total_seconds() / 60)
    if duration < MIN_SESSION_MINUTES:
        return None
    if repo.already_inferred(task_id, start):
        return None

    return repo.create(
        {
            "task_id": task_id,
            "started_at": start,
            "ended_at": end,
            "duration_minutes": duration,
            "file_activity_count": count,
        }
    )
