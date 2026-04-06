"""Entry points for the UI applications."""

from deadline_agent.ui.menubar import DeadlineMenubarApp


def run_menubar() -> None:
    """Start the menubar application (legacy)."""
    app = DeadlineMenubarApp()
    app.run()


def run_widget() -> None:
    """Start the floating desktop widget."""
    from deadline_agent.ui.widget.window import run_widget as _run

    _run()
