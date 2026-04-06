"""Tests for the file activity store layer."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.store.file_repository import FileActivityRepository
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

SAMPLE_ACTIVITY = {
    "path": "/Users/test/Documents/CS101/hw3.pdf",
    "filename": "hw3.pdf",
    "directory": "/Users/test/Documents/CS101",
    "size_bytes": 1024,
    "modified_at": datetime(2026, 3, 24, 10, 0, 0),
    "event_type": "modified",
}


def test_record_activity(session: Session) -> None:
    repo = FileActivityRepository(session)
    activity = repo.record_activity(SAMPLE_ACTIVITY.copy())
    assert activity is not None
    assert activity.id is not None
    assert activity.filename == "hw3.pdf"
    assert activity.event_type == "modified"


def test_link_to_task(session: Session) -> None:
    task_repo = TaskRepository(session)
    file_repo = FileActivityRepository(session)

    task = task_repo.create_task(SAMPLE_TASK.copy())
    assert task is not None
    activity = file_repo.record_activity(SAMPLE_ACTIVITY.copy())

    link = file_repo.link_to_task(activity.id, task.id, confidence=0.8, method="string")
    assert link is not None
    assert link.confidence == 0.8
    assert link.method == "string"


def test_duplicate_link_returns_none(session: Session) -> None:
    task_repo = TaskRepository(session)
    file_repo = FileActivityRepository(session)

    task = task_repo.create_task(SAMPLE_TASK.copy())
    assert task is not None
    activity = file_repo.record_activity(SAMPLE_ACTIVITY.copy())

    link1 = file_repo.link_to_task(activity.id, task.id, confidence=0.8, method="string")
    assert link1 is not None
    link2 = file_repo.link_to_task(activity.id, task.id, confidence=0.9, method="embedding")
    assert link2 is None


def test_get_activity_for_task(session: Session) -> None:
    task_repo = TaskRepository(session)
    file_repo = FileActivityRepository(session)

    task = task_repo.create_task(SAMPLE_TASK.copy())
    assert task is not None
    activity = file_repo.record_activity(SAMPLE_ACTIVITY.copy())
    file_repo.link_to_task(activity.id, task.id, confidence=0.8, method="string")

    results = file_repo.get_activity_for_task(task.id)
    assert len(results) == 1
    assert results[0].filename == "hw3.pdf"


def test_get_activity_for_task_empty(session: Session) -> None:
    task_repo = TaskRepository(session)
    file_repo = FileActivityRepository(session)

    task = task_repo.create_task(SAMPLE_TASK.copy())
    assert task is not None

    results = file_repo.get_activity_for_task(task.id)
    assert len(results) == 0


def test_has_activity_for_task(session: Session) -> None:
    task_repo = TaskRepository(session)
    file_repo = FileActivityRepository(session)

    task = task_repo.create_task(SAMPLE_TASK.copy())
    assert task is not None

    assert not file_repo.has_activity_for_task(task.id)

    activity = file_repo.record_activity(SAMPLE_ACTIVITY.copy())
    file_repo.link_to_task(activity.id, task.id, confidence=0.8, method="string")

    assert file_repo.has_activity_for_task(task.id)


def test_get_recent_activity(session: Session) -> None:
    file_repo = FileActivityRepository(session)
    file_repo.record_activity(SAMPLE_ACTIVITY.copy())

    results = file_repo.get_recent_activity(hours=72)
    assert len(results) == 1


def test_get_recent_activity_excludes_old(session: Session) -> None:
    file_repo = FileActivityRepository(session)
    old_activity = {
        **SAMPLE_ACTIVITY,
        "path": "/Users/test/old_file.pdf",
        "filename": "old_file.pdf",
    }
    activity = file_repo.record_activity(old_activity)
    # Manually set created_at to 4 days ago to simulate old activity
    activity.created_at = datetime.now() - timedelta(days=4)
    session.commit()

    results = file_repo.get_recent_activity(hours=72)
    assert len(results) == 0


def test_get_links_for_task(session: Session) -> None:
    task_repo = TaskRepository(session)
    file_repo = FileActivityRepository(session)

    task = task_repo.create_task(SAMPLE_TASK.copy())
    assert task is not None
    a1 = file_repo.record_activity(SAMPLE_ACTIVITY.copy())
    a2 = file_repo.record_activity(
        {
            **SAMPLE_ACTIVITY,
            "path": "/Users/test/Documents/CS101/hw3_draft.docx",
            "filename": "hw3_draft.docx",
        }
    )
    file_repo.link_to_task(a1.id, task.id, confidence=0.7, method="string")
    file_repo.link_to_task(a2.id, task.id, confidence=0.9, method="embedding")

    links = file_repo.get_links_for_task(task.id)
    assert len(links) == 2
    # Ordered by confidence desc
    assert links[0].confidence == 0.9
    assert links[1].confidence == 0.7
