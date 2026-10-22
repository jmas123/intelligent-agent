"""Rumps-based macOS menubar app for deadline monitoring."""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import rumps

USER_TZ = ZoneInfo("America/New_York")

from deadline_agent.store.action_repository import ActionRepository
from deadline_agent.store.priority_repository import PriorityRepository
from deadline_agent.store.repository import TaskRepository
from deadline_agent.store.risk_repository import RiskRepository
from deadline_agent.store.session import SessionLocal, init_db

logger = logging.getLogger(__name__)


def _relative_due(due_str: str | None) -> str:
    """Convert ISO date to relative label for display."""
    if not due_str:
        return "no date"
    try:
        due = datetime.fromisoformat(due_str)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        due_local = due.astimezone(USER_TZ)
        now_local = datetime.now(USER_TZ)
        delta_days = (due_local.date() - now_local.date()).days
        if delta_days < 0:
            return "overdue"
        if delta_days == 0:
            return "today"
        if delta_days == 1:
            return "tomorrow"
        if delta_days < 7:
            return due_local.strftime("%A")
        return due_local.strftime("%b %d")
    except (ValueError, TypeError):
        return due_str


class DeadlineMenubarApp(rumps.App):  # type: ignore[misc]
    """Menubar app showing upcoming deadlines."""

    def __init__(self) -> None:
        super().__init__("DA", quit_button=None)
        init_db()
        self._build_menu()

    def _build_menu(self) -> None:
        """Rebuild the menu from current database state."""
        self.menu.clear()

        # Proposed actions section
        with SessionLocal() as session:
            action_repo = ActionRepository(session)
            pending_actions = action_repo.list_pending(limit=5)

        if pending_actions:
            self.menu.add(
                rumps.MenuItem(f"Proposed Actions ({len(pending_actions)})", callback=None)
            )
            self.menu.add(rumps.separator)
            for action in pending_actions:
                item = rumps.MenuItem(action.title)
                item.add(
                    rumps.MenuItem(
                        "Approve",
                        callback=self._make_action_callback(action.id, "approve"),
                    )
                )
                item.add(
                    rumps.MenuItem(
                        "Reject",
                        callback=self._make_action_callback(action.id, "reject"),
                    )
                )
                self.menu.add(item)
            self.menu.add(rumps.separator)

        with SessionLocal() as session:
            repo = TaskRepository(session)
            p_repo = PriorityRepository(session)
            r_repo = RiskRepository(session)
            tasks = repo.list_tasks(status="pending", limit=10)

            # Build priority and risk lookup maps
            priority_map: dict[int, int] = {}  # task_id -> rank
            risk_map: dict[int, str] = {}  # task_id -> risk_level
            risk_pct_map: dict[int, int] = {}  # task_id -> P(miss) %
            for ranking, task_obj in p_repo.get_ranked(limit=10):
                priority_map[task_obj.id] = ranking.rank
            for risk, task_obj in r_repo.get_all_active():
                risk_map[task_obj.id] = risk.risk_level
                risk_pct_map[task_obj.id] = int(risk.probability_of_missing * 100)

        # Sort tasks by priority rank (ranked first, then unranked by urgency)
        def _sort_key(t: object) -> tuple[int, int]:
            rank = priority_map.get(t.id, 999)  # type: ignore[union-attr]
            return (rank, -t.urgency_score)  # type: ignore[union-attr]

        tasks.sort(key=_sort_key)

        # Top priority header
        if tasks and tasks[0].id in priority_map:
            top = tasks[0]
            top_due = _relative_due(top.due_date_iso)
            risk_tag = ""
            if top.id in risk_pct_map:
                risk_tag = f" [{risk_pct_map[top.id]}% risk]"
            self.menu.add(rumps.MenuItem(
                f"#1 Priority: {top.title} — {top_due}{risk_tag}",
                callback=None,
            ))
            self.menu.add(rumps.separator)

        self.menu.add(rumps.MenuItem("Upcoming Deadlines", callback=None))
        self.menu.add(rumps.separator)

        if not tasks:
            self.menu.add(rumps.MenuItem("No pending tasks", callback=None))
            self.title = "DA"
        else:
            self.title = f"DA ({len(tasks)})"
            for task in tasks:
                # Build label with priority rank and risk
                rank = priority_map.get(task.id)
                rank_prefix = f"#{rank} " if rank else ""
                risk_level = risk_map.get(task.id, "")
                risk_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(risk_level, "")
                due = _relative_due(task.due_date_iso)
                label = f"{rank_prefix}{risk_icon}{task.title} — {due}"

                item = rumps.MenuItem(label)
                item.add(
                    rumps.MenuItem(
                        "Mark Done",
                        callback=self._make_status_callback(task.id, "done"),
                    )
                )
                item.add(
                    rumps.MenuItem(
                        "Dismiss",
                        callback=self._make_status_callback(task.id, "dismissed"),
                    )
                )
                self.menu.add(item)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Open Chat", callback=self._on_open_chat))
        self.menu.add(rumps.MenuItem("Refresh", callback=self._on_refresh))
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    def _make_status_callback(self, task_id: int, status: str):  # type: ignore[no-untyped-def]
        """Create a callback that updates a task's status."""

        def callback(_: rumps.MenuItem) -> None:
            with SessionLocal() as session:
                repo = TaskRepository(session)
                repo.update_status(task_id, status)
            self._build_menu()
            rumps.notification(
                "Deadline Agent",
                "",
                f"Task marked as {status}.",
            )

        return callback

    def _make_action_callback(self, action_id: int, decision: str):  # type: ignore[no-untyped-def]
        """Create a callback that approves or rejects an action."""

        def callback(_: rumps.MenuItem) -> None:
            import asyncio
            import json

            with SessionLocal() as session:
                repo = ActionRepository(session)
                if decision == "approve":
                    action = repo.get(action_id)
                    if action and action.type == "calendar_block":
                        # Check for conflicts before approving
                        payload = json.loads(action.payload)
                        try:
                            from deadline_agent.reasoning.calendar_gaps import (
                                fetch_free_busy,
                            )

                            busy = asyncio.run(
                                fetch_free_busy(payload["start_iso"], payload["end_iso"])
                            )
                            if busy:
                                # Conflict — find alternatives and show notification
                                from deadline_agent.reasoning.negotiation import (
                                    create_conflict_session,
                                )

                                session_data = asyncio.run(
                                    create_conflict_session(
                                        session, action, payload["start_iso"], payload["end_iso"]
                                    )
                                )
                                alt_count = len(session_data.get("alternatives", []))
                                msg = f"{alt_count} alternative(s) available."
                                rumps.notification(
                                    "Deadline Agent",
                                    "Time conflict detected",
                                    msg,
                                )
                                self._build_menu()
                                return
                        except Exception:
                            pass  # Calendar API unavailable — proceed

                    action = repo.approve(action_id)
                    if action:
                        from deadline_agent.actions.executor import execute_action

                        asyncio.run(execute_action(session, action))
                    msg = "Action approved and executed."
                else:
                    repo.reject(action_id)
                    msg = "Action rejected."
            self._build_menu()
            rumps.notification("Deadline Agent", "", msg)

        return callback

    def _on_open_chat(self, _: rumps.MenuItem) -> None:
        """Open the chat UI in the default browser."""
        import webbrowser

        from deadline_agent.config import settings

        webbrowser.open(settings.openwebui_url)

    def _on_refresh(self, _: rumps.MenuItem) -> None:
        """Refresh the task list."""
        self._build_menu()

    @rumps.timer(60)  # type: ignore[untyped-decorator]
    def _auto_refresh(self, _: object) -> None:
        """Auto-refresh task list periodically."""
        self._build_menu()
