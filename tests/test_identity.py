"""Tests for the identity model: repository, synthesizer, and state injection."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from deadline_agent.models import (
    BehavioralPattern,
    IdentityDocument,
    SemesterRecord,
    WeeklySnapshot,
)


class TestIdentityRepository:
    def test_get_current_empty(self, session: Session) -> None:
        from deadline_agent.store.identity_repository import IdentityRepository

        repo = IdentityRepository(session)
        assert repo.get_current() is None

    def test_upsert_creates_first_doc(self, session: Session) -> None:
        from deadline_agent.store.identity_repository import IdentityRepository

        repo = IdentityRepository(session)
        doc = repo.upsert(
            document_json='{"test": true}',
            document_markdown="## Test\nHello",
        )
        assert doc.version == 1
        assert doc.document_markdown == "## Test\nHello"

    def test_upsert_increments_version(self, session: Session) -> None:
        from deadline_agent.store.identity_repository import IdentityRepository

        repo = IdentityRepository(session)
        doc1 = repo.upsert(document_json="{}", document_markdown="v1")
        assert doc1.version == 1

        doc2 = repo.upsert(document_json="{}", document_markdown="v2")
        assert doc2.version == 2
        assert doc2.document_markdown == "v2"

        # Should still be one row
        current = repo.get_current()
        assert current is not None
        assert current.version == 2

    def test_get_markdown_empty(self, session: Session) -> None:
        from deadline_agent.store.identity_repository import IdentityRepository

        repo = IdentityRepository(session)
        assert repo.get_markdown() == ""

    def test_get_markdown(self, session: Session) -> None:
        from deadline_agent.store.identity_repository import IdentityRepository

        repo = IdentityRepository(session)
        repo.upsert(document_json="{}", document_markdown="## My Identity")
        assert repo.get_markdown() == "## My Identity"


class TestIdentitySynthesizer:
    def test_compute_analytics(self, session: Session) -> None:
        from deadline_agent.memory.identity_synthesizer import _compute_analytics

        patterns = [
            BehavioralPattern(
                pattern_type="peak_hours",
                pattern_key="19-21",
                value=json.dumps({"label": "7-9 PM", "total_count": 320, "rank": "primary"}),
                sample_count=50,
                confidence=0.9,
            ),
            BehavioralPattern(
                pattern_type="effort_accuracy",
                pattern_key="exam",
                value=json.dumps({"ratio": 1.8}),
                sample_count=10,
                confidence=0.7,
            ),
            BehavioralPattern(
                pattern_type="procrastination",
                pattern_key="assignment",
                value=json.dumps({"mean_days_before_deadline": 1.2}),
                sample_count=15,
                confidence=0.8,
            ),
        ]

        analytics = _compute_analytics(patterns, [], [], {})

        assert analytics["work_rhythms"]["peak_windows"][0]["label"] == "7-9 PM"
        assert analytics["effort_estimation"]["blind_spots"][0]["ratio"] == 1.8
        assert analytics["procrastination_profile"]["tendencies"][0]["days_before"] == 1.2

    def test_fallback_markdown(self, session: Session) -> None:
        from deadline_agent.memory.identity_synthesizer import _fallback_markdown

        analytics = {
            "work_rhythms": {
                "peak_windows": [{"label": "7-9 PM", "count": 320, "rank": "Primary"}],
                "session_patterns": [{"type": "assignment", "avg_minutes": 47}],
                "completion_rate": 0.85,
                "weeks_of_data": 12,
            },
            "effort_estimation": {
                "blind_spots": [{"type": "exam", "ratio": 1.8, "description": "Underestimate exams by 1.8x"}],
                "accurate": [],
            },
            "procrastination_profile": {
                "tendencies": [{"type": "assignment", "days_before": 1.2}],
            },
            "stress_responses": {"late_night_weeks": 3, "zero_activity_weeks": 1, "sample_weeks": 12},
            "growth_trajectory": {"semesters": []},
            "knowledge_graph": {"by_type": {"course": ["CS 101"]}},
        }

        md = _fallback_markdown(analytics)
        assert "## Work Rhythms" in md
        assert "7-9 PM" in md
        assert "Underestimate exams by 1.8x" in md
        assert "CS 101" in md

    @pytest.mark.asyncio
    async def test_synthesize_identity_with_mock_llm(self, session: Session) -> None:
        from deadline_agent.memory.identity_synthesizer import synthesize_identity

        # Add some patterns so synthesis has data
        session.add(BehavioralPattern(
            pattern_type="peak_hours",
            pattern_key="19-21",
            value=json.dumps({"label": "7-9 PM", "total_count": 100, "rank": "primary"}),
            sample_count=20,
            confidence=0.8,
        ))
        session.flush()

        with patch("deadline_agent.reasoning.engine.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "## Work Rhythms\nYour peak window is 7-9 PM."

            # Also need to patch settings to avoid export path issues
            with patch("deadline_agent.memory.identity_synthesizer.settings") as mock_settings:
                mock_settings.identity_export_path = ""

                doc = await synthesize_identity(session)

        assert doc.version == 1
        assert "7-9 PM" in doc.document_markdown
        assert doc.document_json != "{}"


class TestStateInjection:
    def test_identity_in_prompt(self) -> None:
        from deadline_agent.reasoning.state import StateSnapshot

        snapshot = StateSnapshot(
            now=datetime.now(UTC),
            identity_context="## Work Rhythms\nYour peak is 7-9 PM.",
        )
        prompt = snapshot.to_prompt()
        assert "ABOUT YOU (durable identity):" in prompt
        assert "Your peak is 7-9 PM" in prompt

    def test_no_identity_section_when_empty(self) -> None:
        from deadline_agent.reasoning.state import StateSnapshot

        snapshot = StateSnapshot(now=datetime.now(UTC))
        prompt = snapshot.to_prompt()
        assert "ABOUT YOU" not in prompt
