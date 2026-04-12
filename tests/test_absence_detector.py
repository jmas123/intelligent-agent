"""Tests for absence detection."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.awareness.absence_detector import detect_absences
from deadline_agent.models import FileActivity


def _add_activity(
    session: Session,
    days_ago: int,
    hour: int = 14,
    life_track: str | None = None,
) -> None:
    dt = datetime.now(UTC) - timedelta(days=days_ago, hours=-hour)
    dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
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


class TestAbsenceDetection:
    def test_no_data_returns_empty(self, session: Session) -> None:
        signals = detect_absences(session)
        assert signals == []

    def test_project_activity_drop(self, session: Session) -> None:
        """Project activity dropping to zero should generate a signal."""
        # Weeks 2-4: regular project activity
        for week in range(1, 4):
            for day in range(5):
                _add_activity(
                    session,
                    days_ago=week * 7 + day,
                    hour=14,
                    life_track="project",
                )

        # Week 0 (current): no project activity at all
        # But add some non-project activity so the system has data
        for day in range(3):
            _add_activity(session, days_ago=day, hour=14, life_track="school")

        session.commit()
        signals = detect_absences(session)
        # Should detect the project activity drop
        project_signals = [s for s in signals if "project" in s.lower()]
        assert len(project_signals) >= 1

    def test_consistent_activity_no_signal(self, session: Session) -> None:
        """Consistent activity across weeks should not generate signals."""
        for week in range(4):
            for day in range(5):
                _add_activity(
                    session,
                    days_ago=week * 7 + day,
                    hour=14,
                    life_track="project",
                )
                # Evening activity too
                _add_activity(
                    session,
                    days_ago=week * 7 + day,
                    hour=20,
                )

        session.commit()
        signals = detect_absences(session)
        # Should have no absence signals for project work
        project_signals = [s for s in signals if "project" in s.lower()]
        assert len(project_signals) == 0

    def test_recruiting_drop_detected(self, session: Session) -> None:
        """Active recruiting that suddenly stops should be flagged."""
        # Weeks 1-3: heavy recruiting activity (8+ events per week)
        for week in range(1, 4):
            for day in range(6):
                _add_activity(
                    session,
                    days_ago=week * 7 + day,
                    hour=10,
                    life_track="recruiting",
                )
                _add_activity(
                    session,
                    days_ago=week * 7 + day,
                    hour=15,
                    life_track="recruiting",
                )

        # Week 0: near-zero recruiting
        _add_activity(session, days_ago=1, hour=10, life_track="school")
        session.commit()

        signals = detect_absences(session)
        recruiting_signals = [s for s in signals if "recruiting" in s.lower()]
        assert len(recruiting_signals) >= 1
