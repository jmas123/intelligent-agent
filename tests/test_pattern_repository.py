"""Tests for PatternRepository."""

import json

from deadline_agent.store.pattern_repository import PatternRepository


def test_upsert_creates_new(session):
    """First upsert creates a new pattern."""
    repo = PatternRepository(session)
    p = repo.upsert(
        pattern_type="effort_accuracy",
        pattern_key="assignment",
        value=json.dumps({"ratio": 1.5}),
        sample_count=5,
        confidence=0.5,
    )

    assert p.id is not None
    assert p.pattern_type == "effort_accuracy"
    assert p.pattern_key == "assignment"
    assert p.sample_count == 5
    assert p.confidence == 0.5


def test_upsert_updates_existing(session):
    """Second upsert with same type+key updates rather than duplicating."""
    repo = PatternRepository(session)
    p1 = repo.upsert(
        pattern_type="effort_accuracy",
        pattern_key="assignment",
        value=json.dumps({"ratio": 1.5}),
        sample_count=5,
        confidence=0.5,
    )
    p2 = repo.upsert(
        pattern_type="effort_accuracy",
        pattern_key="assignment",
        value=json.dumps({"ratio": 1.8}),
        sample_count=10,
        confidence=1.0,
    )

    assert p2.id == p1.id
    assert json.loads(p2.value)["ratio"] == 1.8
    assert p2.sample_count == 10
    assert p2.confidence == 1.0

    # Only one record exists
    assert len(repo.get_all()) == 1


def test_get_by_type(session):
    """Filter patterns by type."""
    repo = PatternRepository(session)
    repo.upsert("effort_accuracy", "assignment", "{}", 5, 0.5)
    repo.upsert("effort_accuracy", "exam", "{}", 3, 0.3)
    repo.upsert("peak_hours", "14", "{}", 100, 1.0)

    effort = repo.get_by_type("effort_accuracy")
    assert len(effort) == 2

    peak = repo.get_by_type("peak_hours")
    assert len(peak) == 1


def test_get_one(session):
    """Get a single pattern by type and key."""
    repo = PatternRepository(session)
    repo.upsert("lead_time", "exam", json.dumps({"mean_hours": 48}), 7, 0.7)

    p = repo.get_one("lead_time", "exam")
    assert p is not None
    assert json.loads(p.value)["mean_hours"] == 48

    assert repo.get_one("lead_time", "nonexistent") is None


def test_get_all(session):
    """Returns all patterns sorted by type then key."""
    repo = PatternRepository(session)
    repo.upsert("peak_hours", "14", "{}", 50, 1.0)
    repo.upsert("effort_accuracy", "exam", "{}", 3, 0.3)
    repo.upsert("effort_accuracy", "assignment", "{}", 5, 0.5)

    all_patterns = repo.get_all()
    assert len(all_patterns) == 3
    # Sorted: effort_accuracy/assignment, effort_accuracy/exam, peak_hours/14
    assert all_patterns[0].pattern_key == "assignment"
    assert all_patterns[1].pattern_key == "exam"
    assert all_patterns[2].pattern_type == "peak_hours"
