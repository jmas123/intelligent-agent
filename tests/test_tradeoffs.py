"""Tests for cross-domain tradeoff reasoning."""

from datetime import UTC, datetime, timedelta

from deadline_agent.models import Base, LifeContext, RecruitingApplication, Task
from deadline_agent.reasoning.tradeoffs import compute_tradeoff_context

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_task(session, title="HW4", task_type="assignment", course="CS 101", urgency=3):
    t = Task(
        title=title,
        due_date_iso=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        source="test",
        type=task_type,
        course=course,
        urgency_score=urgency,
        confidence=0.9,
        raw_hash=f"hash_{title}_{datetime.now().isoformat()}",
    )
    session.add(t)
    session.flush()
    return t


def _make_app(session, company, status="applied"):
    app = RecruitingApplication(
        company_name=company,
        company_normalized=company.lower(),
        status=status,
        last_signal_at=datetime.now(UTC),
    )
    session.add(app)
    session.flush()
    return app


def test_tradeoff_no_apps():
    session = _make_session()
    task = _make_task(session)
    session.commit()

    stmts = compute_tradeoff_context([task], [], session)
    assert stmts == []


def test_tradeoff_with_interview_vs_homework():
    session = _make_session()
    task = _make_task(session, urgency=2)
    # Create many applications, one with interview status
    for i in range(10):
        _make_app(session, f"Company{i}", status="closed")
    _make_app(session, "Cisco", status="interview")
    session.commit()

    stmts = compute_tradeoff_context([task], [], session)
    assert len(stmts) >= 1
    assert "Cisco" in stmts[0] or "interview" in stmts[0].lower()


def test_tradeoff_recruiting_exam_conflict():
    session = _make_session()
    _make_task(session, title="Midterm", task_type="exam", course="Math 201", urgency=5)
    _make_task(session, title="Google Prep", task_type="interview_prep", urgency=4)
    _make_app(session, "Google", status="interview")
    session.commit()

    now = datetime.now(UTC)
    contexts = [
        LifeContext(
            season="recruiting",
            start_date=(now - timedelta(days=7)).strftime("%Y-%m-%d"),
            end_date=(now + timedelta(days=30)).strftime("%Y-%m-%d"),
            source="auto",
            active=True,
        ),
        LifeContext(
            season="exams",
            start_date=(now - timedelta(days=3)).strftime("%Y-%m-%d"),
            end_date=(now + timedelta(days=10)).strftime("%Y-%m-%d"),
            source="auto",
            active=True,
        ),
    ]
    for c in contexts:
        session.add(c)
    session.commit()

    tasks = list(session.scalars(
        session.query(Task).statement
    ).all())

    stmts = compute_tradeoff_context(tasks, contexts, session)
    assert any("exam" in s.lower() for s in stmts)


def test_tradeoff_no_responses_many_apps():
    session = _make_session()
    _make_task(session)
    for i in range(15):
        _make_app(session, f"Company{i}", status="applied")
    session.commit()

    ctx = LifeContext(
        season="recruiting",
        start_date="2026-03-01",
        end_date="2026-05-01",
        source="auto",
        active=True,
    )
    session.add(ctx)
    session.commit()

    tasks = list(session.scalars(
        session.query(Task).statement
    ).all())

    stmts = compute_tradeoff_context(tasks, [ctx], session)
    assert any("0 active responses" in s for s in stmts)
