"""Tests for the action repository."""

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.store.action_repository import ActionRepository

SAMPLE_ACTION = {
    "type": "calendar_block",
    "title": "Block 2h for HW3",
    "payload": json.dumps(
        {
            "summary": "Work on HW3",
            "start_iso": "2026-03-25T09:00:00",
            "end_iso": "2026-03-25T11:00:00",
        }
    ),
}


def test_propose(session: Session) -> None:
    repo = ActionRepository(session)
    action = repo.propose(SAMPLE_ACTION.copy())
    assert action.id is not None
    assert action.status == "proposed"
    assert action.type == "calendar_block"


def test_list_pending(session: Session) -> None:
    repo = ActionRepository(session)
    repo.propose(SAMPLE_ACTION.copy())
    repo.propose({**SAMPLE_ACTION, "title": "Draft email to TA"})

    pending = repo.list_pending()
    assert len(pending) == 2


def test_approve(session: Session) -> None:
    repo = ActionRepository(session)
    action = repo.propose(SAMPLE_ACTION.copy())

    approved = repo.approve(action.id)
    assert approved is not None
    assert approved.status == "approved"
    assert approved.approved_at is not None


def test_approve_not_found(session: Session) -> None:
    repo = ActionRepository(session)
    assert repo.approve(999) is None


def test_approve_only_pending(session: Session) -> None:
    repo = ActionRepository(session)
    action = repo.propose(SAMPLE_ACTION.copy())
    repo.reject(action.id)

    assert repo.approve(action.id) is None


def test_reject(session: Session) -> None:
    repo = ActionRepository(session)
    action = repo.propose(SAMPLE_ACTION.copy())

    rejected = repo.reject(action.id)
    assert rejected is not None
    assert rejected.status == "rejected"


def test_mark_executed(session: Session) -> None:
    repo = ActionRepository(session)
    action = repo.propose(SAMPLE_ACTION.copy())
    repo.approve(action.id)

    executed = repo.mark_executed(action.id)
    assert executed is not None
    assert executed.status == "executed"
    assert executed.executed_at is not None


def test_mark_executed_with_error(session: Session) -> None:
    repo = ActionRepository(session)
    action = repo.propose(SAMPLE_ACTION.copy())
    repo.approve(action.id)

    failed = repo.mark_executed(action.id, error="Calendar API 403")
    assert failed is not None
    assert failed.execution_error == "Calendar API 403"


def test_expire_stale(session: Session) -> None:
    repo = ActionRepository(session)
    old = repo.propose(SAMPLE_ACTION.copy())
    old.created_at = datetime.now() - timedelta(hours=48)
    session.commit()

    fresh = repo.propose({**SAMPLE_ACTION, "title": "Fresh action"})

    expired = repo.expire_stale(hours=24)
    assert expired == 1

    pending = repo.list_pending()
    assert len(pending) == 1
    assert pending[0].id == fresh.id


def test_list_pending_excludes_non_proposed(session: Session) -> None:
    repo = ActionRepository(session)
    action = repo.propose(SAMPLE_ACTION.copy())
    repo.approve(action.id)

    pending = repo.list_pending()
    assert len(pending) == 0
