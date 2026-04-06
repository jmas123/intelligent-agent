"""Tests for the store layer."""

from sqlalchemy.orm import Session

from deadline_agent.store.repository import TaskRepository

SAMPLE_TASK = {
    "title": "Submit HW3",
    "due_date_iso": "2026-03-25T23:59:00",
    "source": "gmail",
    "type": "assignment",
    "course": "CS 101",
    "urgency_score": 4,
    "confidence": 0.92,
    "raw_hash": "abc123def456",
}


def test_create_task(session: Session) -> None:
    repo = TaskRepository(session)
    task = repo.create_task(SAMPLE_TASK.copy())
    assert task is not None
    assert task.title == "Submit HW3"
    assert task.status == "pending"
    assert task.id is not None


def test_duplicate_hash_returns_none(session: Session) -> None:
    repo = TaskRepository(session)
    repo.create_task(SAMPLE_TASK.copy())
    duplicate = repo.create_task(SAMPLE_TASK.copy())
    assert duplicate is None


def test_exists_by_hash(session: Session) -> None:
    repo = TaskRepository(session)
    assert not repo.exists_by_hash("abc123def456")
    repo.create_task(SAMPLE_TASK.copy())
    assert repo.exists_by_hash("abc123def456")


def test_list_tasks_filters(session: Session) -> None:
    repo = TaskRepository(session)
    repo.create_task(SAMPLE_TASK.copy())
    repo.create_task({**SAMPLE_TASK, "raw_hash": "other1", "source": "moodle", "title": "Quiz 2"})

    all_tasks = repo.list_tasks()
    assert len(all_tasks) == 2

    gmail_only = repo.list_tasks(source="gmail")
    assert len(gmail_only) == 1
    assert gmail_only[0].title == "Submit HW3"

    moodle_only = repo.list_tasks(source="moodle")
    assert len(moodle_only) == 1


def test_get_task(session: Session) -> None:
    repo = TaskRepository(session)
    created = repo.create_task(SAMPLE_TASK.copy())
    assert created is not None
    fetched = repo.get_task(created.id)
    assert fetched is not None
    assert fetched.title == "Submit HW3"


def test_update_status(session: Session) -> None:
    repo = TaskRepository(session)
    created = repo.create_task(SAMPLE_TASK.copy())
    assert created is not None
    updated = repo.update_status(created.id, "done")
    assert updated is not None
    assert updated.status == "done"


def test_update_status_not_found(session: Session) -> None:
    repo = TaskRepository(session)
    result = repo.update_status(999, "done")
    assert result is None
