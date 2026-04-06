"""pywebview window setup with PyObjC patches for overlay behavior."""

import logging
from pathlib import Path

import webview  # type: ignore[import-untyped]

from deadline_agent.ui.widget.bridge import WidgetBridge

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "assets"


def _apply_macos_patches(window: webview.Window) -> None:  # type: ignore[name-defined]
    """Apply PyObjC patches for all-Spaces, hide-from-Dock, and transparency."""
    try:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSColor,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
        )

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        # pywebview fires shown on a background thread; AppKit calls must
        # happen on the main thread, so we dispatch via callAfter.
        def _apply_window_patches() -> None:
            try:
                for ns_window in app.windows():
                    ns_window.setCollectionBehavior_(
                        NSWindowCollectionBehaviorCanJoinAllSpaces
                        | NSWindowCollectionBehaviorFullScreenAuxiliary
                    )
                    # Apply transparency without the deprecated
                    # drawsTransparentBackground KVO that pywebview uses
                    ns_window.setOpaque_(False)
                    ns_window.setHasShadow_(False)
                    ns_window.setBackgroundColor_(NSColor.clearColor())
                    wk_webview = ns_window.contentView()
                    if wk_webview is not None:
                        wk_webview._setDrawsBackground_(False)
            except Exception:
                logger.debug("Could not apply window patches", exc_info=True)

        def on_shown() -> None:
            from PyObjCTools.AppHelper import callAfter

            callAfter(_apply_window_patches)

        window.events.shown += on_shown
    except ImportError:
        logger.warning("PyObjC not available — skipping macOS window patches")


def _setup_hotkey(window: webview.Window) -> None:  # type: ignore[name-defined]
    """Register global Cmd+Shift+D hotkey to toggle widget visibility."""
    try:
        from pynput import keyboard

        def _on_activate() -> None:
            if window.hidden:
                window.show()
            else:
                window.hide()

        hotkey = keyboard.HotKey(
            keyboard.HotKey.parse("<cmd>+<shift>+d"),
            _on_activate,
        )

        def _for_canonical(f):  # type: ignore[no-untyped-def]
            return lambda k: f(listener.canonical(k))

        listener = keyboard.Listener(
            on_press=_for_canonical(hotkey.press),
            on_release=_for_canonical(hotkey.release),
        )
        listener.daemon = True
        listener.start()
        logger.info("Global hotkey Cmd+Shift+D registered")
    except ImportError:
        logger.warning("pynput not available — global hotkey disabled")
    except Exception:
        logger.warning("Failed to register global hotkey", exc_info=True)


def _setup_status_bar(window: webview.Window) -> None:  # type: ignore[name-defined]
    """Create the menubar status item."""
    try:
        from deadline_agent.ui.widget.statusbar import create_status_item

        def toggle() -> None:
            if window.hidden:
                window.show()
            else:
                window.hide()

        def quit_app() -> None:
            window.destroy()

        create_status_item(toggle, quit_app)
    except ImportError:
        logger.warning("PyObjC not available — status bar disabled")


def run_widget() -> None:
    """Launch the floating widget window."""
    from deadline_agent.config import settings

    bridge = WidgetBridge()

    width = getattr(settings, "widget_width", 380)
    height = getattr(settings, "widget_height", 560)

    window = webview.create_window(
        "Deadline Agent",
        url=str(ASSETS_DIR / "index.html"),
        js_api=bridge,
        width=width,
        height=height,
        frameless=True,
        easy_drag=True,
        on_top=True,
        transparent=False,  # transparency applied in _apply_macos_patches
        text_select=True,
    )

    _apply_macos_patches(window)
    _setup_hotkey(window)

    def on_start() -> None:
        from PyObjCTools.AppHelper import callAfter

        callAfter(_setup_status_bar, window)

    webview.start(func=on_start, debug=False)
