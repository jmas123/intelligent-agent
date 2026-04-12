"""Tests for the /api/chat/ endpoints."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from deadline_agent.main import app
from deadline_agent.models import Base, Task
from deadline_agent.store.repository import TaskRepository
from deadline_agent.store.session import get_session


@pytest.fixture
def chat_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def chat_session(chat_engine: Engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=chat_engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
async def chat_client(
    chat_session: Session,
) -> AsyncClient:  # type: ignore[misc]
    """Client with get_session overridden to use the test session."""

    def _override() -> Generator[Session, None, None]:
        yield chat_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _make_task(session: Session, **overrides: Any) -> Task:
    """Insert a task with sensible defaults."""
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "title": "Test Task",
        "due_date_iso": (now + timedelta(hours=6)).isoformat(),
        "source": "gmail",
        "type": "assignment",
        "course": "CS 101",
        "urgency_score": 3,
        "confidence": 0.9,
        "raw_hash": f"hash-{now.timestamp()}-{id(overrides)}",
        "status": "pending",
    }
    defaults.update(overrides)
    repo = TaskRepository(session)
    task = repo.create_task(defaults)
    assert task is not None
    return task


# ── GET /api/chat/tasks ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tasks_empty(chat_client: AsyncClient) -> None:
    resp = await chat_client.get("/api/chat/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_tasks_returns_tasks(chat_client: AsyncClient, chat_session: Session) -> None:
    _make_task(chat_session, title="Alpha", raw_hash="a1")
    _make_task(chat_session, title="Beta", raw_hash="b2", status="done")

    resp = await chat_client.get("/api/chat/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    # Filter by status
    resp = await chat_client.get("/api/chat/tasks", params={"status": "pending"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Alpha"


@pytest.mark.asyncio
async def test_list_tasks_due_today(chat_client: AsyncClient, chat_session: Session) -> None:
    from zoneinfo import ZoneInfo
    # Use noon today in local TZ to avoid day-boundary issues at late hours
    local_now = datetime.now(UTC).astimezone(ZoneInfo("America/New_York"))
    noon_today = local_now.replace(hour=12, minute=0, second=0, microsecond=0)
    if noon_today < local_now:
        noon_today = local_now.replace(hour=23, minute=0, second=0, microsecond=0)
    due_today_utc = noon_today.astimezone(UTC)
    _make_task(
        chat_session,
        title="Today",
        due_date_iso=due_today_utc.isoformat(),
        raw_hash="today-1",
    )
    _make_task(
        chat_session,
        title="Next Week",
        due_date_iso=(datetime.now(UTC) + timedelta(days=5)).isoformat(),
        raw_hash="week-1",
    )

    resp = await chat_client.get("/api/chat/tasks", params={"due_today": True})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Today"


# ── GET /api/chat/tasks/{id} ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_task_found(chat_client: AsyncClient, chat_session: Session) -> None:
    task = _make_task(chat_session, raw_hash="find-me")

    resp = await chat_client.get(f"/api/chat/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Test Task"


@pytest.mark.asyncio
async def test_get_task_not_found(chat_client: AsyncClient) -> None:
    resp = await chat_client.get("/api/chat/tasks/9999")
    assert resp.status_code == 404


# ── PATCH /api/chat/tasks/{id}/status ─────────────────────────────────


@pytest.mark.asyncio
async def test_update_status(chat_client: AsyncClient, chat_session: Session) -> None:
    task = _make_task(chat_session, raw_hash="status-1")

    resp = await chat_client.patch(
        f"/api/chat/tasks/{task.id}/status",
        json={"status": "done"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


@pytest.mark.asyncio
async def test_update_status_invalid(chat_client: AsyncClient, chat_session: Session) -> None:
    task = _make_task(chat_session, raw_hash="status-2")

    resp = await chat_client.patch(
        f"/api/chat/tasks/{task.id}/status",
        json={"status": "invalid_status"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_status_not_found(chat_client: AsyncClient) -> None:
    resp = await chat_client.patch(
        "/api/chat/tasks/9999/status",
        json={"status": "done"},
    )
    assert resp.status_code == 404


# ── GET /api/chat/digest ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_digest_empty(chat_client: AsyncClient) -> None:
    resp = await chat_client.get("/api/chat/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["text"] is None
    assert data["task_count"] == 0


@pytest.mark.asyncio
async def test_digest_with_tasks(chat_client: AsyncClient, chat_session: Session) -> None:
    now = datetime.now(UTC)
    _make_task(
        chat_session,
        title="Upcoming HW",
        due_date_iso=(now + timedelta(days=2)).isoformat(),
        raw_hash="digest-1",
    )

    resp = await chat_client.get("/api/chat/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["text"] is not None
    assert "Upcoming HW" in data["text"]
    assert data["task_count"] == 1


# ── GET /api/chat/context ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_empty(chat_client: AsyncClient) -> None:
    resp = await chat_client.get("/api/chat/context")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks_due_today"] == []
    assert data["tasks_due_this_week"] == []
    assert data["overdue_tasks"] == []
    assert data["task_count_by_status"]["pending"] == 0
    assert data["proactive_alerts"] == []


@pytest.mark.asyncio
async def test_context_with_due_today(chat_client: AsyncClient, chat_session: Session) -> None:
    from zoneinfo import ZoneInfo
    local_now = datetime.now(UTC).astimezone(ZoneInfo("America/New_York"))
    later_today = local_now.replace(hour=23, minute=30, second=0, microsecond=0)
    due_utc = later_today.astimezone(UTC)
    _make_task(
        chat_session,
        title="Due Tonight",
        due_date_iso=due_utc.isoformat(),
        raw_hash="ctx-today",
    )
    _make_task(
        chat_session,
        title="Due Next Week",
        due_date_iso=(datetime.now(UTC) + timedelta(days=5)).isoformat(),
        raw_hash="ctx-week",
    )

    resp = await chat_client.get("/api/chat/context")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks_due_today"]) == 1
    assert data["tasks_due_today"][0]["title"] == "Due Tonight"
    assert len(data["tasks_due_this_week"]) == 1
    assert data["tasks_due_this_week"][0]["title"] == "Due Next Week"
    assert data["task_count_by_status"]["pending"] == 2
    alert_types = [a["type"] for a in data["proactive_alerts"]]
    assert "due_today" in alert_types


@pytest.mark.asyncio
async def test_context_with_overdue(chat_client: AsyncClient, chat_session: Session) -> None:
    now = datetime.now(UTC)
    _make_task(
        chat_session,
        title="Overdue Item",
        due_date_iso=(now - timedelta(days=1)).isoformat(),
        raw_hash="ctx-overdue",
    )

    resp = await chat_client.get("/api/chat/context")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["overdue_tasks"]) == 1
    assert data["overdue_tasks"][0]["title"] == "Overdue Item"
    alert_types = [a["type"] for a in data["proactive_alerts"]]
    assert "overdue" in alert_types


# ── Repository helpers ────────────────────────────────────────────────


def test_repo_list_tasks_due_today(session: Session) -> None:
    from zoneinfo import ZoneInfo
    local_now = datetime.now(UTC).astimezone(ZoneInfo("America/New_York"))
    later_today = local_now.replace(hour=23, minute=30, second=0, microsecond=0)
    due_today_utc = later_today.astimezone(UTC)
    tomorrow_utc = (later_today + timedelta(days=1)).astimezone(UTC)
    _make_task(
        session,
        title="Today",
        due_date_iso=due_today_utc.isoformat(),
        raw_hash="r-today",
    )
    _make_task(
        session,
        title="Tomorrow",
        due_date_iso=tomorrow_utc.isoformat(),
        raw_hash="r-tmrw",
    )

    repo = TaskRepository(session)
    due_today = repo.list_tasks_due_today()
    assert len(due_today) == 1
    assert due_today[0].title == "Today"


def test_repo_list_overdue(session: Session) -> None:
    now = datetime.now(UTC)
    _make_task(
        session,
        title="Past Due",
        due_date_iso=(now - timedelta(hours=5)).isoformat(),
        raw_hash="r-over",
    )
    _make_task(
        session,
        title="Future",
        due_date_iso=(now + timedelta(hours=5)).isoformat(),
        raw_hash="r-future",
    )

    repo = TaskRepository(session)
    overdue = repo.list_overdue_tasks()
    assert len(overdue) == 1
    assert overdue[0].title == "Past Due"


def test_repo_count_by_status(session: Session) -> None:
    _make_task(session, raw_hash="c1", status="pending")
    _make_task(session, raw_hash="c2", status="pending")
    _make_task(session, raw_hash="c3", status="done")

    repo = TaskRepository(session)
    counts = repo.count_by_status()
    assert counts["pending"] == 2
    assert counts["done"] == 1
    assert counts["dismissed"] == 0
