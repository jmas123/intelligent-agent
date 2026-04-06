"""Tests for the LLM-powered digest v2."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.notifications.digest import generate_digest_v2, send_digest_v2


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
@patch("deadline_agent.reasoning.engine.generate_summary", new_callable=AsyncMock)
async def test_digest_v2_uses_llm(mock_summary: AsyncMock, session: Session) -> None:
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_summary.return_value = "Focus on HW3 today — it's due in 4 hours."

    result = await generate_digest_v2(session)
    assert result == "Focus on HW3 today — it's due in 4 hours."


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine.generate_summary", new_callable=AsyncMock)
async def test_digest_v2_falls_back_to_v1(mock_summary: AsyncMock, session: Session) -> None:
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_summary.return_value = None  # LLM returned nothing

    result = await generate_digest_v2(session)
    assert result is not None
    assert "upcoming deadline" in result


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine.generate_summary", new_callable=AsyncMock)
async def test_digest_v2_falls_back_on_error(mock_summary: AsyncMock, session: Session) -> None:
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_summary.side_effect = Exception("LLM crashed")

    result = await generate_digest_v2(session)
    assert result is not None
    assert "upcoming deadline" in result


@pytest.mark.asyncio
async def test_digest_v2_no_tasks(session: Session) -> None:
    result = await generate_digest_v2(session)
    assert result is None


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine.generate_summary", new_callable=AsyncMock)
@patch("deadline_agent.notifications.digest.send_notification", return_value=True)
async def test_send_digest_v2(
    mock_notify: object, mock_summary: AsyncMock, session: Session
) -> None:
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_summary.return_value = "Focus on HW3 today."

    result = await send_digest_v2(session)
    assert result is True
