"""Tests for compound trigger engine and activity baseline."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from deadline_agent.events import (
    RECRUITING_STATUS_CHANGED,
    TASK_CREATED,
    Event,
    EventBus,
)
from deadline_agent.models import FileActivity, Task
from deadline_agent.triggers import ActivityBaseline, CompoundTriggerEngine


@pytest.fixture
def bus():
    return EventBus()


class FakeFactory:
    """Context-manager factory wrapping a test session."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self._session

    def __exit__(self, *args):
        pass


@pytest.fixture
def trigger_eng(session, bus):
    """Trigger engine wired to a test session and bus."""
    eng = CompoundTriggerEngine(FakeFactory(session))
    eng.register(bus)
    return eng


class TestActivityBaseline:
    @pytest.mark.asyncio
    async def test_not_enough_data(self, session):
        """No drop detected with insufficient data."""
        baseline = ActivityBaseline(FakeFactory(session))
        baseline._last_check = None
        await baseline.check_and_emit()
        assert not baseline.is_dropped()

    @pytest.mark.asyncio
    async def test_drop_detected(self, session):
        """Drop detected when today's count is well below average."""
        now = datetime.now(UTC)
        for day_offset in range(1, 8):
            for i in range(10):
                fa = FileActivity(
                    path=f"/tmp/file_{day_offset}_{i}.pdf",
                    filename=f"file_{day_offset}_{i}.pdf",
                    directory="/tmp",
                    size_bytes=100,
                    modified_at=now - timedelta(days=day_offset),
                    event_type="modified",
                )
                fa.created_at = now - timedelta(days=day_offset)
                session.add(fa)
        # Today: only 2 events (80% drop from avg of 10)
        for i in range(2):
            fa = FileActivity(
                path=f"/tmp/today_{i}.pdf",
                filename=f"today_{i}.pdf",
                directory="/tmp",
                size_bytes=100,
                modified_at=now,
                event_type="modified",
            )
            session.add(fa)
        session.commit()

        baseline = ActivityBaseline(FakeFactory(session))
        baseline._last_check = None
        await baseline.check_and_emit()
        assert baseline.is_dropped()

    @pytest.mark.asyncio
    async def test_no_drop_when_active(self, session):
        """No drop when today's count is close to average."""
        now = datetime.now(UTC)
        for day_offset in range(1, 8):
            for i in range(5):
                fa = FileActivity(
                    path=f"/tmp/f_{day_offset}_{i}.pdf",
                    filename=f"f_{day_offset}_{i}.pdf",
                    directory="/tmp",
                    size_bytes=100,
                    modified_at=now - timedelta(days=day_offset),
                    event_type="modified",
                )
                fa.created_at = now - timedelta(days=day_offset)
                session.add(fa)
        for i in range(4):
            fa = FileActivity(
                path=f"/tmp/today_{i}.pdf",
                filename=f"today_{i}.pdf",
                directory="/tmp",
                size_bytes=100,
                modified_at=now,
                event_type="modified",
            )
            session.add(fa)
        session.commit()

        baseline = ActivityBaseline(FakeFactory(session))
        baseline._last_check = None
        await baseline.check_and_emit()
        assert not baseline.is_dropped()


class TestCompoundTriggerEngine:
    @pytest.mark.asyncio
    async def test_recruiting_notification(self, bus, trigger_eng):
        """Recruiting status change sends notification."""
        with patch(
            "deadline_agent.triggers.send_notification"
        ) as mock_notify:
            await bus.emit(
                Event(
                    type=RECRUITING_STATUS_CHANGED,
                    payload={
                        "company": "Google",
                        "old_status": "applied",
                        "new_status": "interview",
                        "subject": "Interview invite",
                        "app_id": 1,
                    },
                )
            )
            mock_notify.assert_called_once()
            assert "Google" in mock_notify.call_args.kwargs["title"]

    @pytest.mark.asyncio
    async def test_recruiting_dedup(self, bus, trigger_eng):
        """Same recruiting notification not sent twice in one day."""
        with patch(
            "deadline_agent.triggers.send_notification"
        ) as mock_notify:
            event = Event(
                type=RECRUITING_STATUS_CHANGED,
                payload={
                    "company": "Google",
                    "new_status": "interview",
                    "subject": "Test",
                    "app_id": 1,
                },
            )
            await bus.emit(event)
            await bus.emit(event)
            assert mock_notify.call_count == 1

    @pytest.mark.asyncio
    async def test_deadline_cluster_with_drop(
        self, session, bus, trigger_eng
    ):
        """Cluster trigger fires when deadlines + activity drop."""
        now = datetime.now(UTC)
        for i in range(3):
            task = Task(
                title=f"Task{i}",
                source="gmail",
                type="assignment",
                urgency_score=3,
                confidence=0.9,
                raw_hash=f"trigger_test_{i}",
                status="pending",
                due_date_iso=(now + timedelta(days=i + 1)).isoformat(),
            )
            session.add(task)
        session.commit()

        trigger_eng.activity_baseline._dropped = True

        with patch(
            "deadline_agent.triggers.send_notification"
        ) as mock_notify:
            await bus.emit(
                Event(
                    type=TASK_CREATED,
                    payload={
                        "task_id": 1,
                        "title": "T",
                        "source": "gmail",
                    },
                )
            )
            mock_notify.assert_called_once()
            assert "3" in mock_notify.call_args.kwargs["body"]
            assert "dropped" in mock_notify.call_args.kwargs["body"]

    @pytest.mark.asyncio
    async def test_no_cluster_without_drop(
        self, session, bus, trigger_eng
    ):
        """Cluster trigger does NOT fire without activity drop."""
        now = datetime.now(UTC)
        for i in range(3):
            task = Task(
                title=f"Task{i}",
                source="gmail",
                type="assignment",
                urgency_score=3,
                confidence=0.9,
                raw_hash=f"no_drop_{i}",
                status="pending",
                due_date_iso=(now + timedelta(days=i + 1)).isoformat(),
            )
            session.add(task)
        session.commit()

        trigger_eng.activity_baseline._dropped = False

        with patch(
            "deadline_agent.triggers.send_notification"
        ) as mock_notify:
            await bus.emit(
                Event(
                    type=TASK_CREATED,
                    payload={
                        "task_id": 1,
                        "title": "T",
                        "source": "gmail",
                    },
                )
            )
            mock_notify.assert_not_called()
