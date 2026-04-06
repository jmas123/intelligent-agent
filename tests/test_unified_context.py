"""Tests for the unified context builder."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.reasoning.state import UnifiedContext, build_unified_context


def _add_task(session: Session, hours_until_due: int = 4, **overrides: object) -> Task:
    due = datetime.now(UTC) + timedelta(hours=hours_until_due)
    defaults: dict[str, object] = {
        "title": "Submit HW3",
        "due_date_iso": due.isoformat(),
        "source": "gmail",
        "type": "assignment",
        "course": "CS 101",
        "urgency_score": 4,
        "confidence": 0.92,
        "raw_hash": f"hash_{hours_until_due}_{id(overrides)}",
        "status": "pending",
    }
    defaults.update(overrides)
    task = Task(**defaults)
    session.add(task)
    session.flush()
    return task


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.calendar_gaps.fetch_free_busy", new_callable=AsyncMock)
async def test_build_unified_context(mock_freebusy: AsyncMock, session: Session) -> None:
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_freebusy.return_value = []

    ctx = await build_unified_context(session)
    assert isinstance(ctx, UnifiedContext)
    assert ctx.total_pending >= 1
    assert isinstance(ctx.calendar_gaps, list)
    assert isinstance(ctx.weekly_stats, dict)


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.calendar_gaps.fetch_free_busy", new_callable=AsyncMock)
async def test_unified_context_includes_weekly_stats(
    mock_freebusy: AsyncMock, session: Session
) -> None:
    # Add a done task to count in weekly stats
    task = _add_task(session, hours_until_due=-24, status="done", raw_hash="done1")
    task.updated_at = datetime.now()
    session.commit()

    mock_freebusy.return_value = []

    ctx = await build_unified_context(session)
    assert ctx.weekly_stats.get("done_count") == 1


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.calendar_gaps.fetch_free_busy", new_callable=AsyncMock)
async def test_unified_context_calendar_failure_graceful(
    mock_freebusy: AsyncMock, session: Session
) -> None:
    mock_freebusy.side_effect = Exception("API down")

    ctx = await build_unified_context(session)
    assert ctx.calendar_gaps == []  # Graceful fallback


def test_unified_context_to_prompt() -> None:
    ctx = UnifiedContext(
        now=datetime.now(UTC),
        calendar_gaps=[
            {"start": "2026-03-25T10:00", "end": "2026-03-25T14:00", "duration_minutes": 240}
        ],
        weekly_stats={
            "done_count": 3,
            "slipped_count": 1,
            "by_course": {"CS 101": {"done": 2, "slipped": 1}},
        },
    )
    prompt = ctx.to_prompt()
    assert "FREE CALENDAR WINDOWS" in prompt
    assert "240min free" in prompt
    assert "WEEKLY STATS" in prompt
    assert "CS 101" in prompt
