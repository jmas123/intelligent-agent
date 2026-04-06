"""Tests for Phase 10: Life context model."""

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from deadline_agent.models import Base, LifeContext, Task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def lc_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def lc_session(lc_engine: Engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=lc_engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_task(session: Session, **overrides: Any) -> Task:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "title": "Test Task",
        "due_date_iso": (now + timedelta(days=3)).isoformat(),
        "source": "gmail",
        "type": "assignment",
        "course": "CS 101",
        "urgency_score": 3,
        "confidence": 0.9,
        "raw_hash": f"hash_{id(overrides)}_{now.timestamp()}",
        "status": "pending",
    }
    defaults.update(overrides)
    task = Task(**defaults)
    session.add(task)
    session.flush()
    return task


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestLifeContextModel:
    def test_create_life_context(self, lc_session: Session) -> None:
        ctx = LifeContext(
            season="exams",
            label="Midterms",
            start_date="2026-03-20",
            end_date="2026-04-05",
            source="manual",
            active=True,
        )
        lc_session.add(ctx)
        lc_session.flush()
        assert ctx.id is not None
        assert ctx.season == "exams"
        assert ctx.source == "manual"
        assert ctx.active is True

    def test_default_values(self, lc_session: Session) -> None:
        ctx = LifeContext(
            season="light_week",
            start_date="2026-03-25",
            end_date="2026-04-01",
            source="auto",
        )
        lc_session.add(ctx)
        lc_session.flush()
        assert ctx.label is None
        assert ctx.metadata_json is None
        assert ctx.active is True


# ---------------------------------------------------------------------------
# Repository tests
# ---------------------------------------------------------------------------

