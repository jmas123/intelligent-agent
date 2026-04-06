"""Python<->JS API bridge for the widget.

Exposes methods to JavaScript via pywebview's JS API bridge.
All methods call the FastAPI chat API endpoints via httpx.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:8000/api/chat"


class WidgetBridge:
    """Bridge between the widget JS frontend and the FastAPI backend."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self._base_url = base_url
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def get_context(self) -> dict:  # type: ignore[type-arg]
        """Fetch full context snapshot (tasks, alerts, insights)."""
        try:
            resp = self._client.get("/context")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch context: %s", e)
            return {"error": str(e)}

    def get_tasks(
        self,
        status: str | None = None,
        due_today: bool = False,
        due_this_week: bool = False,
        limit: int = 50,
    ) -> list:  # type: ignore[type-arg]
        """Fetch tasks with optional filters."""
        params: dict[str, str | int | bool] = {"limit": limit}
        if status:
            params["status"] = status
        if due_today:
            params["due_today"] = True
        if due_this_week:
            params["due_this_week"] = True
        try:
            resp = self._client.get("/tasks", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch tasks: %s", e)
            return []

    def update_task_status(self, task_id: int, status: str) -> dict:  # type: ignore[type-arg]
        """Mark a task as done, dismissed, or pending."""
        try:
            resp = self._client.patch(
                f"/tasks/{task_id}/status",
                json={"status": status},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to update task %d: %s", task_id, e)
            return {"error": str(e)}

    def ask_question(self, question: str) -> dict:  # type: ignore[type-arg]
        """Send a natural language query to the reasoning engine."""
        try:
            resp = self._client.post(
                "/query",
                json={"question": question},
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to query: %s", e)
            return {"error": str(e)}

    def get_digest(self) -> dict:  # type: ignore[type-arg]
        """Fetch the morning digest."""
        try:
            resp = self._client.get("/digest")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch digest: %s", e)
            return {"error": str(e)}

    def get_insights(self) -> list:  # type: ignore[type-arg]
        """Fetch active LLM-generated insights."""
        try:
            resp = self._client.get("/insights")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch insights: %s", e)
            return []

    def dismiss_insight(self, insight_id: int) -> dict:  # type: ignore[type-arg]
        """Dismiss an insight."""
        try:
            resp = self._client.post(f"/insights/{insight_id}/dismiss")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to dismiss insight %d: %s", insight_id, e)
            return {"error": str(e)}

    def get_actions(self) -> list:  # type: ignore[type-arg]
        """Fetch pending proposed actions."""
        try:
            resp = self._client.get("/actions")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch actions: %s", e)
            return []

    def approve_action(
        self,
        action_id: int,
        start_iso: str | None = None,
        duration_minutes: int | None = None,
    ) -> dict:  # type: ignore[type-arg]
        """Approve and execute a proposed action with optional time overrides."""
        body: dict[str, object] = {}
        if start_iso:
            body["start_iso"] = start_iso
        if duration_minutes:
            body["duration_minutes"] = duration_minutes
        try:
            kwargs: dict[str, object] = {}
            if body:
                kwargs["json"] = body
            resp = self._client.post(f"/actions/{action_id}/approve", **kwargs)
            if resp.status_code == 409:
                data = resp.json().get("detail", {})
                if isinstance(data, dict):
                    return {"conflict": True, **data}
                return {"conflict": True, "message": "Time slot already booked", "alternatives": []}
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to approve action %d: %s", action_id, e)
            return {"error": str(e)}

    def reject_action(self, action_id: int) -> dict:  # type: ignore[type-arg]
        """Reject a proposed action."""
        try:
            resp = self._client.post(f"/actions/{action_id}/reject")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to reject action %d: %s", action_id, e)
            return {"error": str(e)}

    def get_weekly_review(self) -> dict:  # type: ignore[type-arg]
        """Fetch the weekly review summary."""
        try:
            resp = self._client.get("/weekly-review", timeout=120.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch weekly review: %s", e)
            return {"error": str(e)}

    def get_patterns(self) -> list:  # type: ignore[type-arg]
        """Fetch learned behavioral patterns."""
        try:
            resp = self._client.get("/patterns")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch patterns: %s", e)
            return []

    def run_analysis(self) -> dict:  # type: ignore[type-arg]
        """Trigger behavioral analysis (session inference + pattern detection)."""
        try:
            resp = self._client.post("/analyze", timeout=120.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to run analysis: %s", e)
            return {"error": str(e)}

    def get_negotiation(self, session_id: int) -> dict:  # type: ignore[type-arg]
        """Fetch a negotiation session's current state."""
        try:
            resp = self._client.get(f"/negotiations/{session_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch negotiation %d: %s", session_id, e)
            return {"error": str(e)}

    def send_negotiation_message(self, session_id: int, message: str) -> dict:  # type: ignore[type-arg]
        """Send a message in an active negotiation session."""
        try:
            resp = self._client.post(
                "/negotiate",
                json={"session_id": session_id, "message": message},
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to negotiate %d: %s", session_id, e)
            return {"error": str(e)}

    def list_negotiations(self, status: str = "active") -> list:  # type: ignore[type-arg]
        """List negotiation sessions."""
        try:
            resp = self._client.get("/negotiations", params={"status": status})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to list negotiations: %s", e)
            return []

    def search_gmail(self, query: str, max_results: int = 10) -> list:  # type: ignore[type-arg]
        """Search Gmail messages."""
        try:
            resp = self._client.post(
                "/search-gmail",
                json={"query": query, "max_results": max_results},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to search Gmail: %s", e)
            return []

    def get_recruiting_pipeline(self) -> dict:  # type: ignore[type-arg]
        """Fetch recruiting pipeline data."""
        try:
            resp = self._client.get("/recruiting-pipeline")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch recruiting pipeline: %s", e)
            return {"error": str(e)}

    def refresh_recruiting_pipeline(self) -> dict:  # type: ignore[type-arg]
        """Trigger recruiting pipeline refresh from existing data."""
        try:
            resp = self._client.post("/recruiting-pipeline/refresh", timeout=60.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to refresh recruiting pipeline: %s", e)
            return {"error": str(e)}
