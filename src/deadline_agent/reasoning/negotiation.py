"""Multi-turn scheduling negotiation logic."""

import json
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import ProposedAction, Task
from deadline_agent.reasoning.calendar_gaps import (
    fetch_free_busy,
    find_gaps,
)

logger = logging.getLogger(__name__)

USER_TZ = ZoneInfo("America/New_York")

# Task type priority for rescheduling comparisons
TYPE_PRIORITY: dict[str, int] = {
    "interview_prep": 5,
    "exam": 4,
    "assignment": 3,
    "meeting": 2,
    "networking": 2,
    "reminder": 1,
    "personal": 1,
    "announcement": 0,
}

NEGOTIATION_SYSTEM_PROMPT = (
    "You are a scheduling negotiation assistant for a student's deadline manager. "
    "The student wants to schedule time for a task but may have encountered a conflict.\n\n"
    "You have access to:\n"
    "- The conversation history of this negotiation\n"
    "- Available alternative time slots\n"
    "- The student's tasks with priority/urgency scores\n"
    "- Their existing calendar events\n\n"
    "When the user says things like 'move it to 10:30' or 'push back to tomorrow', "
    "interpret that relative to the current negotiation context. Extract the intended "
    "time.\n\n"
    "When the user says 'this is more important' or 'push less important things', "
    "set action to 'reschedule_priority'.\n\n"
    "When the user picks one of the suggested alternatives (e.g. 'option 2', 'the Tuesday one'), "
    "set action to 'accept_alternative' with the matching action_id.\n\n"
    "If the user proposes a specific time, set action to 'propose_time' with start_iso and "
    "duration_minutes.\n\n"
    "If you need more information, set action to 'clarify' with your question in explanation."
)


class NegotiationAction(BaseModel):
    """Structured LLM output for a negotiation turn."""

    action: str  # propose_time | accept_alternative | reschedule_priority | clarify
    start_iso: str | None = None
    duration_minutes: int | None = None
    action_id: int | None = None
    explanation: str


def compute_effective_priority(task: Task) -> float:
    """Combine urgency_score and type priority into a single comparable value."""
    type_pri = TYPE_PRIORITY.get(task.type, 1)
    return task.urgency_score * 2 + type_pri


def score_slot_proximity(original_start: datetime, candidate_start: datetime) -> float:
    """Score how close a candidate slot is to the originally requested time.

    Lower = better. Factors: day distance (weighted heavily), time-of-day distance.
    """
    day_diff = abs((candidate_start.date() - original_start.date()).days)
    # Time-of-day distance in hours
    orig_minutes = original_start.hour * 60 + original_start.minute
    cand_minutes = candidate_start.hour * 60 + candidate_start.minute
    time_diff_hours = abs(orig_minutes - cand_minutes) / 60.0
    return day_diff * 10.0 + time_diff_hours


