"""Centralized ambient state tracker for mode, idle, and session detection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class AmbientState:
    """Mutable singleton tracking the user's current ambient state.

    Detects idle-to-active transitions (session starts) and
    life_track transitions (context switches).
    """

    current_mode: str | None = None  # recruiting | project | school | None
    last_life_track: str | None = None  # previous mode before transition
    last_activity_at: datetime | None = None
    session_active: bool = False
    session_started_at: datetime | None = None
    idle_threshold_minutes: int = 30

    def is_idle(self) -> bool:
        """Check if user has been idle longer than the threshold."""
        if self.last_activity_at is None:
            return True
        elapsed = (datetime.now(UTC) - self.last_activity_at).total_seconds() / 60
        return elapsed >= self.idle_threshold_minutes

    def record_activity(
        self, life_track: str | None, timestamp: datetime
    ) -> tuple[bool, str | None]:
        """Record file activity and detect state transitions.

        Returns:
            (session_just_started, previous_mode_if_switched)
            - session_just_started: True if this is the first activity after idle
            - previous_mode_if_switched: the old mode if a context switch occurred
        """
        was_idle = self.is_idle()
        self.last_activity_at = timestamp

        session_just_started = False
        previous_mode: str | None = None

        # Detect session start (idle → active)
        if was_idle and not self.session_active:
            self.session_active = True
            self.session_started_at = timestamp
            session_just_started = True
            logger.info("Work session started (was idle)")

        # Detect context switch (mode change)
        effective_track = life_track or "school"  # None defaults to school
        if self.current_mode is not None and effective_track != self.current_mode:
            previous_mode = self.current_mode
            logger.info(
                "Context switch detected: %s → %s",
                self.current_mode,
                effective_track,
            )

        self.last_life_track = self.current_mode
        self.current_mode = effective_track

        return session_just_started, previous_mode

    def reset_session(self) -> None:
        """Mark the session as ended (called when idle detected)."""
        if self.session_active:
            self.session_active = False
            self.session_started_at = None
            logger.debug("Session reset (idle)")


# Module-level singleton
ambient_state = AmbientState()
