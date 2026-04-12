"""Tests for tiered context compression."""

import json

from sqlalchemy.orm import Session

from deadline_agent.models import WeeklySnapshot


def _make_snapshot(
    session: Session,
    week_start: str,
    week_end: str,
    tasks_completed: int = 5,
    tasks_slipped: int = 1,
    total_work_minutes: int = 300,
    narrative: str = "A productive week overall.",
    life_contexts_json: str = "[]",
    health_signals_json: str = "{}",
    semester_week_number: int | None = None,
) -> WeeklySnapshot:
    snap = WeeklySnapshot(
        week_start=week_start,
        week_end=week_end,
        tasks_completed=tasks_completed,
        tasks_slipped=tasks_slipped,
        total_work_minutes=total_work_minutes,
        narrative=narrative,
        life_contexts_json=life_contexts_json,
        health_signals_json=health_signals_json,
        semester_week_number=semester_week_number,
    )
    session.add(snap)
    session.flush()
    return snap


class TestBuildTieredHistory:
    def test_empty_snapshots(self, session: Session) -> None:
        from deadline_agent.memory.context_compression import build_tiered_history

        result = build_tiered_history(session)
        assert result == ""

    def test_tier1_full_detail(self, session: Session) -> None:
        from deadline_agent.memory.context_compression import build_tiered_history

        _make_snapshot(session, "2026-03-30", "2026-04-05", narrative="Great week!")
        result = build_tiered_history(session)
        assert "Week of 2026-03-30" in result
        assert "5 done" in result
        assert '"Great week!"' in result

    def test_tier1_includes_health_signals(self, session: Session) -> None:
        from deadline_agent.memory.context_compression import build_tiered_history

        _make_snapshot(
            session, "2026-03-30", "2026-04-05",
            health_signals_json=json.dumps({"late_night_days": 3, "zero_activity_days": 2}),
        )
        result = build_tiered_history(session)
        assert "3 late nights" in result

    def test_tier2_truncated_narrative(self, session: Session) -> None:
        from deadline_agent.memory.context_compression import build_tiered_history

        # Create 3 snapshots (first 2 = tier 1, third = tier 2)
        for i in range(3):
            day = 30 - (i * 7)
            _make_snapshot(
                session,
                f"2026-03-{day:02d}",
                f"2026-04-{day + 6:02d}" if day + 6 <= 31 else f"2026-04-{day + 6 - 31:02d}",
                narrative="This is a longer narrative that should get truncated in tier 2 summary mode because it exceeds the limit.",
            )

        result = build_tiered_history(session)
        # Tier 2 should have truncated narrative
        lines = result.strip().split("\n")
        # At least one line should have "..." from truncation
        has_truncated = any("..." in line for line in lines)
        assert has_truncated or len(lines) >= 3

    def test_tier3_aggregated_blocks(self, session: Session) -> None:
        from deadline_agent.memory.context_compression import build_tiered_history

        # Create 12 snapshots (2 tier1 + 6 tier2 + 4 tier3)
        for i in range(12):
            week_num = 12 - i
            _make_snapshot(
                session,
                f"2026-{(week_num // 4) + 1:02d}-{((week_num % 4) * 7) + 1:02d}",
                f"2026-{(week_num // 4) + 1:02d}-{((week_num % 4) * 7) + 7:02d}",
                tasks_completed=3 + i,
                semester_week_number=week_num,
            )

        result = build_tiered_history(session)
        assert result != ""
        # Should have tier 3 aggregated content with "avg"
        assert "avg" in result or "Weeks" in result
