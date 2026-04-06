"""Tests for macOS notification delivery."""

import subprocess
from unittest.mock import patch

from deadline_agent.notifications.macos import _escape_applescript, send_notification


class TestEscapeApplescript:
    def test_escapes_quotes(self) -> None:
        assert _escape_applescript('say "hello"') == 'say \\"hello\\"'

    def test_escapes_backslash(self) -> None:
        assert _escape_applescript("path\\to") == "path\\\\to"

    def test_plain_text_unchanged(self) -> None:
        assert _escape_applescript("Hello World") == "Hello World"


class TestSendNotification:
    @patch("deadline_agent.notifications.macos.platform.system", return_value="Darwin")
    @patch("deadline_agent.notifications.macos.subprocess.run")
    def test_sends_notification(self, mock_run: object, mock_system: object) -> None:
        result = send_notification("Test Title", "Test body")
        assert result is True

    @patch("deadline_agent.notifications.macos.platform.system", return_value="Darwin")
    @patch("deadline_agent.notifications.macos.subprocess.run")
    def test_includes_subtitle(self, mock_run: object, mock_system: object) -> None:
        send_notification("Title", "Body", subtitle="Sub")

        args = mock_run.call_args  # type: ignore[union-attr]
        script = args[0][0][2]  # osascript -e <script>
        assert 'subtitle "Sub"' in script

    @patch("deadline_agent.notifications.macos.platform.system", return_value="Darwin")
    @patch("deadline_agent.notifications.macos.subprocess.run")
    def test_no_sound(self, mock_run: object, mock_system: object) -> None:
        send_notification("Title", "Body", sound=False)
        args = mock_run.call_args  # type: ignore[union-attr]
        script = args[0][0][2]
        assert "sound name" not in script

    @patch("deadline_agent.notifications.macos.platform.system", return_value="Linux")
    def test_non_macos_returns_false(self, mock_system: object) -> None:
        result = send_notification("Title", "Body")
        assert result is False

    @patch("deadline_agent.notifications.macos.platform.system", return_value="Darwin")
    @patch(
        "deadline_agent.notifications.macos.subprocess.run",
        side_effect=subprocess.TimeoutExpired("osascript", 5),
    )
    def test_timeout_returns_false(self, mock_run: object, mock_system: object) -> None:
        result = send_notification("Title", "Body")
        assert result is False
