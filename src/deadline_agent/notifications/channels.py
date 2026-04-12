"""Tiered notification routing: ambient, low, and high priority channels."""

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

logger = logging.getLogger(__name__)


class NotificationPriority(Enum):
    """Notification urgency levels."""

    AMBIENT = "ambient"  # Widget update only, no OS notification
    LOW = "low"  # Silent OS notification + widget
    HIGH = "high"  # OS notification with sound + widget


@dataclass
class Notification:
    """A notification with priority-based routing."""

    title: str
    body: str
    subtitle: str = ""
    priority: NotificationPriority = NotificationPriority.HIGH
    category: str = ""  # recruiting, school, session, briefing
    ttl_seconds: int = 3600
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class NotificationRouter:
    """Routes notifications based on priority level.

    HIGH: macOS notification with sound + ambient queue
    LOW: macOS notification without sound + ambient queue
    AMBIENT: ambient queue only (widget polling)
    """

    def __init__(self) -> None:
        self._ambient_queue: list[Notification] = []
        self._max_ambient: int = 20
        self._lock = threading.Lock()

    async def send(self, notification: Notification) -> bool:
        """Route a notification based on its priority."""
        from deadline_agent.notifications.macos import send_notification

        # Add to ambient queue for widget access
        with self._lock:
            self._ambient_queue.append(notification)
            # Trim old entries
            if len(self._ambient_queue) > self._max_ambient:
                self._ambient_queue = self._ambient_queue[-self._max_ambient :]

        if notification.priority == NotificationPriority.HIGH:
            return send_notification(
                title=notification.title,
                body=notification.body,
                subtitle=notification.subtitle,
                sound=True,
            )
        elif notification.priority == NotificationPriority.LOW:
            return send_notification(
                title=notification.title,
                body=notification.body,
                subtitle=notification.subtitle,
                sound=False,
            )
        # AMBIENT: no OS notification
        logger.debug("Ambient notification queued: %s", notification.title)
        return True

    def get_ambient_queue(self) -> list[dict[str, str]]:
        """Return ambient notifications as dicts for API serialization."""
        now = datetime.now(UTC)
        with self._lock:
            # Filter expired notifications
            self._ambient_queue = [
                n
                for n in self._ambient_queue
                if (now - n.timestamp).total_seconds() < n.ttl_seconds
            ]
            return [
                {
                    "title": n.title,
                    "body": n.body,
                    "category": n.category,
                    "priority": n.priority.value,
                    "timestamp": n.timestamp.isoformat(),
                }
                for n in self._ambient_queue
            ]

    def clear_ambient(self) -> None:
        """Clear the ambient queue."""
        with self._lock:
            self._ambient_queue.clear()


# Module-level singleton
notification_router = NotificationRouter()
