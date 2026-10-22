"""Tests for Phase 32 — Session Start Intelligence."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from deadline_agent.awareness.session_intelligence import (
    BriefingPriority,
    SessionBriefing,
    TimeBlock,
    _build_recommendation,
    _humanize_minutes,
    _plan_time_blocks,
    build_session_briefing,
    build_session_end_summary,
    format_session_briefing,
)
from deadline_agent.models import (
    Base,
    FileActivity,
    FileTaskLink,
    PriorityRanking,
    Task,
    TaskRisk,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_task(session: Session, title: str, due_hours: float, raw_hash: str) -> Task:
    now = datetime.now(UTC)
    task = Task(
        title=title,
        due_date_iso=(now + timedelta(hours=due_hours)).isoformat(),
        source="test",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash=raw_hash,
    )
    session.add(task)
    session.commit()
    return task


# ── Pure functions ──────────────────────────────────────────────────


def test_humanize_minutes():
    assert _humanize_minutes(0) == "no free time"
    assert _humanize_minutes(45) == "45 min"
    assert _humanize_minutes(60) == "1 hour"
    assert _humanize_minutes(120) == "2 hours"
    assert _humanize_minutes(150) == "2.5 hours"


def test_plan_time_blocks_single_priority():
    p = BriefingPriority(
        task_id=1, rank=1, title="OS Exam", course="CS 401",
        time_label="due in 2 days", risk_level="high",
        probability_pct=72, suggested_action="Start review", why="...",
    )
    blocks = _plan_time_blocks(180, [p])
    assert len(blocks) == 1
    assert blocks[0].title == "OS Exam"
    assert 25 <= blocks[0].minutes <= 90


def test_plan_time_blocks_multiple_priorities():
    ps = [
        BriefingPriority(
            task_id=i, rank=i, title=f"T{i}", course=None,
            time_label="due tomorrow", risk_level="medium",
            probability_pct=50, suggested_action="...", why="...",
        )
        for i in range(1, 4)
    ]
    blocks = _plan_time_blocks(180, ps)
    assert 1 <= len(blocks) <= 3
    assert sum(b.minutes for b in blocks) <= 180
    # Top priority is first
    assert blocks[0].task_id == 1


def test_plan_time_blocks_no_time():
    p = BriefingPriority(
        task_id=1, rank=1, title="X", course=None,
        time_label="due in 1d", risk_level="low",
        probability_pct=None, suggested_action="...", why="...",
    )
    assert _plan_time_blocks(0, [p]) == []
    assert _plan_time_blocks(180, []) == []


def test_recommendation_text():
    blocks = [
        TimeBlock(task_id=1, title="OS Exam", minutes=90, rationale="high risk"),
    ]
    assert "90 min on OS Exam" in _build_recommendation(blocks, 180)
    assert "after" in _build_recommendation([], 0)


# ── Builder ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.calendar_gaps.fetch_events", new_callable=AsyncMock, return_value=[])
async def test_build_briefing_uses_priority_engine(_, session: Session):
    """When PriorityRanking rows exist, the briefing uses them in rank order."""
    t1 = _add_task(session, "OS Exam", 48, "p32-1")
    t2 = _add_task(session, "HW4", 72, "p32-2")

    session.add(PriorityRanking(
        task_id=t1.id, rank=1, score=0.9,
        risk_if_ignored="will miss",
        suggested_action="Start OS review now",
        reasoning="overdue tomorrow",
    ))
    session.add(PriorityRanking(
        task_id=t2.id, rank=2, score=0.6,
        risk_if_ignored="catch-up risk",
        suggested_action="Spend 45 min",
        reasoning="due in 3 days",
    ))
    session.add(TaskRisk(
        task_id=t1.id, probability_of_missing=0.72,
        hours_needed=4.0, hours_available=2.5,
        confidence=0.7, risk_level="high",
        summary="72% likely to miss", confidence_reason="",
    ))
    session.commit()

    briefing = await build_session_briefing(session)

    assert len(briefing.priorities) == 2
    assert briefing.priorities[0].task_id == t1.id
    assert briefing.priorities[0].risk_level == "high"
    assert briefing.priorities[0].probability_pct == 72
    assert briefing.priorities[0].suggested_action == "Start OS review now"
    # Recommendation is the rendered next-step
    assert "OS Exam" in briefing.recommendation
    # Available time should be positive (calendar lookup falls back to EOD)
    assert briefing.available_minutes >= 0


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.calendar_gaps.fetch_events", new_callable=AsyncMock, return_value=[])
async def test_build_briefing_falls_back_when_priority_empty(_, session: Session):
    """No PriorityRanking rows → fallback to deadline ordering."""
    _add_task(session, "Far HW", 168, "p32-far")
    _add_task(session, "Closer HW", 24, "p32-close")

    briefing = await build_session_briefing(session)
    titles = [p.title for p in briefing.priorities]
    # Earliest deadline first
    assert titles[0] == "Closer HW"
    assert "Closer HW" in briefing.recommendation


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.calendar_gaps.fetch_events", new_callable=AsyncMock, return_value=[])
async def test_build_briefing_empty_when_no_tasks(_, session: Session):
    """Empty DB → empty priorities, but still a valid briefing object."""
    briefing = await build_session_briefing(session)
    assert briefing.priorities == []
    assert briefing.extras == []


# ── Formatter ───────────────────────────────────────────────────────


def test_format_session_briefing_full():
    b = SessionBriefing(
        generated_at=datetime.now(UTC).isoformat(),
        available_minutes=180,
        available_label="3 hours",
        next_event=None,
        life_context="Exams",
        priorities=[
            BriefingPriority(
                task_id=1, rank=1, title="OS Exam", course="CS 401",
                time_label="due in 2 days", risk_level="high",
                probability_pct=72, suggested_action="Start review",
                why="high P(miss), no progress yet",
            ),
        ],
        extras=["Cisco follow-up stale 6d"],
        time_blocks=[
            TimeBlock(task_id=1, title="OS Exam", minutes=90, rationale="high risk")
        ],
        recommendation="Start with 90 min on OS Exam now.",
    )
    text = format_session_briefing(b)
    assert "You have 3 hours free." in text
    assert "Most important:" in text
    assert "OS Exam" in text
    assert "Cisco follow-up" in text
    assert "Recommendation:" in text
    assert "90 min" in text


def test_format_session_briefing_compact():
    b = SessionBriefing(
        generated_at=datetime.now(UTC).isoformat(),
        available_minutes=60,
        available_label="1 hour",
        next_event=None,
        life_context=None,
        priorities=[
            BriefingPriority(
                task_id=1, rank=1, title="OS Exam", course=None,
                time_label="due tomorrow", risk_level="high",
                probability_pct=72, suggested_action="Start review now",
                why="...",
            ),
        ],
        extras=[],
        time_blocks=[],
        recommendation="Start with 30 min on OS Exam now.",
    )
    text = format_session_briefing(b, compact=True)
    assert "1 hour" in text
    assert "OS Exam" in text
    assert text.count("\n") == 0  # one line


# ── End-of-session summary ─────────────────────────────────────────


def test_session_end_summary_worked_vs_skipped(session: Session):
    """Recap distinguishes priorities that had file activity vs ones that didn't."""
    t1 = _add_task(session, "OS Exam", 24, "p32-end-1")
    t2 = _add_task(session, "HW4", 48, "p32-end-2")

    session_start = datetime.now(UTC) - timedelta(minutes=90)
    session_end = datetime.now(UTC)

    # Log file activity for t1 only
    fa = FileActivity(
        path="/tmp/os_notes.pdf",
        filename="os_notes.pdf",
        directory="/tmp",
        size_bytes=1024,
        modified_at=session_start + timedelta(minutes=30),
        event_type="modified",
    )
    session.add(fa)
    session.commit()
    session.add(FileTaskLink(
        file_activity_id=fa.id, task_id=t1.id, confidence=0.9, method="filename",
    ))
    session.commit()

    briefing = SessionBriefing(
        generated_at=session_start.isoformat(),
        available_minutes=180,
        available_label="3 hours",
        next_event=None,
        life_context=None,
        priorities=[
            BriefingPriority(
                task_id=t1.id, rank=1, title="OS Exam", course=None,
                time_label="due tomorrow", risk_level="high",
                probability_pct=72, suggested_action="...", why="...",
            ),
            BriefingPriority(
                task_id=t2.id, rank=2, title="HW4", course=None,
                time_label="due in 2 days", risk_level="medium",
                probability_pct=40, suggested_action="...", why="...",
            ),
        ],
        extras=[],
        time_blocks=[],
        recommendation="...",
    )

    summary = build_session_end_summary(session, briefing, session_start, session_end)
    assert summary is not None
    assert "OS Exam" in summary
    assert "Still pending: HW4" in summary
    assert "Session:" in summary
