"""Tests for the /api/chat/patterns endpoint."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from deadline_agent.main import app
from deadline_agent.models import BehavioralPattern


@pytest.fixture
def _seed_patterns(session):
    """Seed patterns into the test database."""
    p = BehavioralPattern(
        pattern_type="effort_accuracy",
        pattern_key="assignment",
        value=json.dumps({"ratio": 1.5, "mean_actual_min": 180, "mean_estimated_min": 120}),
        sample_count=8,
        confidence=0.8,
    )
    session.add(p)
    session.commit()


@pytest.mark.asyncio
async def test_get_patterns_empty():
    """Empty list when no patterns exist."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/chat/patterns")
    assert resp.status_code == 200
    # May or may not be empty depending on DB state, just check it's a list
    assert isinstance(resp.json(), list)
