"""Phase 25: Recruiting report generation.

Computes recruiting pipeline stats and generates LLM-powered weekly reports.
"""

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import BehavioralPattern, RecruitingApplication
from deadline_agent.store.pattern_repository import PatternRepository
from deadline_agent.store.recruiting_repository import RecruitingRepository

logger = logging.getLogger(__name__)

RECRUITING_REPORT_PROMPT = (
    "You are a recruiting strategy advisor. Given the student's recruiting pipeline stats "
    "and analytics, write a concise weekly recruiting report (4-6 sentences). Cover:\n"
    "- Pipeline health: are applications converting? Is the funnel healthy?\n"
    "- What's working: which tiers/methods/timing have the best response rates?\n"
    "- What needs attention: stale applications, over-indexing, gaps\n"
    "- One actionable next step for this week\n\n"
    "Be data-driven and direct. No fluff."
)


def compute_recruiting_stats(session: Session) -> dict[str, Any]:
    """Aggregate recruiting pipeline metrics for reporting."""
    repo = RecruitingRepository(session)
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)

    all_apps = repo.list_all()
    active = [a for a in all_apps if a.status != "closed"]

    # Count by status
    by_status: dict[str, int] = defaultdict(int)
    for app in all_apps:
        by_status[app.status] += 1

    # Overall response rate
    total = len(all_apps)
    responded = sum(
        1 for a in all_apps
        if a.status in ("response", "interview", "offer")
    )
    response_rate = responded / total if total else 0

    # New applications this week
    new_this_week = sum(
        1 for a in all_apps
        if a.created_at and a.created_at.replace(tzinfo=UTC) >= week_ago
    )

    # Status changes this week (signals added this week)
    status_changes = 0
    for app in all_apps:
        try:
            signals = json.loads(app.signals_json)
            for s in signals:
                sig_date = s.get("date", "")
                if sig_date and sig_date >= week_ago.isoformat()[:10]:
                    status_changes += 1
        except (json.JSONDecodeError, TypeError):
            pass

    # Stale count
    stale_count = sum(
        1 for a in active
        if a.last_signal_at
        and (now - a.last_signal_at.replace(tzinfo=UTC)).days >= 14
    )

    # By tier
    by_tier: dict[str, int] = defaultdict(int)
    for app in all_apps:
        by_tier[app.company_tier or "unclassified"] += 1

    # Top fit scores from patterns
    fit_scores: list[dict[str, Any]] = []
    try:
        pattern_repo = PatternRepository(session)
        for p in pattern_repo.get_by_type("recruiting_fit_score"):
            data = json.loads(p.value)
            fit_scores.append({
                "company": data.get("company", p.pattern_key),
                "score": data.get("score", 0),
                "rank": data.get("rank", 99),
            })
        fit_scores.sort(key=lambda x: x["rank"])
    except Exception:
        pass

    return {
        "active_count": len(active),
        "total_count": total,
        "by_status": dict(by_status),
        "response_rate": round(response_rate, 3),
        "new_this_week": new_this_week,
        "status_changes_this_week": status_changes,
        "stale_count": stale_count,
        "by_tier": dict(by_tier),
        "top_fit_scores": fit_scores[:5],
    }


def _stats_to_report_prompt(stats: dict[str, Any]) -> str:
    """Format recruiting stats for LLM report generation."""
    lines = [
        f"Total applications: {stats['total_count']}",
        f"Active: {stats['active_count']}, Stale (14+ days): {stats['stale_count']}",
        f"Response rate: {stats['response_rate']:.0%}",
        f"New this week: {stats['new_this_week']}",
        f"Status changes this week: {stats['status_changes_this_week']}",
        "",
        "By status:",
    ]
    for status, count in stats.get("by_status", {}).items():
        lines.append(f"  {status}: {count}")

    lines.append("")
    lines.append("By tier:")
    for tier, count in stats.get("by_tier", {}).items():
        lines.append(f"  {tier}: {count}")

    fit_scores = stats.get("top_fit_scores", [])
    if fit_scores:
        lines.append("")
        lines.append("Top fit-scored active applications:")
        for fs in fit_scores:
            lines.append(f"  #{fs['rank']} {fs['company']} (score: {fs['score']:.0%})")

    # Include any recruiting patterns
    lines.append("")
    return "\n".join(lines)


async def generate_recruiting_report(session: Session) -> str:
    """Generate an LLM-powered weekly recruiting report."""
    from deadline_agent.config import settings
    from deadline_agent.extraction.extractor import ExtractionError
    from deadline_agent.reasoning.engine import _call_anthropic, _call_ollama

    stats = compute_recruiting_stats(session)

    if stats["total_count"] == 0:
        return "No recruiting applications tracked yet."

    stats_text = _stats_to_report_prompt(stats)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"report": {"type": "string"}},
        "required": ["report"],
    }

    result: str | None = None
    try:
        result = await _call_ollama(RECRUITING_REPORT_PROMPT, stats_text, schema)
    except ExtractionError:
        if settings.use_anthropic_fallback and settings.anthropic_api_key:
            try:
                result = await _call_anthropic(RECRUITING_REPORT_PROMPT, stats_text)
            except ExtractionError:
                pass

    if result is None:
        result = f"Recruiting Report\n\n{stats_text}"

    return result
