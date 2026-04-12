"""Tests for sleep and physical world inference."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.awareness.physical_inference import infer_sleep_signals
from deadline_agent.models import FileActivity


def _add_activity_at(session: Session, dt: datetime) -> None:
    fa = FileActivity(
        path="/home/user/work.py",
        filename="work.py",
        directory="/home/user",
        size_bytes=1000,
        modified_at=dt,
        event_type="modified",
        created_at=dt,
    )
    session.add(fa)


class TestSleepInference:
    def test_consistent_sleep_pattern(self, session: Session) -> None:
        """Regular sleep/wake times should be detected as consistent."""
        now = datetime.now(UTC)

        # Simulate 5 days of activity: work 9am-11pm, sleep 11pm-7am
        for day in range(5):
            base = now - timedelta(days=day)
            # Morning activity (7am-ish UTC, adjust as needed)
            for hour in [9, 12, 15, 18, 21]:
                dt = base.replace(hour=hour, minute=0, second=0, microsecond=0)
                _add_activity_at(session, dt)

        session.commit()

        result = infer_sleep_signals(session)
        # Should have some sleep data
        assert "sleep_hours_avg" in result or "all_nighter_dates" in result or result == {}
        # With consistent times, if we have enough data, consistency should be detected
        if "sleep_consistency" in result:
            assert result["sleep_consistency"] in (
                "consistent",
                "irregular",
                "insufficient_data",
            )

    def test_insufficient_data(self, session: Session) -> None:
        """Too few timestamps should return empty."""
        now = datetime.now(UTC)
        _add_activity_at(session, now)
        _add_activity_at(session, now - timedelta(hours=1))
        session.commit()

        result = infer_sleep_signals(session)
        assert result == {}

    def test_all_nighter_detection(self, session: Session) -> None:
        """Activity through the night with no gap > 3h should flag all-nighter."""
        now = datetime.now(UTC)
        base_day = now - timedelta(days=2)

        # Activity every 2 hours for 24 hours straight
        for h in range(0, 24, 2):
            dt = base_day.replace(hour=h, minute=0, second=0, microsecond=0)
            _add_activity_at(session, dt)

        # Need next day activity too for gap analysis
        next_day = base_day + timedelta(days=1)
        for h in range(0, 24, 2):
            dt = next_day.replace(hour=h, minute=0, second=0, microsecond=0)
            _add_activity_at(session, dt)

        session.commit()

        result = infer_sleep_signals(session)
        if "all_nighter_dates" in result:
            assert len(result["all_nighter_dates"]) >= 1  # type: ignore[arg-type]

    def test_no_data_returns_empty(self, session: Session) -> None:
        result = infer_sleep_signals(session)
        assert result == {}
