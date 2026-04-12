"""Tests for goal tracking and gap computation."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.awareness.goal_tracker import compute_goal_gaps
from deadline_agent.models import FileActivity, Goal
from deadline_agent.store.goal_repository import GoalRepository


def _add_activity(
    session: Session,
    life_track: str,
    days_ago: int = 0,
) -> None:
    dt = datetime.now(UTC) - timedelta(days=days_ago)
    fa = FileActivity(
        path="/home/user/work.py",
        filename="work.py",
        directory="/home/user",
        size_bytes=1000,
        modified_at=dt,
        event_type="modified",
        life_track=life_track,
        created_at=dt,
    )
    session.add(fa)


class TestGoalRepository:
    def test_create_and_list(self, session: Session) -> None:
        repo = GoalRepository(session)
        goal = repo.create("Study 4h/week on CS 301", "academic", "4h/week")
        assert goal.id is not None
        assert goal.status == "active"

        active = repo.list_active()
        assert len(active) == 1
        assert active[0].description == "Study 4h/week on CS 301"

    def test_update_status(self, session: Session) -> None:
        repo = GoalRepository(session)
        goal = repo.create("Run 3x per week", "health")

        result = repo.update_status(goal.id, "achieved")
        assert result is not None
        assert result.status == "achieved"

        # Should not appear in active list
        active = repo.list_active()
        assert len(active) == 0

    def test_update_nonexistent(self, session: Session) -> None:
        repo = GoalRepository(session)
        result = repo.update_status(999, "achieved")
        assert result is None


class TestGoalGaps:
    def test_no_goals_returns_empty(self, session: Session) -> None:
        gaps = compute_goal_gaps(session)
        assert gaps == []

    def test_zero_activity_gap(self, session: Session) -> None:
        """Goal with zero matching activity should report a gap."""
        repo = GoalRepository(session)
        repo.create("Focus on recruiting", "recruiting")

        gaps = compute_goal_gaps(session)
        assert len(gaps) == 1
        assert "zero activity" in gaps[0].lower()

    def test_activity_present_no_gap(self, session: Session) -> None:
        """Goal with recent matching activity should not report zero-activity gap."""
        repo = GoalRepository(session)
        repo.create("Focus on recruiting", "recruiting")

        # Add recruiting activity
        for i in range(5):
            _add_activity(session, "recruiting", days_ago=i)
        session.flush()

        gaps = compute_goal_gaps(session)
        # Should NOT have a zero-activity gap
        zero_gaps = [g for g in gaps if "zero activity" in g.lower()]
        assert len(zero_gaps) == 0

    def test_health_goal_no_data(self, session: Session) -> None:
        """Health goals should note that no activity data is available."""
        repo = GoalRepository(session)
        repo.create("Exercise 3x per week", "health")

        gaps = compute_goal_gaps(session)
        assert len(gaps) == 1
        assert "no activity data" in gaps[0].lower()
