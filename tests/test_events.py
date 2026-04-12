"""Tests for the event bus."""

import pytest

from deadline_agent.events import (
    TASK_CREATED,
    Event,
    EventBus,
)


@pytest.fixture
def bus():
    """Fresh event bus for each test."""
    return EventBus()


@pytest.mark.asyncio
async def test_emit_fires_subscriber(bus):
    """Subscribed handler receives the event."""
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe(TASK_CREATED, handler)
    await bus.emit(Event(type=TASK_CREATED, payload={"task_id": 1}))

    assert len(received) == 1
    assert received[0].payload["task_id"] == 1


@pytest.mark.asyncio
async def test_emit_no_subscribers(bus):
    """Emit with no subscribers does not raise."""
    await bus.emit(Event(type=TASK_CREATED))


@pytest.mark.asyncio
async def test_multiple_subscribers(bus):
    """Multiple handlers all receive the event."""
    counts = {"a": 0, "b": 0}

    async def handler_a(event: Event):
        counts["a"] += 1

    async def handler_b(event: Event):
        counts["b"] += 1

    bus.subscribe(TASK_CREATED, handler_a)
    bus.subscribe(TASK_CREATED, handler_b)
    await bus.emit(Event(type=TASK_CREATED))

    assert counts == {"a": 1, "b": 1}


@pytest.mark.asyncio
async def test_handler_error_does_not_block_others(bus):
    """A failing handler doesn't prevent subsequent handlers from running."""
    results = []

    async def failing_handler(event: Event):
        raise ValueError("boom")

    async def good_handler(event: Event):
        results.append("ok")

    bus.subscribe(TASK_CREATED, failing_handler)
    bus.subscribe(TASK_CREATED, good_handler)
    await bus.emit(Event(type=TASK_CREATED))

    assert results == ["ok"]


@pytest.mark.asyncio
async def test_event_type_isolation(bus):
    """Handlers only fire for their subscribed event type."""
    received = []

    async def handler(event: Event):
        received.append(event.type)

    bus.subscribe(TASK_CREATED, handler)
    await bus.emit(Event(type="other_event"))

    assert received == []


@pytest.mark.asyncio
async def test_clear_removes_all_subscribers(bus):
    """clear() removes all registered handlers."""
    called = []

    async def handler(event: Event):
        called.append(True)

    bus.subscribe(TASK_CREATED, handler)
    bus.clear()
    await bus.emit(Event(type=TASK_CREATED))

    assert called == []


def test_event_auto_timestamp():
    """Events get a timestamp automatically."""
    event = Event(type=TASK_CREATED)
    assert event.timestamp is not None
