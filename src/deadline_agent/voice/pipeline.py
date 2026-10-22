"""Voice logging pipeline: record → transcribe → extract → apply."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from deadline_agent.config import settings

logger = logging.getLogger(__name__)

VOICE_EXTRACTION_SYSTEM = (
    "You are extracting a structured update from a brief voice note. "
    "The user is updating their deadline tracking system. "
    "Match the intent to one of: marking a task done, logging progress on a task, "
    "switching life context (e.g., 'switching to interview prep'), or adding a free-form note. "
    "If the user mentions a task, match it to the closest title from their pending tasks list."
)

VOICE_UPDATE_TOOL = {
    "name": "voice_update",
    "description": "Extract a structured update from a voice transcription.",
    "input_schema": {
        "type": "object",
        "properties": {
            "update_type": {
                "type": "string",
                "enum": ["task_complete", "task_progress", "context_switch", "note"],
                "description": "Type of update the user is making.",
            },
            "task_title_hint": {
                "type": "string",
                "description": "Best guess at which task this refers to (from the pending list).",
            },
            "status": {
                "type": "string",
                "enum": ["done", "in_progress", "pending"],
                "description": "New status if task_complete or task_progress.",
            },
            "context_season": {
                "type": "string",
                "description": "New life context if context_switch (e.g., recruiting, exams).",
            },
            "note": {
                "type": "string",
                "description": "Free-form note content if update_type is note.",
            },
        },
        "required": ["update_type"],
    },
}


def record_and_transcribe(max_seconds: int = 15) -> str:
    """Record audio and transcribe it. Returns transcribed text.

    Raises ImportError if sounddevice or whisper are not installed.
    """
    from deadline_agent.voice.recorder import AudioRecorder
    from deadline_agent.voice.transcriber import WhisperTranscriber

    recorder = AudioRecorder()
    recorder.start_recording()

    import time

    time.sleep(max_seconds)

    wav_path = recorder.stop_recording()

    transcriber = WhisperTranscriber(model_name=settings.whisper_model)
    text = transcriber.transcribe(wav_path)
    recorder.cleanup(wav_path)

    return text


async def extract_structured_update(
    text: str, session: Session
) -> dict[str, Any]:
    """Parse transcribed text into a structured update using LLM tool calling.

    Falls back to a simple note if LLM is unavailable.
    """
    if not text.strip():
        return {"update_type": "note", "note": ""}

    # Get pending task titles for context
    from sqlalchemy import select

    from deadline_agent.models import Task

    pending_tasks = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .order_by(Task.urgency_score.desc())
            .limit(20)
        ).all()
    )
    task_list = "\n".join(f"- {t.title}" for t in pending_tasks) or "No pending tasks."

    prompt = (
        f"Voice transcription: \"{text}\"\n\n"
        f"Pending tasks:\n{task_list}\n\n"
        "Extract the structured update."
    )

    try:
        from deadline_agent.reasoning.engine import get_anthropic_client

        client = get_anthropic_client()
        response = client.messages.create(
            model=settings.reasoning_model
            if settings.reasoning_provider == "anthropic"
            else "claude-haiku-4-5-20251001",
            max_tokens=256,
            system=VOICE_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            tools=[VOICE_UPDATE_TOOL],
            tool_choice={"type": "tool", "name": "voice_update"},
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "voice_update":
                return block.input

    except Exception:
        logger.warning("LLM extraction failed, falling back to note", exc_info=True)

    # Fallback: store as note
    return {"update_type": "note", "note": text}


async def apply_update(
    update: dict[str, Any], session: Session
) -> str:
    """Apply a structured voice update. Returns a human-readable result string."""
    update_type = update.get("update_type", "note")

    if update_type == "task_complete":
        return await _apply_task_complete(update, session)
    elif update_type == "task_progress":
        return await _apply_task_progress(update, session)
    elif update_type == "context_switch":
        return _apply_context_switch(update)
    elif update_type == "note":
        return _apply_note(update, session)
    else:
        return f"Unknown update type: {update_type}"


async def _apply_task_complete(update: dict, session: Session) -> str:
    """Mark a task as done based on voice input."""
    hint = update.get("task_title_hint", "")
    task = _fuzzy_match_task(hint, session)
    if not task:
        return f"Could not find a task matching '{hint}'"

    task.status = "done"
    session.flush()

    # Emit status change event for de-escalation
    from deadline_agent.events import TASK_STATUS_CHANGED, Event, event_bus

    await event_bus.emit(
        Event(
            type=TASK_STATUS_CHANGED,
            payload={"task_id": task.id, "new_status": "done"},
        )
    )

    return f"Marked '{task.title}' as done"


async def _apply_task_progress(update: dict, session: Session) -> str:
    """Log progress on a task."""
    hint = update.get("task_title_hint", "")
    task = _fuzzy_match_task(hint, session)
    if not task:
        return f"Could not find a task matching '{hint}'"

    # Create a short work session
    from deadline_agent.models import WorkSession

    now = datetime.now(UTC)
    ws = WorkSession(
        task_id=task.id,
        started_at=now - __import__("datetime").timedelta(minutes=5),
        ended_at=now,
        duration_minutes=5,
        file_activity_count=0,
    )
    session.add(ws)
    session.flush()

    return f"Logged progress on '{task.title}'"


def _apply_context_switch(update: dict) -> str:
    """Switch life context based on voice input."""
    season = update.get("context_season", "")
    if not season:
        return "No context specified"

    try:
        from deadline_agent.awareness.ambient_state import ambient_state

        old = ambient_state.current_mode
        ambient_state.current_mode = season
        return f"Switched context: {old or 'default'} → {season}"
    except Exception:
        return f"Context switch to '{season}' noted"


def _apply_note(update: dict, session: Session) -> str:
    """Store a voice note as an insight."""
    note = update.get("note", "")
    if not note:
        return "Empty note"

    from deadline_agent.models import Insight

    insight = Insight(
        text=note,
        source="voice",
        category="voice_note",
    )
    session.add(insight)
    session.flush()

    return f"Note saved: {note[:50]}{'...' if len(note) > 50 else ''}"


def _fuzzy_match_task(hint: str, session: Session) -> Any:
    """Find the best matching pending task by title similarity."""
    if not hint:
        return None

    from sqlalchemy import select

    from deadline_agent.models import Task

    tasks = list(
        session.scalars(
            select(Task).where(Task.status == "pending")
        ).all()
    )

    if not tasks:
        return None

    hint_lower = hint.lower()

    # Exact substring match first
    for task in tasks:
        if hint_lower in task.title.lower():
            return task

    # Word overlap scoring
    hint_words = set(hint_lower.split())
    best_task = None
    best_score = 0
    for task in tasks:
        title_words = set(task.title.lower().split())
        overlap = len(hint_words & title_words)
        if overlap > best_score:
            best_score = overlap
            best_task = task

    return best_task if best_score > 0 else None
