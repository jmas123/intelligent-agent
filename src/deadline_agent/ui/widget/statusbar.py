"""Minimal macOS status bar item via PyObjC (replaces rumps menubar)."""
# ruff: noqa: N802  — Objective-C method names must follow Apple's conventions

import logging

import objc
from AppKit import (
    NSApplication,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)

logger = logging.getLogger(__name__)

_status_item = None
_toggle_callback = None


def create_status_item(toggle_fn: object, quit_fn: object) -> None:
    """Create an NSStatusItem in the macOS menu bar.

    Args:
        toggle_fn: Called when the user clicks the status bar item or "Toggle Widget".
        quit_fn: Called when the user clicks "Quit".
    """
    global _status_item, _toggle_callback
    _toggle_callback = toggle_fn

    status_bar = NSStatusBar.systemStatusBar()
    _status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    _status_item.setTitle_("DA")
    _status_item.setHighlightMode_(True)

    menu = NSMenu.alloc().init()

    toggle_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Toggle Widget", "toggleWidget:", ""
    )
    toggle_item.setTarget_(_StatusBarDelegate.sharedDelegate())
    menu.addItem_(toggle_item)

    menu.addItem_(NSMenuItem.separatorItem())

    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit", "quitApp:", ""
    )
    quit_item.setTarget_(_StatusBarDelegate.sharedDelegate())
    menu.addItem_(quit_item)

    _status_item.setMenu_(menu)

    # Store callbacks on delegate
    delegate = _StatusBarDelegate.sharedDelegate()
    delegate._toggle_fn = toggle_fn
    delegate._quit_fn = quit_fn


def update_title(title: str) -> None:
    """Update the status bar item title (e.g., task count)."""
    if _status_item is not None:
        _status_item.setTitle_(title)


class _StatusBarDelegate(objc.lookUpClass("NSObject")):  # type: ignore[misc]
    """Delegate handling status bar menu actions."""

    _instance = None
    _toggle_fn = None
    _quit_fn = None

    @classmethod
    def sharedDelegate(cls) -> "_StatusBarDelegate":
        if cls._instance is None:
            cls._instance = cls.alloc().init()
        return cls._instance

    @objc.typedSelector(b"v@:@")  # type: ignore[misc]
    def toggleWidget_(self, sender: object) -> None:  # type: ignore[override]
        if self._toggle_fn:
            self._toggle_fn()

    @objc.typedSelector(b"v@:@")  # type: ignore[misc]
    def quitApp_(self, sender: object) -> None:  # type: ignore[override]
        if self._quit_fn:
            self._quit_fn()
        NSApplication.sharedApplication().terminate_(None)
