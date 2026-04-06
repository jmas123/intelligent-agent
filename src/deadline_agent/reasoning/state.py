"""State snapshot builder for LLM reasoning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import BehavioralPattern, FileActivity, LifeContext, Task
from deadline_agent.notifications.digest import _relative_day
from deadline_agent.store.file_repository import FileActivityRepository

USER_TZ = ZoneInfo("America/New_York")


def _format_pattern(p: BehavioralPattern) -> str:
    """Format a behavioral pattern as natural language for LLM context."""
    import json

    try:
        data = json.loads(p.value)
    except (json.JSONDecodeError, TypeError):
        return f"[{p.pattern_type}] {p.pattern_key}: {p.value}"

    if p.pattern_type == "effort_accuracy":
        ratio = data.get("ratio", 1.0)
        if ratio > 1.1:
            return f"You typically spend {ratio:.1f}x your estimated time on {p.pattern_key}s"
        if ratio < 0.9:
            return f"You typically finish {p.pattern_key}s faster than estimated ({ratio:.1f}x)"
        return f"Your time estimates for {p.pattern_key}s are accurate ({ratio:.1f}x)"

    if p.pattern_type == "peak_hours":
        label = data.get("label", p.pattern_key)
        count = data.get("total_count", data.get("activity_count", 0))
        rank = data.get("rank", "secondary")
        if rank == "primary":
            return f"Your most productive window is {label} ({count} file edits)"
        return f"You're also active {label} ({count} file edits)"

    if p.pattern_type == "lead_time":
        mean_h = data.get("mean_hours", 0)
        if mean_h >= 48:
            days = mean_h / 24
            return f"You typically start {p.pattern_key} prep {days:.1f} days before deadline"
        return f"You typically start {p.pattern_key}s {mean_h:.0f} hours before deadline"

    if p.pattern_type == "session_duration":
        mean_min = data.get("mean_minutes", 0)
        return f"Average work session on {p.pattern_key}s: {mean_min:.0f} minutes"

    if p.pattern_type == "work_by_time":
        window = data.get("primary_window", "")
        # Don't pluralize "recruiting" or "project"
        label = p.pattern_key if p.pattern_key in ("recruiting", "project") else f"{p.pattern_key}s"
        return f"You typically work on {label} between {window}"

    if p.pattern_type == "procrastination":
        days = data.get("mean_days_before_deadline", 0)
        if days < 1:
            return f"You tend to start {p.pattern_key}s less than a day before the deadline"
        return f"You typically start {p.pattern_key}s {days:.1f} days before the deadline"

    return f"[{p.pattern_type}] {p.pattern_key}: {p.value}"


@dataclass
class StateSnapshot:
    """Aggregated user state for LLM reasoning. Ephemeral — never persisted."""

    now: datetime
    due_today: list[Task] = field(default_factory=list)
    due_this_week: list[Task] = field(default_factory=list)
    overdue: list[Task] = field(default_factory=list)
    recent_file_activity: list[FileActivity] = field(default_factory=list)
    unworked_deadlines: list[Task] = field(default_factory=list)
    calendar_events: list[dict[str, str]] = field(default_factory=list)
    behavioral_patterns: list[BehavioralPattern] = field(default_factory=list)
    life_contexts: list[LifeContext] = field(default_factory=list)
    total_pending: int = 0
    total_done: int = 0

    def to_prompt(self) -> str:
        """Format snapshot as structured text for LLM input."""
        local_now = self.now.astimezone(USER_TZ)
        lines: list[str] = [f"Current time: {local_now.strftime('%A, %B %d %Y %I:%M %p %Z')}"]
        lines.append(f"Tasks: {self.total_pending} pending, {self.total_done} completed")
        lines.append("")

        if self.overdue:
            lines.append(f"OVERDUE ({len(self.overdue)}):")
            for t in self.overdue:
                course = f" ({t.course})" if t.course else ""
                day = _relative_day(t.due_date_iso) if t.due_date_iso else "?"
                lines.append(f"  - {t.title}{course} [was due {day}]")
            lines.append("")

        if self.due_today:
            lines.append(f"DUE TODAY ({len(self.due_today)}):")
            for t in self.due_today:
                course = f" ({t.course})" if t.course else ""
                lines.append(f"  - {t.title}{course} [urgency: {t.urgency_score}/5]")
            lines.append("")

        if self.due_this_week:
            lines.append(f"DUE THIS WEEK ({len(self.due_this_week)}):")
            for t in self.due_this_week:
                course = f" ({t.course})" if t.course else ""
                day = _relative_day(t.due_date_iso) if t.due_date_iso else "?"
                lines.append(f"  - {t.title}{course} [{day}, urgency: {t.urgency_score}/5]")
            lines.append("")

        if self.unworked_deadlines:
            lines.append(f"NO WORK DETECTED ({len(self.unworked_deadlines)}):")
            for t in self.unworked_deadlines:
                course = f" ({t.course})" if t.course else ""
                day = _relative_day(t.due_date_iso) if t.due_date_iso else "?"
                lines.append(f"  - {t.title}{course} [due {day}, no matching files found]")
            lines.append("")

        if self.calendar_events:
            lines.append(f"CALENDAR EVENTS ({len(self.calendar_events)}):")
            for ev in self.calendar_events:
                start_str = ev.get("start", "")
                end_str = ev.get("end", "")
                summary = ev.get("summary", "(No title)")
                location = ev.get("location", "")
                # Format time portion if it's a datetime (not all-day)
                if "T" in start_str:
                    try:
                        start_dt = datetime.fromisoformat(start_str).astimezone(USER_TZ)
                        end_dt = datetime.fromisoformat(end_str).astimezone(USER_TZ)
                        local_today = self.now.astimezone(USER_TZ).date()
                        if start_dt.date() == local_today:
                            time_range = f"{start_dt.strftime('%I:%M %p')} - {end_dt.strftime('%I:%M %p')}"
                        else:
                            time_range = f"{start_dt.strftime('%a %b %d %I:%M %p')} - {end_dt.strftime('%I:%M %p')}"
                    except (ValueError, TypeError):
                        time_range = f"{start_str} - {end_str}"
                else:
                    time_range = "All day"
                loc = f" @ {location}" if location else ""
                lines.append(f"  - {time_range}: {summary}{loc}")
            lines.append("")

        if self.recent_file_activity:
            lines.append(f"RECENT FILE ACTIVITY ({len(self.recent_file_activity)}):")
            for a in self.recent_file_activity[:10]:
                lines.append(f"  - {a.filename} ({a.event_type}, {a.directory})")
            if len(self.recent_file_activity) > 10:
                lines.append(f"  ...and {len(self.recent_file_activity) - 10} more")

        if self.life_contexts:
            lines.append("")
            lines.append(f"ACTIVE LIFE CONTEXT ({len(self.life_contexts)}):")
            for ctx in self.life_contexts:
                label = f" \u2014 {ctx.label}" if ctx.label else ""
                source_tag = " (user-set)" if ctx.source == "manual" else " (auto-detected)"
                lines.append(
                    f"  - {ctx.season.upper()}{label}: "
                    f"{ctx.start_date} to {ctx.end_date}{source_tag}"
                )

        if self.behavioral_patterns:
            lines.append("")
            lines.append(f"BEHAVIORAL PATTERNS ({len(self.behavioral_patterns)} learned):")
            for p in self.behavioral_patterns:
                desc = _format_pattern(p)
                if desc:
                    lines.append(f"  - {desc}")

        return "\n".join(lines)


@dataclass
class UnifiedContext(StateSnapshot):
    """Extended state with calendar gaps and weekly stats."""

    calendar_gaps: list[dict[str, object]] = field(default_factory=list)
    weekly_stats: dict[str, object] = field(default_factory=dict)
    completed_this_week: list[Task] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Format full context including calendar and weekly data."""
        base = super().to_prompt()
        lines = [base]

        if self.calendar_gaps:
            lines.append("")
            lines.append(f"FREE CALENDAR WINDOWS ({len(self.calendar_gaps)}):")
            for g in self.calendar_gaps[:8]:
                start = str(g.get("start", ""))[:16]
                dur = g.get("duration_minutes", 0)
                lines.append(f"  - {start} ({dur}min free)")
            if len(self.calendar_gaps) > 8:
                lines.append(f"  ...and {len(self.calendar_gaps) - 8} more")

        if self.weekly_stats:
            lines.append("")
            lines.append("WEEKLY STATS:")
            done = self.weekly_stats.get("done_count", 0)
            slipped = self.weekly_stats.get("slipped_count", 0)
            lines.append(f"  Completed: {done}, Slipped: {slipped}")
            by_course = self.weekly_stats.get("by_course", {})
            if isinstance(by_course, dict):
                for course, stats in by_course.items():
                    if isinstance(stats, dict):
                        d = stats.get("done", 0)
                        s = stats.get("slipped", 0)
                        lines.append(f"  {course}: {d} done, {s} slipped")

        return "\n".join(lines)


