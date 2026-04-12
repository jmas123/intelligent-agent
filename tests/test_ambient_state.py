"""Tests for ambient state tracker."""

from datetime import UTC, datetime, timedelta

from deadline_agent.awareness.ambient_state import AmbientState


def test_initially_idle():
    state = AmbientState()
    assert state.is_idle() is True
    assert state.session_active is False
    assert state.current_mode is None


def test_record_activity_starts_session():
    state = AmbientState(idle_threshold_minutes=30)
    now = datetime.now(UTC)

    session_started, prev_mode = state.record_activity("project", now)
    assert session_started is True
    assert prev_mode is None
    assert state.session_active is True
    assert state.current_mode == "project"


def test_subsequent_activity_no_session_start():
    state = AmbientState(idle_threshold_minutes=30)
    now = datetime.now(UTC)

    state.record_activity("project", now)
    # Second activity 5 min later
    session_started, _ = state.record_activity("project", now + timedelta(minutes=5))
    assert session_started is False


def test_context_switch_detected():
    state = AmbientState(idle_threshold_minutes=30)
    now = datetime.now(UTC)

    state.record_activity("school", now)
    _, prev_mode = state.record_activity("recruiting", now + timedelta(minutes=5))
    assert prev_mode == "school"
    assert state.current_mode == "recruiting"


def test_no_switch_same_mode():
    state = AmbientState(idle_threshold_minutes=30)
    now = datetime.now(UTC)

    state.record_activity("school", now)
    _, prev_mode = state.record_activity("school", now + timedelta(minutes=5))
    assert prev_mode is None


def test_none_track_defaults_to_school():
    state = AmbientState(idle_threshold_minutes=30)
    now = datetime.now(UTC)

    state.record_activity(None, now)
    assert state.current_mode == "school"


def test_idle_after_gap():
    state = AmbientState(idle_threshold_minutes=30)
    now = datetime.now(UTC)

    state.record_activity("school", now - timedelta(minutes=60))
    assert state.is_idle() is True


def test_not_idle_within_threshold():
    state = AmbientState(idle_threshold_minutes=30)
    now = datetime.now(UTC)

    state.record_activity("school", now)
    assert state.is_idle() is False


def test_reset_session():
    state = AmbientState(idle_threshold_minutes=30)
    now = datetime.now(UTC)

    state.record_activity("school", now)
    assert state.session_active is True

    state.reset_session()
    assert state.session_active is False
    assert state.session_started_at is None


def test_session_restart_after_idle():
    state = AmbientState(idle_threshold_minutes=1)
    now = datetime.now(UTC)

    state.record_activity("school", now - timedelta(minutes=5))
    state.reset_session()

    # New activity after idle
    session_started, _ = state.record_activity("recruiting", now)
    assert session_started is True
    assert state.current_mode == "recruiting"
