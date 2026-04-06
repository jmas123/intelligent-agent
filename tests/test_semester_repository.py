"""Tests for SemesterRecordRepository."""

from deadline_agent.store.semester_repository import SemesterRecordRepository


def test_create_and_get(session):
    """Create a record and retrieve it by ID."""
    repo = SemesterRecordRepository(session)
    record = repo.create(
        {
            "term_name": "Winter 2026",
            "start_date": "2026-01-10",
            "end_date": "2026-04-25",
            "tasks_completed": 42,
            "tasks_slipped": 5,
            "total_work_minutes": 3600,
            "debrief_text": "Good semester overall.",
        }
    )
    assert record.id is not None
    assert record.term_name == "Winter 2026"

    fetched = repo.get(record.id)
    assert fetched is not None
    assert fetched.tasks_completed == 42


def test_get_by_term(session):
    """Retrieve a record by term name."""
    repo = SemesterRecordRepository(session)
    repo.create(
        {
            "term_name": "Fall 2025",
            "start_date": "2025-09-01",
            "end_date": "2025-12-20",
        }
    )

    found = repo.get_by_term("Fall 2025")
    assert found is not None
    assert found.start_date == "2025-09-01"

    not_found = repo.get_by_term("Summer 2025")
    assert not_found is None


def test_list_all(session):
    """List records in reverse chronological order."""
    repo = SemesterRecordRepository(session)
    repo.create(
        {
            "term_name": "Fall 2025",
            "start_date": "2025-09-01",
            "end_date": "2025-12-20",
        }
    )
    repo.create(
        {
            "term_name": "Winter 2026",
            "start_date": "2026-01-10",
            "end_date": "2026-04-25",
        }
    )

    records = repo.list_all()
    assert len(records) == 2


def test_update(session):
    """Update a record's fields."""
    repo = SemesterRecordRepository(session)
    record = repo.create(
        {
            "term_name": "Winter 2026",
            "start_date": "2026-01-10",
            "end_date": "2026-04-25",
            "debrief_text": "Draft",
        }
    )

    updated = repo.update(
        record.id, {"debrief_text": "Final version.", "tasks_completed": 50}
    )
    assert updated is not None
    assert updated.debrief_text == "Final version."
    assert updated.tasks_completed == 50


def test_update_nonexistent(session):
    """Update returns None for nonexistent record."""
    repo = SemesterRecordRepository(session)
    assert repo.update(999, {"debrief_text": "nope"}) is None
