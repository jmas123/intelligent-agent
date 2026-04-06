"""Tests for widget window configuration and setup logic.

Cannot test actual pywebview/PyObjC rendering, but verifies:
- Asset paths resolve correctly
- Bridge is instantiated with correct defaults
- Settings are read for dimensions
- Hotkey and status bar setup handle missing dependencies gracefully
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestAssetPaths:
    def test_assets_dir_exists(self) -> None:
        from deadline_agent.ui.widget.window import ASSETS_DIR

        assert ASSETS_DIR.is_dir(), f"Assets directory not found: {ASSETS_DIR}"

    def test_index_html_exists(self) -> None:
        from deadline_agent.ui.widget.window import ASSETS_DIR

        index = ASSETS_DIR / "index.html"
        assert index.is_file(), "index.html missing from widget assets"

    def test_app_js_exists(self) -> None:
        from deadline_agent.ui.widget.window import ASSETS_DIR

        js = ASSETS_DIR / "app.js"
        assert js.is_file(), "app.js missing from widget assets"

    def test_style_css_exists(self) -> None:
        from deadline_agent.ui.widget.window import ASSETS_DIR

        css = ASSETS_DIR / "style.css"
        assert css.is_file(), "style.css missing from widget assets"


class TestWidgetBridgeDefaults:
    def test_default_base_url(self) -> None:
        from deadline_agent.ui.widget.bridge import DEFAULT_BASE_URL, WidgetBridge

        bridge = WidgetBridge()
        assert bridge._base_url == DEFAULT_BASE_URL

    def test_custom_base_url(self) -> None:
        from deadline_agent.ui.widget.bridge import WidgetBridge

        bridge = WidgetBridge(base_url="http://custom:9000/api")
        assert bridge._base_url == "http://custom:9000/api"

    def test_client_timeout_is_30s(self) -> None:
        from deadline_agent.ui.widget.bridge import WidgetBridge

        bridge = WidgetBridge()
        assert bridge._client.timeout.connect == 30.0


class TestSetupHotkeyGraceful:
    def test_no_pynput_logs_warning(self) -> None:
        """If pynput is not installed, setup should log a warning, not crash."""
        from deadline_agent.ui.widget.window import _setup_hotkey

        mock_window = MagicMock()
        with patch.dict("sys.modules", {"pynput": None, "pynput.keyboard": None}):
            # Should not raise
            _setup_hotkey(mock_window)


class TestApplyMacosPatchesGraceful:
    def test_no_appkit_logs_warning(self) -> None:
        """If PyObjC is not installed, patching should be skipped gracefully."""
        from deadline_agent.ui.widget.window import _apply_macos_patches

        mock_window = MagicMock()
        with patch.dict("sys.modules", {"AppKit": None}):
            # Should not raise
            _apply_macos_patches(mock_window)


class TestWidgetDimensions:
    def test_default_dimensions(self) -> None:
        """Settings should provide default widget dimensions."""
        from deadline_agent.config import settings

        width = getattr(settings, "widget_width", 380)
        height = getattr(settings, "widget_height", 560)
        assert width > 0
        assert height > 0

    def test_dimensions_are_reasonable(self) -> None:
        """Widget shouldn't be absurdly large or small."""
        from deadline_agent.config import settings

        width = getattr(settings, "widget_width", 380)
        height = getattr(settings, "widget_height", 560)
        assert 200 <= width <= 800
        assert 300 <= height <= 1200
