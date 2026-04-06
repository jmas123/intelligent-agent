"""Tests for the /api/chat/debrief endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from deadline_agent.main import app
from deadline_agent.models import SemesterRecord


@pytest.mark.asyncio
async def test_list_debriefs_empty():
    """Empty list when no debriefs exist."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        resp = await ac.get("/api/chat/debrief")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_debrief_not_found():
    """404 for non-existent debrief."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        resp = await ac.get("/api/chat/debrief/99999")
    assert resp.status_code == 404


def test_semester_record_model(session):
    """SemesterRecord model creates correctly."""
    record = SemesterRecord(
        term_name="Winter 2026",
        start_date="2026-01-10",
        end_date="2026-04-25",
        tasks_completed=30,
        tasks_slipped=5,
        total_work_minutes=2400,
        debrief_text="Good semester.",
    )
    session.add(record)
    session.flush()

    assert record.id is not None
    assert record.term_name == "Winter 2026"
    assert record.tasks_completed == 30
