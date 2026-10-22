"""Behavioral simulation engine — what-if projections over your own patterns.

Phase 23: LLM-based narrative projections.
Phase 31: Deterministic risk recomputation under scenarios + grounded LLM narrative.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.reasoning.state import build_state_snapshot

logger = logging.getLogger(__name__)

# ── Phase 23 constants ──────────────────────────────────────────────

SIMULATION_SYSTEM_PROMPT = (
    "You are a behavioral simulation engine for a university student. You have "
    "access to their real behavioral data: work patterns, peak hours, effort "
    "accuracy, sleep signals, health signals, and weekly history. "
    "Given a hypothetical scenario (a 'what-if'), project how their life would "
    "change over the next 2-4 weeks. Be concrete and quantitative where the data "
    "supports it. Be honest when the data is too sparse for a reliable projection. "
    "Always ground projections in the specific patterns provided — never invent "
    "data points. Structure your response as the simulation_output tool."
)

SIMULATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenario_understood": {
            "type": "string",
            "description": "One-sentence restatement of the scenario being simulated",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "How confident you are in this projection given available data",
        },
        "confidence_reason": {
            "type": "string",
            "description": "Why confidence is at this level (data density, pattern reliability)",
        },
        "projected_impacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": ["academics", "recruiting", "health", "schedule", "social"],
                    },
                    "impact": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["positive", "neutral", "concerning", "critical"],
                    },
                },
                "required": ["domain", "impact", "severity"],
            },
            "description": "Projected impacts across life domains",
        },
        "weekly_projection": {
            "type": "string",
            "description": "Week-by-week narrative of how the next 2-4 weeks would unfold under this scenario",
        },
        "recommendation": {
            "type": "string",
            "description": "What you'd actually recommend given this projection",
        },
    },
    "required": [
        "scenario_understood",
        "confidence",
        "confidence_reason",
        "projected_impacts",
        "weekly_projection",
        "recommendation",
    ],
}


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class SimulationResult:
    """Result of a behavioral simulation (Phase 23)."""

    scenario: str
    confidence: str
    confidence_reason: str
    projected_impacts: list[dict[str, str]]
    weekly_projection: str
    recommendation: str
    data_density: dict[str, int]


@dataclass
class ScenarioModification:
    """A single atomic change to the current state for simulation."""

    type: str  # skip_event | add_event | remove_task | add_task | change_deadline | reduce_hours
    event_summary: str | None = None
    event_start: str | None = None
    event_end: str | None = None
    task_id: int | None = None
    task_title: str | None = None
    task_type: str | None = None
    task_due_date: str | None = None
    hours_estimate: float | None = None
    reduce_hours: float | None = None
    reduce_days: int | None = None


@dataclass
class ScenarioRiskDelta:
    """Before/after risk comparison for a single task."""

    task_id: int
    task_title: str
    course: str | None
    p_miss_before: float
    p_miss_after: float
    delta: float
    risk_level_before: str
    risk_level_after: str
    hours_available_before: float
    hours_available_after: float


@dataclass
class EnhancedSimulationResult:
    """Extended simulation result with grounded risk deltas (Phase 31)."""

    scenario: str
    confidence: str
    confidence_reason: str
    projected_impacts: list[dict[str, str]]
    weekly_projection: str
    recommendation: str
    data_density: dict[str, int]
    modifications: list[ScenarioModification]
    risk_deltas: list[ScenarioRiskDelta]
    projected_schedule: list[dict[str, Any]]
    drop_analysis: dict[str, Any] | None
    session_id: str | None


# ── Session management for conversational follow-up ──────────────────

_simulation_sessions: dict[str, list[ScenarioModification]] = {}


def _get_or_create_session(
    session_id: str | None,
) -> tuple[str, list[ScenarioModification]]:
    """Get or create a simulation session for conversational follow-up."""
    if session_id and session_id in _simulation_sessions:
        return session_id, _simulation_sessions[session_id]
    new_id = str(uuid.uuid4())[:8]
    _simulation_sessions[new_id] = []
    return new_id, []


# ── Phase 23: Original simulation (kept for backward compat) ─────────


def _assess_data_density(session: Any) -> dict[str, int]:
    """Count how much data we have for reliable projections."""
    from sqlalchemy import func, select

    from deadline_agent.models import (
        BehavioralPattern,
        FileActivity,
        WeeklySnapshot,
        WorkSession,
    )

    pattern_count = session.scalar(select(func.count(BehavioralPattern.id))) or 0
    snapshot_count = session.scalar(select(func.count(WeeklySnapshot.id))) or 0
    session_count = session.scalar(select(func.count(WorkSession.id))) or 0
    activity_count = session.scalar(select(func.count(FileActivity.id))) or 0

    return {
        "behavioral_patterns": pattern_count,
        "weekly_snapshots": snapshot_count,
        "work_sessions": session_count,
        "file_activities": activity_count,
    }


def _build_simulation_context(
    session: Any,
    scenario: str,
    risk_deltas: list[ScenarioRiskDelta] | None = None,
    projected_schedule: list[dict[str, Any]] | None = None,
    drop_analysis: dict[str, Any] | None = None,
) -> str:
    """Build the user prompt with all behavioral data for the LLM."""
    snapshot = build_state_snapshot(session)
    prompt = snapshot.to_prompt()

    # Add data density assessment
    density = _assess_data_density(session)
    prompt += "\n\nDATA DENSITY (for confidence calibration):\n"
    for key, count in density.items():
        prompt += f"  - {key.replace('_', ' ')}: {count}\n"

    # Add weekly history if available
    from deadline_agent.store.snapshot_repository import WeeklySnapshotRepository

    snap_repo = WeeklySnapshotRepository(session)
    recent_weeks = snap_repo.get_recent(limit=8)
    if recent_weeks:
        prompt += "\nWEEKLY HISTORY (most recent first):\n"
        for w in recent_weeks:
            prompt += (
                f"  - Week of {w.week_start}: "
                f"{w.tasks_completed} done, {w.tasks_slipped} slipped, "
                f"{w.total_work_minutes}min worked\n"
            )

    # Phase 31: Add grounded risk deltas
    if risk_deltas:
        prompt += "\nRISK DELTAS (deterministic, grounded — use these exact numbers):\n"
        for rd in risk_deltas:
            sign = "+" if rd.delta > 0 else ""
            prompt += (
                f"  - {rd.task_title}"
                f"{' (' + rd.course + ')' if rd.course else ''}: "
                f"P(miss) {rd.p_miss_before:.0%} → {rd.p_miss_after:.0%} "
                f"({sign}{rd.delta:.0%}), "
                f"risk: {rd.risk_level_before} → {rd.risk_level_after}, "
                f"hours available: {rd.hours_available_before:.1f} → {rd.hours_available_after:.1f}\n"
            )
        prompt += "  IMPORTANT: Ground your narrative in these specific numbers. Do not invent different risk values.\n"

    # Phase 31: Add projected schedule
    if projected_schedule:
        prompt += "\nPROJECTED FREE WINDOWS (under scenario):\n"
        for gap in projected_schedule[:10]:
            prompt += f"  - {gap['start']} to {gap['end']} ({gap['duration_minutes']} min)\n"

    # Phase 31: Add drop analysis
    if drop_analysis:
        prompt += "\nDROP ANALYSIS:\n"
        prompt += f"  Dropped: {drop_analysis.get('dropped_task', '?')}\n"
        prompt += f"  Freed hours: {drop_analysis.get('freed_hours', 0):.1f}h\n"
        prompt += f"  Net risk change: {drop_analysis.get('net_risk_change', 0):+.0%}\n"
        for b in drop_analysis.get("beneficiaries", []):
            prompt += f"  Benefits: {b['task_title']} ({b['delta']:+.0%})\n"

    prompt += f"\n\nSCENARIO TO SIMULATE:\n{scenario}\n"
    prompt += (
        "\nProject what happens to this student's academics, health, schedule, "
        "and social life over the next 2-4 weeks under this scenario. "
        "Ground every projection in the specific data above."
    )

    return prompt


async def _call_simulation_llm(user_prompt: str) -> str:
    """Call LLM with higher token limit for simulation responses."""
    from deadline_agent.extraction.extractor import ExtractionError
    from deadline_agent.reasoning.engine import _log_anthropic_usage, get_anthropic_client

    if settings.reasoning_provider == "anthropic" and settings.anthropic_api_key:
        try:
            client = get_anthropic_client()
            tool = {
                "name": "simulation_output",
                "description": "Structured behavioral simulation output",
                "input_schema": SIMULATION_SCHEMA,
            }
            response = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=2048,
                system=[
                    {"type": "text", "text": SIMULATION_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": user_prompt}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "simulation_output"},
            )
            _log_anthropic_usage(response, settings.anthropic_model)
            for block in response.content:
                if block.type == "tool_use":
                    return json.dumps(block.input)
            raise ExtractionError("No tool_use block in response")
        except ExtractionError:
            raise
        except Exception as e:
            logger.warning("Anthropic simulation failed, trying Ollama: %s", e)

    # Fallback to Ollama or primary Ollama
    try:
        from ollama import AsyncClient

        model = settings.lora_reasoning_model or settings.reasoning_model or settings.extraction_model
        ollama_client = AsyncClient(host=settings.ollama_base_url)
        response = await ollama_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": SIMULATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            format=SIMULATION_SCHEMA,
        )
        return response.message.content or ""
    except Exception as e:
        from deadline_agent.extraction.extractor import ExtractionError

        raise ExtractionError(f"Simulation LLM call failed: {e}") from e


async def simulate_scenario(session: Any, scenario: str) -> SimulationResult:
    """Run a behavioral simulation (Phase 23, backward compatible)."""
    user_prompt = _build_simulation_context(session, scenario)
    density = _assess_data_density(session)

    raw = await _call_simulation_llm(user_prompt)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return SimulationResult(
            scenario=scenario,
            confidence="low",
            confidence_reason="LLM did not return structured output",
            projected_impacts=[],
            weekly_projection=raw,
            recommendation="",
            data_density=density,
        )

    return SimulationResult(
        scenario=data.get("scenario_understood", scenario),
        confidence=data.get("confidence", "low"),
        confidence_reason=data.get("confidence_reason", ""),
        projected_impacts=data.get("projected_impacts", []),
        weekly_projection=data.get("weekly_projection", ""),
        recommendation=data.get("recommendation", ""),
        data_density=density,
    )


# ── Phase 31: Scenario parsing ──────────────────────────────────────

SCENARIO_PARSE_TOOL = {
    "name": "parse_scenario",
    "description": "Parse a natural language what-if scenario into structured modifications.",
    "input_schema": {
        "type": "object",
        "properties": {
            "modifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "skip_event",
                                "add_event",
                                "remove_task",
                                "add_task",
                                "change_deadline",
                                "reduce_hours",
                            ],
                        },
                        "event_summary": {"type": "string"},
                        "event_start": {"type": "string"},
                        "event_end": {"type": "string"},
                        "task_id": {"type": "integer"},
                        "task_title": {"type": "string"},
                        "task_type": {"type": "string"},
                        "task_due_date": {"type": "string"},
                        "hours_estimate": {"type": "number"},
                        "reduce_hours": {"type": "number"},
                        "reduce_days": {"type": "integer"},
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["modifications"],
    },
}


async def _parse_scenario_modifications(
    scenario: str, session: Session
) -> list[ScenarioModification]:
    """Convert natural language scenario to structured modifications via LLM."""
    from sqlalchemy import select

    from deadline_agent.models import Task

    # Build context: pending tasks + upcoming calendar events
    tasks = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso.is_not(None))
            .order_by(Task.due_date_iso.asc())
            .limit(30)
        ).all()
    )

    task_list = "\n".join(
        f"  - ID={t.id} | {t.title} | type={t.type} | due={t.due_date_iso}"
        + (f" | course={t.course}" if t.course else "")
        for t in tasks
    )

    # Fetch upcoming calendar events
    now = datetime.now(UTC)
    try:
        from deadline_agent.reasoning.calendar_gaps import fetch_events

        events = await fetch_events(now.isoformat(), (now + timedelta(days=14)).isoformat())
    except Exception:
        events = []

    event_list = "\n".join(
        f"  - {e.get('summary', '?')} | {e.get('start', '?')} — {e.get('end', '?')}"
        for e in (events or [])
        if "T" in e.get("start", "")  # skip all-day events
    )

    prompt = (
        f"Scenario: \"{scenario}\"\n\n"
        f"Pending tasks:\n{task_list or '  (none)'}\n\n"
        f"Upcoming calendar events (next 14 days):\n{event_list or '  (none)'}\n\n"
        "Parse this scenario into one or more modifications. "
        "Match references like 'skip class' or 'drop CS 301' to actual tasks/events above."
    )

    try:
        from deadline_agent.reasoning.engine import get_anthropic_client

        client = get_anthropic_client()
        response = await client.messages.create(
            model=settings.anthropic_model
            if settings.reasoning_provider == "anthropic"
            else "claude-haiku-4-5-20251001",
            max_tokens=512,
            system="You parse what-if scenarios into structured modifications. Use the task IDs and event summaries provided.",
            messages=[{"role": "user", "content": prompt}],
            tools=[SCENARIO_PARSE_TOOL],
            tool_choice={"type": "tool", "name": "parse_scenario"},
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "parse_scenario":
                mods_raw = block.input.get("modifications", [])
                return [ScenarioModification(**m) for m in mods_raw]

    except Exception:
        logger.warning("Scenario parsing failed, using raw scenario", exc_info=True)

    # Fallback: return empty modifications (LLM narrative will handle it)
    return []


# ── Phase 31: Deterministic risk recomputation ──────────────────────


def _build_modified_busy_blocks(
    original_busy: list[dict[str, str]],
    modifications: list[ScenarioModification],
    events: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Apply skip/add event modifications to busy blocks."""
    modified = list(original_busy)

    for mod in modifications:
        if mod.type == "skip_event" and mod.event_summary:
            # Remove busy block matching the event
            summary_lower = mod.event_summary.lower()
            # Find matching event to get its time
            for ev in events:
                if summary_lower in ev.get("summary", "").lower():
                    ev_start = ev.get("start", "")
                    ev_end = ev.get("end", "")
                    # Remove the matching busy block
                    modified = [
                        b
                        for b in modified
                        if not (b["start"] == ev_start and b["end"] == ev_end)
                    ]
                    # Also try fuzzy time matching (busy blocks might not align exactly)
                    if ev_start:
                        modified = [
                            b
                            for b in modified
                            if not (ev_start <= b["start"] and b["end"] <= ev_end)
                        ]
                    break

        elif mod.type == "add_event" and mod.event_start and mod.event_end:
            modified.append({"start": mod.event_start, "end": mod.event_end})

    return modified


