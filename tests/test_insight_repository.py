"""Tests for the insight repository."""

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.store.insight_repository import InsightRepository

SAMPLE_INSIGHT = {
    "type": "no_progress",
    "content": "Project 4 is due in 2 days and you haven't started",
    "related_task_ids": json.dumps([1, 2]),
}


def test_create_insight(session: Session) -> None:
    repo = InsightRepository(session)
    insight = repo.create(SAMPLE_INSIGHT.copy())
    assert insight.id is not None
    assert insight.type == "no_progress"
    assert insight.dismissed is False


def test_list_active(session: Session) -> None:
    repo = InsightRepository(session)
    repo.create(SAMPLE_INSIGHT.copy())
    repo.create({**SAMPLE_INSIGHT, "type": "workload_spike", "content": "Heavy week ahead"})

    active = repo.list_active()
    assert len(active) == 2


def test_list_active_excludes_dismissed(session: Session) -> None:
    repo = InsightRepository(session)
    insight = repo.create(SAMPLE_INSIGHT.copy())
    repo.dismiss(insight.id)

    active = repo.list_active()
    assert len(active) == 0


def test_dismiss(session: Session) -> None:
    repo = InsightRepository(session)
    insight = repo.create(SAMPLE_INSIGHT.copy())

    dismissed = repo.dismiss(insight.id)
    assert dismissed is not None
    assert dismissed.dismissed is True


def test_dismiss_not_found(session: Session) -> None:
    repo = InsightRepository(session)
    result = repo.dismiss(999)
    assert result is None


def test_get_for_task(session: Session) -> None:
    repo = InsightRepository(session)
    repo.create({**SAMPLE_INSIGHT, "related_task_ids": json.dumps([1, 2])})
    repo.create(
        {
            **SAMPLE_INSIGHT,
            "type": "suggestion",
            "content": "Other insight",
            "related_task_ids": json.dumps([3]),
        }
    )

    task_1_insights = repo.get_for_task(1)
    assert len(task_1_insights) == 1
    assert task_1_insights[0].type == "no_progress"

    task_3_insights = repo.get_for_task(3)
    assert len(task_3_insights) == 1

    task_99_insights = repo.get_for_task(99)
    assert len(task_99_insights) == 0


def test_clear_stale(session: Session) -> None:
    repo = InsightRepository(session)
    old = repo.create(SAMPLE_INSIGHT.copy())
    # Manually set created_at to 48 hours ago
    old.created_at = datetime.now() - timedelta(hours=48)
    session.commit()

    fresh = repo.create({**SAMPLE_INSIGHT, "type": "suggestion", "content": "Fresh insight"})

    deleted = repo.clear_stale(max_age_hours=24)
    assert deleted == 1

    active = repo.list_active()
    assert len(active) == 1
    assert active[0].id == fresh.id