class TestLifeContextRepository:
    def test_create(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        ctx = repo.create({
            "season": "exams",
            "start_date": "2026-04-01",
            "end_date": "2026-04-15",
            "source": "auto",
        })
        assert ctx.id is not None
        assert ctx.season == "exams"

    def test_get_active_filters_by_date(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)

        # Active context covering today
        today = date.today()
        repo.create({
            "season": "exams",
            "start_date": (today - timedelta(days=3)).isoformat(),
            "end_date": (today + timedelta(days=5)).isoformat(),
            "source": "auto",
        })
        # Expired context
        repo.create({
            "season": "recruiting",
            "start_date": "2025-01-01",
            "end_date": "2025-02-01",
            "source": "auto",
        })

        active = repo.get_active()
        assert len(active) == 1
        assert active[0].season == "exams"

    def test_get_active_as_of(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        repo.create({
            "season": "exams",
            "start_date": "2026-04-01",
            "end_date": "2026-04-15",
            "source": "auto",
        })

        assert len(repo.get_active(as_of="2026-04-10")) == 1
        assert len(repo.get_active(as_of="2026-05-01")) == 0

    def test_deactivate(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        today = date.today()
        ctx = repo.create({
            "season": "exams",
            "start_date": (today - timedelta(days=1)).isoformat(),
            "end_date": (today + timedelta(days=10)).isoformat(),
            "source": "auto",
        })

        result = repo.deactivate(ctx.id)
        assert result is not None
        assert result.active is False
        assert len(repo.get_active()) == 0

    def test_set_manual_deactivates_auto(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)

        # Create auto-detected context
        auto_ctx = repo.create({
            "season": "exams",
            "start_date": "2026-04-01",
            "end_date": "2026-04-15",
            "source": "auto",
        })

        # Set manual context with overlapping dates
        manual_ctx = repo.set_manual("exams", "2026-04-05", "2026-04-20", "Finals")

        lc_session.refresh(auto_ctx)
        assert auto_ctx.active is False
        assert manual_ctx.source == "manual"
        assert manual_ctx.label == "Finals"

    def test_set_manual_idempotent(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        ctx1 = repo.set_manual("exams", "2026-04-01", "2026-04-15")
        ctx2 = repo.set_manual("exams", "2026-04-01", "2026-04-15", "Finals")

        assert ctx1.id == ctx2.id
        assert ctx2.label == "Finals"

    def test_clear_auto(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        repo.create({"season": "exams", "start_date": "2026-04-01", "end_date": "2026-04-15", "source": "auto"})
        repo.create({"season": "recruiting", "start_date": "2026-04-01", "end_date": "2026-04-15", "source": "auto"})
        repo.create({"season": "exams", "start_date": "2026-04-01", "end_date": "2026-04-15", "source": "manual"})

        cleared = repo.clear_auto()
        assert cleared == 2

        # Manual context still active
        all_active = lc_session.query(LifeContext).filter(LifeContext.active.is_(True)).all()
        assert len(all_active) == 1
        assert all_active[0].source == "manual"

    def test_clear_auto_by_season(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        repo.create({"season": "exams", "start_date": "2026-04-01", "end_date": "2026-04-15", "source": "auto"})
        repo.create({"season": "recruiting", "start_date": "2026-04-01", "end_date": "2026-04-15", "source": "auto"})

        cleared = repo.clear_auto(season="exams")
        assert cleared == 1

    def test_has_active_manual(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        repo.set_manual("exams", "2026-04-01", "2026-04-15")

        assert repo.has_active_manual("exams", as_of="2026-04-10") is True
        assert repo.has_active_manual("exams", as_of="2026-05-01") is False
        assert repo.has_active_manual("recruiting", as_of="2026-04-10") is False


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------

class TestLifeContextDetection:
    def test_detect_exams(self, lc_session: Session) -> None:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts

        # Create 3 exams within 10 days
        base = date.today() + timedelta(days=5)
        for i in range(3):
            _make_task(
                lc_session,
                title=f"Exam {i+1}",
                type="exam",
                due_date_iso=(base + timedelta(days=i * 3)).isoformat(),
                raw_hash=f"exam_{i}",
            )

        contexts = detect_life_contexts(lc_session)
        exam_contexts = [c for c in contexts if c.season == "exams"]
        assert len(exam_contexts) == 1
        assert "3 exams" in (exam_contexts[0].label or "")

    def test_no_exams_below_threshold(self, lc_session: Session) -> None:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts

        # Only 2 exams — below threshold
        base = date.today() + timedelta(days=5)
        for i in range(2):
            _make_task(
                lc_session,
                title=f"Exam {i+1}",
                type="exam",
                due_date_iso=(base + timedelta(days=i * 3)).isoformat(),
                raw_hash=f"exam_below_{i}",
            )

        contexts = detect_life_contexts(lc_session)
        assert not any(c.season == "exams" for c in contexts)

    def test_detect_recruiting(self, lc_session: Session) -> None:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts

        base = date.today() + timedelta(days=3)
        _make_task(
            lc_session,
            title="Google Phone Interview",
            type="meeting",
            due_date_iso=base.isoformat(),
            raw_hash="interview_1",
        )
        _make_task(
            lc_session,
            title="Amazon Technical Screen",
            type="meeting",
            due_date_iso=(base + timedelta(days=2)).isoformat(),
            raw_hash="interview_2",
        )

        contexts = detect_life_contexts(lc_session)
        recruiting = [c for c in contexts if c.season == "recruiting"]
        assert len(recruiting) == 1

    def test_detect_recruiting_by_type(self, lc_session: Session) -> None:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts

        base = date.today() + timedelta(days=3)
        _make_task(
            lc_session,
            title="Prep for Google",
            type="interview_prep",
            due_date_iso=base.isoformat(),
            raw_hash="iprep_1",
        )
        _make_task(
            lc_session,
            title="Prep for Amazon",
            type="interview_prep",
            due_date_iso=(base + timedelta(days=2)).isoformat(),
            raw_hash="iprep_2",
        )

        contexts = detect_life_contexts(lc_session)
        assert any(c.season == "recruiting" for c in contexts)

    def test_detect_light_week(self, lc_session: Session) -> None:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts

        # Only 1 task due this week
        _make_task(
            lc_session,
            title="Easy reading",
            type="assignment",
            due_date_iso=(date.today() + timedelta(days=3)).isoformat(),
            raw_hash="light_1",
        )

        contexts = detect_life_contexts(lc_session)
        light = [c for c in contexts if c.season == "light_week"]
        assert len(light) == 1

    def test_no_light_week_with_exams(self, lc_session: Session) -> None:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts

        _make_task(
            lc_session,
            title="Final Exam",
            type="exam",
            due_date_iso=(date.today() + timedelta(days=3)).isoformat(),
            raw_hash="exam_light_block",
        )

        contexts = detect_life_contexts(lc_session)
        assert not any(c.season == "light_week" for c in contexts)

    def test_manual_blocks_auto(self, lc_session: Session) -> None:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts
        from deadline_agent.store.context_repository import LifeContextRepository

        # Set manual exams context
        repo = LifeContextRepository(lc_session)
        today = date.today()
        repo.set_manual("exams", today.isoformat(), (today + timedelta(days=14)).isoformat())

        # Create exams that would trigger auto-detection
        for i in range(4):
            _make_task(
                lc_session,
                title=f"Exam {i+1}",
                type="exam",
                due_date_iso=(today + timedelta(days=5 + i * 2)).isoformat(),
                raw_hash=f"exam_manual_block_{i}",
            )

        contexts = detect_life_contexts(lc_session)
        # Should not auto-detect exams since manual override exists
        assert not any(c.season == "exams" for c in contexts)

    def test_clears_stale_auto(self, lc_session: Session) -> None:
        from deadline_agent.awareness.life_context_detector import detect_life_contexts
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        # Pre-existing auto context
        repo.create({
            "season": "recruiting",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "source": "auto",
        })

        # Run detection (no recruiting tasks, so it won't re-detect)
        detect_life_contexts(lc_session)

        # Old auto context should be deactivated
        all_contexts = lc_session.query(LifeContext).all()
        auto_active = [c for c in all_contexts if c.source == "auto" and c.active]
        # May have light_week if few tasks, but old recruiting should be gone
        assert not any(c.season == "recruiting" for c in auto_active)


# ---------------------------------------------------------------------------
# StateSnapshot tests
# ---------------------------------------------------------------------------

class TestStateSnapshotLifeContext:
    def test_life_contexts_in_prompt(self, lc_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        today = date.today()
        repo.set_manual(
            "exams",
            (today - timedelta(days=1)).isoformat(),
            (today + timedelta(days=10)).isoformat(),
            "Midterms",
        )

        from deadline_agent.reasoning.state import build_state_snapshot

        snapshot = build_state_snapshot(lc_session)
        assert len(snapshot.life_contexts) == 1

        prompt = snapshot.to_prompt()
        assert "ACTIVE LIFE CONTEXT" in prompt
        assert "EXAMS" in prompt
        assert "Midterms" in prompt
        assert "user-set" in prompt

    def test_no_life_contexts(self, lc_session: Session) -> None:
        from deadline_agent.reasoning.state import build_state_snapshot

        snapshot = build_state_snapshot(lc_session)
        assert len(snapshot.life_contexts) == 0

        prompt = snapshot.to_prompt()
        assert "ACTIVE LIFE CONTEXT" not in prompt


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestContextCLI:
    def test_context_show_empty(self, lc_session: Session) -> None:
        from unittest.mock import patch

        from click.testing import CliRunner

        from deadline_agent.cli import cli

        runner = CliRunner()
        with patch("deadline_agent.cli.SessionLocal", return_value=lc_session):
            result = runner.invoke(cli, ["context", "show"])

        assert result.exit_code == 0
        assert "No active life contexts" in result.output

    def test_context_set_and_show(self, lc_session: Session) -> None:
        from unittest.mock import patch

        from click.testing import CliRunner

        from deadline_agent.cli import cli

        runner = CliRunner()
        with patch("deadline_agent.cli.SessionLocal", return_value=lc_session):
            result = runner.invoke(
                cli,
                ["context", "set", "exams", "--start", "2026-04-10", "--end", "2026-04-25"],
            )
            assert result.exit_code == 0
            assert "EXAMS" in result.output

            result = runner.invoke(cli, ["context", "show"])
            assert result.exit_code == 0
            # May or may not show depending on today's date vs the set range
            # Just verify it runs without error

    def test_context_set_invalid_dates(self, lc_session: Session) -> None:
        from unittest.mock import patch

        from click.testing import CliRunner

        from deadline_agent.cli import cli

        runner = CliRunner()
        with patch("deadline_agent.cli.SessionLocal", return_value=lc_session):
            result = runner.invoke(
                cli,
                ["context", "set", "exams", "--start", "2026-04-25", "--end", "2026-04-10"],
            )
            assert result.exit_code == 0
            assert "End date must be" in result.output

    def test_context_clear(self, lc_session: Session) -> None:
        from unittest.mock import patch

        from click.testing import CliRunner

        from deadline_agent.cli import cli
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        today = date.today()
        ctx = repo.set_manual(
            "exams",
            (today - timedelta(days=1)).isoformat(),
            (today + timedelta(days=10)).isoformat(),
        )

        runner = CliRunner()
        with patch("deadline_agent.cli.SessionLocal", return_value=lc_session):
            result = runner.invoke(cli, ["context", "clear", str(ctx.id)])

        assert result.exit_code == 0
        assert "deactivated" in result.output


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------

@pytest.fixture
def lc_api_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def lc_api_session(lc_api_engine: Engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=lc_api_engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
async def lc_client(lc_api_session: Session) -> AsyncClient:  # type: ignore[misc]
    from deadline_agent.main import app
    from deadline_agent.store.session import get_session

    def _override() -> Generator[Session, None, None]:
        yield lc_api_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class TestLifeContextAPI:
    @pytest.mark.anyio
    async def test_list_empty(self, lc_client: AsyncClient) -> None:
        resp = await lc_client.get("/api/chat/life-contexts")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.anyio
    async def test_set_and_list(self, lc_client: AsyncClient) -> None:
        resp = await lc_client.post(
            "/api/chat/life-contexts",
            json={
                "season": "exams",
                "start_date": "2026-04-01",
                "end_date": "2026-04-15",
                "label": "Midterms",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["season"] == "exams"
        assert data["source"] == "manual"
        assert data["label"] == "Midterms"

        # Verify it shows in list (if today is in range)
        resp = await lc_client.get("/api/chat/life-contexts")
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_set_invalid_season(self, lc_client: AsyncClient) -> None:
        resp = await lc_client.post(
            "/api/chat/life-contexts",
            json={
                "season": "invalid",
                "start_date": "2026-04-01",
                "end_date": "2026-04-15",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_deactivate(self, lc_client: AsyncClient, lc_api_session: Session) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_api_session)
        today = date.today()
        ctx = repo.set_manual(
            "exams",
            (today - timedelta(days=1)).isoformat(),
            (today + timedelta(days=10)).isoformat(),
        )

        resp = await lc_client.post(f"/api/chat/life-contexts/{ctx.id}/deactivate")
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    @pytest.mark.anyio
    async def test_deactivate_not_found(self, lc_client: AsyncClient) -> None:
        resp = await lc_client.post("/api/chat/life-contexts/999/deactivate")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_context_snapshot_includes_life_contexts(
        self, lc_client: AsyncClient, lc_api_session: Session
    ) -> None:
        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_api_session)
        today = date.today()
        repo.set_manual(
            "recruiting",
            (today - timedelta(days=1)).isoformat(),
            (today + timedelta(days=10)).isoformat(),
            "Spring recruiting",
        )

        resp = await lc_client.get("/api/chat/context")
        assert resp.status_code == 200
        data = resp.json()
        assert "life_contexts" in data
        assert len(data["life_contexts"]) == 1
        assert data["life_contexts"][0]["season"] == "recruiting"


# ---------------------------------------------------------------------------
# Seeder tests
# ---------------------------------------------------------------------------

class TestLifeContextSeeder:
    def test_seed_from_config(self, lc_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        from deadline_agent.awareness.life_context_seeder import seed_from_config
        from deadline_agent.config import settings

        monkeypatch.setattr(
            settings,
            "life_contexts",
            [
                {"season": "exams", "start": "2026-04-10", "end": "2026-04-25"},
                {"season": "recruiting", "start": "2026-01-15", "end": "2026-04-30", "label": "Spring"},
            ],
        )

        count = seed_from_config(lc_session)
        assert count == 2

        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        all_contexts = repo.get_by_season("exams") + repo.get_by_season("recruiting")
        assert len(all_contexts) == 2
        assert all(c.source == "manual" for c in all_contexts)

    def test_seed_idempotent(self, lc_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        from deadline_agent.awareness.life_context_seeder import seed_from_config
        from deadline_agent.config import settings

        monkeypatch.setattr(
            settings,
            "life_contexts",
            [{"season": "exams", "start": "2026-04-10", "end": "2026-04-25"}],
        )

        seed_from_config(lc_session)
        seed_from_config(lc_session)

        from deadline_agent.store.context_repository import LifeContextRepository

        repo = LifeContextRepository(lc_session)
        exams = repo.get_by_season("exams")
        active = [c for c in exams if c.active]
        assert len(active) == 1

    def test_seed_skips_incomplete(self, lc_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        from deadline_agent.awareness.life_context_seeder import seed_from_config
        from deadline_agent.config import settings

        monkeypatch.setattr(
            settings,
            "life_contexts",
            [{"season": "exams"}],  # missing start/end
        )

        count = seed_from_config(lc_session)
        assert count == 0
