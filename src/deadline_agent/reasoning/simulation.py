"""Behavioral simulation engine — what-if projections over your own patterns.

Composes existing BehavioralPatterns and WeeklySnapshots into forward
projections for hypothetical scenarios like adding commitments, dropping
courses, or accepting offers.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from deadline_agent.config import settings
from deadline_agent.reasoning.state import build_state_snapshot

logger = logging.getLogger(__name__)

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
                        "enum": [
                            "academics",
                            "recruiting",
                            "health",
                            "schedule",
                            "social",
                        ],
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


@dataclass
class SimulationResult:
    """Result of a behavioral simulation."""

    scenario: str
    confidence: str
    confidence_reason: str
    projected_impacts: list[dict[str, str]]
    weekly_projection: str
    recommendation: str
    data_density: dict[str, int]


def _assess_data_density(session: Any) -> dict[str, int]:
    """Count how much data we have for reliable projections."""
    from sqlalchemy import func, select

    from deadline_agent.models import (
        BehavioralPattern,
        FileActivity,
        WeeklySnapshot,
        WorkSession,
    )

    pattern_count = session.scalar(
        select(func.count(BehavioralPattern.id))
    ) or 0
    snapshot_count = session.scalar(
        select(func.count(WeeklySnapshot.id))
    ) or 0
    session_count = session.scalar(
        select(func.count(WorkSession.id))
    ) or 0
    activity_count = session.scalar(
        select(func.count(FileActivity.id))
    ) or 0

    return {
        "behavioral_patterns": pattern_count,
        "weekly_snapshots": snapshot_count,
        "work_sessions": session_count,
        "file_activities": activity_count,
    }


def _build_simulation_context(session: Any, scenario: str) -> str:
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

    if settings.reasoning_provider == "anthropic" and settings.anthropic_api_key:
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=settings.anthropic_api_key)
            tool = {
                "name": "simulation_output",
                "description": "Structured behavioral simulation output",
                "input_schema": SIMULATION_SCHEMA,
            }
            response = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=4096,
                system=SIMULATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "simulation_output"},
            )
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
        client = AsyncClient(host=settings.ollama_base_url)
        response = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": SIMULATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            format=SIMULATION_SCHEMA,
        )
        return response.message.content or ""
    except Exception as e:
        raise ExtractionError(f"Simulation LLM call failed: {e}") from e


async def simulate_scenario(session: Any, scenario: str) -> SimulationResult:
    """Run a behavioral simulation for a what-if scenario.

    Composes current state + behavioral patterns + weekly history into a
    forward projection using structured LLM output with 4096 max tokens.
    """
    user_prompt = _build_simulation_context(session, scenario)
    density = _assess_data_density(session)

    raw = await _call_simulation_llm(user_prompt)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # LLM returned plain text — wrap it
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
