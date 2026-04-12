"""Tests for social graph and relationship tracking."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.orm import Session

from deadline_agent.awareness.social_graph import (
    compute_relationship_alerts,
    update_relationship_trends,
)
from deadline_agent.store.relationship_repository import RelationshipRepository


class TestRelationshipRepository:
    def test_upsert_creates(self, session: Session) -> None:
        repo = RelationshipRepository(session)
        rel = repo.upsert("Alice", "email")
        assert rel.id is not None
        assert rel.person == "Alice"
        assert rel.interaction_count == 1

    def test_upsert_increments(self, session: Session) -> None:
        repo = RelationshipRepository(session)
        repo.upsert("Alice", "email")
        rel = repo.upsert("Alice", "email")
        assert rel.interaction_count == 2

    def test_case_insensitive_dedup(self, session: Session) -> None:
        repo = RelationshipRepository(session)
        repo.upsert("Alice Smith", "email")
        rel = repo.upsert("alice smith", "email")
        assert rel.interaction_count == 2

    def test_different_channels_separate(self, session: Session) -> None:
        repo = RelationshipRepository(session)
        r1 = repo.upsert("Bob", "email")
        r2 = repo.upsert("Bob", "calendar")
        assert r1.id != r2.id

    def test_list_stale(self, session: Session) -> None:
        repo = RelationshipRepository(session)
        old_date = datetime.now(UTC) - timedelta(days=20)
        repo.upsert("Old Contact", "email", interaction_at=old_date)
        repo.upsert("Recent Contact", "email", interaction_at=datetime.now(UTC))

        stale = repo.list_stale(days=14)
        assert len(stale) == 1
        assert stale[0].person == "Old Contact"

    def test_list_atrophying(self, session: Session) -> None:
        repo = RelationshipRepository(session)
        rel = repo.upsert("Fading Friend", "email")
        repo.update_trend(rel.id, "atrophying")

        atrophying = repo.list_atrophying()
        assert len(atrophying) == 1


class TestRelationshipAlerts:
    @patch("deadline_agent.config.settings")
    def test_atrophying_generates_alert(self, mock_settings, session: Session) -> None:
        mock_settings.enable_social_graph = True
        repo = RelationshipRepository(session)
        old_date = datetime.now(UTC) - timedelta(days=30)
        rel = repo.upsert("Prof Smith", "email", interaction_at=old_date)
        repo.update_trend(rel.id, "atrophying")

        alerts = compute_relationship_alerts(session)
        assert len(alerts) >= 1
        assert "Prof Smith" in alerts[0]

    def test_no_alerts_for_active(self, session: Session) -> None:
        repo = RelationshipRepository(session)
        repo.upsert("Active Contact", "email", interaction_at=datetime.now(UTC))

        alerts = compute_relationship_alerts(session)
        assert len(alerts) == 0


class TestTrendUpdates:
    @patch("deadline_agent.config.settings")
    def test_old_frequent_becomes_atrophying(self, mock_settings, session: Session) -> None:
        mock_settings.enable_social_graph = True
        repo = RelationshipRepository(session)
        old_date = datetime.now(UTC) - timedelta(days=25)
        rel = repo.upsert("Old Friend", "email", interaction_at=old_date)
        # Simulate prior interactions
        rel.interaction_count = 10
        session.commit()

        updated = update_relationship_trends(session)
        assert updated >= 1

        refreshed = repo.get_by_person("Old Friend", "email")
        assert refreshed is not None
        assert refreshed.trend == "atrophying"

    @patch("deadline_agent.config.settings")
    def test_recent_frequent_becomes_growing(self, mock_settings, session: Session) -> None:
        mock_settings.enable_social_graph = True
        repo = RelationshipRepository(session)
        recent = datetime.now(UTC) - timedelta(days=2)
        rel = repo.upsert("New Friend", "email", interaction_at=recent)
        rel.interaction_count = 8
        session.commit()

        update_relationship_trends(session)

        refreshed = repo.get_by_person("New Friend", "email")
        assert refreshed is not None
        assert refreshed.trend == "growing"
