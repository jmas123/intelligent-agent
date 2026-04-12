"""Tests for outgoing tone analyzer.

Since the tone analyzer requires Gmail API access, these tests focus on
the pattern storage integration using the PatternRepository directly.
"""

import json

from sqlalchemy.orm import Session

from deadline_agent.store.pattern_repository import PatternRepository


class TestTonePatternStorage:
    """Test that tone patterns can be stored and retrieved correctly."""

    def test_message_length_pattern(self, session: Session) -> None:
        repo = PatternRepository(session)
        value = json.dumps({
            "avg_snippet_length": 42.5,
            "message_count": 15,
            "period": "6h",
        })
        pattern = repo.upsert("outgoing_tone", "message_length", value, 15, 0.8)
        assert pattern.pattern_type == "outgoing_tone"
        assert pattern.pattern_key == "message_length"

        data = json.loads(pattern.value)
        assert data["avg_snippet_length"] == 42.5

    def test_response_latency_pattern(self, session: Session) -> None:
        repo = PatternRepository(session)
        value = json.dumps({
            "avg_response_latency_hours": 8.5,
            "reply_count": 7,
        })
        pattern = repo.upsert("outgoing_tone", "response_latency", value, 7, 0.7)

        data = json.loads(pattern.value)
        assert data["avg_response_latency_hours"] == 8.5

    def test_format_pattern_short_messages(self, session: Session) -> None:
        """Verify that _format_pattern renders short message alerts."""
        from deadline_agent.reasoning.state import _format_pattern

        repo = PatternRepository(session)
        value = json.dumps({
            "avg_snippet_length": 30,
            "message_count": 10,
        })
        pattern = repo.upsert("outgoing_tone", "message_length", value, 10, 0.8)

        result = _format_pattern(pattern)
        assert "unusually short" in result

    def test_format_pattern_high_latency(self, session: Session) -> None:
        from deadline_agent.reasoning.state import _format_pattern

        repo = PatternRepository(session)
        value = json.dumps({
            "avg_response_latency_hours": 18.5,
            "reply_count": 5,
        })
        pattern = repo.upsert("outgoing_tone", "response_latency", value, 5, 0.5)

        result = _format_pattern(pattern)
        assert "reply time" in result
        assert "18.5" in result

    def test_format_pattern_normal_length_empty(self, session: Session) -> None:
        """Normal message length should not surface."""
        from deadline_agent.reasoning.state import _format_pattern

        repo = PatternRepository(session)
        value = json.dumps({
            "avg_snippet_length": 150,
            "message_count": 10,
        })
        pattern = repo.upsert("outgoing_tone", "message_length", value, 10, 0.8)

        result = _format_pattern(pattern)
        assert result == ""
