"""Tests for the file event processor."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from deadline_agent.awareness.processor import process_file_events
from deadline_agent.awareness.watcher import FileEvent
from deadline_agent.models import Base, FileActivity, FileTaskLink, Task


@pytest.fixture
def processor_engine() -> Engine:
    """Separate engine for processor tests to avoid session conflicts."""
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def processor_session_factory(processor_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=processor_engine)


def _add_task(session: Session) -> Task:
    task = Task(
        title="HW3 Report",
        due_date_iso="2026-03-25T23:59:00",
        source="gmail",
        type="assignment",
        course="CS 101",
        urgency_score=4,
        confidence=0.92,
        raw_hash="abc123",
    )
    session.add(task)
    session.commit()
    return task


@pytest.mark.asyncio
async def test_processor_records_activity_and_links(
    processor_session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    # Create a real file
    test_file = tmp_path / "CS101" / "hw3-report.pdf"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("test content")

    # Seed a task
    with processor_session_factory() as session:
        _add_task(session)

    # Enqueue an event
    queue: asyncio.Queue[FileEvent] = asyncio.Queue()
    event = FileEvent(
        path=str(test_file),
        event_type="created",
        timestamp=datetime.now(),
    )
    await queue.put(event)

    # Run processor for one item then cancel
    task = asyncio.create_task(process_file_events(processor_session_factory, queue))
    await queue.join()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Verify activity was recorded and linked
    with processor_session_factory() as session:
        activities = session.query(FileActivity).all()
        assert len(activities) == 1
        assert activities[0].filename == "hw3-report.pdf"

        links = session.query(FileTaskLink).all()
        assert len(links) == 1
        assert links[0].method == "string"
        assert links[0].confidence >= 0.5


@pytest.mark.asyncio
async def test_processor_skips_nonexistent_file(
    processor_session_factory: sessionmaker[Session],
) -> None:
    queue: asyncio.Queue[FileEvent] = asyncio.Queue()
    event = FileEvent(
        path="/nonexistent/file.pdf",
        event_type="created",
        timestamp=datetime.now(),
    )
    await queue.put(event)

    task = asyncio.create_task(process_file_events(processor_session_factory, queue))
    await queue.join()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # No activity should be recorded
    with processor_session_factory() as session:
        activities = session.query(FileActivity).all()
        assert len(activities) == 0


@pytest.mark.asyncio
async def test_processor_no_links_below_threshold(
    processor_session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    # File in unrelated directory
    test_file = tmp_path / "random" / "notes.pdf"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("test content")

    with processor_session_factory() as session:
        _add_task(session)

    queue: asyncio.Queue[FileEvent] = asyncio.Queue()
    event = FileEvent(
        path=str(test_file),
        event_type="modified",
        timestamp=datetime.now(),
    )
    await queue.put(event)

    task = asyncio.create_task(process_file_events(processor_session_factory, queue))
    await queue.join()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Activity recorded but no links
    with processor_session_factory() as session:
        activities = session.query(FileActivity).all()
        assert len(activities) == 1
        links = session.query(FileTaskLink).all()
        assert len(links) == 0
