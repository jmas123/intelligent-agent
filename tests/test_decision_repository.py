"""Tests for decision repository."""

from sqlalchemy.orm import Session

from deadline_agent.store.decision_repository import DecisionRepository


class TestDecisionRepository:
    def test_create_and_list(self, session: Session) -> None:
        repo = DecisionRepository(session)
        d = repo.create(
            "Job offer choice",
            "Accept startup offer",
            alternatives=["Big tech", "Startup", "Grad school"],
        )
        assert d.id is not None
        assert d.chosen_option == "Accept startup offer"

        recent = repo.list_recent()
        assert len(recent) == 1

    def test_record_outcome(self, session: Session) -> None:
        repo = DecisionRepository(session)
        d = repo.create("Chose depth over breadth", "Focus on OS project")

        result = repo.record_outcome(d.id, "Completed ahead of deadline")
        assert result is not None
        assert result.outcome == "Completed ahead of deadline"
        assert result.outcome_recorded_at is not None

    def test_record_outcome_nonexistent(self, session: Session) -> None:
        repo = DecisionRepository(session)
        result = repo.record_outcome(999, "some outcome")
        assert result is None

    def test_list_with_outcomes(self, session: Session) -> None:
        repo = DecisionRepository(session)
        d1 = repo.create("Decision A", "Option 1")
        d2 = repo.create("Decision B", "Option 2")

        repo.record_outcome(d1.id, "Good result")

        with_outcomes = repo.list_with_outcomes()
        assert len(with_outcomes) == 1
        assert with_outcomes[0].id == d1.id

    def test_alternatives_stored(self, session: Session) -> None:
        import json

        repo = DecisionRepository(session)
        d = repo.create(
            "Which framework",
            "FastAPI",
            alternatives=["Django", "Flask", "FastAPI"],
        )

        alts = json.loads(d.alternatives_considered)
        assert "Django" in alts
        assert "Flask" in alts
        assert "FastAPI" in alts