async def find_nearest_alternatives(
    original_start: str,
    original_end: str,
    search_days: int = 3,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Find the closest available time slots to the originally requested time.

    Returns list of {start_iso, end_iso, duration_minutes, proximity_score},
    sorted by proximity (best first).
    """
    orig_start_dt = datetime.fromisoformat(original_start).astimezone(USER_TZ)
    orig_end_dt = datetime.fromisoformat(original_end).astimezone(USER_TZ)
    duration_needed = int((orig_end_dt - orig_start_dt).total_seconds() / 60)

    # Search from start of original day through search_days forward
    search_start = orig_start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    search_end = search_start + timedelta(days=search_days + 1)

    busy = await fetch_free_busy(search_start.isoformat(), search_end.isoformat())
    gaps = find_gaps(
        busy, search_start.isoformat(), search_end.isoformat(), min_minutes=duration_needed
    )

    alternatives: list[dict[str, Any]] = []
    for gap in gaps:
        gap_start = datetime.fromisoformat(gap["start"]).astimezone(USER_TZ)
        # Use start of the gap, capped to duration_needed
        alt_end = gap_start + timedelta(minutes=duration_needed)
        proximity = score_slot_proximity(orig_start_dt, gap_start)

        # Skip the original conflicting slot itself
        if abs((gap_start - orig_start_dt).total_seconds()) < 60:
            continue

        alternatives.append(
            {
                "start_iso": gap_start.isoformat(),
                "end_iso": alt_end.isoformat(),
                "duration_minutes": duration_needed,
                "proximity_score": round(proximity, 2),
            }
        )

    alternatives.sort(key=lambda a: a["proximity_score"])
    return alternatives[:max_results]


async def create_conflict_session(
    db_session: Session,
    conflicting_action: ProposedAction,
    conflict_start: str,
    conflict_end: str,
) -> dict[str, Any]:
    """On 409 conflict, find nearby free slots and create a NegotiationSession.

    Returns {session_id, alternatives: [{action_id, start_iso, end_iso, duration_minutes}]}.
    """
    from deadline_agent.store.action_repository import ActionRepository
    from deadline_agent.store.negotiation_repository import NegotiationRepository

    alternatives = await find_nearest_alternatives(conflict_start, conflict_end)

    neg_repo = NegotiationRepository(db_session)
    action_repo = ActionRepository(db_session)

    # Create ProposedAction entries for each alternative
    original_payload = json.loads(conflicting_action.payload)
    alt_records: list[dict[str, Any]] = []
    for alt in alternatives:
        alt_payload = {
            "summary": original_payload.get("summary", ""),
            "start_iso": alt["start_iso"],
            "end_iso": alt["end_iso"],
        }
        if original_payload.get("description"):
            alt_payload["description"] = original_payload["description"]

        action = action_repo.propose(
            {
                "type": "calendar_block",
                "task_id": conflicting_action.task_id,
                "title": f"Alt: {alt['start_iso'][:16]} — {original_payload.get('summary', '')}",
                "payload": json.dumps(alt_payload),
            }
        )
        alt_records.append(
            {
                "action_id": action.id,
                "start_iso": alt["start_iso"],
                "end_iso": alt["end_iso"],
                "duration_minutes": alt["duration_minutes"],
            }
        )

    # Create negotiation session
    neg = neg_repo.create(
        {
            "trigger": "conflict_409",
            "original_action_id": conflicting_action.id,
            "proposed_alternatives": json.dumps(alt_records),
        }
    )
    # Add initial system message
    neg_repo.add_message(
        neg.id,
        "system",
        f"Time slot conflict detected for '{original_payload.get('summary', '')}' "
        f"at {conflict_start[:16]}. Found {len(alt_records)} alternative(s).",
    )

    return {"session_id": neg.id, "alternatives": alt_records}


def find_bumpable_blocks(
    db_session: Session,
    target_task: Task,
    time_range_start: str,
    time_range_end: str,
) -> list[dict[str, Any]]:
    """Find executed calendar blocks that could be moved for a higher-priority task.

    Returns blocks for tasks with lower effective priority, sorted by
    priority (most bumpable first).
    """
    target_priority = compute_effective_priority(target_task)

    # Find executed calendar_block actions in the time range
    stmt = (
        select(ProposedAction)
        .where(ProposedAction.type == "calendar_block")
        .where(ProposedAction.status == "executed")
    )
    executed = list(db_session.scalars(stmt).all())

    bumpable: list[dict[str, Any]] = []
    for action in executed:
        payload = json.loads(action.payload)
        block_start = payload.get("start_iso", "")
        block_end = payload.get("end_iso", "")
        if not block_start or not block_end:
            continue
        # Check time overlap
        if block_end <= time_range_start or block_start >= time_range_end:
            continue

        # Look up associated task
        if action.task_id is None:
            continue
        task = db_session.get(Task, action.task_id)
        if task is None:
            continue

        block_priority = compute_effective_priority(task)
        if block_priority < target_priority:
            bumpable.append(
                {
                    "action_id": action.id,
                    "task_id": task.id,
                    "task_title": task.title,
                    "task_type": task.type,
                    "priority": block_priority,
                    "start_iso": block_start,
                    "end_iso": block_end,
                    "summary": payload.get("summary", ""),
                }
            )

    bumpable.sort(key=lambda b: b["priority"])
    return bumpable


async def handle_negotiation_turn(
    db_session: Session,
    session_id: int | None,
    user_message: str,
) -> dict[str, Any]:
    """Process one turn of a scheduling negotiation.

    If session_id is None, creates a new user-initiated session.
    Returns {session_id, reply, proposed_actions, status}.
    """
    from deadline_agent.extraction.extractor import ExtractionError
    from deadline_agent.reasoning.engine import call_llm
    from deadline_agent.reasoning.state import build_state_snapshot
    from deadline_agent.store.action_repository import ActionRepository
    from deadline_agent.store.negotiation_repository import NegotiationRepository

    neg_repo = NegotiationRepository(db_session)
    action_repo = ActionRepository(db_session)

    if session_id is None:
        # Create a new user-initiated session
        neg = neg_repo.create({"trigger": "user_initiated"})
        session_id = neg.id
    else:
        neg = neg_repo.get(session_id)
        if neg is None:
            return {
                "session_id": session_id,
                "reply": "Negotiation session not found.",
                "proposed_actions": [],
                "status": "abandoned",
            }
        if neg.status != "active":
            return {
                "session_id": session_id,
                "reply": f"This negotiation is already {neg.status}.",
                "proposed_actions": [],
                "status": neg.status,
            }

    # Add user message to history
    neg_repo.add_message(session_id, "user", user_message)
    neg = neg_repo.get(session_id)
    assert neg is not None

    # Build context for LLM
    snapshot = build_state_snapshot(db_session)
    history = json.loads(neg.conversation_history)
    alternatives = json.loads(neg.proposed_alternatives)

    context_parts = [
        f"Student's current state:\n{snapshot.to_prompt()}",
        f"\nNegotiation history ({len(history)} messages):",
    ]
    for msg in history:
        context_parts.append(f"  [{msg['role']}]: {msg['content']}")

    if alternatives:
        context_parts.append(f"\nCurrent alternatives ({len(alternatives)}):")
        for i, alt in enumerate(alternatives, 1):
            context_parts.append(
                f"  Option {i}: {alt['start_iso'][:16]} "
                f"({alt['duration_minutes']}min, action_id={alt['action_id']})"
            )

    if neg.original_action_id:
        orig = action_repo.get(neg.original_action_id)
        if orig:
            orig_payload = json.loads(orig.payload)
            context_parts.append(
                f"\nOriginal request: {orig_payload.get('summary', '')} "
                f"at {orig_payload.get('start_iso', '')[:16]}"
            )

    user_prompt = "\n".join(context_parts) + f"\n\nUser says: {user_message}"

    schema = NegotiationAction.model_json_schema()

    try:
        raw = await call_llm(NEGOTIATION_SYSTEM_PROMPT, user_prompt, schema=schema)
        parsed = NegotiationAction.model_validate_json(raw)
    except (ExtractionError, Exception) as e:
        logger.warning("LLM negotiation call failed: %s", e)
        reply = "I'm having trouble processing your request right now. Could you try rephrasing?"
        neg_repo.add_message(session_id, "system", reply)
        return {
            "session_id": session_id,
            "reply": reply,
            "proposed_actions": alternatives,
            "status": "active",
        }

    reply = parsed.explanation

    if parsed.action == "accept_alternative" and parsed.action_id:
        # User chose an alternative — resolve session
        neg_repo.resolve(session_id, {"chosen_action_id": parsed.action_id, "summary": reply})
        neg_repo.add_message(session_id, "system", reply)
        return {
            "session_id": session_id,
            "reply": reply,
            "proposed_actions": alternatives,
            "status": "resolved",
        }

    if parsed.action == "propose_time" and parsed.start_iso:
        # Validate and create a new proposal
        duration = parsed.duration_minutes or 60
        start_dt = datetime.fromisoformat(parsed.start_iso)
        end_dt = start_dt + timedelta(minutes=duration)

        # Check for conflicts
        try:
            busy = await fetch_free_busy(start_dt.isoformat(), end_dt.isoformat())
            if busy:
                reply += " However, that slot also has a conflict. Let me find alternatives."
                new_alts = await find_nearest_alternatives(start_dt.isoformat(), end_dt.isoformat())
                # Create actions for new alternatives
                alt_records: list[dict[str, Any]] = []
                for alt in new_alts:
                    # Get summary from original action if available
                    summary = "Scheduled block"
                    if neg.original_action_id:
                        orig = action_repo.get(neg.original_action_id)
                        if orig:
                            summary = json.loads(orig.payload).get("summary", summary)
                    alt_payload = {
                        "summary": summary,
                        "start_iso": alt["start_iso"],
                        "end_iso": alt["end_iso"],
                    }
                    action = action_repo.propose(
                        {
                            "type": "calendar_block",
                            "task_id": neg.original_action_id
                            and action_repo.get(neg.original_action_id)
                            and action_repo.get(neg.original_action_id).task_id,  # type: ignore[union-attr]
                            "title": f"Alt: {alt['start_iso'][:16]} — {summary}",
                            "payload": json.dumps(alt_payload),
                        }
                    )
                    alt_records.append(
                        {
                            "action_id": action.id,
                            "start_iso": alt["start_iso"],
                            "end_iso": alt["end_iso"],
                            "duration_minutes": alt["duration_minutes"],
                        }
                    )
                neg_repo.set_alternatives(session_id, alt_records)
                alternatives = alt_records
            else:
                # Slot is free — create proposal
                summary = "Scheduled block"
                task_id = None
                if neg.original_action_id:
                    orig = action_repo.get(neg.original_action_id)
                    if orig:
                        summary = json.loads(orig.payload).get("summary", summary)
                        task_id = orig.task_id
                new_payload = {
                    "summary": summary,
                    "start_iso": start_dt.isoformat(),
                    "end_iso": end_dt.isoformat(),
                }
                action = action_repo.propose(
                    {
                        "type": "calendar_block",
                        "task_id": task_id,
                        "title": f"Proposed: {start_dt.isoformat()[:16]} — {summary}",
                        "payload": json.dumps(new_payload),
                    }
                )
                alt_records = [
                    {
                        "action_id": action.id,
                        "start_iso": start_dt.isoformat(),
                        "end_iso": end_dt.isoformat(),
                        "duration_minutes": duration,
                    }
                ]
                neg_repo.set_alternatives(session_id, alt_records)
                alternatives = alt_records
        except Exception as e:
            logger.warning("Free/busy check failed during negotiation: %s", e)

    elif parsed.action == "reschedule_priority":
        # Find what can be bumped
        task_id = None
        if neg.original_action_id:
            orig = action_repo.get(neg.original_action_id)
            if orig:
                task_id = orig.task_id
        if task_id:
            task = db_session.get(Task, task_id)
            if task:
                # Look at a 3-day window around now
                local_now = datetime.now(USER_TZ)
                range_start = local_now.isoformat()
                range_end = (local_now + timedelta(days=3)).isoformat()
                bumpable = find_bumpable_blocks(db_session, task, range_start, range_end)
                if bumpable:
                    bump_lines = []
                    for b in bumpable[:3]:
                        bump_lines.append(
                            f"'{b['task_title']}' ({b['task_type']}, priority {b['priority']:.0f}) "
                            f"at {b['start_iso'][:16]}"
                        )
                    reply += "\n\nBlocks that could be moved:\n" + "\n".join(
                        f"  - {line}" for line in bump_lines
                    )
                else:
                    reply += "\n\nNo lower-priority blocks found to reschedule."

    neg_repo.add_message(session_id, "system", reply)
    return {
        "session_id": session_id,
        "reply": reply,
        "proposed_actions": alternatives,
        "status": "active",
    }