async def build_unified_context(session: Session) -> UnifiedContext:
    """Build full cross-domain context with calendar gaps and weekly stats."""
    snapshot = build_state_snapshot(session)
    now = snapshot.now

    # Fetch calendar gaps (next 7 days)
    calendar_gaps: list[dict[str, object]] = []
    try:
        from deadline_agent.reasoning.calendar_gaps import (
            fetch_free_busy,
            find_gaps,
        )

        local_now = now.astimezone(USER_TZ)
        start_iso = local_now.isoformat()
        end_iso = (local_now + timedelta(days=7)).isoformat()
        busy = await fetch_free_busy(start_iso, end_iso)
        calendar_gaps = find_gaps(busy, start_iso, end_iso)
    except Exception:
        pass  # Calendar unavailable — proceed without gaps

    # Compute weekly stats
    week_ago = (now - timedelta(days=7)).isoformat()
    done_this_week = list(
        session.scalars(
            select(Task).where(Task.status == "done").where(Task.updated_at >= week_ago)
        ).all()
    )
    slipped = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso < now.isoformat())
            .where(Task.due_date_iso.is_not(None))
        ).all()
    )

    by_course: dict[str, dict[str, int]] = {}
    for t in done_this_week:
        c = t.course or "Other"
        by_course.setdefault(c, {"done": 0, "slipped": 0})
        by_course[c]["done"] += 1
    for t in slipped:
        c = t.course or "Other"
        by_course.setdefault(c, {"done": 0, "slipped": 0})
        by_course[c]["slipped"] += 1

    weekly_stats: dict[str, object] = {
        "done_count": len(done_this_week),
        "slipped_count": len(slipped),
        "by_course": by_course,
    }

    return UnifiedContext(
        now=snapshot.now,
        due_today=snapshot.due_today,
        due_this_week=snapshot.due_this_week,
        overdue=snapshot.overdue,
        recent_file_activity=snapshot.recent_file_activity,
        unworked_deadlines=snapshot.unworked_deadlines,
        total_pending=snapshot.total_pending,
        total_done=snapshot.total_done,
        calendar_gaps=calendar_gaps,
        weekly_stats=weekly_stats,
        completed_this_week=done_this_week,
    )


