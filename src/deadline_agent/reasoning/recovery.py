"""Recovery plan generation: concrete catch-up schedules for at-risk tasks."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from deadline_agent.models import BehavioralPattern, Task
from deadline_agent.reasoning.scheduler import EFFORT_MAP, estimate_effort

logger = logging.getLogger(__name__)

RECOVERY_SYSTEM_PROMPT = (
    "You are an academic productivity coach. A student has missed or is at risk of "
    "missing deadlines. Generate a concrete, realistic recovery plan.\n\n"
    "RULES:\n"
    "- Each plan must include specific daily time blocks (day + time window + action)\n"
    "- Use the student's peak productive hours when available\n"
    "- Factor in effort accuracy: if they underestimate by 1.4x, pad time accordingly\n"
    "- If multiple tasks compete, suggest which to deprioritize and why\n"
    "- Be honest if a deadline is unrecoverable — say so and suggest damage control\n"
    "- Keep each plan to 3-5 daily blocks maximum\n"
    "- Reference specific task names and realistic hour counts"
)


class DailyBlock(BaseModel):
    """A single scheduled work block in a recovery plan."""

    day: str
    time_window: str
    action: str


class RecoveryPlan(BaseModel):
    """Structured recovery plan for an at-risk or overdue task."""

    task_title: str
    status: str  # at_risk | overdue
    days_behind: float
    estimated_hours_remaining: float
    daily_blocks: list[DailyBlock]
    tradeoff_note: str


class RecoveryPlanResponse(BaseModel):
    """Schema for LLM-generated recovery plans."""

    plans: list[RecoveryPlan]


def _get_peak_window(patterns: list[BehavioralPattern]) -> str | None:
    """Extract primary peak hours window label from patterns."""
    for p in patterns:
        if p.pattern_type == "peak_hours":
            try:
                data = json.loads(p.value)
                if data.get("rank") == "primary":
                    return data.get("label", "")
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def _build_recovery_prompt(
    at_risk_tasks: list[Task],
    calendar_gaps: list[dict[str, Any]],
    patterns: list[BehavioralPattern],
    session: Session,
) -> str:
    """Build the user prompt for recovery plan generation."""
    lines: list[str] = []

    peak_window = _get_peak_window(patterns)
    if peak_window:
        lines.append(f"Student's peak productive window: {peak_window}")
        lines.append("")

    lines.append("TASKS NEEDING RECOVERY:")
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    for task in at_risk_tasks:
        course = f" ({task.course})" if task.course else ""
        effort_min = estimate_effort(task, session)
        effort_hours = round(effort_min / 60, 1)

        due_info = ""
        status = "at_risk"
        days_behind = 0.0
        if task.due_date_iso:
            try:
                due = datetime.fromisoformat(task.due_date_iso)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=UTC)
                delta = (due - now).total_seconds() / 86400
                if delta < 0:
                    status = "overdue"
                    days_behind = abs(delta)
                    due_info = f" [OVERDUE by {days_behind:.1f} days]"
                else:
                    due_info = f" [due in {delta:.1f} days]"
            except (ValueError, TypeError):
                pass

        lines.append(
            f"  - {task.title}{course}{due_info} "
            f"| est. {effort_hours}h | urgency {task.urgency_score}/5 | {status}"
        )

    if calendar_gaps:
        lines.append("")
        lines.append("AVAILABLE CALENDAR GAPS:")
        for gap in calendar_gaps[:6]:
            start = str(gap.get("start", ""))[:16]
            dur = gap.get("duration_minutes", 0)
            lines.append(f"  - {start} ({dur}min free)")

    lines.append("")
    lines.append(
        "Generate a recovery plan for each task. Include specific daily blocks "
        "and note any tradeoffs (what to deprioritize)."
    )

    return "\n".join(lines)


async def generate_recovery_plans(
    session: Session,
    at_risk_tasks: list[Task],
    calendar_gaps: list[dict[str, Any]],
    patterns: list[BehavioralPattern],
) -> list[RecoveryPlan]:
    """Generate concrete catch-up schedules for at-risk/overdue tasks.

    Uses peak hours, calendar gaps, and effort patterns to produce
    actionable daily work blocks.
    """
    if not at_risk_tasks:
        return []

    from deadline_agent.reasoning.engine import call_llm

    prompt = _build_recovery_prompt(at_risk_tasks, calendar_gaps, patterns, session)
    schema = RecoveryPlanResponse.model_json_schema()

    try:
        raw = await call_llm(RECOVERY_SYSTEM_PROMPT, prompt, schema=schema)
        parsed = RecoveryPlanResponse.model_validate_json(raw)
        return parsed.plans
    except Exception:
        logger.exception("Recovery plan generation failed")
        return []
