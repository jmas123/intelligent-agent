"""Tests for the macOS status bar module.

Since these tests run in a test environment, we test the module's structure
and delegate logic rather than actual NSStatusBar rendering.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestStatusBarDelegate:
    def test_shared_delegate_is_singleton(self) -> None:
        from deadline_agent.ui.widget.statusbar import _StatusBarDelegate

        d1 = _StatusBarDelegate.sharedDelegate()
        d2 = _StatusBarDelegate.sharedDelegate()
        assert d1 is d2

    def test_toggle_calls_callback(self) -> None:
        from deadline_agent.ui.widget.statusbar import _StatusBarDelegate

        delegate = _StatusBarDelegate.sharedDelegate()
        mock_fn = MagicMock()
        delegate._toggle_fn = mock_fn

        delegate.toggleWidget_(None)
        mock_fn.assert_called_once()

    def test_toggle_noop_without_callback(self) -> None:
        from deadline_agent.ui.widget.statusbar import _StatusBarDelegate

        delegate = _StatusBarDelegate.sharedDelegate()
        delegate._toggle_fn = None
        # Should not raise
        delegate.toggleWidget_(None)

    def test_quit_calls_callback(self) -> None:
        from deadline_agent.ui.widget.statusbar import _StatusBarDelegate

        delegate = _StatusBarDelegate.sharedDelegate()
        mock_fn = MagicMock()
        delegate._quit_fn = mock_fn

        with patch("deadline_agent.ui.widget.statusbar.NSApplication") as mock_app:
            delegate.quitApp_(None)
            mock_fn.assert_called_once()
            mock_app.sharedApplication().terminate_.assert_called_once_with(None)


class TestUpdateTitle:
    def test_updates_when_status_item_exists(self) -> None:
        import deadline_agent.ui.widget.statusbar as sb

        mock_item = MagicMock()
        original = sb._status_item
        try:
            sb._status_item = mock_item
            sb.update_title("DA (3)")
            mock_item.setTitle_.assert_called_once_with("DA (3)")
        finally:
            sb._status_item = original

    def test_noop_when_no_status_item(self) -> None:
        import deadline_agent.ui.widget.statusbar as sb

        original = sb._status_item
        try:
            sb._status_item = None
            # Should not raise
            sb.update_title("DA (3)")
        finally:
            sb._status_item = original


class TestCreateStatusItem:
    def test_delegate_stores_callbacks(self) -> None:
        """Verify that callbacks are stored on the shared delegate.

        We can't call create_status_item() directly in tests because
        NSStatusBar requires a running NSApplication event loop.
        Instead, test the delegate wiring in isolation.
        """
        from deadline_agent.ui.widget.statusbar import _StatusBarDelegate

        delegate = _StatusBarDelegate.sharedDelegate()
        toggle = MagicMock()
        quit_fn = MagicMock()

        delegate._toggle_fn = toggle
        delegate._quit_fn = quit_fn

        assert delegate._toggle_fn is toggle
        assert delegate._quit_fn is quit_fn

        delegate.toggleWidget_(None)
        toggle.assert_called_once()

        with patch("deadline_agent.ui.widget.statusbar.NSApplication") as mock_app:
            delegate.quitApp_(None)
            quit_fn.assert_called_once()
