"""Tests for WeeklySnapshot model and repository."""

from datetime import UTC, datetime

from deadline_agent.models import Base, WeeklySnapshot
from deadline_agent.store.snapshot_repository import WeeklySnapshotRepository

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_create_snapshot():
    session = _make_session()
    repo = WeeklySnapshotRepository(session)
    snap = repo.create({
        "week_start": "2026-03-30",
        "week_end": "2026-04-05",
        "tasks_completed": 5,
        "tasks_slipped": 1,
        "tasks_upcoming": 3,
        "total_work_minutes": 480,
        "narrative": "Good week overall.",
    })
    assert snap.id is not None
    assert snap.tasks_completed == 5
    assert snap.narrative == "Good week overall."


def test_get_by_week():
    session = _make_session()
    repo = WeeklySnapshotRepository(session)
    repo.create({"week_start": "2026-03-30", "week_end": "2026-04-05"})
    result = repo.get_by_week("2026-03-30")
    assert result is not None
    assert result.week_start == "2026-03-30"

    assert repo.get_by_week("2026-01-01") is None


def test_get_recent_ordering():
    session = _make_session()
    repo = WeeklySnapshotRepository(session)
    repo.create({"week_start": "2026-03-16", "week_end": "2026-03-22"})
    repo.create({"week_start": "2026-03-23", "week_end": "2026-03-29"})
    repo.create({"week_start": "2026-03-30", "week_end": "2026-04-05"})

    recent = repo.get_recent(limit=2)
    assert len(recent) == 2
    assert recent[0].week_start == "2026-03-30"
    assert recent[1].week_start == "2026-03-23"


def test_upsert_creates_new():
    session = _make_session()
    repo = WeeklySnapshotRepository(session)
    snap = repo.upsert("2026-03-30", {
        "week_end": "2026-04-05",
        "tasks_completed": 3,
    })
    assert snap.tasks_completed == 3


def test_upsert_updates_existing():
    session = _make_session()
    repo = WeeklySnapshotRepository(session)
    repo.create({
        "week_start": "2026-03-30",
        "week_end": "2026-04-05",
        "tasks_completed": 3,
    })
    snap = repo.upsert("2026-03-30", {
        "tasks_completed": 7,
        "narrative": "Updated narrative.",
    })
    assert snap.tasks_completed == 7
    assert snap.narrative == "Updated narrative."

    # Should still be only one record
    all_snaps = repo.get_recent(limit=10)
    assert len(all_snaps) == 1