def _compute_hours_available_from_gaps(
    task_due_iso: str | None,
    gaps: list[dict[str, Any]],
    focus_discount: float,
    now: datetime,
) -> float:
    """Compute available hours from pre-computed gaps before a task's deadline."""
    if not task_due_iso:
        return 100.0  # no deadline — plenty of time

    try:
        due = datetime.fromisoformat(task_due_iso)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return 100.0

    if due <= now:
        return 0.0

    total_minutes = 0.0
    for gap in gaps:
        try:
            gap_start = datetime.fromisoformat(gap["start"])
            gap_end = datetime.fromisoformat(gap["end"])
        except (ValueError, KeyError):
            continue

        # Only count gaps before deadline
        if gap_start >= due:
            continue
        effective_end = min(gap_end, due)
        if effective_end > gap_start:
            total_minutes += (effective_end - gap_start).total_seconds() / 60

    return round((total_minutes / 60.0) * focus_discount, 2)


async def _compute_scenario_risks(
    session: Session, modifications: list[ScenarioModification]
) -> tuple[list[ScenarioRiskDelta], list[dict[str, Any]]]:
    """Recompute P(miss) for all tasks under modified scenario.

    Returns (risk_deltas, projected_schedule_gaps).
    """
    from deadline_agent.models import BehavioralPattern, Task
    from deadline_agent.reasoning.calendar_gaps import fetch_free_busy, find_gaps
    from deadline_agent.reasoning.risk_engine import (
        _classify_risk,
        _compute_probability_of_missing,
        _estimate_hours_needed,
        _get_focus_discount,
    )
    from deadline_agent.store.risk_repository import RiskRepository
    from deadline_agent.store.session_repository import WorkSessionRepository

    now = datetime.now(UTC)
    two_weeks = (now + timedelta(days=14)).isoformat()

    # Get current baseline risks
    risk_repo = RiskRepository(session)
    current_risks = {r.task_id: r for r, _ in risk_repo.get_all_active()}

    # Get patterns and focus discount
    from sqlalchemy import select

    patterns = list(session.scalars(select(BehavioralPattern)).all())
    focus_discount = _get_focus_discount(patterns)

    # Fetch calendar data once
    try:
        original_busy = await fetch_free_busy(now.isoformat(), two_weeks)
    except Exception:
        original_busy = []

    try:
        from deadline_agent.reasoning.calendar_gaps import fetch_events

        events = await fetch_events(now.isoformat(), two_weeks)
    except Exception:
        events = []

    # Apply modifications to busy blocks
    modified_busy = _build_modified_busy_blocks(original_busy, modifications, events or [])

    # Compute gaps under modified calendar
    modified_gaps = find_gaps(modified_busy, now.isoformat(), two_weeks, min_minutes=30)

    # Handle task modifications
    removed_task_ids = {
        mod.task_id for mod in modifications if mod.type == "remove_task" and mod.task_id
    }

    # Handle reduce_hours
    for mod in modifications:
        if mod.type == "reduce_hours" and mod.reduce_hours and mod.reduce_days:
            # Shrink each gap by reduce_hours/day for reduce_days days
            # Simple approach: reduce total available proportionally
            pass  # handled via gap adjustment below

    # Get pending tasks
    pending_tasks = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso.is_not(None))
        ).all()
    )

    ws_repo = WorkSessionRepository(session)
    deltas: list[ScenarioRiskDelta] = []

    for task in pending_tasks:
        if task.id in removed_task_ids:
            continue  # dropped from scenario

        hours_worked = ws_repo.get_total_duration(task.id) / 60.0
        hours_needed = _estimate_hours_needed(task, hours_worked, session)

        # New hours_available under modified calendar
        hours_available_after = _compute_hours_available_from_gaps(
            task.due_date_iso, modified_gaps, focus_discount, now
        )

        # Apply reduce_hours
        for mod in modifications:
            if mod.type == "reduce_hours" and mod.reduce_hours and mod.reduce_days:
                hours_available_after = max(
                    0, hours_available_after - (mod.reduce_hours * mod.reduce_days)
                )

        # Recompute P(miss)
        p_miss_after = _compute_probability_of_missing(
            hours_needed, hours_available_after, hours_worked, task, now, patterns
        )
        risk_level_after = _classify_risk(p_miss_after)

        # Get baseline
        baseline = current_risks.get(task.id)
        if baseline:
            p_miss_before = baseline.probability_of_missing
            risk_level_before = baseline.risk_level
            hours_available_before = baseline.hours_available
        else:
            # No existing risk — compute baseline from original gaps
            original_gaps = find_gaps(original_busy, now.isoformat(), two_weeks, min_minutes=30)
            hours_available_before = _compute_hours_available_from_gaps(
                task.due_date_iso, original_gaps, focus_discount, now
            )
            p_miss_before = _compute_probability_of_missing(
                hours_needed, hours_available_before, hours_worked, task, now, patterns
            )
            risk_level_before = _classify_risk(p_miss_before)

        delta = round(p_miss_after - p_miss_before, 2)

        deltas.append(
            ScenarioRiskDelta(
                task_id=task.id,
                task_title=task.title,
                course=task.course,
                p_miss_before=p_miss_before,
                p_miss_after=p_miss_after,
                delta=delta,
                risk_level_before=risk_level_before,
                risk_level_after=risk_level_after,
                hours_available_before=hours_available_before,
                hours_available_after=hours_available_after,
            )
        )

    # Sort by absolute delta descending (biggest impact first)
    deltas.sort(key=lambda d: abs(d.delta), reverse=True)

    return deltas, modified_gaps


