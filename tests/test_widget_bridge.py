"""Tests for the widget bridge API client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from deadline_agent.ui.widget.bridge import WidgetBridge


@pytest.fixture
def bridge() -> WidgetBridge:
    return WidgetBridge(base_url="http://test:8000/api/chat")


class TestGetContext:
    def test_success(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"now": "2026-01-01T00:00:00", "overdue_tasks": []}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp) as mock_get:
            result = bridge.get_context()
            mock_get.assert_called_once_with("/context")
            assert result["now"] == "2026-01-01T00:00:00"

    def test_connection_error(self, bridge: WidgetBridge) -> None:
        with patch.object(
            bridge._client, "get", side_effect=httpx.ConnectError("refused")
        ):
            result = bridge.get_context()
            assert "error" in result


class TestGetTasks:
    def test_with_filters(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1, "title": "Test"}]
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp) as mock_get:
            result = bridge.get_tasks(status="pending", due_today=True, limit=10)
            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args
            assert call_kwargs[1]["params"]["status"] == "pending"
            assert call_kwargs[1]["params"]["due_today"] is True
            assert len(result) == 1

    def test_error_returns_empty(self, bridge: WidgetBridge) -> None:
        with patch.object(
            bridge._client, "get", side_effect=httpx.ConnectError("refused")
        ):
            result = bridge.get_tasks()
            assert result == []


class TestUpdateTaskStatus:
    def test_mark_done(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1, "status": "done"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "patch", return_value=mock_resp) as mock_patch:
            result = bridge.update_task_status(1, "done")
            mock_patch.assert_called_once_with(
                "/tasks/1/status", json={"status": "done"}
            )
            assert result["status"] == "done"


class TestAskQuestion:
    def test_success(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"answer": "You have 3 tasks due today."}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "post", return_value=mock_resp) as mock_post:
            result = bridge.ask_question("What's due today?")
            mock_post.assert_called_once_with(
                "/query",
                json={"question": "What's due today?"},
                timeout=120.0,
            )
            assert "answer" in result


class TestGetDigest:
    def test_success(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"text": "Morning digest...", "task_count": 5}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp):
            result = bridge.get_digest()
            assert result["task_count"] == 5


class TestInsights:
    def test_get_insights(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1, "type": "suggestion", "content": "Test"}]
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp):
            result = bridge.get_insights()
            assert len(result) == 1

    def test_dismiss_insight(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1, "dismissed": True}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "post", return_value=mock_resp) as mock_post:
            result = bridge.dismiss_insight(1)
            mock_post.assert_called_once_with("/insights/1/dismiss")
            assert result["dismissed"] is True


class TestActions:
    def test_get_actions(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1, "type": "calendar_block"}]
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "get", return_value=mock_resp):
            result = bridge.get_actions()
            assert len(result) == 1

    def test_approve_action(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1, "status": "executed"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "post", return_value=mock_resp) as mock_post:
            result = bridge.approve_action(1)
            mock_post.assert_called_once_with("/actions/1/approve")
            assert result["status"] == "executed"

    def test_reject_action(self, bridge: WidgetBridge) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1, "status": "rejected"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(bridge._client, "post", return_value=mock_resp) as mock_post:
            result = bridge.reject_action(1)
            mock_post.assert_called_once_with("/actions/1/reject")
            assert result["status"] == "rejected"
