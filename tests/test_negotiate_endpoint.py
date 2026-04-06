"""Tests for the /api/chat/negotiate and /api/chat/negotiations endpoints."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from deadline_agent.main import app
from deadline_agent.models import NegotiationSession, ProposedAction


@pytest.mark.asyncio
async def test_list_negotiations_empty():
    """Empty list when no sessions exist."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/chat/negotiations")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_negotiation_not_found():
    """404 for non-existent session."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/chat/negotiations/99999")
    assert resp.status_code == 404


def test_negotiation_session_schema(session):
    """NegotiationSession model creates correctly via SQLAlchemy."""
    neg = NegotiationSession(trigger="conflict_409")
    session.add(neg)
    session.flush()

    assert neg.id is not None
    assert neg.status == "active"
    assert neg.trigger == "conflict_409"
    assert json.loads(neg.proposed_alternatives) == []
    assert json.loads(neg.conversation_history) == []
    assert neg.resolution is None


def test_negotiation_session_with_action(session):
    """NegotiationSession links to a ProposedAction."""
    action = ProposedAction(
        type="calendar_block",
        title="Test block",
        payload='{"summary": "test"}',
    )
    session.add(action)
    session.flush()

    neg = NegotiationSession(
        trigger="conflict_409",
        original_action_id=action.id,
    )
    session.add(neg)
    session.flush()

    assert neg.original_action_id == action.id