# ── Phase 31: Drop/add analysis ─────────────────────────────────────


def _compute_drop_analysis(
    session: Session,
    modifications: list[ScenarioModification],
    risk_deltas: list[ScenarioRiskDelta],
) -> dict[str, Any] | None:
    """Compute impact of dropping a task."""
    removed = [m for m in modifications if m.type == "remove_task" and m.task_id]
    if not removed:
        return None

    from deadline_agent.models import Task
    from deadline_agent.reasoning.scheduler import estimate_effort

    mod = removed[0]
    task = session.get(Task, mod.task_id)
    if not task:
        return None

    freed_minutes = estimate_effort(task, session)
    freed_hours = round(freed_minutes / 60.0, 1)

    # Find tasks that benefit most (negative delta = risk decreased)
    beneficiaries = [
        {"task_title": d.task_title, "delta": d.delta, "p_miss_after": d.p_miss_after}
        for d in risk_deltas
        if d.delta < 0
    ][:3]

    net_risk_change = sum(d.delta for d in risk_deltas) if risk_deltas else 0.0

    return {
        "dropped_task": task.title,
        "dropped_course": task.course,
        "dropped_due": task.due_date_iso,
        "freed_hours": freed_hours,
        "beneficiaries": beneficiaries,
        "net_risk_change": round(net_risk_change, 2),
    }


