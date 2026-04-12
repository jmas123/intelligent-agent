"""Tests for focus quality measurement."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.awareness.focus_quality import (
    analyze_focus_quality,
    analyze_session_fragmentation,
    compute_inter_file_intervals,
)
from deadline_agent.models import FileActivity, WorkSession


def _add_file_activity(
    session: Session,
    path: str,
    size_bytes: int,
    minutes_ago: int,
    event_type: str = "modified",
) -> FileActivity:
    now = datetime.now(UTC)
    ts = now - timedelta(minutes=minutes_ago)
    fa = FileActivity(
        path=path,
        filename=path.split("/")[-1],
        directory="/".join(path.split("/")[:-1]),
        size_bytes=size_bytes,
        modified_at=ts,
        event_type=event_type,
        created_at=ts,
    )
    session.add(fa)
    session.flush()
    return fa


class TestFocusQuality:
    def test_stuck_signal(self, session: Session) -> None:
        """Many saves with little size change should produce a stuck signal."""
        for i in range(8):
            _add_file_activity(session, "/home/user/essay.docx", 1000 + i, 60 - i * 5)
        session.commit()

        results = analyze_focus_quality(session)
        assert len(results) == 1
        filename, status = results[0]
        assert filename == "essay.docx"
        assert status == "stuck"

    def test_productive_signal(self, session: Session) -> None:
        """Large size changes per save should indicate productive work."""
        for i in range(6):
            _add_file_activity(
                session, "/home/user/code.py", 1000 + i * 2000, 60 - i * 5
            )
        session.commit()

        results = analyze_focus_quality(session)
        assert len(results) == 1
        assert results[0][1] == "productive"

    def test_too_few_edits_skipped(self, session: Session) -> None:
        """Files with < 3 edits should be skipped."""
        _add_file_activity(session, "/home/user/notes.txt", 500, 30)
        _add_file_activity(session, "/home/user/notes.txt", 510, 20)
        session.commit()

        results = analyze_focus_quality(session)
        assert len(results) == 0

    def test_no_activity_returns_empty(self, session: Session) -> None:
        results = analyze_focus_quality(session)
        assert results == []


class TestSessionFragmentation:
    def test_no_sessions_returns_empty(self, session: Session) -> None:
        results = analyze_session_fragmentation(session)
        assert results == []

    def test_fragmented_session(self, session: Session) -> None:
        """Session with many unique files should show fragmentation."""
        now = datetime.now(UTC)
        start = now - timedelta(minutes=30)
        end = now - timedelta(minutes=1)

        ws = WorkSession(
            task_id=1,
            started_at=start,
            ended_at=end,
            duration_minutes=29,
            file_activity_count=10,
        )
        session.add(ws)

        # Add file activity during the session
        for i in range(10):
            ts = start + timedelta(minutes=i * 2)
            fa = FileActivity(
                path=f"/home/user/file{i}.py",
                filename=f"file{i}.py",
                directory="/home/user",
                size_bytes=1000,
                modified_at=ts,
                event_type="modified",
                created_at=ts,
            )
            session.add(fa)

        session.commit()

        results = analyze_session_fragmentation(session)
        assert len(results) == 1
        assert results[0][1] > 0  # Non-zero fragmentation score


class TestInterFileIntervals:
    def test_computes_intervals(self, session: Session) -> None:
        """Should compute average time between file switches."""
        _add_file_activity(session, "/home/user/a.py", 100, 30)
        _add_file_activity(session, "/home/user/b.py", 200, 25)
        _add_file_activity(session, "/home/user/c.py", 300, 20)
        session.commit()

        result = compute_inter_file_intervals(session)
        assert "avg_interval_seconds" in result
        assert result["switch_count"] == 2

    def test_no_activity_returns_empty(self, session: Session) -> None:
        result = compute_inter_file_intervals(session)
        assert result == {}
