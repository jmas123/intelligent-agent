"""Tests for weekly review."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.reasoning.weekly_review import compute_weekly_stats, generate_weekly_review


def _add_task(session: Session, **overrides: object) -> Task:
    defaults: dict[str, object] = {
        "title": "Submit HW3",
        "due_date_iso": (datetime.now(UTC) + timedelta(hours=48)).isoformat(),
        "source": "gmail",
        "type": "assignment",
        "course": "CS 101",
        "urgency_score": 3,
        "confidence": 0.9,
        "raw_hash": f"hash_{id(overrides)}",
        "status": "pending",
    }
    defaults.update(overrides)
    task = Task(**defaults)
    session.add(task)
    session.flush()
    return task


def test_compute_weekly_stats_empty(session: Session) -> None:
    stats = compute_weekly_stats(session)
    assert stats["done_count"] == 0
    assert stats["slipped_count"] == 0


def test_compute_weekly_stats_with_done(session: Session) -> None:
    task = _add_task(session, status="done", raw_hash="done1")
    task.updated_at = datetime.now()
    session.commit()

    stats = compute_weekly_stats(session)
    assert stats["done_count"] == 1
    assert "Submit HW3" in stats["done_tasks"]


def test_compute_weekly_stats_with_slipped(session: Session) -> None:
    _add_task(
        session,
        status="pending",
        due_date_iso=(datetime.now(UTC) - timedelta(hours=24)).isoformat(),
        raw_hash="slip1",
    )

    stats = compute_weekly_stats(session)
    assert stats["slipped_count"] == 1


def test_compute_weekly_stats_by_course(session: Session) -> None:
    t1 = _add_task(session, status="done", course="CS 101", raw_hash="c1")
    t1.updated_at = datetime.now()
    t2 = _add_task(session, status="done", course="Math 200", raw_hash="c2")
    t2.updated_at = datetime.now()
    session.commit()

    stats = compute_weekly_stats(session)
    by_course = stats["by_course"]
    assert "CS 101" in by_course
    assert "Math 200" in by_course
    assert by_course["CS 101"]["done"] == 1


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.weekly_review._call_ollama", new_callable=AsyncMock)
async def test_generate_weekly_review_llm(mock_ollama: AsyncMock, session: Session) -> None:
    task = _add_task(session, status="done", raw_hash="rev1")
    task.updated_at = datetime.now()
    session.commit()

    mock_ollama.return_value = json.dumps({"review": "Great week! You completed HW3."})

    result = await generate_weekly_review(session)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_generate_weekly_review_no_activity(session: Session) -> None:
    result = await generate_weekly_review(session)
    assert result == "No task activity this week."
