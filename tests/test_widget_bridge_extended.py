"""Extended bridge tests covering edge cases missing from test_widget_bridge.py.

Covers: 409 conflict handling, approve with time overrides, weekly review,
HTTP 500 errors, timeout behavior, and get_tasks default params.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from deadline_agent.ui.widget.bridge import WidgetBridge


@pytest.fixture
def bridge() -> WidgetBridge:
    return WidgetBridge(base_url="http://test:8000/api/chat")


# ---------------------------------------------------------------------------
# approve_action edge cases
# ---------------------------------------------------------------------------


class TestApproveActionEdgeCases:
    def test_409_conflict_returns_alternatives(self, bridge: WidgetBridge) -> None:
        """Widget should return conflict data with alternatives on 409."""
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.json.return_value = {
            "detail": {
                "message": "Time slot already booked",
                "negotiation_session_id": 1,
                "alternatives": [{"action_id": 2, "start_iso": "2026-04-10T16:00:00"}],
            }
        }

        with patch.object(bridge._client, "post", return_value=mock_resp):
            result = bridge.approve_action(1)
            assert result["conflict"] is True
            assert result["message"] == "Time slot already booked"
            assert result["negotiation_session_id"] == 1
            assert len(result["alternatives"]) == 1

    def test_409_conflict_fallback_format(self, bridge: WidgetBridge) -> None:
        """Widget handles old-style 409 (plain string detail) gracefully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.json.return_value = {"detail": "Time slot already booked"}

        with patch.object(bridge._client, "post", return_value=mock_resp):
            result = bridge.approve_action(1)
            assert result["conflict"] is True
            assert result["alternatives"] == []

    def test_with_time_overrides(self, bridge: WidgetBridge) -> None:
        """User edits start time and duration before approving in the widget."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 1, "status": "executed"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "post", return_value=mock_resp) as mock_post:
            result = bridge.approve_action(
                1,
                start_iso="2026-04-01T10:00:00",
                duration_minutes=90,
            )
            call_kwargs = mock_post.call_args
            body = call_kwargs[1]["json"]
            assert body["start_iso"] == "2026-04-01T10:00:00"
            assert body["duration_minutes"] == 90
            assert result["status"] == "executed"

    def test_with_only_start_override(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 1, "status": "executed"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "post", return_value=mock_resp) as mock_post:
            bridge.approve_action(1, start_iso="2026-04-01T14:00:00")
            body = mock_post.call_args[1]["json"]
            assert body["start_iso"] == "2026-04-01T14:00:00"
            assert "duration_minutes" not in body

    def test_without_overrides_no_json_body(self, bridge: WidgetBridge) -> None:
        """When no overrides, POST should not include a JSON body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 1, "status": "executed"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "post", return_value=mock_resp) as mock_post:
            bridge.approve_action(1)
            call_args = mock_post.call_args
            assert "json" not in call_args[1]

    def test_connection_error(self, bridge: WidgetBridge) -> None:
        with patch.object(
            bridge._client, "post", side_effect=httpx.ConnectError("refused")
        ):
            result = bridge.approve_action(1)
            assert "error" in result


# ---------------------------------------------------------------------------
# get_weekly_review
# ---------------------------------------------------------------------------


class TestGetWeeklyReview:
    def test_success(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"review": "This week you completed 5 tasks."}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp) as mock_get:
            result = bridge.get_weekly_review()
            mock_get.assert_called_once_with("/weekly-review", timeout=120.0)
            assert "review" in result

    def test_timeout_error(self, bridge: WidgetBridge) -> None:
        with patch.object(
            bridge._client, "get", side_effect=httpx.ReadTimeout("timed out")
        ):
            result = bridge.get_weekly_review()
            assert "error" in result


# ---------------------------------------------------------------------------
# HTTP 500 / server errors
# ---------------------------------------------------------------------------


class TestServerErrors:
    """Verify bridge gracefully handles 5xx responses from the backend."""

    def _mock_500(self) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=mock_resp,
        )
        return mock_resp

    def test_get_context_500(self, bridge: WidgetBridge) -> None:
        with patch.object(bridge._client, "get", return_value=self._mock_500()):
            result = bridge.get_context()
            assert "error" in result

    def test_get_tasks_500(self, bridge: WidgetBridge) -> None:
        with patch.object(bridge._client, "get", return_value=self._mock_500()):
            result = bridge.get_tasks()
            assert result == []

    def test_get_insights_500(self, bridge: WidgetBridge) -> None:
        with patch.object(bridge._client, "get", return_value=self._mock_500()):
            result = bridge.get_insights()
            assert result == []

    def test_get_actions_500(self, bridge: WidgetBridge) -> None:
        with patch.object(bridge._client, "get", return_value=self._mock_500()):
            result = bridge.get_actions()
            assert result == []

    def test_get_digest_500(self, bridge: WidgetBridge) -> None:
        with patch.object(bridge._client, "get", return_value=self._mock_500()):
            result = bridge.get_digest()
            assert "error" in result

    def test_update_task_500(self, bridge: WidgetBridge) -> None:
        with patch.object(bridge._client, "patch", return_value=self._mock_500()):
            result = bridge.update_task_status(1, "done")
            assert "error" in result

    def test_ask_question_500(self, bridge: WidgetBridge) -> None:
        with patch.object(bridge._client, "post", return_value=self._mock_500()):
            result = bridge.ask_question("hello")
            assert "error" in result


# ---------------------------------------------------------------------------
# get_tasks parameter construction
# ---------------------------------------------------------------------------


class TestGetTasksParams:
    def test_default_params(self, bridge: WidgetBridge) -> None:
        """Only limit should be sent when no filters are set."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp) as mock_get:
            bridge.get_tasks()
            params = mock_get.call_args[1]["params"]
            assert params == {"limit": 50}

    def test_due_this_week_param(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp) as mock_get:
            bridge.get_tasks(due_this_week=True)
            params = mock_get.call_args[1]["params"]
            assert params["due_this_week"] is True

    def test_all_filters_combined(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp) as mock_get:
            bridge.get_tasks(status="pending", due_today=True, limit=10)
            params = mock_get.call_args[1]["params"]
            assert params["status"] == "pending"
            assert params["due_today"] is True
            assert params["limit"] == 10


# ---------------------------------------------------------------------------
# dismiss_insight error
# ---------------------------------------------------------------------------


class TestDismissInsightError:
    def test_connection_error(self, bridge: WidgetBridge) -> None:
        with patch.object(
            bridge._client, "post", side_effect=httpx.ConnectError("refused")
        ):
            result = bridge.dismiss_insight(42)
            assert "error" in result


# ---------------------------------------------------------------------------
# reject_action error
# ---------------------------------------------------------------------------


class TestRejectActionError:
    def test_connection_error(self, bridge: WidgetBridge) -> None:
        with patch.object(
            bridge._client, "post", side_effect=httpx.ConnectError("refused")
        ):
            result = bridge.reject_action(1)
            assert "error" in result
