"""Tests for near-duplicate detection."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.store.dedup import cosine_similarity, find_near_duplicates


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector(self) -> None:
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        assert cosine_similarity(a, b) == 0.0

    def test_similar_vectors(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [1.1, 2.1, 3.1]
        score = cosine_similarity(a, b)
        assert score > 0.99


class TestFindNearDuplicates:
    def test_finds_similar_task(self, session: Session) -> None:
        embedding = [1.0, 0.0, 0.0]
        task = Task(
            title="Submit HW3",
            source="gmail",
            type="assignment",
            urgency_score=3,
            confidence=0.9,
            raw_hash="existing-hash",
            embedding=json.dumps([0.99, 0.1, 0.0]),
        )
        session.add(task)
        session.commit()

        matches = find_near_duplicates(session, embedding, threshold=0.9)
        assert len(matches) == 1
        assert matches[0][0].title == "Submit HW3"
        assert matches[0][1] > 0.9

    def test_ignores_dissimilar(self, session: Session) -> None:
        task = Task(
            title="Unrelated task",
            source="gmail",
            type="assignment",
            urgency_score=1,
            confidence=0.9,
            raw_hash="other-hash",
            embedding=json.dumps([0.0, 0.0, 1.0]),
        )
        session.add(task)
        session.commit()

        matches = find_near_duplicates(session, [1.0, 0.0, 0.0], threshold=0.85)
        assert len(matches) == 0

    def test_ignores_dismissed_tasks(self, session: Session) -> None:
        task = Task(
            title="Dismissed task",
            source="gmail",
            type="assignment",
            urgency_score=1,
            confidence=0.9,
            raw_hash="dismissed-hash",
            status="dismissed",
            embedding=json.dumps([1.0, 0.0, 0.0]),
        )
        session.add(task)
        session.commit()

        matches = find_near_duplicates(session, [1.0, 0.0, 0.0], threshold=0.5)
        assert len(matches) == 0

    def test_no_tasks_returns_empty(self, session: Session) -> None:
        matches = find_near_duplicates(session, [1.0, 0.0, 0.0])
        assert matches == []


class TestGenerateEmbedding:
    @pytest.mark.asyncio
    async def test_generates_embedding(self) -> None:
        mock_response = AsyncMock()
        mock_response.embeddings = [[0.1, 0.2, 0.3]]

        with patch("deadline_agent.store.dedup.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.embed.return_value = mock_response
            mock_cls.return_value = mock_client

            from deadline_agent.store.dedup import generate_embedding

            result = await generate_embedding("test text")

        assert result == [0.1, 0.2, 0.3]
