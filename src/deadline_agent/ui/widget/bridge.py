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

    def get_recruiting_analytics(self) -> dict:  # type: ignore[type-arg]
        """Fetch recruiting analytics data."""
        try:
            resp = self._client.get("/recruiting-analytics")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch recruiting analytics: %s", e)
            return {"error": str(e)}

    # Phase 19: Goals & Decisions

    def get_goals(self) -> list:  # type: ignore[type-arg]
        """Fetch active goals."""
        try:
            resp = self._client.get("/goals")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch goals: %s", e)
            return []

    def create_goal(self, description: str, category: str, target_metric: str = "") -> dict:  # type: ignore[type-arg]
        """Create a new goal."""
        body: dict[str, str] = {"description": description, "category": category}
        if target_metric:
            body["target_metric"] = target_metric
        try:
            resp = self._client.post("/goals", json=body)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to create goal: %s", e)
            return {"error": str(e)}

    def update_goal_status(self, goal_id: int, status: str) -> dict:  # type: ignore[type-arg]
        """Mark a goal as achieved or abandoned."""
        try:
            resp = self._client.patch(f"/goals/{goal_id}/status", json={"status": status})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to update goal %d: %s", goal_id, e)
            return {"error": str(e)}

    def get_decisions(self, limit: int = 10) -> list:  # type: ignore[type-arg]
        """Fetch recent decisions."""
        try:
            resp = self._client.get("/decisions", params={"limit": limit})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch decisions: %s", e)
            return []

    def create_decision(self, description: str, chosen_option: str, alternatives: str = "") -> dict:  # type: ignore[type-arg]
        """Record a decision."""
        body: dict[str, object] = {"description": description, "chosen_option": chosen_option}
        if alternatives:
            body["alternatives"] = [a.strip() for a in alternatives.split(",") if a.strip()]
        try:
            resp = self._client.post("/decisions", json=body)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to create decision: %s", e)
            return {"error": str(e)}

    def record_outcome(self, decision_id: int, outcome: str) -> dict:  # type: ignore[type-arg]
        """Record the outcome of a decision."""
        try:
            resp = self._client.patch(f"/decisions/{decision_id}/outcome", json={"outcome": outcome})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to record outcome %d: %s", decision_id, e)
            return {"error": str(e)}

    # Phase 20: Relationships

    def get_relationships(self, limit: int = 50) -> list:  # type: ignore[type-arg]
        """Fetch tracked relationships."""
        try:
            resp = self._client.get("/relationships", params={"limit": limit})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch relationships: %s", e)
            return []

    # Phase 24: Task affects

    def get_affects(self) -> list:  # type: ignore[type-arg]
        """Fetch inferred task-type affect map."""
        try:
            resp = self._client.get("/affects")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch affects: %s", e)
            return []

    # ── New: Recovery, Scheduling, Identity, Debrief, Life Contexts ──

    def get_recovery_plans(self) -> dict:  # type: ignore[type-arg]
        """Generate recovery plans for overdue/at-risk tasks."""
        try:
            resp = self._client.post("/recovery-plan", json={}, timeout=120.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to get recovery plans: %s", e)
            return {"plans": []}

    def run_simulation(self, scenario: str) -> dict:  # type: ignore[type-arg]
        """Run a behavioral what-if simulation."""
        try:
            resp = self._client.post(
                "/simulate",
                json={"scenario": scenario},
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to run simulation: %s", e)
            return {"error": str(e)}

    def get_calendar_gaps(self) -> list:  # type: ignore[type-arg]
        """Fetch free calendar windows for the next 7 days."""
        try:
            resp = self._client.get("/calendar-gaps")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch calendar gaps: %s", e)
            return []

    def propose_schedule(self) -> list:  # type: ignore[type-arg]
        """Run smart scheduler to propose time blocks."""
        try:
            resp = self._client.post("/propose-schedule", json={}, timeout=120.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to propose schedule: %s", e)
            return []

    def get_identity(self) -> dict:  # type: ignore[type-arg]
        """Fetch the durable identity document."""
        try:
            resp = self._client.get("/identity")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch identity: %s", e)
            return {"error": str(e)}

    def synthesize_identity(self) -> dict:  # type: ignore[type-arg]
        """Trigger identity document re-synthesis."""
        try:
            resp = self._client.post("/identity/synthesize", timeout=120.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to synthesize identity: %s", e)
            return {"error": str(e)}

    def get_knowledge_graph(self) -> dict:  # type: ignore[type-arg]
        """Fetch knowledge graph summary."""
        try:
            resp = self._client.get("/knowledge-graph")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch knowledge graph: %s", e)
            return {"error": str(e)}

    def get_weekly_snapshots(self) -> list:  # type: ignore[type-arg]
        """Fetch recent weekly snapshots."""
        try:
            resp = self._client.get("/weekly-snapshots")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch weekly snapshots: %s", e)
            return []

    def generate_debrief(self, term: str = "") -> dict:  # type: ignore[type-arg]
        """Generate a semester debrief."""
        body: dict[str, str] = {}
        if term:
            body["term"] = term
        try:
            resp = self._client.post("/debrief", json=body, timeout=120.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to generate debrief: %s", e)
            return {"error": str(e)}

    def get_debriefs(self) -> list:  # type: ignore[type-arg]
        """Fetch all stored debriefs."""
        try:
            resp = self._client.get("/debrief")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch debriefs: %s", e)
            return []

    def get_life_contexts(self) -> list:  # type: ignore[type-arg]
        """Fetch active life contexts."""
        try:
            resp = self._client.get("/life-contexts")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch life contexts: %s", e)
            return []

    def set_life_context(
        self, season: str, start_date: str, end_date: str, label: str = ""
    ) -> dict:  # type: ignore[type-arg]
        """Set a manual life context."""
        body: dict[str, str] = {
            "season": season,
            "start_date": start_date,
            "end_date": end_date,
        }
        if label:
            body["label"] = label
        try:
            resp = self._client.post("/life-contexts", json=body)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to set life context: %s", e)
            return {"error": str(e)}

    def deactivate_life_context(self, context_id: int) -> dict:  # type: ignore[type-arg]
        """Deactivate a life context."""
        try:
            resp = self._client.post(f"/life-contexts/{context_id}/deactivate")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to deactivate context %d: %s", context_id, e)
            return {"error": str(e)}

    # Phase 16: Ambient presence

    def get_ambient_notifications(self) -> list:  # type: ignore[type-arg]
        """Fetch ambient (low-priority) notifications for the ticker."""
        try:
            resp = self._client.get("/ambient-notifications")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch ambient notifications: %s", e)
            return []

    def get_current_mode(self) -> dict:  # type: ignore[type-arg]
        """Fetch the current active mode and related context."""
        try:
            resp = self._client.get("/current-mode")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch current mode: %s", e)
            return {}

    def start_voice_input(self) -> dict:  # type: ignore[type-arg]
        """Start recording voice input."""
        try:
            from deadline_agent.voice.recorder import AudioRecorder

            if not hasattr(self, "_recorder"):
                self._recorder = AudioRecorder()
            self._recorder.start_recording()
            return {"status": "recording"}
        except ImportError:
            return {"error": "Voice input requires sounddevice. Install with: pip install sounddevice"}
        except Exception as e:
            logger.error("Voice recording start failed: %s", e)
            return {"error": str(e)}

    def stop_voice_input(self) -> dict:  # type: ignore[type-arg]
        """Stop recording, transcribe, and query the reasoning engine."""
        try:
            if not hasattr(self, "_recorder") or not self._recorder.is_recording():
                return {"error": "Not currently recording"}

            wav_path = self._recorder.stop_recording()

            from deadline_agent.config import settings
            from deadline_agent.voice.transcriber import WhisperTranscriber

            if not hasattr(self, "_transcriber"):
                self._transcriber = WhisperTranscriber(model_name=settings.whisper_model)

            text = self._transcriber.transcribe(wav_path)
            self._recorder.cleanup(wav_path)

            if not text:
                return {"error": "No speech detected"}

            # Query the reasoning engine with transcribed text
            result = self.ask_question(text)
            return {"transcription": text, **result}
        except ImportError:
            return {"error": "Voice input requires openai-whisper. Install with: pip install openai-whisper"}
        except Exception as e:
            logger.error("Voice input failed: %s", e)
            return {"error": str(e)}
