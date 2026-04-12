"""Pre-meeting briefing: generate a 60-second brief before every calendar event."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from deadline_agent.config import settings
from deadline_agent.events import MEETING_UPCOMING, Event, EventBus
from deadline_agent.notifications.channels import (
    Notification,
    NotificationPriority,
    notification_router,
)

logger = logging.getLogger(__name__)

USER_TZ = ZoneInfo("America/New_York")

MEETING_BRIEFING_SYSTEM_PROMPT = (
    "You are a personal assistant generating a 60-second pre-meeting briefing. "
    "The user has a meeting starting soon. Synthesize the context below into a concise, "
    "actionable briefing.\n\n"
    "Structure:\n"
    "1. One sentence: what the meeting is and when it starts\n"
    "2. For each attendee: who they are, your last interaction, and relationship trend\n"
    "3. Outstanding items: tasks, follow-ups, or open threads with attendees\n"
    "4. Suggested goal: what you should get out of this meeting (inferred from context)\n\n"
    "Rules:\n"
    "- Keep it to 4-6 sentences total. Be direct and conversational.\n"
    "- If you have no relationship data for someone, just state their name.\n"
    "- If there are outstanding tasks related to attendees, lead with those.\n"
    "- End with one concrete thing to accomplish in the meeting.\n"
    "- Do not use bullet points. Write in flowing prose."
)


class MeetingBriefingGenerator:
    """Generates a pre-meeting briefing when a meeting is approaching.

    Subscribes to MEETING_UPCOMING events and produces a concise summary
    of attendees, relationship context, outstanding items, and meeting goals.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def register(self, bus: EventBus) -> None:
        bus.subscribe(MEETING_UPCOMING, self._on_meeting_upcoming)

    async def _on_meeting_upcoming(self, event: Event) -> None:
        cal_event = event.payload.get("event", {})
        minutes_until = event.payload.get("minutes_until", 0)

        try:
            with self._session_factory() as session:
                attendee_context = self._build_attendee_context(
                    cal_event.get("attendees", []), session
                )
                briefing = await self._generate_briefing(
                    cal_event, attendee_context, minutes_until, session
                )
                if briefing:
                    await notification_router.send(
                        Notification(
                            title=f"Meeting in {int(minutes_until)} min: {cal_event.get('summary', '')}",
                            body=briefing,
                            priority=NotificationPriority.HIGH,
                            category="briefing",
                            ttl_seconds=int(minutes_until * 60),
                        )
                    )
                    logger.info(
                        "Meeting briefing sent for: %s", cal_event.get("summary")
                    )
        except Exception:
            logger.exception("Meeting briefing error for: %s", cal_event.get("summary"))

    def _build_attendee_context(
        self, attendees: list[dict], session: Any
    ) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from deadline_agent.models import Relationship, Task
        from deadline_agent.store.relationship_repository import RelationshipRepository

        repo = RelationshipRepository(session)
        contexts: list[dict[str, Any]] = []

        for attendee in attendees:
            if attendee.get("self"):
                continue

            email = attendee.get("email", "")
            display_name = attendee.get("displayName") or email.split("@")[0]

            info: dict[str, Any] = {
                "name": display_name,
                "email": email,
                "response_status": attendee.get("responseStatus", ""),
            }

            # Look up relationship by name then email
            matches = repo.search_by_name_or_email(display_name)
            if not matches and email:
                matches = repo.search_by_name_or_email(email.split("@")[0])

            if matches:
                best = matches[0]
                info["last_interaction"] = (
                    best.last_interaction_at.isoformat()
                    if best.last_interaction_at
                    else None
                )
                info["interaction_count"] = best.interaction_count
                info["trend"] = best.trend
                info["channel"] = best.channel

            # Find pending tasks mentioning this person
            first_name = display_name.split()[0] if display_name else ""
            if first_name and len(first_name) > 2:
                related_tasks = list(
                    session.scalars(
                        select(Task)
                        .where(Task.status == "pending")
                        .where(
                            Task.title.ilike(f"%{first_name}%")
                            | Task.course.ilike(f"%{first_name}%")
                        )
                        .limit(5)
                    ).all()
                )
                if related_tasks:
                    info["outstanding_tasks"] = [
                        {"title": t.title, "due": t.due_date_iso, "type": t.type}
                        for t in related_tasks
                    ]

            contexts.append(info)

        return contexts

    async def _generate_briefing(
        self,
        cal_event: dict,
        attendee_context: list[dict[str, Any]],
        minutes_until: float,
        session: Any,
    ) -> str | None:
        from sqlalchemy import func, select

        from deadline_agent.models import Task

        # Count overdue and due-today tasks for context
        now = datetime.now(USER_TZ)
        end_of_today = now.replace(hour=23, minute=59, second=59)

        overdue_count = (
            session.execute(
                select(func.count(Task.id))
                .where(Task.status == "pending")
                .where(Task.due_date_iso < now.isoformat())
                .where(Task.due_date_iso.is_not(None))
            ).scalar()
            or 0
        )

        due_today_count = (
            session.execute(
                select(func.count(Task.id))
                .where(Task.status == "pending")
                .where(Task.due_date_iso >= now.isoformat())
                .where(Task.due_date_iso < end_of_today.isoformat())
            ).scalar()
            or 0
        )

        # Format attendee context
        attendee_lines: list[str] = []
        for ctx in attendee_context:
            line = f"- {ctx['name']} ({ctx['email']})"
            if ctx.get("trend"):
                line += f" | trend: {ctx['trend']}"
            if ctx.get("last_interaction"):
                line += f" | last interaction: {ctx['last_interaction']}"
            if ctx.get("interaction_count"):
                line += f" | {ctx['interaction_count']} total interactions"
            attendee_lines.append(line)

            if ctx.get("outstanding_tasks"):
                for task in ctx["outstanding_tasks"]:
                    attendee_lines.append(
                        f"  outstanding: {task['title']} (due {task['due']}, type: {task['type']})"
                    )

        attendee_text = "\n".join(attendee_lines) if attendee_lines else "No attendee data available."

        user_prompt = (
            f"MEETING: {cal_event.get('summary', '(No title)')}\n"
            f"STARTS: {cal_event.get('start', '')} ({int(minutes_until)} minutes from now)\n"
            f"LOCATION: {cal_event.get('location') or 'Not specified'}\n"
            f"DESCRIPTION: {cal_event.get('description') or 'None'}\n\n"
            f"ATTENDEES:\n{attendee_text}\n\n"
            f"YOUR STATE: {overdue_count} overdue tasks, {due_today_count} due today."
        )

        try:
            from deadline_agent.reasoning.engine import call_llm

            return await call_llm(MEETING_BRIEFING_SYSTEM_PROMPT, user_prompt)
        except Exception:
            logger.warning("LLM unavailable for meeting briefing, using fallback")
            # Fallback: simple non-LLM briefing
            parts = [
                f"{cal_event.get('summary', 'Meeting')} starts in {int(minutes_until)} minutes."
            ]
            if cal_event.get("location"):
                parts.append(f"Location: {cal_event['location']}.")
            if attendee_context:
                names = [c["name"] for c in attendee_context]
                parts.append(f"Attendees: {', '.join(names)}.")
            if overdue_count:
                parts.append(f"You have {overdue_count} overdue tasks.")
            return " ".join(parts)


