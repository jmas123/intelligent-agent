"""Risk Prediction Engine — computes per-task probability of missing deadlines.

Deterministic computation (no LLM needed):
1. Estimate hours needed (adjusted by effort accuracy patterns)
2. Compute hours available (calendar free blocks before deadline, discounted by focus quality)
3. Factor in progress (hours already worked vs total needed)
4. Compute P(miss) from the ratio + deadline proximity + behavioral signals
5. Assign confidence based on data density

Output: "You are 72% likely to miss this unless you act today"
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from deadline_agent.models import BehavioralPattern, Task, TaskRisk
from deadline_agent.reasoning.scheduler import EFFORT_MAP, estimate_effort
from deadline_agent.store.risk_repository import RiskRepository

logger = logging.getLogger(__name__)


# ── Hours-needed estimation ───────────────────────────────────────────


def _estimate_hours_needed(task: Task, hours_worked: float, session: Session) -> float:
    """Estimate remaining hours needed for a task.

    Uses effort accuracy patterns to adjust the base estimate, then subtracts
    hours already worked.
    """
    total_minutes = estimate_effort(task, session)
    total_hours = total_minutes / 60.0
    remaining = max(0.0, total_hours - hours_worked)
    return round(remaining, 2)


# ── Hours-available computation ───────────────────────────────────────


async def _compute_hours_available(
    task: Task,
    now: datetime,
    focus_discount: float,
) -> float:
    """Compute available working hours between now and task deadline.

    Fetches calendar free blocks, filters to reasonable hours,
    and discounts by focus quality.
    """
    if not task.due_date_iso:
        return 0.0

    try:
        due = datetime.fromisoformat(task.due_date_iso)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return 0.0

    if due <= now:
        return 0.0  # already past due

    try:
        from deadline_agent.reasoning.calendar_gaps import fetch_free_busy, find_gaps
        from deadline_agent.reasoning.state import USER_TZ

        local_now = now.astimezone(USER_TZ)
        local_due = due.astimezone(USER_TZ)

        busy = await fetch_free_busy(local_now.isoformat(), local_due.isoformat())
        gaps = find_gaps(busy, local_now.isoformat(), local_due.isoformat(), min_minutes=30)

        total_minutes = sum(g.get("duration_minutes", 0) for g in gaps)
        # Apply focus discount: if focus quality is low, effective hours are less
        effective_minutes = total_minutes * focus_discount
        return round(effective_minutes / 60.0, 2)
    except Exception:
        # Calendar unavailable — estimate from raw hours, assume 10 useful hours/day
        hours_until = (due - now).total_seconds() / 3600
        days_until = hours_until / 24
        raw_hours = days_until * 10  # 10 productive hours per day (8am-10pm minus overhead)
        return round(raw_hours * focus_discount, 2)


# ── Focus quality discount ────────────────────────────────────────────


def _get_focus_discount(patterns: list[BehavioralPattern]) -> float:
    """Compute a 0-1 discount factor based on focus quality patterns.

    1.0 = perfect focus (no discount)
    Lower values = fragmented/unfocused work, effective hours are less than calendar hours
    """
    for p in patterns:
        if p.pattern_type == "session_fragmentation":
            try:
                data = json.loads(p.value)
                frag = data.get("avg_fragmentation", 0)
                # High fragmentation = lower effective hours
                if frag > 0.7:
                    return 0.5
                if frag > 0.5:
                    return 0.65
                if frag > 0.3:
                    return 0.8
            except (json.JSONDecodeError, TypeError):
                pass

    for p in patterns:
        if p.pattern_type == "focus_quality":
            try:
                data = json.loads(p.value)
                status = data.get("status", "normal")
                if status == "stuck":
                    return 0.6
                if status == "productive":
                    return 0.95
            except (json.JSONDecodeError, TypeError):
                pass

    return 0.85  # default: slight discount for context switching overhead


# ── Confidence scoring ────────────────────────────────────────────────


def _compute_confidence(
    task: Task,
    patterns: list[BehavioralPattern],
    hours_worked: float,
) -> tuple[float, str]:
    """Compute 0-1 confidence in the risk estimate and explain why.

    Higher confidence when:
    - More behavioral pattern data exists for this task type
    - Calendar data is available
    - Work sessions have been recorded
    """
    signals = 0
    reasons: list[str] = []

    # Check for effort accuracy pattern for this task type
    has_effort_pattern = any(
        p.pattern_type == "effort_accuracy" and p.pattern_key == task.type
        for p in patterns
    )
    if has_effort_pattern:
        signals += 2
    else:
        reasons.append(f"no effort accuracy data for {task.type}s")

    # Check for lead time pattern
    has_lead_time = any(
        p.pattern_type == "lead_time" and p.pattern_key == task.type
        for p in patterns
    )
    if has_lead_time:
        signals += 1

    # Check if we have work session data
    if hours_worked > 0:
        signals += 1
    else:
        reasons.append("no work sessions recorded")

    # Check for general pattern density
    relevant_patterns = [
        p for p in patterns
        if p.pattern_type in ("effort_accuracy", "peak_hours", "lead_time", "session_duration")
    ]
    if len(relevant_patterns) >= 4:
        signals += 1

    # Due date presence
    if task.due_date_iso:
        signals += 1
    else:
        reasons.append("no due date set")

    # Convert to 0-1
    max_signals = 6
    confidence = min(1.0, signals / max_signals)

    if confidence >= 0.7:
        reason = "good data density — estimate is grounded"
    elif confidence >= 0.4:
        reason = f"moderate confidence — {'; '.join(reasons) if reasons else 'some data gaps'}"
    else:
        reason = f"low confidence — {'; '.join(reasons) if reasons else 'insufficient data'}"

    return round(confidence, 2), reason


# ── P(miss) computation ──────────────────────────────────────────────


def _compute_probability_of_missing(
    hours_needed: float,
    hours_available: float,
    hours_worked: float,
    task: Task,
    now: datetime,
    patterns: list[BehavioralPattern],
) -> float:
    """Compute probability of missing a deadline (0-1).

    Core formula: based on the ratio of hours needed to hours available,
    adjusted by behavioral signals.
    """
    if not task.due_date_iso:
        return 0.3  # no deadline — moderate baseline risk

    try:
        due = datetime.fromisoformat(task.due_date_iso)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return 0.3

    # Already overdue
    if due <= now:
        return 1.0

    hours_until_due = (due - now).total_seconds() / 3600

    # If no work needed, risk is zero
    if hours_needed <= 0:
        return 0.0

    # If no available hours, risk is very high
    if hours_available <= 0:
        return 0.95

    # Base risk from hours ratio
    # ratio < 1.0 means not enough time; ratio > 1.0 means buffer
    ratio = hours_available / hours_needed
    if ratio >= 3.0:
        base_risk = 0.05
    elif ratio >= 2.0:
        base_risk = 0.15
    elif ratio >= 1.5:
        base_risk = 0.25
    elif ratio >= 1.0:
        base_risk = 0.40
    elif ratio >= 0.75:
        base_risk = 0.60
    elif ratio >= 0.5:
        base_risk = 0.75
    else:
        base_risk = 0.90

    # Procrastination adjustment: if they haven't started and deadline is close
    if hours_worked == 0 and hours_until_due < 48:
        base_risk = min(1.0, base_risk + 0.15)

    # Lead time pattern adjustment
    for p in patterns:
        if p.pattern_type == "lead_time" and p.pattern_key == task.type:
            try:
                data = json.loads(p.value)
                typical_lead_hours = data.get("mean_hours", 0)
                if typical_lead_hours > 0 and hours_worked == 0:
                    # If they usually start X hours before deadline and haven't started
                    if hours_until_due < typical_lead_hours:
                        # They're behind their own pattern
                        base_risk = min(1.0, base_risk + 0.10)
            except (json.JSONDecodeError, TypeError):
                pass

    # Task affect adjustment: avoidance/anxiety tasks have higher risk
    for p in patterns:
        if p.pattern_type == "task_affect" and p.pattern_key == task.type:
            try:
                data = json.loads(p.value)
                no_work_rate = data.get("no_work_rate", 0)
                if no_work_rate > 0.5:
                    base_risk = min(1.0, base_risk + 0.10)
                lag = data.get("mean_start_lag_pct", 0.5)
                if lag > 0.9:
                    base_risk = min(1.0, base_risk + 0.05)
            except (json.JSONDecodeError, TypeError):
                pass

    return round(min(1.0, max(0.0, base_risk)), 2)


# ── Risk level classification ────────────────────────────────────────


def _classify_risk(p_miss: float) -> str:
    """Classify probability into risk level."""
    if p_miss >= 0.75:
        return "critical"
    if p_miss >= 0.50:
        return "high"
    if p_miss >= 0.25:
        return "medium"
    return "low"


# ── Summary generation ────────────────────────────────────────────────


def _generate_summary(
    task: Task,
    p_miss: float,
    hours_needed: float,
    hours_available: float,
    hours_worked: float,
    risk_level: str,
    now: datetime,
) -> str:
    """Generate a human-readable risk summary."""
    pct = int(p_miss * 100)

    if p_miss >= 0.90:
        urgency = "almost certain to miss"
    elif p_miss >= 0.70:
        urgency = f"{pct}% likely to miss"
    elif p_miss >= 0.50:
        urgency = f"{pct}% chance of missing"
    elif p_miss >= 0.25:
        urgency = "moderate risk"
    else:
        urgency = "on track"

    parts = [f"{urgency}"]

    if hours_needed > 0:
        parts.append(f"~{hours_needed:.1f}h remaining work")
    if hours_available > 0:
        parts.append(f"{hours_available:.1f}h available")
    elif task.due_date_iso:
        parts.append("no free time before deadline")

    if hours_worked > 0:
        parts.append(f"{hours_worked:.1f}h already invested")
    elif task.due_date_iso:
        try:
            due = datetime.fromisoformat(task.due_date_iso)
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            hours_left = (due - now).total_seconds() / 3600
            if hours_left > 0 and hours_left < 48:
                parts.append("not started yet")
        except (ValueError, TypeError):
            pass

    if p_miss >= 0.50:
        parts.append("act today")

    return " — ".join(parts)


# ── Main computation ─────────────────────────────────────────────────


async def compute_task_risks(session: Session) -> list[TaskRisk]:
    """Compute and store risk assessments for all pending tasks.

    1. Gather pending tasks with deadlines
    2. For each: estimate hours needed, compute hours available, compute P(miss)
    3. Store results in task_risks table
    """
    from deadline_agent.reasoning.state import build_state_snapshot
    from deadline_agent.store.session_repository import WorkSessionRepository

    snapshot = build_state_snapshot(session)
    now = snapshot.now
    patterns = snapshot.behavioral_patterns
    focus_discount = _get_focus_discount(patterns)

    # Collect all pending tasks
    all_tasks: list[Task] = []
    seen_ids: set[int] = set()
    for task_list in (snapshot.overdue, snapshot.due_today, snapshot.due_this_week):
        for t in task_list:
            if t.id not in seen_ids:
                all_tasks.append(t)
                seen_ids.add(t.id)

    if not all_tasks:
        logger.info("No pending tasks for risk assessment")
        return []

    repo = RiskRepository(session)
    ws_repo = WorkSessionRepository(session)
    risks: list[TaskRisk] = []

    for task in all_tasks:
        # Hours already worked
        total_work_minutes = ws_repo.get_total_duration(task.id)
        hours_worked = total_work_minutes / 60.0

        # Hours needed (adjusted by effort accuracy patterns, minus work done)
        hours_needed = _estimate_hours_needed(task, hours_worked, session)

        # Hours available (calendar gaps before deadline, discounted by focus)
        hours_available = await _compute_hours_available(task, now, focus_discount)

        # Confidence
        confidence, confidence_reason = _compute_confidence(task, patterns, hours_worked)

        # P(miss)
        p_miss = _compute_probability_of_missing(
            hours_needed, hours_available, hours_worked, task, now, patterns
        )

        # Classification and summary
        risk_level = _classify_risk(p_miss)
        summary = _generate_summary(
            task, p_miss, hours_needed, hours_available, hours_worked, risk_level, now
        )

        risk = repo.upsert(
            task.id,
            {
                "probability_of_missing": p_miss,
                "hours_needed": hours_needed,
                "hours_available": hours_available,
                "hours_worked": hours_worked,
                "confidence": confidence,
                "confidence_reason": confidence_reason,
                "risk_level": risk_level,
                "summary": summary,
            },
        )
        risks.append(risk)

    session.commit()
    logger.info("Risk engine: assessed %d tasks (critical=%d, high=%d)",
                len(risks),
                sum(1 for r in risks if r.risk_level == "critical"),
                sum(1 for r in risks if r.risk_level == "high"))

    return risks


def get_current_risks(session: Session) -> list[dict[str, Any]]:
    """Get current risk assessments as dicts for display.

    Does NOT recompute — returns the last computed assessments.
    """
    from deadline_agent.reasoning.priority_engine import _hours_until_due

    repo = RiskRepository(session)
    results: list[dict[str, Any]] = []

    for risk, task in repo.get_all_active():
        hours = _hours_until_due(task, datetime.now(UTC))
        if hours is not None:
            if hours < 0:
                time_label = f"OVERDUE by {abs(hours):.0f}h"
            elif hours < 24:
                time_label = f"due in {hours:.0f}h"
            elif hours < 168:
                time_label = f"due in {hours / 24:.1f} days"
            else:
                time_label = f"due in {hours / 24:.0f} days"
        else:
            time_label = "no due date"

        results.append({
            "task_id": task.id,
            "title": task.title,
            "course": task.course,
            "type": task.type,
            "time_label": time_label,
            "probability_of_missing": risk.probability_of_missing,
            "probability_pct": int(risk.probability_of_missing * 100),
            "hours_needed": risk.hours_needed,
            "hours_available": risk.hours_available,
            "hours_worked": risk.hours_worked,
            "confidence": risk.confidence,
            "confidence_reason": risk.confidence_reason,
            "risk_level": risk.risk_level,
            "summary": risk.summary,
        })

    return results
