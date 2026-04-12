"""State snapshot builder for LLM reasoning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
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

    if p.pattern_type == "focus_quality":
        status = data.get("status", "normal")
        saves = data.get("save_count", 0)
        avg = data.get("avg_change_per_save", 0)
        if status == "stuck":
            return (
                f"Low focus on {p.pattern_key}: {saves} saves with ~{avg:.0f} bytes net change each "
                f"(many saves, little output change)"
            )
        if status == "productive":
            return f"High focus on {p.pattern_key}: productive output ({avg:.0f} bytes/save)"
        return ""  # Don't surface "normal" patterns

    if p.pattern_type == "outgoing_tone":
        if p.pattern_key == "message_length":
            avg_len = data.get("avg_snippet_length", 0)
            count = data.get("message_count", 0)
            if avg_len < 50:
                return f"Your recent messages are unusually short (~{avg_len:.0f} chars, {count} messages)"
            return ""
        if p.pattern_key == "response_latency":
            hours = data.get("avg_response_latency_hours", 0)
            if hours > 12:
                return f"Your average reply time increased to {hours:.1f} hours"
            return ""
        return ""

    if p.pattern_type == "session_fragmentation":
        frag = data.get("avg_fragmentation", 0)
        avg_dur = data.get("avg_session_duration_min", 0)
        if frag > 0.5:
            return (
                f"Your sessions averaged {avg_dur:.0f} min with frequent file switching "
                f"— fragmented work pattern"
            )
        if frag < 0.1 and avg_dur > 30:
            return f"Sustained focus: sessions averaged {avg_dur:.0f} min with minimal switching"
        return ""

    # Phase 25: Recruiting analytics
    if p.pattern_type == "recruiting_response_rate":
        total = data.get("total", 0)
        rate = data.get("rate", 0)
        responded = data.get("responded", 0)
        if p.pattern_key == "overall":
            return f"Overall recruiting response rate: {rate:.0%} ({responded}/{total} applications)"
        label = p.pattern_key.replace("tier:", "").replace("method:", "")
        prefix = "Tier" if p.pattern_key.startswith("tier:") else "Method"
        return f"{prefix} '{label}' response rate: {rate:.0%} ({responded}/{total})"

    if p.pattern_type == "recruiting_over_index":
        dominant = data.get("dominant", "?")
        pct = data.get("pct", 0)
        dom_rate = data.get("response_rate_dominant", 0)
        other_rate = data.get("response_rate_others", 0)
        return (
            f"Over-indexing alert: {pct:.0f}% of applications target {dominant} "
            f"(response rate {dom_rate:.0%} vs {other_rate:.0%} for others)"
        )

    if p.pattern_type == "recruiting_tier_gap":
        count = data.get("count", 0)
        total = data.get("total", 0)
        if count == 0:
            return f"Gap: 0 applications to {p.pattern_key} companies (out of {total} total)"
        pct = data.get("pct", 0)
        return f"Underweight: only {count} applications ({pct:.0f}%) to {p.pattern_key} companies"

    if p.pattern_type == "recruiting_resume_effectiveness":
        rate = data.get("rate", 0)
        total = data.get("total", 0)
        responded = data.get("responded", 0)
        return f"Resume variant '{p.pattern_key}': {rate:.0%} response rate ({responded}/{total})"

    if p.pattern_type == "recruiting_temporal":
        rate = data.get("rate", 0)
        total = data.get("total", 0)
        if p.pattern_key.startswith("day:"):
            day_name = data.get("day_name", p.pattern_key)
            return f"Applications sent on {day_name}s: {rate:.0%} response rate ({total} sent)"
        bucket = data.get("bucket", p.pattern_key)
        return f"Applications sent in {bucket}: {rate:.0%} response rate ({total} sent)"

    if p.pattern_type == "recruiting_fit_score":
        company = data.get("company", p.pattern_key)
        score = data.get("score", 0)
        rank = data.get("rank", "?")
        factors = data.get("matching_factors", [])
        factors_str = f" ({', '.join(factors)})" if factors else ""
        return f"#{rank} fit: {company} — score {score:.0%}{factors_str}"

    # Phase 24: Task affect
    if p.pattern_type == "task_affect":
        lag = data.get("mean_start_lag_pct", 0.5)
        no_work = data.get("no_work_rate", 0)
        completion = data.get("completion_rate", 0)
        if no_work > 0.5:
            return (
                f"You show an anxiety pattern with {p.pattern_key}s — "
                f"{no_work:.0%} are left untouched"
            )
        if lag > 0.9:
            return (
                f"You show an avoidance pattern with {p.pattern_key}s — "
                f"you consistently start in the final {1 - lag:.0%} of available time"
            )
        if lag < 0.3 and completion > 0.8:
            return (
                f"You enjoy working on {p.pattern_key}s — "
                f"you typically start early and complete them reliably"
            )
        return ""

    if p.pattern_type == "energy_proxy":
        avg_gap = data.get("avg_gap_minutes", 0)
        energy = data.get("energy_label", "neutral")
        if energy == "draining":
            return (
                f"Working on {p.pattern_key}s appears draining — "
                f"you take long breaks afterward (avg {avg_gap:.0f}min gap)"
            )
        if energy == "energizing":
            return (
                f"{p.pattern_key.capitalize()}s seem energizing — "
                f"you often start another task right after (avg {avg_gap:.0f}min gap)"
            )
        return ""

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
    health_signals: dict[str, object] = field(default_factory=dict)
    stale_applications: list[dict[str, object]] = field(
        default_factory=list
    )
    # Phase 15: Genuine reasoning context
    compressed_history: str = ""
    causal_statements: list[str] = field(default_factory=list)
    tradeoff_statements: list[str] = field(default_factory=list)
    longitudinal_context: str = ""
    spike_predictions: list[str] = field(default_factory=list)
    # Phase 17: Identity model
    identity_context: str = ""
    # Phase 18: Focus quality + physical inference
    absence_signals: list[str] = field(default_factory=list)
    # Phase 19: Intention tracking + decision memory
    goal_gaps: list[str] = field(default_factory=list)
    recent_decisions: list[str] = field(default_factory=list)
    # Phase 20: Social graph
    relationship_alerts: list[str] = field(default_factory=list)
    # Phase 24: Task affect
    task_affect_context: list[str] = field(default_factory=list)
    # Phase 25: Recruiting analytics
    recruiting_analytics: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Format snapshot as structured text for LLM input."""
        local_now = self.now.astimezone(USER_TZ)
        lines: list[str] = [f"Current time: {local_now.strftime('%A, %B %d %Y %I:%M %p %Z')}"]
        lines.append(f"Tasks: {self.total_pending} pending, {self.total_done} completed")
        lines.append("")

        if self.identity_context:
            lines.append("ABOUT YOU (durable identity):")
            lines.append(self.identity_context)
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
                attendees = [
                    a.get("displayName") or a.get("email", "")
                    for a in ev.get("attendees", [])
                    if not a.get("self")
                ]
                att = f" [attendees: {', '.join(attendees)}]" if attendees else ""
                lines.append(f"  - {time_range}: {summary}{loc}{att}")
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

        if self.health_signals or self.absence_signals:
            lines.append("")
            lines.append("HEALTH SIGNALS:")
            ln = self.health_signals.get("late_night_days", 0)
            if ln:
                lines.append(
                    f"  - {ln} late-night work sessions (1-5 AM) "
                    f"in the past 7 days"
                )
            zd = self.health_signals.get("zero_activity_days", 0)
            if zd:
                lines.append(
                    f"  - {zd} days with zero file activity this week"
                )
            # Sleep inference
            sleep_start = self.health_signals.get("sleep_window_avg_start")
            sleep_end = self.health_signals.get("sleep_window_avg_end")
            sleep_hours = self.health_signals.get("sleep_hours_avg")
            sleep_consistency = self.health_signals.get("sleep_consistency")
            if sleep_start and sleep_end:
                consistency_note = f" ({sleep_consistency})" if sleep_consistency else ""
                hours_note = f", ~{sleep_hours}h" if sleep_hours else ""
                lines.append(
                    f"  - Inferred sleep: {sleep_start} - {sleep_end}"
                    f"{hours_note}{consistency_note}"
                )
            all_nighters = self.health_signals.get("all_nighter_dates", [])
            if all_nighters:
                dates = ", ".join(str(d) for d in all_nighters)  # type: ignore[union-attr]
                lines.append(f"  - All-nighter(s) detected: {dates}")
            # Absence signals
            for signal in self.absence_signals:
                lines.append(f"  - {signal}")

        if self.goal_gaps:
            lines.append("")
            lines.append(f"INTENTION vs BEHAVIOR ({len(self.goal_gaps)}):")
            for gap in self.goal_gaps:
                lines.append(f"  - {gap}")

        if self.recent_decisions:
            lines.append("")
            lines.append(f"DECISION HISTORY ({len(self.recent_decisions)}):")
            for d in self.recent_decisions:
                lines.append(f"  - {d}")

        if self.relationship_alerts:
            lines.append("")
            lines.append(f"RELATIONSHIP SIGNALS ({len(self.relationship_alerts)}):")
            for alert in self.relationship_alerts:
                lines.append(f"  - {alert}")

        if self.task_affect_context:
            lines.append("")
            lines.append(f"TASK AFFECT ({len(self.task_affect_context)}):")
            for ctx in self.task_affect_context:
                lines.append(f"  - {ctx}")

        if self.recruiting_analytics:
            lines.append("")
            lines.append(f"RECRUITING ANALYTICS ({len(self.recruiting_analytics)}):")
            for ra in self.recruiting_analytics:
                lines.append(f"  - {ra}")

        if self.stale_applications:
            lines.append("")
            lines.append(
                f"STALE APPLICATIONS ({len(self.stale_applications)} "
                f"awaiting follow-up):"
            )
            for app in self.stale_applications:
                lines.append(
                    f"  - {app['company']}: {app['status']}, "
                    f"no signal in {app['days_since']} days"
                )

        # Phase 15: Genuine reasoning sections
        if self.causal_statements:
            lines.append("")
            lines.append("CAUSAL ANALYSIS:")
            for stmt in self.causal_statements:
                lines.append(f"  - {stmt}")

        if self.tradeoff_statements:
            lines.append("")
            lines.append("PRIORITY TRADEOFFS:")
            for stmt in self.tradeoff_statements:
                lines.append(f"  - {stmt}")

        if self.compressed_history:
            lines.append("")
            lines.append("RECENT WEEKS (compressed history):")
            lines.append(self.compressed_history)

        if self.longitudinal_context:
            lines.append("")
            lines.append("CROSS-SEMESTER PATTERNS:")
            lines.append(self.longitudinal_context)

        if self.spike_predictions:
            lines.append("")
            lines.append("WORKLOAD SPIKE PREDICTIONS:")
            for pred in self.spike_predictions:
                lines.append(f"  - {pred}")

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

    # Tasks due today (from now to end of local day — past-due items are in overdue)
    due_today = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= now_iso)
            .where(Task.due_date_iso < end_of_today)
            .order_by(Task.due_date_iso.asc())
        ).all()
    )

    # Also include tasks that were due earlier today but haven't been completed
    due_earlier_today = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= start_of_today)
            .where(Task.due_date_iso < now_iso)
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

    # Merge: tasks due earlier today go into due_today, not overdue
    due_today = due_earlier_today + due_today

    # Overdue tasks (due before start of today — yesterday or earlier)
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

    # Health signals: late-night work and zero-activity days
    health_signals: dict[str, object] = {}
    try:
        seven_ago = (now - timedelta(days=7)).isoformat()
        recent_ts = list(
            session.scalars(
                select(FileActivity.modified_at).where(
                    FileActivity.created_at >= seven_ago
                )
            ).all()
        )
        late_dates: set[str] = set()
        for ts in recent_ts:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            local = ts.astimezone(USER_TZ)
            if 1 <= local.hour <= 4:
                late_dates.add(local.strftime("%Y-%m-%d"))
        active_days_stmt = (
            select(func.date(FileActivity.created_at))
            .where(FileActivity.created_at >= seven_ago)
            .group_by(func.date(FileActivity.created_at))
        )
        active_day_count = len(
            list(session.execute(active_days_stmt).all())
        )
        zero_days = max(0, 7 - active_day_count)
        if late_dates or zero_days >= 2:
            health_signals = {
                "late_night_days": len(late_dates),
                "zero_activity_days": zero_days,
            }
    except Exception:
        pass

    # Stale recruiting applications (no signal in 7+ days, not closed)
    stale_apps: list[dict[str, object]] = []
    try:
        from deadline_agent.store.recruiting_repository import (
            RecruitingRepository,
        )

        recruiting_repo = RecruitingRepository(session)
        for app in recruiting_repo.list_active():
            if app.last_signal_at:
                signal_dt = app.last_signal_at
                if signal_dt.tzinfo is None:
                    signal_dt = signal_dt.replace(tzinfo=UTC)
                days = (now - signal_dt).days
                if days >= 7:
                    stale_apps.append(
                        {
                            "company": app.company_name,
                            "status": app.status,
                            "days_since": days,
                        }
                    )
    except Exception:
        pass

    # Phase 17: Identity model
    identity_context = ""
    try:
        from deadline_agent.store.identity_repository import IdentityRepository

        identity_repo = IdentityRepository(session)
        identity_context = identity_repo.get_markdown()
    except Exception:
        pass

    # Phase 18: Sleep inference
    try:
        from deadline_agent.awareness.physical_inference import infer_sleep_signals

        sleep_signals = infer_sleep_signals(session)
        health_signals.update(sleep_signals)
    except Exception:
        pass

    # Phase 18: Absence detection
    absence_signals: list[str] = []
    try:
        from deadline_agent.awareness.absence_detector import detect_absences

        absence_signals = detect_absences(session)
    except Exception:
        pass

    # Phase 19: Goal gaps
    goal_gaps: list[str] = []
    try:
        from deadline_agent.awareness.goal_tracker import compute_goal_gaps

        goal_gaps = compute_goal_gaps(session)
    except Exception:
        pass

    # Phase 19: Recent decisions
    recent_decisions: list[str] = []
    try:
        import json as _json

        from deadline_agent.store.decision_repository import DecisionRepository

        decision_repo = DecisionRepository(session)
        for d in decision_repo.list_recent(limit=5):
            outcome_note = ""
            if d.outcome:
                outcome_note = f" → outcome: {d.outcome}"
            days_ago = (now - d.created_at.replace(tzinfo=UTC)).days
            recent_decisions.append(
                f"{days_ago}d ago: {d.description} (chose: {d.chosen_option}){outcome_note}"
            )
    except Exception:
        pass

    # Phase 20: Relationship alerts
    relationship_alerts: list[str] = []
    try:
        from deadline_agent.awareness.social_graph import compute_relationship_alerts

        relationship_alerts = compute_relationship_alerts(session)
    except Exception:
        pass

    # Phase 24: Task affect context
    task_affect_context: list[str] = []
    try:
        from deadline_agent.awareness.task_affect import get_intervention, get_task_affect_map

        affect_map = get_task_affect_map(session)
        for task_type, affect in affect_map.items():
            if affect.affect_label == "neutral":
                continue
            intervention = get_intervention(affect.affect_label)
            ctx = f"You {affect.affect_label.replace('_', ' ')} {task_type}s"
            if intervention:
                ctx += f" — {intervention}"
            task_affect_context.append(ctx)
    except Exception:
        pass

    # Phase 25: Recruiting analytics from behavioral patterns
    recruiting_analytics: list[str] = []
    try:
        recruiting_pattern_types = {
            "recruiting_response_rate", "recruiting_over_index",
            "recruiting_tier_gap", "recruiting_resume_effectiveness",
            "recruiting_temporal", "recruiting_fit_score",
        }
        for p in patterns:
            if p.pattern_type in recruiting_pattern_types:
                desc = _format_pattern(p)
                if desc:
                    recruiting_analytics.append(desc)
    except Exception:
        pass

    # Phase 15: Genuine reasoning context
    compressed_history = ""
    causal_statements: list[str] = []
    tradeoff_statements: list[str] = []
    longitudinal_context = ""
    spike_predictions: list[str] = []

    try:
        from deadline_agent.memory.context_compression import build_tiered_history

        compressed_history = build_tiered_history(session)
    except Exception:
        # Fall back to original flat compression
        try:
            from deadline_agent.reasoning.context_window import build_compressed_history

            compressed_history = build_compressed_history(session)
        except Exception:
            pass

    try:
        from deadline_agent.reasoning.causal import (
            compute_causal_context,
            compute_effort_context,
        )

        at_risk = unworked + overdue
        if at_risk and patterns:
            causal_statements = compute_causal_context(at_risk, patterns, now, session)
            causal_statements += compute_effort_context(at_risk, patterns, session)
    except Exception:
        pass

    try:
        from deadline_agent.reasoning.tradeoffs import compute_tradeoff_context

        tradeoff_statements = compute_tradeoff_context(
            due_today + due_this_week, active_contexts, session
        )
    except Exception:
        pass

    try:
        from deadline_agent.reasoning.context_window import (
            _get_current_semester_week,
            build_longitudinal_context,
            predict_workload_spikes,
        )

        longitudinal_context = build_longitudinal_context(session)
        semester_week = _get_current_semester_week(session)
        spike_predictions = predict_workload_spikes(
            session, semester_week, due_today + due_this_week, active_contexts
        )
    except Exception:
        pass

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
        health_signals=health_signals,
        stale_applications=stale_apps,
        compressed_history=compressed_history,
        causal_statements=causal_statements,
        tradeoff_statements=tradeoff_statements,
        longitudinal_context=longitudinal_context,
        spike_predictions=spike_predictions,
        identity_context=identity_context,
        absence_signals=absence_signals,
        goal_gaps=goal_gaps,
        recent_decisions=recent_decisions,
        relationship_alerts=relationship_alerts,
        task_affect_context=task_affect_context,
        recruiting_analytics=recruiting_analytics,
    )
