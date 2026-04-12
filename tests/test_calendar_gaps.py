"""Tests for calendar gap finder."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deadline_agent.reasoning.calendar_gaps import fetch_free_busy, find_gaps


def test_find_gaps_simple() -> None:
    # Use EDT (-04:00) timestamps so "reasonable hours" (8am-10pm local) align
    busy = [
        {"start": "2026-03-25T09:00:00-04:00", "end": "2026-03-25T10:00:00-04:00"},
        {"start": "2026-03-25T14:00:00-04:00", "end": "2026-03-25T15:00:00-04:00"},
    ]
    gaps = find_gaps(
        busy,
        "2026-03-25T08:00:00-04:00",
        "2026-03-25T22:00:00-04:00",
        min_minutes=60,
    )
    # Gaps: 8:00-9:00 (1h), 10:00-14:00 (4h), 15:00-22:00 (7h)
    assert len(gaps) == 3
    assert gaps[0]["duration_minutes"] == 60
    assert gaps[1]["duration_minutes"] == 240
    assert gaps[2]["duration_minutes"] == 420


def test_find_gaps_no_busy() -> None:
    gaps = find_gaps(
        [],
        "2026-03-25T08:00:00-04:00",
        "2026-03-25T22:00:00-04:00",
        min_minutes=60,
    )
    assert len(gaps) == 1
    assert gaps[0]["duration_minutes"] == 840  # 14h


def test_find_gaps_fully_busy() -> None:
    busy = [
        {"start": "2026-03-25T06:00:00+00:00", "end": "2026-03-25T23:00:00+00:00"},
    ]
    gaps = find_gaps(
        busy,
        "2026-03-25T08:00:00+00:00",
        "2026-03-25T22:00:00+00:00",
        min_minutes=60,
    )
    assert len(gaps) == 0


def test_find_gaps_filters_short() -> None:
    busy = [
        {"start": "2026-03-25T09:00:00-04:00", "end": "2026-03-25T09:30:00-04:00"},
    ]
    gaps = find_gaps(
        busy,
        "2026-03-25T08:00:00-04:00",
        "2026-03-25T10:00:00-04:00",
        min_minutes=60,
    )
    # 8-9 = 60min (passes), 9:30-10 = 30min (filtered)
    assert len(gaps) == 1
    assert gaps[0]["duration_minutes"] == 60


def test_find_gaps_multi_day() -> None:
    gaps = find_gaps(
        [],
        "2026-03-25T08:00:00+00:00",
        "2026-03-26T22:00:00+00:00",
        min_minutes=60,
    )
    # Two days of 8am-10pm = 14h each
    assert len(gaps) == 2


@pytest.mark.asyncio
async def test_fetch_free_busy_success() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = "fake-token"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "calendars": {
            "primary": {"busy": [{"start": "2026-03-25T09:00:00Z", "end": "2026-03-25T10:00:00Z"}]}
        }
    }

    with patch("deadline_agent.reasoning.calendar_gaps.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        result = await fetch_free_busy(
            "2026-03-25T00:00:00Z",
            "2026-03-26T00:00:00Z",
            token_manager=mock_tm,
        )

    assert len(result) == 1
    assert result[0]["start"] == "2026-03-25T09:00:00Z"


@pytest.mark.asyncio
async def test_fetch_free_busy_no_token() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = None

    result = await fetch_free_busy(
        "2026-03-25T00:00:00Z", "2026-03-26T00:00:00Z", token_manager=mock_tm
    )
    assert result == []
