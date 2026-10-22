"""Session briefing event handler — Phase 32 entry point.

Subscribes to SESSION_STARTED, asks the Phase 32 builder for a structured
briefing, caches it for the session-end summary, and routes the rendered text
through the intervention engine.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from deadline_agent.awareness.session_intelligence import (
    build_session_briefing,
    format_session_briefing,
    remember_briefing,
)
from deadline_agent.events import SESSION_STARTED, Event, EventBus

logger = logging.getLogger(__name__)


class SessionBriefingGenerator:
    """Produces a Phase 32 structured briefing on each session start.

    Dedup: one briefing per unique session_started timestamp.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._last_briefing_session_ts: str | None = None

    def register(self, bus: EventBus) -> None:
        bus.subscribe(SESSION_STARTED, self._on_session_started)

    async def _on_session_started(self, event: Event) -> None:
        ts = event.payload.get("timestamp", "")
        if ts and ts == self._last_briefing_session_ts:
            return
        self._last_briefing_session_ts = ts

        life_track = event.payload.get("life_track")

        try:
            with self._session_factory() as session:
                briefing = await build_session_briefing(session, life_track=life_track)
                if not briefing.priorities and not briefing.extras:
                    return  # nothing worth interrupting for

                if ts:
                    remember_briefing(ts, briefing)

                body = format_session_briefing(briefing)
                from deadline_agent.reasoning.intervention_engine import (
                    get_intervention_engine,
                )

                engine = get_intervention_engine()
                await engine.evaluate(
                    session,
                    task_id=None,
                    risk_level="medium",
                    category="briefing",
                    title="Session briefing",
                    body=body,
                )
                session.commit()
                logger.info("Session briefing generated (%d priorities)", len(briefing.priorities))
        except Exception:
            logger.exception("Session briefing error")

class SessionEndSummarizer:
    """Produces a recap when the user steps away.

    Subscribes to SESSION_ENDED events emitted by the ambient state tracker
    when an active session goes idle. Compares the cached briefing's
    recommendation to what the user actually worked on.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def register(self, bus: EventBus) -> None:
        from deadline_agent.events import SESSION_ENDED

        bus.subscribe(SESSION_ENDED, self._on_session_ended)

    async def _on_session_ended(self, event: Event) -> None:
        from deadline_agent.awareness.session_intelligence import (
            build_session_end_summary,
            get_remembered_briefing,
        )

        started_iso = event.payload.get("session_started_at")
        ended_iso = event.payload.get("session_ended_at")
        if not started_iso:
            return
        briefing = get_remembered_briefing(started_iso)
        if not briefing:
            return

        try:
            started_at = datetime.fromisoformat(started_iso)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
        except ValueError:
            return

        try:
            ended_at = (
                datetime.fromisoformat(ended_iso) if ended_iso else datetime.now(UTC)
            )
            if ended_at.tzinfo is None:
                ended_at = ended_at.replace(tzinfo=UTC)
        except ValueError:
            ended_at = datetime.now(UTC)

        try:
            with self._session_factory() as session:
                summary = build_session_end_summary(
                    session, briefing, started_at, ended_at
                )
                if not summary:
                    return

                from deadline_agent.reasoning.intervention_engine import (
                    get_intervention_engine,
                )

                engine = get_intervention_engine()
                await engine.evaluate(
                    session,
                    task_id=None,
                    risk_level="low",
                    category="session_end",
                    title="Session recap",
                    body=summary,
                )
                session.commit()
                logger.info("Session end summary delivered")
        except Exception:
            logger.exception("Session end summary error")
