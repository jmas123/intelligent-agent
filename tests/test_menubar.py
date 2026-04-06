"""Tests for the menubar app."""

from datetime import UTC

from deadline_agent.ui.menubar import _relative_due


class TestRelativeDue:
    def test_none_returns_no_date(self) -> None:
        assert _relative_due(None) == "no date"

    def test_invalid_string(self) -> None:
        assert _relative_due("not-a-date") == "not-a-date"

    def test_today(self) -> None:
        from datetime import datetime

        now = datetime.now(UTC)
        result = _relative_due(now.isoformat())
        assert result == "today"

    def test_tomorrow(self) -> None:
        from datetime import datetime, timedelta

        tomorrow = datetime.now(UTC) + timedelta(days=1)
        result = _relative_due(tomorrow.isoformat())
        assert result == "tomorrow"

    def test_past_due(self) -> None:
        from datetime import datetime, timedelta

        past = datetime.now(UTC) - timedelta(days=2)
        result = _relative_due(past.isoformat())
        assert result == "overdue"

    def test_next_week(self) -> None:
        from datetime import datetime, timedelta

        future = datetime.now(UTC) + timedelta(days=5)
        result = _relative_due(future.isoformat())
        assert result == future.strftime("%A")
