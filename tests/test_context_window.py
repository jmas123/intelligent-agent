"""Tests for compressed history, longitudinal context, and spike prediction."""

import json

from deadline_agent.models import Base, SemesterRecord, WeeklySnapshot
from deadline_agent.reasoning.context_window import (
    build_compressed_history,
    build_longitudinal_context,
    predict_workload_spikes,
)
from deadline_agent.store.snapshot_repository import WeeklySnapshotRepository

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_compressed_history_empty():
    session = _make_session()
    result = build_compressed_history(session)
    assert result == ""


def test_compressed_history_with_snapshots():
    session = _make_session()
    repo = WeeklySnapshotRepository(session)
    repo.create({
        "week_start": "2026-03-23",
        "week_end": "2026-03-29",
        "tasks_completed": 5,
        "tasks_slipped": 1,
        "total_work_minutes": 600,
        "narrative": "A productive week with some procrastination on discussion posts.",
        "semester_week_number": 10,
    })
    repo.create({
        "week_start": "2026-03-30",
        "week_end": "2026-04-05",
        "tasks_completed": 3,
        "tasks_slipped": 2,
        "total_work_minutes": 420,
        "narrative": "Recruiting dominated this week. Two interviews consumed most energy.",
        "life_contexts_json": json.dumps([{"season": "recruiting"}]),
    })

    result = build_compressed_history(session, weeks=4)
    assert "2026-03-30" in result
    assert "2026-03-23" in result
    assert "3 done" in result
    assert "Recruiting" in result


def test_compressed_history_truncates_narrative():
    session = _make_session()
    repo = WeeklySnapshotRepository(session)
    long_narrative = "x " * 200  # Very long narrative
    repo.create({
        "week_start": "2026-03-30",
        "week_end": "2026-04-05",
        "narrative": long_narrative,
    })

    result = build_compressed_history(session)
    assert "..." in result


def test_longitudinal_context_empty():
    session = _make_session()
    result = build_longitudinal_context(session)
    assert result == ""


def test_longitudinal_context_with_semester_records():
    session = _make_session()

    # Need pattern_repository and semester_repository tables
    from deadline_agent.models import BehavioralPattern

    # Add a semester record with analytics
    record = SemesterRecord(
        term_name="Winter 2026",
        start_date="2026-01-15",
        end_date="2026-04-30",
        analytics_json=json.dumps({
            "effort_accuracy_by_type": {
                "assignment": {"ratio": 1.8, "sample_count": 15},
            },
            "procrastination_trend": {
                "first_half_avg_days": 3.2,
                "second_half_avg_days": 2.1,
                "trend": "improving",
            },
            "workload_distribution": [
                {"week": 11, "tasks_completed": 8},
                {"week": 12, "tasks_completed": 5},
            ],
        }),
    )
    session.add(record)

    # Add a current pattern with different ratio
    pattern = BehavioralPattern(
        pattern_type="effort_accuracy",
        pattern_key="assignment",
        value=json.dumps({"ratio": 1.3}),
        sample_count=10,
        confidence=0.8,
    )
    session.add(pattern)
    session.commit()

    result = build_longitudinal_context(session)
    assert "1.8x" in result
    assert "1.3x" in result
    assert "improving" in result


def test_spike_prediction_empty():
    session = _make_session()
    result = predict_workload_spikes(session, None, [], [])
    assert result == []


def test_spike_prediction_with_history():
    session = _make_session()

    record = SemesterRecord(
        term_name="Fall 2025",
        start_date="2025-09-01",
        end_date="2025-12-15",
        analytics_json=json.dumps({
            "workload_distribution": [
                {"week": 11, "tasks_completed": 9},
                {"week": 12, "tasks_completed": 4},
            ],
        }),
    )
    session.add(record)
    session.commit()

    from deadline_agent.models import Task
    from datetime import UTC, datetime, timedelta

    # Create upcoming tasks
    tasks = []
    for i in range(5):
        t = Task(
            title=f"Task {i}",
            due_date_iso=(datetime.now(UTC) + timedelta(days=i)).isoformat(),
            source="test",
            type="assignment",
            urgency_score=3,
            confidence=0.9,
            raw_hash=f"spike_hash_{i}",
        )
        session.add(t)
        tasks.append(t)
    session.commit()

    result = predict_workload_spikes(session, 11, tasks, [])
    assert len(result) >= 1
    assert "Fall 2025" in result[0]
    assert "9 tasks" in result[0]


def test_spike_prediction_slip_trend():
    session = _make_session()
    repo = WeeklySnapshotRepository(session)
    # Two consecutive weeks with slips
    repo.create({
        "week_start": "2026-03-23",
        "week_end": "2026-03-29",
        "tasks_slipped": 2,
    })
    repo.create({
        "week_start": "2026-03-30",
        "week_end": "2026-04-05",
        "tasks_slipped": 3,
    })

    result = predict_workload_spikes(session, 11, [], [])
    assert any("slipped" in s.lower() for s in result)
