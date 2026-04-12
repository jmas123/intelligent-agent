"""Tests for Phase 14 life awareness features."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from deadline_agent.awareness.life_context_detector import (
    _detect_burnout,
    _detect_crunch_week,
)
from deadline_agent.models import FileActivity, RecruitingApplication, Task
from deadline_agent.store.context_repository import LifeContextRepository

EST = ZoneInfo("America/New_York")


class TestDetectBurnout:
    def test_burnout_detected(self, session):
        """Burnout detected with 3+ late-night days in past week."""
        now = datetime.now(UTC)
        repo = LifeContextRepository(session)

        # Create file activity at 2 AM local on 3 different days
        for day_offset in range(1, 4):
            # 2 AM EST = 7 AM UTC (winter) or 6 AM UTC (summer)
            local_2am = datetime(
                now.year,
                now.month,
                now.day,
                2,
                0,
                tzinfo=EST,
            ) - timedelta(days=day_offset)
            utc_time = local_2am.astimezone(UTC)
            fa = FileActivity(
                path=f"/tmp/late_{day_offset}.pdf",
                filename=f"late_{day_offset}.pdf",
                directory="/tmp",
                size_bytes=100,
                modified_at=utc_time,
                event_type="modified",
            )
            fa.created_at = utc_time
            session.add(fa)
        session.commit()

        ctx = _detect_burnout(session, repo)
        assert ctx is not None
        assert ctx.season == "burnout"
        assert "late night" in ctx.label.lower()

    def test_no_burnout_without_late_nights(self, session):
        """No burnout without sufficient late-night work."""
        now = datetime.now(UTC)
        repo = LifeContextRepository(session)

        # Create daytime activity only
        for day_offset in range(1, 5):
            local_2pm = datetime(
                now.year,
                now.month,
                now.day,
                14,
                0,
                tzinfo=EST,
            ) - timedelta(days=day_offset)
            utc_time = local_2pm.astimezone(UTC)
            fa = FileActivity(
                path=f"/tmp/day_{day_offset}.pdf",
                filename=f"day_{day_offset}.pdf",
                directory="/tmp",
                size_bytes=100,
                modified_at=utc_time,
                event_type="modified",
            )
            fa.created_at = utc_time
            session.add(fa)
        session.commit()

        ctx = _detect_burnout(session, repo)
        assert ctx is None


class TestDetectCrunchWeek:
    def test_crunch_detected(self, session):
        """Crunch week detected with 5+ tasks due in 7 days."""
        now = datetime.now(UTC)
        repo = LifeContextRepository(session)

        for i in range(5):
            task = Task(
                title=f"CrunchTask{i}",
                source="gmail",
                type="assignment",
                urgency_score=3,
                confidence=0.9,
                raw_hash=f"crunch_{i}",
                status="pending",
                due_date_iso=(now + timedelta(days=i + 1)).isoformat(),
            )
            session.add(task)
        session.commit()

        ctx = _detect_crunch_week(session, repo)
        assert ctx is not None
        assert ctx.season == "crunch_week"

    def test_no_crunch_with_few_tasks(self, session):
        """No crunch week with <5 tasks."""
        now = datetime.now(UTC)
        repo = LifeContextRepository(session)

        for i in range(3):
            task = Task(
                title=f"EasyTask{i}",
                source="gmail",
                type="assignment",
                urgency_score=2,
                confidence=0.9,
                raw_hash=f"easy_{i}",
                status="pending",
                due_date_iso=(now + timedelta(days=i + 1)).isoformat(),
            )
            session.add(task)
        session.commit()

        ctx = _detect_crunch_week(session, repo)
        assert ctx is None


class TestHealthSignalsInSnapshot:
    def test_stale_applications_in_snapshot(self, session):
        """Stale applications appear in state snapshot."""
        from deadline_agent.reasoning.state import build_state_snapshot

        app = RecruitingApplication(
            company_name="OldCorp",
            company_normalized="oldcorp",
            status="applied",
            last_signal_at=datetime.now(UTC) - timedelta(days=14),
        )
        session.add(app)
        session.commit()

        snapshot = build_state_snapshot(session)
        assert len(snapshot.stale_applications) >= 1
        found = [
            a
            for a in snapshot.stale_applications
            if a["company"] == "OldCorp"
        ]
        assert len(found) == 1
        assert found[0]["days_since"] >= 14

    def test_no_stale_if_recent_signal(self, session):
        """Recent applications are not flagged as stale."""
        from deadline_agent.reasoning.state import build_state_snapshot

        app = RecruitingApplication(
            company_name="FreshCorp",
            company_normalized="freshcorp",
            status="applied",
            last_signal_at=datetime.now(UTC) - timedelta(days=2),
        )
        session.add(app)
        session.commit()

        snapshot = build_state_snapshot(session)
        found = [
            a
            for a in snapshot.stale_applications
            if a["company"] == "FreshCorp"
        ]
        assert len(found) == 0

    def test_snapshot_prompt_includes_health(self, session):
        """State snapshot prompt includes health signals when present."""
        from deadline_agent.reasoning.state import build_state_snapshot

        # Create late-night activity
        now = datetime.now(UTC)
        for day_offset in range(1, 4):
            local_2am = datetime(
                now.year,
                now.month,
                now.day,
                2,
                0,
                tzinfo=EST,
            ) - timedelta(days=day_offset)
            utc_time = local_2am.astimezone(UTC)
            fa = FileActivity(
                path=f"/tmp/h_{day_offset}.pdf",
                filename=f"h_{day_offset}.pdf",
                directory="/tmp",
                size_bytes=100,
                modified_at=utc_time,
                event_type="modified",
            )
            fa.created_at = utc_time
            session.add(fa)
        session.commit()

        snapshot = build_state_snapshot(session)
        prompt = snapshot.to_prompt()
        if snapshot.health_signals.get("late_night_days", 0) >= 1:
            assert "HEALTH SIGNALS" in prompt
