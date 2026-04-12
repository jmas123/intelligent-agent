"""In-process async event bus for state-change-driven triggers."""

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Canonical event types
TASK_CREATED = "task_created"
TASK_STATUS_CHANGED = "task_status_changed"
FILE_ACTIVITY_RECORDED = "file_activity_recorded"
RECRUITING_STATUS_CHANGED = "recruiting_status_changed"
EMAIL_RECEIVED = "email_received"
ACTIVITY_DROP_DETECTED = "activity_drop_detected"
LATE_NIGHT_DETECTED = "late_night_detected"
FOLLOW_UP_DUE = "follow_up_due"
CONTEXT_SWITCH_DETECTED = "context_switch_detected"
SESSION_STARTED = "session_started"
FOCUS_QUALITY_UPDATED = "focus_quality_updated"
SLEEP_PATTERN_DETECTED = "sleep_pattern_detected"
ABSENCE_DETECTED = "absence_detected"
TONE_SHIFT_DETECTED = "tone_shift_detected"
MEETING_UPCOMING = "meeting_upcoming"


@dataclass
class Event:
    """A discrete state change in the system."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Simple async pub/sub for in-process event routing."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type."""
        self._subscribers[event_type].append(handler)

    async def emit(self, event: Event) -> None:
        """Fire all handlers for the event type. Errors are logged, not raised."""
        for handler in self._subscribers.get(event.type, []):
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "Event handler %s failed for %s",
                    handler.__name__,
                    event.type,
                )

    def clear(self) -> None:
        """Remove all subscribers. Used in tests."""
        self._subscribers.clear()


# Module-level singleton
event_bus = EventBus()
