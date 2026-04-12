"""Tests for tiered notification routing."""

import asyncio
from unittest.mock import patch

import pytest

from deadline_agent.notifications.channels import (
    Notification,
    NotificationPriority,
    NotificationRouter,
)


@pytest.fixture
def router():
    return NotificationRouter()


@pytest.mark.asyncio
async def test_high_priority_sends_with_sound(router):
    with patch("deadline_agent.notifications.macos.send_notification", return_value=True) as mock:
        result = await router.send(
            Notification(title="Alert", body="Test", priority=NotificationPriority.HIGH)
        )
        assert result is True
        mock.assert_called_once_with(title="Alert", body="Test", subtitle="", sound=True)


@pytest.mark.asyncio
async def test_low_priority_sends_without_sound(router):
    with patch("deadline_agent.notifications.macos.send_notification", return_value=True) as mock:
        result = await router.send(
            Notification(title="Info", body="Test", priority=NotificationPriority.LOW)
        )
        assert result is True
        mock.assert_called_once_with(title="Info", body="Test", subtitle="", sound=False)


@pytest.mark.asyncio
async def test_ambient_does_not_call_os(router):
    with patch("deadline_agent.notifications.macos.send_notification") as mock:
        result = await router.send(
            Notification(title="Background", body="Test", priority=NotificationPriority.AMBIENT)
        )
        assert result is True
        mock.assert_not_called()


@pytest.mark.asyncio
async def test_ambient_queue_populated(router):
    await router.send(
        Notification(
            title="Item 1",
            body="Body 1",
            priority=NotificationPriority.AMBIENT,
            category="session",
        )
    )
    queue = router.get_ambient_queue()
    assert len(queue) == 1
    assert queue[0]["title"] == "Item 1"
    assert queue[0]["category"] == "session"


@pytest.mark.asyncio
async def test_ambient_queue_max_size(router):
    router._max_ambient = 3
    for i in range(5):
        await router.send(
            Notification(title=f"Item {i}", body="", priority=NotificationPriority.AMBIENT)
        )
    queue = router.get_ambient_queue()
    assert len(queue) == 3
    assert queue[0]["title"] == "Item 2"  # Oldest trimmed


@pytest.mark.asyncio
async def test_clear_ambient(router):
    await router.send(
        Notification(title="Test", body="", priority=NotificationPriority.AMBIENT)
    )
    assert len(router.get_ambient_queue()) == 1
    router.clear_ambient()
    assert len(router.get_ambient_queue()) == 0


@pytest.mark.asyncio
async def test_high_priority_also_adds_to_ambient(router):
    with patch("deadline_agent.notifications.macos.send_notification", return_value=True):
        await router.send(
            Notification(title="Urgent", body="Test", priority=NotificationPriority.HIGH)
        )
    queue = router.get_ambient_queue()
    assert len(queue) == 1
    assert queue[0]["priority"] == "high"
