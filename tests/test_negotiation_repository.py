"""Tests for NegotiationRepository."""

import json
from datetime import UTC, datetime, timedelta

from deadline_agent.models import ProposedAction
from deadline_agent.store.negotiation_repository import NegotiationRepository


def test_create_and_get(session):
    """Create a session and retrieve it by ID."""
    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "conflict_409"})

    assert neg.id is not None
    assert neg.status == "active"
    assert neg.trigger == "conflict_409"

    fetched = repo.get(neg.id)
    assert fetched is not None
    assert fetched.id == neg.id


def test_list_active(session):
    """List only active sessions."""
    repo = NegotiationRepository(session)
    repo.create({"trigger": "conflict_409"})
    repo.create({"trigger": "user_initiated"})

    # Resolve one
    active = repo.list_active()
    assert len(active) == 2

    repo.resolve(active[0].id, {"summary": "done"})
    assert len(repo.list_active()) == 1


def test_add_message(session):
    """Append messages to conversation history."""
    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "user_initiated"})

    repo.add_message(neg.id, "user", "move it to 10:30")
    repo.add_message(neg.id, "system", "I can do that.")

    neg = repo.get(neg.id)
    history = json.loads(neg.conversation_history)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "system"
    assert "timestamp" in history[0]


def test_add_message_caps_history(session):
    """History is capped at MAX_HISTORY_MESSAGES."""
    from deadline_agent.store.negotiation_repository import MAX_HISTORY_MESSAGES

    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "user_initiated"})

    for i in range(MAX_HISTORY_MESSAGES + 5):
        repo.add_message(neg.id, "user", f"msg {i}")

    neg = repo.get(neg.id)
    history = json.loads(neg.conversation_history)
    assert len(history) == MAX_HISTORY_MESSAGES


def test_add_message_to_resolved_returns_none(session):
    """Cannot add messages to a resolved session."""
    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "conflict_409"})
    repo.resolve(neg.id, {"summary": "done"})

    result = repo.add_message(neg.id, "user", "hello")
    assert result is None


def test_set_alternatives(session):
    """Set and retrieve alternatives."""
    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "conflict_409"})

    alts = [
        {
            "action_id": 1,
            "start_iso": "2026-04-10T14:00:00",
            "end_iso": "2026-04-10T16:00:00",
            "duration_minutes": 120,
        },
        {
            "action_id": 2,
            "start_iso": "2026-04-11T10:00:00",
            "end_iso": "2026-04-11T12:00:00",
            "duration_minutes": 120,
        },
    ]
    repo.set_alternatives(neg.id, alts)

    neg = repo.get(neg.id)
    stored = json.loads(neg.proposed_alternatives)
    assert len(stored) == 2
    assert stored[0]["action_id"] == 1


def test_resolve(session):
    """Resolve a session with chosen outcome."""
    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "conflict_409"})

    repo.resolve(neg.id, {"chosen_action_id": 42, "summary": "picked option 1"})

    neg = repo.get(neg.id)
    assert neg.status == "resolved"
    resolution = json.loads(neg.resolution)
    assert resolution["chosen_action_id"] == 42


def test_abandon(session):
    """Abandon a session."""
    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "user_initiated"})

    repo.abandon(neg.id)

    neg = repo.get(neg.id)
    assert neg.status == "abandoned"


def test_expire_stale(session):
    """Expire sessions with no activity for N hours."""
    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "conflict_409"})

    # Manually set updated_at to the past
    neg.updated_at = datetime.now(UTC) - timedelta(hours=25)
    session.commit()

    expired = repo.expire_stale(hours=24)
    assert expired == 1

    neg = repo.get(neg.id)
    assert neg.status == "abandoned"


def test_expire_stale_skips_recent(session):
    """Recent sessions are not expired."""
    repo = NegotiationRepository(session)
    repo.create({"trigger": "conflict_409"})

    expired = repo.expire_stale(hours=24)
    assert expired == 0


def test_with_original_action(session):
    """Session linked to an original ProposedAction."""
    action = ProposedAction(
        type="calendar_block",
        task_id=None,
        title="Test block",
        payload=json.dumps({
            "summary": "test",
            "start_iso": "2026-04-10T10:00:00",
            "end_iso": "2026-04-10T12:00:00",
        }),
    )
    session.add(action)
    session.flush()

    repo = NegotiationRepository(session)
    neg = repo.create({"trigger": "conflict_409", "original_action_id": action.id})

    assert neg.original_action_id == action.id
