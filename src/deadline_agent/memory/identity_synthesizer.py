"""Identity synthesizer: gathers all data → computes analytics → LLM synthesis → stores.

This is the core of Phase 17. It produces a durable "about you" document that
evolves over time, capturing rhythms, blind spots, values, and growth.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import BehavioralPattern, IdentityDocument

logger = logging.getLogger(__name__)

IDENTITY_SYSTEM_PROMPT = (
    "You are writing a durable 'About You' document for a student's personal "
    "productivity system. This document travels with them — it's their portable "
    "self-knowledge, distilled from months of behavioral data.\n\n"
    "RULES:\n"
    "- Write in second person ('you tend to...', 'your most productive...').\n"
    "- Be specific and data-backed. Use numbers, not vague language.\n"
    "- Be honest but constructive. Blind spots are named clearly, not softened.\n"
    "- Growth is acknowledged concretely ('improved from X to Y').\n"
    "- If previous identity document is provided, EVOLVE it — don't start from scratch. "
    "Preserve what's still true, update what's changed, add new observations.\n"
    "- Organize into these sections: Work Rhythms, Effort Estimation, "
    "Procrastination Profile, Stress Responses, Values & Priorities, "
    "Growth Trajectory, Key People & Organizations, Seasonal Patterns.\n"
    "- Keep it under 600 words. Dense, not padded.\n"
    "- Use markdown formatting with ## headers for each section.\n"
    "- End with a one-line 'Core insight' that captures the most important thing "
    "about this person right now."
)


async def synthesize_identity(session: Session) -> IdentityDocument:
    """Run a full identity synthesis cycle.

    1. Gather all data sources
    2. Compute analytics (pure Python)
    3. LLM synthesis → structured JSON + markdown
    4. Store via IdentityRepository
    """
    from deadline_agent.store.identity_repository import IdentityRepository
    from deadline_agent.store.knowledge_repository import KnowledgeRepository
    from deadline_agent.store.pattern_repository import PatternRepository
    from deadline_agent.store.semester_repository import SemesterRecordRepository
    from deadline_agent.store.snapshot_repository import WeeklySnapshotRepository

    identity_repo = IdentityRepository(session)

    # 1. Gather sources
    pattern_repo = PatternRepository(session)
    patterns = pattern_repo.get_all()

    snapshot_repo = WeeklySnapshotRepository(session)
    snapshots = snapshot_repo.get_recent(limit=52)

    semester_repo = SemesterRecordRepository(session)
    semesters = semester_repo.list_all(limit=5)

    knowledge_repo = KnowledgeRepository(session)
    graph_summary = knowledge_repo.get_graph_summary()

    current_doc = identity_repo.get_current()

    # 2. Compute analytics
    analytics = _compute_analytics(patterns, snapshots, semesters, graph_summary)

    # 3. Build prompt and call LLM
    user_prompt = _build_synthesis_prompt(analytics, current_doc)

    synthesis_sources = {
        "patterns": len(patterns),
        "weekly_snapshots": len(snapshots),
        "semesters": len(semesters),
        "entities": graph_summary.get("entity_count", 0),
        "synthesized_at": datetime.now(UTC).isoformat(),
    }

    try:
        from deadline_agent.reasoning.engine import call_llm

        raw = await call_llm(IDENTITY_SYSTEM_PROMPT, user_prompt)

        # Try to extract from JSON wrapper if present
        try:
            parsed = json.loads(raw)
            markdown = parsed.get("document", parsed.get("markdown", raw))
        except (json.JSONDecodeError, TypeError):
            markdown = raw

    except Exception:
        logger.warning("LLM synthesis failed, storing analytics-only identity", exc_info=True)
        markdown = _fallback_markdown(analytics)

    # Build structured JSON from analytics
    document_json = json.dumps(analytics, ensure_ascii=False, default=str)

    # 4. Store
    doc = identity_repo.upsert(
        document_json=document_json,
        document_markdown=markdown,
        synthesis_sources_json=json.dumps(synthesis_sources),
    )

    # Auto-export if configured
    if settings.identity_export_path:
        try:
            from pathlib import Path

            export_path = Path(settings.identity_export_path).expanduser()
            export_path.parent.mkdir(parents=True, exist_ok=True)
            header = (
                f"# About You — Deadline Agent Identity Document\n"
                f"*Generated: {datetime.now(UTC).strftime('%B %d, %Y')} | "
                f"Version {doc.version}*\n\n"
            )
            export_path.write_text(header + markdown)
            logger.info("Identity exported to %s", export_path)
        except Exception:
            logger.warning("Failed to export identity document", exc_info=True)

    logger.info("Identity synthesized (version %d, %d sources)", doc.version, len(patterns) + len(snapshots))
    return doc


def _compute_analytics(
    patterns: list[BehavioralPattern],
    snapshots: list[Any],
    semesters: list[Any],
    graph_summary: dict[str, Any],
) -> dict[str, Any]:
    """Compute structured analytics from raw data (no LLM)."""
    analytics: dict[str, Any] = {"meta": {"computed_at": datetime.now(UTC).isoformat()}}

    # Work rhythms from patterns
    work_rhythms: dict[str, Any] = {"peak_windows": [], "session_patterns": []}
    for p in patterns:
        try:
            data = json.loads(p.value)
        except (json.JSONDecodeError, TypeError):
            continue

        if p.pattern_type == "peak_hours":
            label = data.get("label", p.pattern_key)
            count = data.get("total_count", data.get("activity_count", 0))
            rank = data.get("rank", "secondary")
            work_rhythms["peak_windows"].append({
                "label": label, "count": count, "rank": rank,
            })
        elif p.pattern_type == "session_duration":
            mean_min = data.get("mean_minutes", 0)
            work_rhythms["session_patterns"].append({
                "type": p.pattern_key, "avg_minutes": round(mean_min),
            })

    # Weekly rhythm from snapshots (which days are heavy)
    if snapshots:
        total_done = sum(s.tasks_completed for s in snapshots)
        total_slipped = sum(s.tasks_slipped for s in snapshots)
        completion_rate = total_done / max(1, total_done + total_slipped)
        work_rhythms["completion_rate"] = round(completion_rate, 2)
        work_rhythms["weeks_of_data"] = len(snapshots)

    analytics["work_rhythms"] = work_rhythms

    # Effort estimation
    effort: dict[str, Any] = {"blind_spots": [], "accurate": []}
    for p in patterns:
        if p.pattern_type != "effort_accuracy":
            continue
        try:
            data = json.loads(p.value)
            ratio = data.get("ratio", 1.0)
            if ratio > 1.3:
                effort["blind_spots"].append({
                    "type": p.pattern_key, "ratio": round(ratio, 2),
                    "description": f"Underestimate {p.pattern_key}s by {ratio:.1f}x",
                })
            elif ratio < 0.8:
                effort["accurate"].append({
                    "type": p.pattern_key, "ratio": round(ratio, 2),
                    "description": f"Overestimate {p.pattern_key}s ({ratio:.1f}x actual)",
                })
            else:
                effort["accurate"].append({
                    "type": p.pattern_key, "ratio": round(ratio, 2),
                    "description": f"{p.pattern_key.title()} estimates are accurate",
                })
        except (json.JSONDecodeError, TypeError):
            continue
    analytics["effort_estimation"] = effort

    # Procrastination profile
    procrastination: dict[str, Any] = {"tendencies": []}
    for p in patterns:
        if p.pattern_type != "procrastination":
            continue
        try:
            data = json.loads(p.value)
            days = data.get("mean_days_before_deadline", 0)
            procrastination["tendencies"].append({
                "type": p.pattern_key, "days_before": round(days, 1),
            })
        except (json.JSONDecodeError, TypeError):
            continue
    analytics["procrastination_profile"] = procrastination

    # Stress responses from health signals in snapshots
    stress: dict[str, Any] = {"late_night_weeks": 0, "zero_activity_weeks": 0}
    for snap in snapshots[:12]:  # last 3 months
        try:
            health = json.loads(snap.health_signals_json)
            if health.get("late_night_days", 0) >= 2:
                stress["late_night_weeks"] += 1
            if health.get("zero_activity_days", 0) >= 3:
                stress["zero_activity_weeks"] += 1
        except (json.JSONDecodeError, TypeError):
            continue
    if snapshots:
        stress["sample_weeks"] = min(12, len(snapshots))
    analytics["stress_responses"] = stress

    # Growth trajectory from semester records
    growth: dict[str, Any] = {"semesters": []}
    for sem in semesters:
        try:
            sem_analytics = json.loads(sem.analytics_json)
        except (json.JSONDecodeError, TypeError):
            sem_analytics = {}
        total = sem.tasks_completed + sem.tasks_slipped
        rate = sem.tasks_completed / max(1, total)
        growth["semesters"].append({
            "term": sem.term_name,
            "completion_rate": round(rate, 2),
            "tasks_completed": sem.tasks_completed,
            "tasks_slipped": sem.tasks_slipped,
            "work_hours": round((sem.total_work_minutes or 0) / 60, 1),
        })
    analytics["growth_trajectory"] = growth

    # Knowledge graph summary
    analytics["knowledge_graph"] = graph_summary

    return analytics


def _build_synthesis_prompt(
    analytics: dict[str, Any],
    current_doc: IdentityDocument | None,
) -> str:
    """Build the user prompt for LLM identity synthesis."""
    parts: list[str] = []

    if current_doc and current_doc.document_markdown.strip():
        parts.append("PREVIOUS IDENTITY DOCUMENT (evolve this, don't replace):")
        parts.append(current_doc.document_markdown)
        parts.append("")

    parts.append("CURRENT DATA:")
    parts.append(json.dumps(analytics, indent=2, default=str))
    parts.append("")
    parts.append(
        "Synthesize this data into an updated identity document. "
        "Output ONLY the markdown document, no preamble."
    )

    return "\n".join(parts)


def _fallback_markdown(analytics: dict[str, Any]) -> str:
    """Generate basic markdown from analytics when LLM is unavailable."""
    lines: list[str] = []

    # Work rhythms
    rhythms = analytics.get("work_rhythms", {})
    lines.append("## Work Rhythms")
    for window in rhythms.get("peak_windows", []):
        lines.append(f"- {window.get('rank', '').title()} window: {window.get('label', '?')} ({window.get('count', 0)} file edits)")
    for session in rhythms.get("session_patterns", []):
        lines.append(f"- Average {session['type']} session: {session['avg_minutes']} minutes")
    if rate := rhythms.get("completion_rate"):
        lines.append(f"- Task completion rate: {rate:.0%} over {rhythms.get('weeks_of_data', '?')} weeks")

    # Effort estimation
    effort = analytics.get("effort_estimation", {})
    lines.append("\n## Effort Estimation")
    for item in effort.get("blind_spots", []):
        lines.append(f"- {item['description']}")
    for item in effort.get("accurate", []):
        lines.append(f"- {item['description']}")

    # Procrastination
    proc = analytics.get("procrastination_profile", {})
    lines.append("\n## Procrastination Profile")
    for t in proc.get("tendencies", []):
        days = t["days_before"]
        if days < 1:
            lines.append(f"- Start {t['type']}s less than a day before deadline")
        else:
            lines.append(f"- Start {t['type']}s {days} days before deadline")

    # Stress
    stress = analytics.get("stress_responses", {})
    lines.append("\n## Stress Responses")
    if stress.get("late_night_weeks"):
        lines.append(f"- Late-night work in {stress['late_night_weeks']}/{stress.get('sample_weeks', '?')} recent weeks")
    if stress.get("zero_activity_weeks"):
        lines.append(f"- Zero-activity days in {stress['zero_activity_weeks']}/{stress.get('sample_weeks', '?')} recent weeks")

    # Growth
    growth = analytics.get("growth_trajectory", {})
    if growth.get("semesters"):
        lines.append("\n## Growth Trajectory")
        for sem in growth["semesters"]:
            lines.append(f"- {sem['term']}: {sem['completion_rate']:.0%} completion ({sem['tasks_completed']} done, {sem['tasks_slipped']} slipped)")

    # Knowledge graph
    kg = analytics.get("knowledge_graph", {})
    if kg.get("by_type"):
        lines.append("\n## Key People & Organizations")
        for etype, names in kg["by_type"].items():
            lines.append(f"- {etype.title()}s: {', '.join(names[:5])}")

    return "\n".join(lines)