async def meeting_briefing_scheduler(session_factory: Any) -> None:
    """Poll for upcoming meetings and emit MEETING_UPCOMING events."""
    from deadline_agent.events import event_bus
    from deadline_agent.reasoning.calendar_gaps import fetch_events

    interval = settings.meeting_briefing_check_interval_minutes * 60
    lead = settings.meeting_briefing_lead_minutes
    briefed_keys: set[str] = set()

    while True:
        await asyncio.sleep(interval)
        if not settings.enable_meeting_briefing:
            continue

        try:
            now = datetime.now(USER_TZ)
            window_end = now + timedelta(minutes=lead + settings.meeting_briefing_check_interval_minutes)
            events = await fetch_events(now.isoformat(), window_end.isoformat())

            for ev in events:
                start_str = ev.get("start", "")
                # Skip all-day events
                if "T" not in start_str:
                    continue

                try:
                    start_dt = datetime.fromisoformat(start_str).astimezone(USER_TZ)
                except ValueError:
                    continue

                minutes_until = (start_dt - now).total_seconds() / 60
                if not (0 < minutes_until <= lead):
                    continue

                event_key = f"{ev.get('summary', '')}|{start_str}"
                if event_key in briefed_keys:
                    continue

                briefed_keys.add(event_key)
                await event_bus.emit(
                    Event(
                        type=MEETING_UPCOMING,
                        payload={"event": ev, "minutes_until": minutes_until},
                    )
                )
                logger.info("Meeting upcoming: %s in %.0f min", ev.get("summary"), minutes_until)

            # Prune to keep set bounded
            if len(briefed_keys) > 100:
                briefed_keys.clear()

        except Exception:
            logger.exception("Meeting briefing scheduler error")
