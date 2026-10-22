"""Phase 32 — Session Start Intelligence.

Produces a structured 20-30 second briefing when the user sits down to work:

    You have 3 hours free.

    Most important:
    - OS exam (high risk, no progress)
    - Recruiter follow-up (stale 6 days)

    Recommendation:
    Start with 90 min OS prep now.

The builder is delivery-agnostic — `build_session_briefing()` returns a
structured `SessionBriefing`, and `format_session_briefing()` renders it
for menubar/voice/CLI/chat. A separate `SessionEndSummarizer` produces a
short recap when the session ends, comparing recommendation vs. actual work.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

USER_TZ = ZoneInfo("America/New_York")

# Day-end fallback when there's no next calendar event.
_DEFAULT_END_HOUR = 22  # 10pm local


# ── Structured briefing ───────────────────────────────────────────────


@dataclass
class TimeBlock:
    """A recommended focus block carved out of available time."""

    task_id: int | None
    title: str
    minutes: int
    rationale: str  # "high P(miss), no progress yet"


@dataclass
class BriefingPriority:
    """One ranked priority surfaced in the briefing."""

    task_id: int
    rank: int
    title: str
    course: str | None
    time_label: str
    risk_level: str  # low | medium | high | critical | unknown
    probability_pct: int | None
    suggested_action: str
    why: str


@dataclass
class SessionBriefing:
    """Phase 32 structured briefing payload."""

    generated_at: str
    available_minutes: int
    available_label: str  # "3 hours" / "45 min" / "until 6pm"
    next_event: dict[str, str] | None  # {summary, start_iso}
    life_context: str | None
    priorities: list[BriefingPriority] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)  # stale follow-ups etc.
    time_blocks: list[TimeBlock] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Available-time computation ────────────────────────────────────────


async def _compute_available_time(
    now_utc: datetime,
) -> tuple[int, str, dict[str, str] | None]:
    """Compute available minutes from now until the next event or end of day.

    Returns (minutes, human_label, next_event_or_None). Falls back to a
    raw end-of-day window if the calendar API is unavailable.
    """
    local_now = now_utc.astimezone(USER_TZ)
    end_of_day = local_now.replace(
        hour=_DEFAULT_END_HOUR, minute=0, second=0, microsecond=0
    )
    if end_of_day <= local_now:
        end_of_day = end_of_day + timedelta(days=1)

    next_event: dict[str, str] | None = None
    horizon = min(end_of_day, local_now + timedelta(hours=12))

    try:
        from deadline_agent.reasoning.calendar_gaps import fetch_events

        events = await fetch_events(local_now.isoformat(), horizon.isoformat())
        # Find the first event that hasn't started yet
        for ev in events:
            start_iso = ev.get("start", "")
            if not start_iso:
                continue
            try:
                start_dt = datetime.fromisoformat(start_iso)
            except ValueError:
                continue
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=USER_TZ)
            start_local = start_dt.astimezone(USER_TZ)
            if start_local <= local_now:
                continue
            next_event = {
                "summary": ev.get("summary", "(meeting)"),
                "start_iso": start_local.isoformat(),
            }
            end_of_day = min(end_of_day, start_local)
            break
    except Exception:
        logger.debug("Calendar lookup failed for available-time", exc_info=True)

    minutes = max(0, int((end_of_day - local_now).total_seconds() // 60))

    if next_event:
        # "until 3:00 PM (Standup)"
        try:
            start_dt = datetime.fromisoformat(next_event["start_iso"])
            t = start_dt.astimezone(USER_TZ).strftime("%-I:%M %p")
            label = f"until {t} ({next_event['summary']})"
        except (KeyError, ValueError):
            label = _humanize_minutes(minutes)
    else:
        label = _humanize_minutes(minutes)

    return minutes, label, next_event


def _humanize_minutes(minutes: int) -> str:
    if minutes <= 0:
        return "no free time"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    if minutes % 60 == 0:
        return f"{minutes // 60} hour{'s' if minutes >= 120 else ''}"
    return f"{hours:.1f} hours"


# ── Priority + extras gathering ───────────────────────────────────────


def _gather_priorities(session: Session, limit: int) -> list[BriefingPriority]:
    """Pull the top-N priorities from the Priority Engine and decorate with risk.

    Falls back to a deadline-ordered list of pending tasks when the Priority
    Engine hasn't computed anything yet (cold start / first session of the day).
    """
    from deadline_agent.reasoning.priority_engine import get_current_priorities
    from deadline_agent.store.risk_repository import RiskRepository

    risk_repo = RiskRepository(session)
    items = get_current_priorities(session, limit=limit)
    out: list[BriefingPriority] = []
    for item in items:
        tid = int(item["task_id"])
        risk = risk_repo.get_for_task(tid)
        out.append(
            BriefingPriority(
                task_id=tid,
                rank=int(item["rank"]),
                title=item["title"],
                course=item.get("course"),
                time_label=item["time_label"],
                risk_level=risk.risk_level if risk else "unknown",
                probability_pct=int(round(risk.probability_of_missing * 100))
                if risk
                else None,
                suggested_action=item["suggested_action"],
                why=item.get("reasoning") or item.get("risk_if_ignored", ""),
            )
        )
    if out:
        return out

    return _fallback_priorities(session, limit)


def _fallback_priorities(session: Session, limit: int) -> list[BriefingPriority]:
    """Heuristic deadline-ordered priorities when the Priority Engine is empty."""
    from sqlalchemy import select

    from deadline_agent.models import Task

    now_utc = datetime.now(UTC)
    stmt = (
        select(Task)
        .where(Task.status == "pending")
        .where(Task.due_date_iso.is_not(None))
        .order_by(Task.due_date_iso.asc())
        .limit(limit)
    )
    tasks = list(session.scalars(stmt).all())
    out: list[BriefingPriority] = []
    for idx, t in enumerate(tasks, 1):
        time_label = _format_time_label(t.due_date_iso, now_utc)
        out.append(
            BriefingPriority(
                task_id=t.id,
                rank=idx,
                title=t.title,
                course=t.course,
                time_label=time_label,
                risk_level="unknown",
                probability_pct=None,
                suggested_action=f"Spend 30 min on {t.title}",
                why=f"Next deadline ({time_label})",
            )
        )
    return out


def _format_time_label(due_iso: str | None, now_utc: datetime) -> str:
    if not due_iso:
        return "no due date"
    try:
        due = datetime.fromisoformat(due_iso)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return "no due date"
    hours = (due - now_utc).total_seconds() / 3600
    if hours < 0:
        return f"OVERDUE by {abs(hours):.0f}h"
    if hours < 24:
        return f"due in {hours:.0f}h"
    if hours < 168:
        return f"due in {hours / 24:.1f} days"
    return f"due in {hours / 24:.0f} days"


def _gather_extras(session: Session, now_utc: datetime, life_track: str | None) -> list[str]:
    """Stale follow-ups, relationship signals — short one-liners for the briefing."""
    extras: list[str] = []
    if life_track in ("recruiting", None):
        try:
            from deadline_agent.store.recruiting_repository import RecruitingRepository

            repo = RecruitingRepository(session)
            for app in repo.list_active():
                if not app.last_signal_at:
                    continue
                last = app.last_signal_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                days = (now_utc - last).days
                if days >= 7:
                    extras.append(f"{app.company_name} follow-up stale {days}d")
                if len(extras) >= 2:
                    break
        except Exception:
            logger.debug("Recruiting lookup failed", exc_info=True)
    return extras


def _active_life_context(session: Session) -> str | None:
    try:
        from deadline_agent.store.context_repository import LifeContextRepository

        contexts = LifeContextRepository(session).get_active()
        if not contexts:
            return None
        ctx = contexts[0]
        return ctx.label or ctx.season.replace("_", " ")
    except Exception:
        return None


# ── Time-block planner ────────────────────────────────────────────────


def _plan_time_blocks(
    available_minutes: int, priorities: list[BriefingPriority]
) -> list[TimeBlock]:
    """Carve `available_minutes` into recommended focus blocks.

    Strategy: the top priority gets the bulk (capped at 90 min for one sitting),
    then shorter follow-up blocks for #2 / #3. Stops when no time remains.
    """
    if available_minutes <= 0 or not priorities:
        return []

    remaining = available_minutes
    blocks: list[TimeBlock] = []

    # Top priority: up to 90 min, but at least 25 min if there's room
    top = priorities[0]
    top_minutes = min(90, max(25, remaining // 2 if len(priorities) > 1 else remaining))
    top_minutes = min(top_minutes, remaining)
    if top_minutes >= 15:
        blocks.append(
            TimeBlock(
                task_id=top.task_id,
                title=top.title,
                minutes=top_minutes,
                rationale=_block_rationale(top),
            )
        )
        remaining -= top_minutes

    # Remaining priorities: 25-45 min blocks each
    for p in priorities[1:]:
        if remaining < 20:
            break
        block_minutes = min(45, remaining)
        if block_minutes < 20:
            break
        blocks.append(
            TimeBlock(
                task_id=p.task_id,
                title=p.title,
                minutes=block_minutes,
                rationale=_block_rationale(p),
            )
        )
        remaining -= block_minutes

    return blocks


def _block_rationale(p: BriefingPriority) -> str:
    if p.probability_pct is not None and p.risk_level in ("high", "critical"):
        return f"{p.risk_level} risk ({p.probability_pct}% miss)"
    if "OVERDUE" in p.time_label.upper():
        return "overdue"
    if "due in" in p.time_label.lower():
        return p.time_label
    return p.risk_level if p.risk_level != "unknown" else "top priority"


def _build_recommendation(blocks: list[TimeBlock], available_minutes: int) -> str:
    if not blocks:
        if available_minutes <= 0:
            return "No free time before your next commitment — pick this up after."
        return "Nothing urgent — use this window for whatever's been waiting."
    head = blocks[0]
    if len(blocks) == 1:
        return f"Start with {head.minutes} min on {head.title} now."
    tail = " then ".join(f"{b.minutes} min on {b.title}" for b in blocks[1:])
    return f"Start with {head.minutes} min on {head.title} now, {tail}."


# ── Top-level builder ─────────────────────────────────────────────────


async def build_session_briefing(
    session: Session,
    life_track: str | None = None,
    *,
    now_utc: datetime | None = None,
    priority_limit: int = 3,
) -> SessionBriefing:
    """Assemble the Phase 32 structured briefing.

    Falls back gracefully if Priority/Risk engines have nothing yet.
    """
    now_utc = now_utc or datetime.now(UTC)
    available_minutes, available_label, next_event = await _compute_available_time(
        now_utc
    )

    priorities = _gather_priorities(session, limit=priority_limit)
    extras = _gather_extras(session, now_utc, life_track)
    life_context = _active_life_context(session)
    blocks = _plan_time_blocks(available_minutes, priorities)
    recommendation = _build_recommendation(blocks, available_minutes)

    return SessionBriefing(
        generated_at=now_utc.isoformat(),
        available_minutes=available_minutes,
        available_label=available_label,
        next_event=next_event,
        life_context=life_context,
        priorities=priorities,
        extras=extras,
        time_blocks=blocks,
        recommendation=recommendation,
    )


# ── Rendering ─────────────────────────────────────────────────────────


def format_session_briefing(b: SessionBriefing, *, compact: bool = False) -> str:
    """Render the briefing as the canonical Phase 32 text block.

    `compact=True` produces a one-paragraph version for menubar/voice;
    `compact=False` produces the full multi-line briefing for CLI/chat.
    """
    if compact:
        parts: list[str] = [f"You have {b.available_label} free."]
        if b.priorities:
            top = b.priorities[0]
            parts.append(f"Top: {top.title} — {top.suggested_action}")
        elif b.extras:
            parts.append(b.extras[0])
        if b.recommendation:
            parts.append(b.recommendation)
        return " ".join(parts)

    lines: list[str] = [f"You have {b.available_label} free."]
    if b.life_context:
        lines.append(f"Context: {b.life_context}.")

    if b.priorities or b.extras:
        lines.append("")
        lines.append("Most important:")
        for p in b.priorities[:3]:
            risk_note = ""
            if p.probability_pct is not None and p.risk_level in ("high", "critical"):
                risk_note = f" ({p.risk_level} risk, {p.probability_pct}% miss)"
            elif p.risk_level not in ("low", "unknown"):
                risk_note = f" ({p.risk_level} risk)"
            lines.append(f"- {p.title} — {p.time_label}{risk_note}")
        for extra in b.extras:
            lines.append(f"- {extra}")

    if b.recommendation:
        lines.append("")
        lines.append("Recommendation:")
        lines.append(b.recommendation)

    return "\n".join(lines)


# ── Session-end summary ──────────────────────────────────────────────


def _cached_briefing_store() -> dict[str, SessionBriefing]:
    """In-process store keyed by session-start timestamp.

    Lives at module scope so SessionBriefingGenerator and SessionEndSummarizer
    can share state without needing a DB table.
    """
    return _BRIEFING_CACHE


_BRIEFING_CACHE: dict[str, SessionBriefing] = {}


def remember_briefing(session_ts: str, briefing: SessionBriefing) -> None:
    _BRIEFING_CACHE[session_ts] = briefing
    # Trim — keep only the last 8 briefings
    if len(_BRIEFING_CACHE) > 8:
        oldest = sorted(_BRIEFING_CACHE.keys())[0]
        _BRIEFING_CACHE.pop(oldest, None)


def get_remembered_briefing(session_ts: str) -> SessionBriefing | None:
    return _BRIEFING_CACHE.get(session_ts)


def build_session_end_summary(
    session: Session,
    briefing: SessionBriefing,
    session_started_at: datetime,
    session_ended_at: datetime,
) -> str | None:
    """Compare what the briefing recommended against what the user actually did.

    Looks at FileActivity since `session_started_at`, checks which briefing
    priorities had any work, and lists the rest as "still pending".
    """
    if not briefing.priorities:
        return None

    from deadline_agent.store.file_repository import FileActivityRepository

    repo = FileActivityRepository(session)
    duration_min = max(
        0, int((session_ended_at - session_started_at).total_seconds() // 60)
    )

    worked: list[BriefingPriority] = []
    skipped: list[BriefingPriority] = []
    for p in briefing.priorities:
        activities = repo.get_activity_for_task(p.task_id, since=session_started_at)
        if activities:
            worked.append(p)
        else:
            skipped.append(p)

    parts: list[str] = []
    if duration_min >= 5:
        parts.append(
            f"Session: {duration_min // 60}h{duration_min % 60:02d}m"
            if duration_min >= 60
            else f"Session: {duration_min} min"
        )
    if worked:
        worked_titles = ", ".join(p.title for p in worked[:2])
        parts.append(f"You worked on {worked_titles}")
    if skipped:
        skipped_titles = ", ".join(p.title for p in skipped[:2])
        parts.append(f"Still pending: {skipped_titles}")

    if not worked and not skipped:
        return None
    return " — ".join(parts) + "."
