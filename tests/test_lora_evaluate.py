"""Tests for LoRA evaluation harness."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.lora.evaluate import evaluate_model, load_test_pairs


@pytest.fixture
def test_pairs() -> list[dict[str, str]]:
    return [
        {
            "input": "Subject: HW5\nContent: Due Friday",
            "output": json.dumps({
                "title": "HW5",
                "due_date_iso": "2026-04-10T23:59:00",
                "source": "gmail",
                "type": "assignment",
                "course": "CS101",
                "urgency_score": 3,
                "confidence": 0.9,
                "raw_hash": "abc123",
            }),
        },
    ]


class TestLoadTestPairs:
    def test_loads_jsonl(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"input": "a", "output": "b"}) + "\n")
            f.write(json.dumps({"input": "c", "output": "d"}) + "\n")
            path = f.name

        pairs = load_test_pairs(path)
        assert len(pairs) == 2
        assert pairs[0]["input"] == "a"

        Path(path).unlink()

    def test_skips_empty_lines(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"input": "a", "output": "b"}) + "\n")
            f.write("\n")
            f.write(json.dumps({"input": "c", "output": "d"}) + "\n")
            path = f.name

        pairs = load_test_pairs(path)
        assert len(pairs) == 2

        Path(path).unlink()


class TestEvaluateModel:
    @pytest.mark.asyncio
    async def test_counts_valid_responses(self, test_pairs: list[dict[str, str]]) -> None:
        mock_response = AsyncMock()
        mock_response.message.content = json.dumps({
            "title": "HW5",
            "due_date_iso": "2026-04-10T23:59:00",
            "source": "gmail",
            "type": "assignment",
            "course": "CS101",
            "urgency_score": 3,
            "confidence": 0.9,
            "raw_hash": "abc123",
        })

        mock_client = AsyncMock()
        mock_client.chat.return_value = mock_response

        with patch("ollama.AsyncClient", return_value=mock_client):
            result = await evaluate_model("test-model", test_pairs)

        assert result["model"] == "test-model"
        assert result["total"] == 1
        assert result["valid_json"] == 1
        assert result["schema_valid"] == 1
        assert result["avg_confidence"] == 0.9
        # All fields match ground truth
        assert result["field_accuracy"]["title"] == 1.0

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self, test_pairs: list[dict[str, str]]) -> None:
        mock_response = AsyncMock()
        mock_response.message.content = "not json"

        mock_client = AsyncMock()
        mock_client.chat.return_value = mock_response

        with patch("ollama.AsyncClient", return_value=mock_client):
            result = await evaluate_model("test-model", test_pairs)

        assert result["valid_json"] == 0
        assert result["schema_valid"] == 0

    @pytest.mark.asyncio
    async def test_handles_connection_failure(self, test_pairs: list[dict[str, str]]) -> None:
        mock_client = AsyncMock()
        mock_client.chat.side_effect = ConnectionError("Ollama down")

        with patch("ollama.AsyncClient", return_value=mock_client):
            result = await evaluate_model("test-model", test_pairs)

        assert result["valid_json"] == 0
