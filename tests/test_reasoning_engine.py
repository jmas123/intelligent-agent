"""Tests for the reasoning engine."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.reasoning.engine import (
    _reset_insight_cache,
    answer_query,
    generate_insights,
    generate_summary,
)


def _add_task(session: Session, hours_until_due: int = 4, **overrides: object) -> Task:
    due = datetime.now(UTC) + timedelta(hours=hours_until_due)
    defaults: dict[str, object] = {
        "title": "Submit HW3",
        "due_date_iso": due.isoformat(),
        "source": "gmail",
        "type": "assignment",
        "course": "CS 101",
        "urgency_score": 4,
        "confidence": 0.92,
        "raw_hash": f"hash_{hours_until_due}_{id(overrides)}",
        "status": "pending",
    }
    defaults.update(overrides)
    task = Task(**defaults)
    session.add(task)
    session.flush()
    return task


@pytest.fixture(autouse=True)
def _clear_reasoning_caches() -> None:
    """Reset caches between tests so each test gets a fresh LLM call."""
    _reset_insight_cache()
    from deadline_agent.reasoning.cache import digest_cache

    digest_cache.clear()


MOCK_INSIGHTS_JSON = json.dumps(
    {
        "insights": [
            {
                "type": "no_progress",
                "content": "HW3 is due today and no files detected",
                "related_task_ids": [1],
                "priority": 5,
            }
        ]
    }
)


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine._use_anthropic_primary", return_value=False)
@patch("deadline_agent.reasoning.engine._call_ollama", new_callable=AsyncMock)
async def test_generate_insights(
    mock_ollama: AsyncMock, mock_provider: object, session: Session
) -> None:
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_ollama.return_value = MOCK_INSIGHTS_JSON

    insights = await generate_insights(session)
    assert len(insights) == 1
    assert insights[0].type == "no_progress"
    assert insights[0].content == "HW3 is due today and no files detected"
    mock_ollama.assert_called_once()


@pytest.mark.asyncio
async def test_generate_insights_no_tasks(session: Session) -> None:
    insights = await generate_insights(session)
    assert len(insights) == 0


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine._call_ollama", new_callable=AsyncMock)
@patch("deadline_agent.reasoning.engine._call_anthropic", new_callable=AsyncMock)
async def test_generate_insights_ollama_fallback(
    mock_anthropic: AsyncMock, mock_ollama: AsyncMock, session: Session
) -> None:
    from deadline_agent.extraction.extractor import ExtractionError

    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_ollama.side_effect = ExtractionError("Ollama down")
    mock_anthropic.return_value = MOCK_INSIGHTS_JSON

    with patch("deadline_agent.reasoning.engine.settings") as mock_settings:
        mock_settings.use_anthropic_fallback = True
        mock_settings.anthropic_api_key = "test-key"
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.reasoning_model = ""
        mock_settings.reasoning_provider = "ollama"
        insights = await generate_insights(session)

    assert len(insights) == 1
    assert insights[0].type == "no_progress"


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine._call_ollama", new_callable=AsyncMock)
async def test_generate_summary(mock_ollama: AsyncMock, session: Session) -> None:
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_ollama.return_value = json.dumps({"summary": "Focus on HW3 today"})

    result = await generate_summary(session)
    assert result is not None
    assert "HW3" in result or "summary" in result.lower() or len(result) > 0


@pytest.mark.asyncio
async def test_generate_summary_no_tasks(session: Session) -> None:
    result = await generate_summary(session)
    assert result is None


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine._use_anthropic_primary", return_value=False)
@patch("deadline_agent.reasoning.engine._call_ollama", new_callable=AsyncMock)
async def test_answer_query(
    mock_ollama: AsyncMock, mock_provider: object, session: Session
) -> None:
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_ollama.return_value = json.dumps({"answer": "Focus on HW3, it's due in 4 hours"})

    result = await answer_query(session, "what should I do today?")
    assert len(result) > 0
    mock_ollama.assert_called_once()


# --- Provider routing tests ---


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine._call_ollama", new_callable=AsyncMock)
@patch("deadline_agent.reasoning.engine._call_anthropic", new_callable=AsyncMock)
async def test_anthropic_primary_insights(
    mock_anthropic: AsyncMock, mock_ollama: AsyncMock, session: Session
) -> None:
    """When reasoning_provider='anthropic', Anthropic is called first."""
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_anthropic.return_value = MOCK_INSIGHTS_JSON

    with patch("deadline_agent.reasoning.engine.settings") as mock_settings:
        mock_settings.reasoning_provider = "anthropic"
        mock_settings.anthropic_api_key = "test-key"
        mock_settings.anthropic_model = "claude-sonnet-4-6"
        insights = await generate_insights(session)

    assert len(insights) == 1
    assert insights[0].type == "no_progress"
    mock_anthropic.assert_called_once()
    mock_ollama.assert_not_called()


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine._call_ollama", new_callable=AsyncMock)
@patch("deadline_agent.reasoning.engine._call_anthropic", new_callable=AsyncMock)
async def test_anthropic_primary_falls_back_to_ollama(
    mock_anthropic: AsyncMock, mock_ollama: AsyncMock, session: Session
) -> None:
    """When Anthropic is primary but fails, falls back to Ollama."""
    from deadline_agent.extraction.extractor import ExtractionError

    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_anthropic.side_effect = ExtractionError("API down")
    mock_ollama.return_value = MOCK_INSIGHTS_JSON

    with patch("deadline_agent.reasoning.engine.settings") as mock_settings:
        mock_settings.reasoning_provider = "anthropic"
        mock_settings.anthropic_api_key = "test-key"
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.reasoning_model = ""
        insights = await generate_insights(session)

    assert len(insights) == 1
    mock_anthropic.assert_called_once()
    mock_ollama.assert_called_once()


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine._call_ollama", new_callable=AsyncMock)
@patch("deadline_agent.reasoning.engine._call_anthropic", new_callable=AsyncMock)
async def test_anthropic_primary_summary(
    mock_anthropic: AsyncMock, mock_ollama: AsyncMock, session: Session
) -> None:
    """Summary uses Anthropic first when configured as primary."""
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_anthropic.return_value = "Focus on HW3 today, it's due in 4 hours."

    with patch("deadline_agent.reasoning.engine.settings") as mock_settings:
        mock_settings.reasoning_provider = "anthropic"
        mock_settings.anthropic_api_key = "test-key"
        mock_settings.anthropic_model = "claude-sonnet-4-6"
        result = await generate_summary(session)

    assert result is not None
    assert "HW3" in result
    mock_anthropic.assert_called_once()
    mock_ollama.assert_not_called()


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.engine._call_ollama", new_callable=AsyncMock)
@patch("deadline_agent.reasoning.engine._call_anthropic", new_callable=AsyncMock)
async def test_anthropic_primary_query(
    mock_anthropic: AsyncMock, mock_ollama: AsyncMock, session: Session
) -> None:
    """Query uses Anthropic first when configured as primary."""
    _add_task(session, hours_until_due=4, raw_hash="t1")
    mock_anthropic.return_value = "You should focus on HW3."

    with patch("deadline_agent.reasoning.engine.settings") as mock_settings:
        mock_settings.reasoning_provider = "anthropic"
        mock_settings.anthropic_api_key = "test-key"
        mock_settings.anthropic_model = "claude-sonnet-4-6"
        result = await answer_query(session, "what should I do?")

    assert "HW3" in result
    mock_anthropic.assert_called_once()
    mock_ollama.assert_not_called()


class TestReasoningProviderConfig:
    """Tests for reasoning_provider config validation."""

    def test_valid_ollama_provider(self) -> None:
        from deadline_agent.config import Settings

        s = Settings(reasoning_provider="ollama")
        assert s.reasoning_provider == "ollama"

    def test_valid_anthropic_provider(self) -> None:
        from deadline_agent.config import Settings

        s = Settings(reasoning_provider="anthropic", anthropic_api_key="test-key")
        assert s.reasoning_provider == "anthropic"

    def test_anthropic_requires_api_key(self) -> None:
        from deadline_agent.config import Settings

        with pytest.raises(ValueError, match="anthropic_api_key is required"):
            Settings(reasoning_provider="anthropic", anthropic_api_key=None)

    def test_invalid_provider_rejected(self) -> None:
        from deadline_agent.config import Settings

        with pytest.raises(ValueError, match="must be 'ollama' or 'anthropic'"):
            Settings(reasoning_provider="openai")