def build_state_snapshot(session: Session) -> StateSnapshot:
    """Gather all data needed for LLM reasoning."""
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    # Use user's local timezone for day boundaries so "today" matches their day
    local_now = now.astimezone(USER_TZ)
    sod = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_today = sod.astimezone(UTC).isoformat()
    end_of_today = (sod + timedelta(days=1)).astimezone(UTC).isoformat()
    end_of_week = (sod + timedelta(days=7)).astimezone(UTC).isoformat()

    # Tasks due today
    due_today = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= start_of_today)
            .where(Task.due_date_iso < end_of_today)
            .order_by(Task.due_date_iso.asc())
        ).all()
    )

    # Tasks due this week (excluding today)
    due_this_week = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= end_of_today)
            .where(Task.due_date_iso < end_of_week)
            .order_by(Task.due_date_iso.asc())
        ).all()
    )

    # Overdue tasks
    overdue = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso < start_of_today)
            .where(Task.due_date_iso.is_not(None))
            .order_by(Task.due_date_iso.asc())
        ).all()
    )

    # Counts
    all_pending = list(session.scalars(select(Task).where(Task.status == "pending")).all())
    total_done = len(list(session.scalars(select(Task).where(Task.status == "done")).all()))

    # File activity
    file_repo = FileActivityRepository(session)
    recent_activity = file_repo.get_recent_activity(hours=72)

    # Unworked deadlines (pending, due within 7 days, no linked files)
    upcoming = due_today + due_this_week
    unworked = [t for t in upcoming if not file_repo.has_activity_for_task(t.id, since_hours=168)]

    # Behavioral patterns (only confident ones)
    patterns: list[BehavioralPattern] = []
    try:
        from deadline_agent.config import settings
        from deadline_agent.store.pattern_repository import PatternRepository

        pattern_repo = PatternRepository(session)
        patterns = [
            p for p in pattern_repo.get_all() if p.confidence >= settings.min_pattern_confidence
        ]
    except Exception:
        pass  # Patterns unavailable — proceed without them

    # Active life contexts
    active_contexts: list[LifeContext] = []
    try:
        from deadline_agent.store.context_repository import LifeContextRepository

        context_repo = LifeContextRepository(session)
        active_contexts = context_repo.get_active()
    except Exception:
        pass  # Contexts unavailable — proceed without them

    return StateSnapshot(
        now=now,
        due_today=due_today,
        due_this_week=due_this_week,
        overdue=overdue,
        recent_file_activity=recent_activity,
        unworked_deadlines=unworked,
        behavioral_patterns=patterns,
        life_contexts=active_contexts,
        total_pending=len(all_pending),
        total_done=total_done,
    )