# ── Phase 31: Enhanced simulation entry point ────────────────────────


async def simulate_scenario_v2(
    session: Session,
    scenario: str,
    session_id: str | None = None,
) -> EnhancedSimulationResult:
    """Run a grounded what-if simulation with deterministic risk deltas.

    1. Parse scenario → structured modifications
    2. Accumulate with session for follow-up
    3. Recompute risks deterministically
    4. Feed grounded deltas to LLM for narrative
    5. Return enhanced result
    """
    density = _assess_data_density(session)

    # 1. Session management
    sid, accumulated = _get_or_create_session(session_id)

    # 2. Parse new modifications
    new_mods = await _parse_scenario_modifications(scenario, session)

    # 3. Accumulate
    accumulated.extend(new_mods)
    _simulation_sessions[sid] = accumulated

    # 4. Deterministic risk recomputation
    risk_deltas, projected_schedule = await _compute_scenario_risks(session, accumulated)

    # 5. Drop analysis
    drop_analysis = _compute_drop_analysis(session, accumulated, risk_deltas)

    # 6. Build grounded LLM prompt
    user_prompt = _build_simulation_context(
        session, scenario, risk_deltas, projected_schedule, drop_analysis
    )

    # 7. LLM narrative
    try:
        raw = await _call_simulation_llm(user_prompt)
        data = json.loads(raw)
    except Exception:
        data = {
            "scenario_understood": scenario,
            "confidence": "low",
            "confidence_reason": "LLM unavailable — showing deterministic results only",
            "projected_impacts": [],
            "weekly_projection": "",
            "recommendation": "",
        }

    return EnhancedSimulationResult(
        scenario=data.get("scenario_understood", scenario),
        confidence=data.get("confidence", "low"),
        confidence_reason=data.get("confidence_reason", ""),
        projected_impacts=data.get("projected_impacts", []),
        weekly_projection=data.get("weekly_projection", ""),
        recommendation=data.get("recommendation", ""),
        data_density=density,
        modifications=accumulated,
        risk_deltas=risk_deltas,
        projected_schedule=projected_schedule,
        drop_analysis=drop_analysis,
        session_id=sid,
    )
