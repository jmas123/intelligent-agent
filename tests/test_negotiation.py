"""Tests for negotiation logic (scheduling, proximity scoring, priority)."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from deadline_agent.models import ProposedAction, Task
from deadline_agent.reasoning.negotiation import (
    TYPE_PRIORITY,
    compute_effective_priority,
    create_conflict_session,
    find_bumpable_blocks,
    find_nearest_alternatives,
    score_slot_proximity,
)

EST = ZoneInfo("America/New_York")


class TestScoreSlotProximity:
    def test_same_time_same_day(self):
        """Same slot scores 0."""
        dt = datetime(2026, 4, 10, 14, 0, tzinfo=EST)
        assert score_slot_proximity(dt, dt) == 0.0

    def test_same_day_different_time(self):
        """Same day, 2 hours later."""
        orig = datetime(2026, 4, 10, 14, 0, tzinfo=EST)
        cand = datetime(2026, 4, 10, 16, 0, tzinfo=EST)
        score = score_slot_proximity(orig, cand)
        assert 0 < score < 10  # Less than a full day penalty

    def test_next_day_same_time(self):
        """Next day at same time has day penalty."""
        orig = datetime(2026, 4, 10, 14, 0, tzinfo=EST)
        cand = datetime(2026, 4, 11, 14, 0, tzinfo=EST)
        score = score_slot_proximity(orig, cand)
        assert score == 10.0  # 1 day * 10

    def test_farther_scores_higher(self):
        """Farther slots score higher."""
        orig = datetime(2026, 4, 10, 14, 0, tzinfo=EST)
        close = datetime(2026, 4, 10, 16, 0, tzinfo=EST)
        far = datetime(2026, 4, 12, 20, 0, tzinfo=EST)
        assert score_slot_proximity(orig, close) < score_slot_proximity(orig, far)


class TestComputeEffectivePriority:
    def test_interview_prep_high(self):
        task = Task(
            title="Mock interview",
            source="gcal",
            type="interview_prep",
            urgency_score=5,
            confidence=1.0,
            raw_hash="pri_1",
        )
        assert compute_effective_priority(task) == 5 * 2 + TYPE_PRIORITY["interview_prep"]

    def test_reminder_low(self):
        task = Task(
            title="Check email",
            source="gmail",
            type="reminder",
            urgency_score=1,
            confidence=1.0,
            raw_hash="pri_2",
        )
        assert compute_effective_priority(task) == 1 * 2 + TYPE_PRIORITY["reminder"]

    def test_higher_urgency_wins(self):
        high = Task(
            title="A",
            source="gmail",
            type="assignment",
            urgency_score=5,
            confidence=1.0,
            raw_hash="pri_3",
        )
        low = Task(
            title="B",
            source="gmail",
            type="assignment",
            urgency_score=1,
            confidence=1.0,
            raw_hash="pri_4",
        )
        assert compute_effective_priority(high) > compute_effective_priority(low)


class TestFindBumpableBlocks:
    def test_finds_lower_priority(self, session):
        """Lower-priority executed blocks are bumpable."""
        high_task = Task(
            title="Interview",
            source="gcal",
            type="interview_prep",
            urgency_score=5,
            confidence=1.0,
            raw_hash="bump_1",
            status="pending",
        )
        low_task = Task(
            title="Read Ch5",
            source="gmail",
            type="reminder",
            urgency_score=1,
            confidence=1.0,
            raw_hash="bump_2",
            status="pending",
        )
        session.add_all([high_task, low_task])
        session.flush()

        action = ProposedAction(
            type="calendar_block",
            status="executed",
            task_id=low_task.id,
            title="Block: Read Ch5",
            payload=json.dumps({
                "summary": "Read Ch5",
                "start_iso": "2026-04-10T14:00:00-04:00",
                "end_iso": "2026-04-10T15:00:00-04:00",
            }),
            executed_at=datetime.utcnow(),
        )
        session.add(action)
        session.flush()

        bumpable = find_bumpable_blocks(
            session,
            high_task,
            "2026-04-10T13:00:00-04:00",
            "2026-04-10T16:00:00-04:00",
        )
        assert len(bumpable) == 1
        assert bumpable[0]["task_title"] == "Read Ch5"

    def test_ignores_higher_priority(self, session):
        """Higher-priority blocks are not bumpable."""
        low_task = Task(
            title="Read Ch5",
            source="gmail",
            type="reminder",
            urgency_score=1,
            confidence=1.0,
            raw_hash="bump_3",
            status="pending",
        )
        high_task = Task(
            title="Interview",
            source="gcal",
            type="interview_prep",
            urgency_score=5,
            confidence=1.0,
            raw_hash="bump_4",
            status="pending",
        )
        session.add_all([low_task, high_task])
        session.flush()

        action = ProposedAction(
            type="calendar_block",
            status="executed",
            task_id=high_task.id,
            title="Block: Interview",
            payload=json.dumps({
                "summary": "Interview",
                "start_iso": "2026-04-10T14:00:00-04:00",
                "end_iso": "2026-04-10T15:00:00-04:00",
            }),
            executed_at=datetime.utcnow(),
        )
        session.add(action)
        session.flush()

        bumpable = find_bumpable_blocks(
            session,
            low_task,
            "2026-04-10T13:00:00-04:00",
            "2026-04-10T16:00:00-04:00",
        )
        assert len(bumpable) == 0

    def test_ignores_out_of_range(self, session):
        """Blocks outside the time range are not returned."""
        high_task = Task(
            title="Interview",
            source="gcal",
            type="interview_prep",
            urgency_score=5,
            confidence=1.0,
            raw_hash="bump_5",
            status="pending",
        )
        low_task = Task(
            title="Read",
            source="gmail",
            type="reminder",
            urgency_score=1,
            confidence=1.0,
            raw_hash="bump_6",
            status="pending",
        )
        session.add_all([high_task, low_task])
        session.flush()

        action = ProposedAction(
            type="calendar_block",
            status="executed",
            task_id=low_task.id,
            title="Block: Read",
            payload=json.dumps({
                "summary": "Read",
                "start_iso": "2026-04-12T14:00:00-04:00",
                "end_iso": "2026-04-12T15:00:00-04:00",
            }),
            executed_at=datetime.utcnow(),
        )
        session.add(action)
        session.flush()

        bumpable = find_bumpable_blocks(
            session,
            high_task,
            "2026-04-10T13:00:00-04:00",
            "2026-04-10T16:00:00-04:00",
        )
        assert len(bumpable) == 0


@pytest.mark.asyncio
async def test_find_nearest_alternatives_returns_sorted():
    """Alternatives are sorted by proximity score."""
    mock_busy = [
        {"start": "2026-04-10T14:00:00-04:00", "end": "2026-04-10T15:00:00-04:00"},
    ]
    with patch(
        "deadline_agent.reasoning.negotiation.fetch_free_busy",
        new_callable=AsyncMock,
        return_value=mock_busy,
    ):
        alts = await find_nearest_alternatives(
            "2026-04-10T14:00:00-04:00",
            "2026-04-10T15:00:00-04:00",
            search_days=2,
        )
        # Should find gaps before and after the busy block
        assert len(alts) > 0
        # Verify sorted by proximity
        scores = [a["proximity_score"] for a in alts]
        assert scores == sorted(scores)


@pytest.mark.asyncio
async def test_create_conflict_session(session):
    """Creates a NegotiationSession with alternatives on conflict."""
    task = Task(
        title="HW1",
        source="gmail",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash="conflict_1",
        status="pending",
    )
    session.add(task)
    session.flush()

    action = ProposedAction(
        type="calendar_block",
        task_id=task.id,
        title="Block: HW1",
        payload=json.dumps({
            "summary": "Work on: HW1",
            "start_iso": "2026-04-10T14:00:00-04:00",
            "end_iso": "2026-04-10T15:30:00-04:00",
        }),
    )
    session.add(action)
    session.flush()

    mock_busy = [
        {"start": "2026-04-10T14:00:00-04:00", "end": "2026-04-10T15:00:00-04:00"},
    ]
    with patch(
        "deadline_agent.reasoning.negotiation.fetch_free_busy",
        new_callable=AsyncMock,
        return_value=mock_busy,
    ):
        result = await create_conflict_session(
            session,
            action,
            "2026-04-10T14:00:00-04:00",
            "2026-04-10T15:30:00-04:00",
        )

    assert "session_id" in result
    assert "alternatives" in result
    assert isinstance(result["alternatives"], list)

    # Verify session was created
    from deadline_agent.store.negotiation_repository import NegotiationRepository

    repo = NegotiationRepository(session)
    neg = repo.get(result["session_id"])
    assert neg is not None
    assert neg.trigger == "conflict_409"
    assert neg.original_action_id == action.id
