"""Cluely-style floating desktop widget for deadline monitoring."""

__all__ = ["run_widget"]


def run_widget() -> None:
    """Launch the floating widget window."""
    from deadline_agent.ui.widget.window import run_widget as _run

    _run()
