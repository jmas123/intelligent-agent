"""Integration tests for widget-facing chat API endpoints.

Tests the full stack: HTTP request → FastAPI router → SQLAlchemy → response,
using an in-memory SQLite database.
"""

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from deadline_agent.api.chat import router
from deadline_agent.models import Base, Insight, ProposedAction, Task
from deadline_agent.store.session import get_session

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# StaticPool forces all connections to share the same in-memory database,
# which avoids "no such table" when FastAPI uses a different thread.
_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionLocal = sessionmaker(bind=_engine)


@pytest.fixture(autouse=True)
def _setup_db():
    """Create tables before each test, drop after."""
    Base.metadata.create_all(_engine)
    yield
    Base.metadata.drop_all(_engine)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
async def client(db: Session) -> AsyncClient:
    """Async test client with overridden DB dependency."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api/chat")

    def _override_session() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _make_task(
    db: Session,
    title: str = "Test Task",
    due_delta_days: int = 0,
    source: str = "gmail",
    status: str = "pending",
    urgency: int = 3,
    raw_hash: str | None = None,
) -> Task:
    """Helper to insert a task and return it."""
    due = datetime.now(UTC) + timedelta(days=due_delta_days)
    task = Task(
        title=title,
        due_date_iso=due.isoformat(),
        source=source,
        type="assignment",
        course="CS101",
        urgency_score=urgency,
        confidence=0.9,
        raw_hash=raw_hash or f"hash-{title}-{due.isoformat()}",
        status=status,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _make_insight(db: Session, type_: str = "suggestion", content: str = "Test insight") -> Insight:
    insight = Insight(
        type=type_,
        content=content,
        related_task_ids="[]",
        dismissed=False,
    )
    db.add(insight)
    db.commit()
    db.refresh(insight)
    return insight


def _make_action(
    db: Session,
    type_: str = "calendar_block",
    title: str = "Study block",
    task_id: int | None = None,
    payload: dict | None = None,
) -> ProposedAction:
    if payload is None:
        start = datetime.now(UTC) + timedelta(hours=2)
        end = start + timedelta(hours=1)
        payload = {"start_iso": start.isoformat(), "end_iso": end.isoformat()}
    action = ProposedAction(
        type=type_,
        status="proposed",
        task_id=task_id,
        title=title,
        payload=json.dumps(payload),
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


# ---------------------------------------------------------------------------
# GET /api/chat/tasks
# ---------------------------------------------------------------------------


class TestListTasks:
    @pytest.mark.anyio
    async def test_empty_returns_empty_list(self, client: AsyncClient) -> None:
        resp = await client.get("/api/chat/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.anyio
    async def test_returns_pending_tasks(self, client: AsyncClient, db: Session) -> None:
        _make_task(db, title="Essay due")
        resp = await client.get("/api/chat/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Essay due"
        assert tasks[0]["status"] == "pending"

    @pytest.mark.anyio
    async def test_filter_by_status(self, client: AsyncClient, db: Session) -> None:
        _make_task(db, title="Done task", status="done", raw_hash="h1")
        _make_task(db, title="Pending task", status="pending", raw_hash="h2")
        resp = await client.get("/api/chat/tasks", params={"status": "done"})
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Done task"

    @pytest.mark.anyio
    async def test_filter_by_source(self, client: AsyncClient, db: Session) -> None:
        _make_task(db, title="Gmail task", source="gmail", raw_hash="h1")
        _make_task(db, title="Moodle task", source="moodle", raw_hash="h2")
        resp = await client.get("/api/chat/tasks", params={"source": "moodle"})
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Moodle task"

    @pytest.mark.anyio
    async def test_limit(self, client: AsyncClient, db: Session) -> None:
        for i in range(5):
            _make_task(db, title=f"Task {i}", raw_hash=f"h{i}")
        resp = await client.get("/api/chat/tasks", params={"limit": 2})
        assert len(resp.json()) == 2

    @pytest.mark.anyio
    async def test_ordered_by_due_date(self, client: AsyncClient, db: Session) -> None:
        _make_task(db, title="Later", due_delta_days=5, raw_hash="h1")
        _make_task(db, title="Sooner", due_delta_days=1, raw_hash="h2")
        resp = await client.get("/api/chat/tasks")
        tasks = resp.json()
        assert tasks[0]["title"] == "Sooner"
        assert tasks[1]["title"] == "Later"


# ---------------------------------------------------------------------------
# GET /api/chat/tasks/{task_id}
# ---------------------------------------------------------------------------


class TestGetTask:
    @pytest.mark.anyio
    async def test_found(self, client: AsyncClient, db: Session) -> None:
        task = _make_task(db, title="Specific task")
        resp = await client.get(f"/api/chat/tasks/{task.id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Specific task"

    @pytest.mark.anyio
    async def test_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/chat/tasks/9999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/chat/tasks/{task_id}/status
# ---------------------------------------------------------------------------


class TestUpdateTaskStatus:
    @pytest.mark.anyio
    async def test_mark_done(self, client: AsyncClient, db: Session) -> None:
        task = _make_task(db)
        resp = await client.patch(
            f"/api/chat/tasks/{task.id}/status",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

    @pytest.mark.anyio
    async def test_mark_dismissed(self, client: AsyncClient, db: Session) -> None:
        task = _make_task(db)
        resp = await client.patch(
            f"/api/chat/tasks/{task.id}/status",
            json={"status": "dismissed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

    @pytest.mark.anyio
    async def test_invalid_status_rejected(self, client: AsyncClient, db: Session) -> None:
        task = _make_task(db)
        resp = await client.patch(
            f"/api/chat/tasks/{task.id}/status",
            json={"status": "archived"},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_not_found(self, client: AsyncClient) -> None:
        resp = await client.patch(
            "/api/chat/tasks/9999/status",
            json={"status": "done"},
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_status_persists_after_update(self, client: AsyncClient, db: Session) -> None:
        task = _make_task(db)
        await client.patch(f"/api/chat/tasks/{task.id}/status", json={"status": "done"})
        resp = await client.get(f"/api/chat/tasks/{task.id}")
        assert resp.json()["status"] == "done"


# ---------------------------------------------------------------------------
# GET /api/chat/context
# ---------------------------------------------------------------------------


class TestContext:
    @pytest.mark.anyio
    async def test_empty_context(self, client: AsyncClient) -> None:
        resp = await client.get("/api/chat/context")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks_due_today"] == []
        assert data["overdue_tasks"] == []
        assert data["task_count_by_status"]["pending"] == 0

    @pytest.mark.anyio
    async def test_overdue_tasks_appear(self, client: AsyncClient, db: Session) -> None:
        _make_task(db, title="Overdue", due_delta_days=-3)
        resp = await client.get("/api/chat/context")
        data = resp.json()
        assert len(data["overdue_tasks"]) == 1
        assert data["overdue_tasks"][0]["title"] == "Overdue"

    @pytest.mark.anyio
    async def test_overdue_alert_generated(self, client: AsyncClient, db: Session) -> None:
        _make_task(db, title="Overdue", due_delta_days=-1)
        resp = await client.get("/api/chat/context")
        alerts = resp.json()["proactive_alerts"]
        types = [a["type"] for a in alerts]
        assert "overdue" in types

    @pytest.mark.anyio
    async def test_done_tasks_excluded_from_sections(self, client: AsyncClient, db: Session) -> None:
        _make_task(db, title="Done", due_delta_days=-1, status="done")
        resp = await client.get("/api/chat/context")
        data = resp.json()
        assert data["overdue_tasks"] == []
        assert data["task_count_by_status"]["done"] == 1

    @pytest.mark.anyio
    async def test_includes_insights(self, client: AsyncClient, db: Session) -> None:
        _make_insight(db, content="You have a workload spike")
        resp = await client.get("/api/chat/context")
        insights = resp.json()["insights"]
        assert len(insights) == 1
        assert insights[0]["content"] == "You have a workload spike"


# ---------------------------------------------------------------------------
# GET /api/chat/insights + POST dismiss
# ---------------------------------------------------------------------------


class TestInsights:
    @pytest.mark.anyio
    async def test_list_insights(self, client: AsyncClient, db: Session) -> None:
        _make_insight(db, content="Insight 1")
        _make_insight(db, content="Insight 2")
        resp = await client.get("/api/chat/insights")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    @pytest.mark.anyio
    async def test_dismiss_insight(self, client: AsyncClient, db: Session) -> None:
        insight = _make_insight(db)
        resp = await client.post(f"/api/chat/insights/{insight.id}/dismiss")
        assert resp.status_code == 200
        assert resp.json()["dismissed"] is True

    @pytest.mark.anyio
    async def test_dismissed_not_in_active_list(self, client: AsyncClient, db: Session) -> None:
        insight = _make_insight(db)
        await client.post(f"/api/chat/insights/{insight.id}/dismiss")
        resp = await client.get("/api/chat/insights")
        assert len(resp.json()) == 0

    @pytest.mark.anyio
    async def test_dismiss_nonexistent(self, client: AsyncClient) -> None:
        resp = await client.post("/api/chat/insights/9999/dismiss")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/chat/actions + approve + reject
# ---------------------------------------------------------------------------


class TestActions:
    @pytest.mark.anyio
    async def test_list_pending_actions(self, client: AsyncClient, db: Session) -> None:
        _make_action(db, title="Study for exam")
        resp = await client.get("/api/chat/actions")
        assert resp.status_code == 200
        actions = resp.json()
        assert len(actions) == 1
        assert actions[0]["title"] == "Study for exam"

    @pytest.mark.anyio
    async def test_reject_action(self, client: AsyncClient, db: Session) -> None:
        action = _make_action(db)
        resp = await client.post(f"/api/chat/actions/{action.id}/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    @pytest.mark.anyio
    async def test_rejected_not_in_pending(self, client: AsyncClient, db: Session) -> None:
        action = _make_action(db)
        await client.post(f"/api/chat/actions/{action.id}/reject")
        resp = await client.get("/api/chat/actions")
        assert len(resp.json()) == 0

    @pytest.mark.anyio
    async def test_reject_nonexistent(self, client: AsyncClient) -> None:
        resp = await client.post("/api/chat/actions/9999/reject")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Response shape validation
# ---------------------------------------------------------------------------


class TestResponseShapes:
    @pytest.mark.anyio
    async def test_task_response_fields(self, client: AsyncClient, db: Session) -> None:
        _make_task(db, title="Shape test", urgency=4)
        resp = await client.get("/api/chat/tasks")
        task = resp.json()[0]
        assert set(task.keys()) >= {"id", "title", "due", "source", "type", "course", "urgency", "status"}
        assert "4/5" in task["urgency"]

    @pytest.mark.anyio
    async def test_context_snapshot_shape(self, client: AsyncClient) -> None:
        resp = await client.get("/api/chat/context")
        data = resp.json()
        assert "now" in data
        assert "tasks_due_today" in data
        assert "tasks_due_this_week" in data
        assert "overdue_tasks" in data
        assert "task_count_by_status" in data
        assert "proactive_alerts" in data
        assert "insights" in data

    @pytest.mark.anyio
    async def test_action_response_fields(self, client: AsyncClient, db: Session) -> None:
        _make_action(db)
        resp = await client.get("/api/chat/actions")
        action = resp.json()[0]
        assert set(action.keys()) >= {
            "id", "type", "status", "task_id", "title",
            "payload", "created_at",
        }
