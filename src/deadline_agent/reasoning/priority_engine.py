"""Priority Engine — ranks pending tasks into a single ordered answer to 'what should I do right now?'

Combines deadline proximity, effort estimates, progress signals, life context,
behavioral patterns, and relationship signals into a scored, ranked list.
The LLM produces the final ranking with human-readable reasoning.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import BehavioralPattern, LifeContext, PriorityRanking, Task
from deadline_agent.reasoning.negotiation import TYPE_PRIORITY
from deadline_agent.reasoning.scheduler import estimate_effort
from deadline_agent.store.priority_repository import PriorityRepository

logger = logging.getLogger(__name__)


# ── Heuristic scoring (runs without LLM) ──────────────────────────────


def _hours_until_due(task: Task, now: datetime) -> float | None:
    """Hours until task is due. Negative means overdue. None if no due date."""
    if not task.due_date_iso:
        return None
    try:
        due = datetime.fromisoformat(task.due_date_iso)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        return (due - now).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


def _urgency_score(task: Task, now: datetime) -> float:
    """0-1 score based on time pressure. Higher = more urgent."""
    hours = _hours_until_due(task, now)
    if hours is None:
        return 0.3  # no due date — moderate baseline
    if hours < 0:
        return 1.0  # overdue
    if hours < 6:
        return 0.95
    if hours < 24:
        return 0.85
    if hours < 48:
        return 0.7
    if hours < 72:
        return 0.5
    if hours < 168:  # 7 days
        return 0.3
    return 0.15


def _type_score(task: Task) -> float:
    """0-1 score based on task type importance."""
    priority = TYPE_PRIORITY.get(task.type, 1)
    return priority / 5.0


def _progress_score(task: Task, has_work: bool) -> float:
    """0-1 score: higher when no progress detected (needs attention)."""
    if not has_work:
        return 0.8  # no work detected — high signal
    return 0.2  # work detected — less urgent


def _effort_adjusted_score(task: Task, session: Session) -> float:
    """0-1 penalty based on effort needed. Higher effort = more urgent to start."""
    minutes = estimate_effort(task, session)
    if minutes >= 180:
        return 0.8
    if minutes >= 120:
        return 0.6
    if minutes >= 60:
        return 0.4
    return 0.2


def _life_context_boost(task: Task, contexts: list[LifeContext]) -> float:
    """Bonus for tasks aligned with active life context."""
    if not contexts:
        return 0.0
    for ctx in contexts:
        if ctx.season == "exams" and task.type in ("exam", "assignment"):
            return 0.15
        if ctx.season == "recruiting" and task.type in ("interview_prep", "networking"):
            return 0.15
    return 0.0


def _risk_score(task: Task, session: Session) -> float:
    """0-1 score from Phase 28 risk assessment. Higher P(miss) = higher priority."""
    try:
        from deadline_agent.store.risk_repository import RiskRepository

        repo = RiskRepository(session)
        risk = repo.get_for_task(task.id)
        if risk:
            return risk.probability_of_missing
    except Exception:
        pass
    return 0.3  # default if no risk data


def compute_heuristic_score(
    task: Task,
    now: datetime,
    has_work: bool,
    contexts: list[LifeContext],
    session: Session,
) -> float:
    """Compute a 0-1 composite priority score from heuristic signals."""
    # Weights — risk gets significant weight when available
    w_urgency = 0.25
    w_risk = 0.20  # Phase 28: P(miss) from risk engine
    w_type = 0.10
    w_progress = 0.15
    w_effort = 0.15
    w_base_urgency = 0.15  # task's own urgency_score (1-5)

    score = (
        w_urgency * _urgency_score(task, now)
        + w_risk * _risk_score(task, session)
        + w_type * _type_score(task)
        + w_progress * _progress_score(task, has_work)
        + w_effort * _effort_adjusted_score(task, session)
        + w_base_urgency * (task.urgency_score / 5.0)
    )

    # Life context boost (additive, capped at 1.0)
    score = min(1.0, score + _life_context_boost(task, contexts))

    return round(score, 3)


# ── LLM-enhanced ranking ──────────────────────────────────────────────


class PriorityItem(BaseModel):
    """Schema for a single ranked priority from LLM."""

    task_id: int
    rank: int
    risk_if_ignored: str
    suggested_action: str
    reasoning: str


class PrioritiesResponse(BaseModel):
    """Schema for the full LLM priority ranking response."""

    priorities: list[PriorityItem]


PRIORITY_SYSTEM_PROMPT = (
    "You are a priority ranking engine for a personal intelligence system. "
    "Your job is to produce a single, ranked answer to: 'what should I do right now?'\n\n"
    "You will receive:\n"
    "- A list of pending tasks with heuristic scores, deadlines, effort estimates, and progress status\n"
    "- Risk assessments: P(miss) probability, hours needed vs available for each task\n"
    "- Behavioral patterns (how this person actually works)\n"
    "- Active life context (what season they're in)\n"
    "- Relationship signals (people who need follow-up)\n"
    "- Goal gaps (intention vs behavior mismatches)\n\n"
    "RULES:\n"
    "- Rank ALL provided tasks from 1 (do first) to N (do last)\n"
    "- For each task, explain WHY it has that rank in 1 sentence\n"
    "- For each task, state the RISK if ignored in 1 sentence\n"
    "- For each task, suggest a CONCRETE next action (not 'work on it' — be specific)\n"
    "- Factor in behavioral patterns: if they procrastinate on essays, rank those higher\n"
    "- Factor in life context: during exam season, academics trump recruiting\n"
    "- Factor in relationship decay: if a recruiter hasn't heard back, that's urgent\n"
    "- Overdue tasks should generally rank highest unless something else is more critical\n"
    "- Tasks with no work detected and tight deadlines should rank very high\n"
    "- Be honest and direct. Don't hedge."
)


def _build_priority_prompt(
    tasks_with_scores: list[tuple[Task, float, bool, int]],
    patterns: list[BehavioralPattern],
    contexts: list[LifeContext],
    goal_gaps: list[str],
    relationship_alerts: list[str],
    risk_context: list[str] | None = None,
) -> str:
    """Build the user prompt for LLM priority ranking.

    tasks_with_scores: list of (task, heuristic_score, has_work, effort_minutes)
    """
    from deadline_agent.reasoning.state import USER_TZ, _format_pattern

    now = datetime.now(UTC).astimezone(USER_TZ)
    lines = [f"Current time: {now.strftime('%A, %B %d %Y %I:%M %p %Z')}\n"]

    lines.append("TASKS TO RANK:")
    for task, score, has_work, effort in tasks_with_scores:
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

        work_status = "work detected" if has_work else "NO WORK detected"
        course = f" ({task.course})" if task.course else ""
        lines.append(
            f"  - [id={task.id}] {task.title}{course} | "
            f"{time_label} | {work_status} | "
            f"~{effort}min effort | type={task.type} | "
            f"heuristic_score={score:.2f}"
        )

    if risk_context:
        lines.append("\nRISK ASSESSMENTS:")
        for ctx in risk_context:
            lines.append(f"  - {ctx}")

    if patterns:
        lines.append("\nBEHAVIORAL PATTERNS:")
        for p in patterns:
            desc = _format_pattern(p)
            if desc:
                lines.append(f"  - {desc}")

    if contexts:
        lines.append("\nACTIVE LIFE CONTEXT:")
        for ctx in contexts:
            label = f" — {ctx.label}" if ctx.label else ""
            lines.append(f"  - {ctx.season.upper()}{label}: {ctx.start_date} to {ctx.end_date}")

    if goal_gaps:
        lines.append("\nGOAL GAPS (intention vs behavior):")
        for gap in goal_gaps:
            lines.append(f"  - {gap}")

    if relationship_alerts:
        lines.append("\nRELATIONSHIP SIGNALS:")
        for alert in relationship_alerts:
            lines.append(f"  - {alert}")

    lines.append(
        "\nRank all tasks. Return a JSON object with a 'priorities' array. "
        "Each entry must have: task_id (int), rank (int, 1=highest), "
        "risk_if_ignored (string), suggested_action (string), reasoning (string)."
    )

    return "\n".join(lines)


async def compute_priorities(session: Session) -> list[PriorityRanking]:
    """Compute and store priority rankings for all pending tasks.

    1. Gather pending tasks and compute heuristic scores
    2. Call LLM for final ranking with reasoning
    3. Store results in priority_rankings table
    4. Emit PRIORITIES_RECOMPUTED event

    Falls back to heuristic-only ranking if LLM is unavailable.
    """
    from deadline_agent.reasoning.state import build_state_snapshot
    from deadline_agent.store.file_repository import FileActivityRepository

    snapshot = build_state_snapshot(session)
    now = snapshot.now

    # Collect all pending tasks that need ranking
    all_tasks: list[Task] = []
    seen_ids: set[int] = set()
    for task_list in (snapshot.overdue, snapshot.due_today, snapshot.due_this_week):
        for t in task_list:
            if t.id not in seen_ids:
                all_tasks.append(t)
                seen_ids.add(t.id)

    if not all_tasks:
        logger.info("No pending tasks to prioritize")
        return []

    # Compute heuristic scores
    file_repo = FileActivityRepository(session)
    tasks_with_scores: list[tuple[Task, float, bool, int]] = []
    for task in all_tasks:
        has_work = file_repo.has_activity_for_task(task.id, since_hours=168)
        score = compute_heuristic_score(
            task, now, has_work, snapshot.life_contexts, session
        )
        effort = estimate_effort(task, session)
        tasks_with_scores.append((task, score, has_work, effort))

    # Sort by heuristic score descending as baseline
    tasks_with_scores.sort(key=lambda x: x[1], reverse=True)

    # Try LLM ranking
    repo = PriorityRepository(session)
    rankings: list[PriorityRanking] = []

    try:
        prompt = _build_priority_prompt(
            tasks_with_scores,
            snapshot.behavioral_patterns,
            snapshot.life_contexts,
            snapshot.goal_gaps,
            snapshot.relationship_alerts,
            risk_context=snapshot.risk_context,
        )

        schema = PrioritiesResponse.model_json_schema()

        from deadline_agent.reasoning.engine import call_llm

        raw = await call_llm(PRIORITY_SYSTEM_PROMPT, prompt, schema)
        parsed = PrioritiesResponse.model_validate_json(raw)

        # Store LLM rankings
        valid_task_ids = {t.id for t in all_tasks}
        for item in parsed.priorities:
            if item.task_id not in valid_task_ids:
                continue
            # Find the heuristic score for this task
            h_score = next(
                (s for t, s, _, _ in tasks_with_scores if t.id == item.task_id),
                0.5,
            )
            ranking = repo.upsert(
                item.task_id,
                {
                    "rank": item.rank,
                    "score": h_score,
                    "risk_if_ignored": item.risk_if_ignored,
                    "suggested_action": item.suggested_action,
                    "reasoning": item.reasoning,
                },
            )
            rankings.append(ranking)

        session.commit()
        logger.info("Priority engine: ranked %d tasks via LLM", len(rankings))

    except Exception:
        logger.warning("LLM priority ranking failed, using heuristic fallback", exc_info=True)
        # Fallback: use heuristic ordering
        for rank, (task, score, has_work, effort) in enumerate(tasks_with_scores, 1):
            hours = _hours_until_due(task, now)
            if hours is not None and hours < 0:
                risk = f"Already overdue by {abs(hours):.0f}h — further delay worsens the situation"
            elif hours is not None and hours < 24:
                risk = f"Due in {hours:.0f}h — missing this deadline is likely without action now"
            elif not has_work:
                risk = "No work detected — the longer you wait, the harder the catch-up"
            else:
                risk = "Lower urgency, but deprioritizing too long creates future crunch"

            ranking = repo.upsert(
                task.id,
                {
                    "rank": rank,
                    "score": score,
                    "risk_if_ignored": risk,
                    "suggested_action": f"Start working on {task.title} for ~{effort}min",
                    "reasoning": f"Heuristic score: {score:.2f} (deadline proximity + type + progress)",
                },
            )
            rankings.append(ranking)

        session.commit()
        logger.info("Priority engine: ranked %d tasks via heuristic fallback", len(rankings))

    # Emit event
    try:
        from deadline_agent.events import Event, event_bus

        await event_bus.emit(
            Event(
                type="priorities_recomputed",
                payload={"count": len(rankings)},
            )
        )
    except Exception:
        logger.warning("Failed to emit priorities_recomputed event", exc_info=True)

    return rankings


def get_current_priorities(
    session: Session, limit: int = 5
) -> list[dict[str, Any]]:
    """Get current priority rankings as dicts for display.

    Returns a list of dicts with task info + priority data, ordered by rank.
    Does NOT recompute — returns the last computed rankings.
    """
    repo = PriorityRepository(session)
    ranked = repo.get_ranked(limit=limit)

    results: list[dict[str, Any]] = []
    for ranking, task in ranked:
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

        results.append(
            {
                "rank": ranking.rank,
                "task_id": task.id,
                "title": task.title,
                "course": task.course,
                "type": task.type,
                "time_label": time_label,
                "score": ranking.score,
                "risk_if_ignored": ranking.risk_if_ignored,
                "suggested_action": ranking.suggested_action,
                "reasoning": ranking.reasoning,
            }
        )

    return results
