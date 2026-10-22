"""Micro-confirmations: low-friction yes/no prompts for ambiguous inferences."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import DailyReconstruction, TaskRisk

logger = logging.getLogger(__name__)


async def trigger_micro_confirmations(
    session: Session, reconstruction: DailyReconstruction
) -> int:
    """Fire micro-confirmation prompts for low-confidence timeline segments.

    Routes through the intervention engine with category="micro_confirmation".
    Returns count of confirmations fired.
    """
    from deadline_agent.reasoning.intervention_engine import get_intervention_engine

    segments = json.loads(reconstruction.timeline_json)
    engine = get_intervention_engine()

    # Filter: low confidence, not yet confirmed, work segments only
    candidates = [
        (i, seg)
        for i, seg in enumerate(segments)
        if seg.get("confidence", 1.0) < settings.micro_confirmation_threshold
        and seg.get("confirmed") is None
        and seg.get("activity_type") == "work"
    ]

    # Sort by duration descending (longest = most impactful)
    candidates.sort(
        key=lambda x: _segment_duration(x[1]),
        reverse=True,
    )

    # Cap at max per day
    candidates = candidates[: settings.max_micro_confirmations_per_day]

    # Materiality check: skip if task has no upcoming deadline or risk
    candidates = [
        (i, seg)
        for i, seg in candidates
        if _is_material(session, seg)
    ]

    fired = 0
    for seg_index, seg in candidates:
        task_title = seg.get("task_title") or "unknown task"
        start = _format_time(seg.get("start_time", ""))
        end = _format_time(seg.get("end_time", ""))

        record = await engine.evaluate(
            session,
            task_id=seg.get("task_id"),
            risk_level="low",
            category="micro_confirmation",
            title="Was this real progress?",
            body=f"Did you work on {task_title} from {start} to {end}?",
        )
        if record.fired:
            fired += 1

    if fired:
        session.commit()
        logger.info(
            "Fired %d micro-confirmation(s) for %s", fired, reconstruction.date
        )
    return fired


def process_confirmation_response(
    session: Session,
    date: str,
    segment_index: int,
    confirmed: bool,
) -> None:
    """Process a user's yes/no response to a micro-confirmation.

    Updates the reconstruction segment and adjusts file-task link confidence.
    """
    from deadline_agent.store.reconstruction_repository import ReconstructionRepository

    repo = ReconstructionRepository(session)
    if not repo.update_segment_confirmation(date, segment_index, confirmed):
        logger.warning("Failed to update segment %d for %s", segment_index, date)
        return

    # Adjust file-task link confidence based on response
    rec = repo.get_by_date(date)
    if not rec:
        return
    segments = json.loads(rec.timeline_json)
    if segment_index >= len(segments):
        return

    seg = segments[segment_index]
    task_id = seg.get("task_id")
    if task_id is None:
        return

    from deadline_agent.models import FileTaskLink

    links = list(
        session.scalars(
            select(FileTaskLink).where(FileTaskLink.task_id == task_id)
        ).all()
    )

    for link in links:
        if confirmed:
            link.confidence = min(1.0, link.confidence + 0.1)
        else:
            link.confidence = max(0.1, link.confidence - 0.2)
    session.flush()

    logger.info(
        "Confirmation for %s segment %d: %s (adjusted %d links)",
        date,
        segment_index,
        "yes" if confirmed else "no",
        len(links),
    )


def _is_material(session: Session, seg: dict) -> bool:
    """Check if confirming this segment would materially affect recommendations."""
    task_id = seg.get("task_id")
    if task_id is None:
        return True  # unlinked work is always worth confirming

    # Check if task has upcoming deadline or active risk
    from deadline_agent.models import Task

    task = session.get(Task, task_id)
    if not task:
        return False

    # Has deadline in next 7 days?
    if task.due_date_iso:
        try:
            due = datetime.fromisoformat(task.due_date_iso)
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            if (due - datetime.now(UTC)).days <= 7:
                return True
        except (ValueError, TypeError):
            pass

    # Has active risk assessment?
    risk = session.scalars(
        select(TaskRisk).where(TaskRisk.task_id == task_id)
    ).first()
    if risk and risk.probability_of_missing > 0.3:
        return True

    return False


def _segment_duration(seg: dict) -> float:
    """Duration of a segment in minutes."""
    try:
        start = datetime.fromisoformat(seg["start_time"])
        end = datetime.fromisoformat(seg["end_time"])
        return max(0, (end - start).total_seconds() / 60)
    except (KeyError, ValueError):
        return 0


def _format_time(iso_str: str) -> str:
    """Format ISO time to readable local time."""
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(iso_str)
        local = dt.astimezone(ZoneInfo("America/New_York"))
        return local.strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return iso_str
