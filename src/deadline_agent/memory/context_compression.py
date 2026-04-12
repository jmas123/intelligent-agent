"""Tiered context compression: recent weeks full, older as summaries, oldest as patterns.

Replaces the flat 4-week window from context_window.build_compressed_history
with a sliding window that preserves months of history without blowing the context budget.
"""

import json
import logging

from sqlalchemy.orm import Session

from deadline_agent.models import WeeklySnapshot

logger = logging.getLogger(__name__)


def build_tiered_history(session: Session) -> str:
    """Build tiered compressed history from WeeklySnapshots.

    Tier 1 (weeks 1-2):  Full detail — narrative, stats, health, life contexts.
    Tier 2 (weeks 3-8):  One-line summary per week.
    Tier 3 (weeks 9+):   Aggregated into 4-week blocks.
    """
    from deadline_agent.store.snapshot_repository import WeeklySnapshotRepository

    repo = WeeklySnapshotRepository(session)
    # Get up to a year of snapshots
    snapshots = repo.get_recent(limit=52)

    if not snapshots:
        return ""

    lines: list[str] = []

    for i, snap in enumerate(snapshots):
        if i < 2:
            # Tier 1: Full detail
            lines.append(_format_full_detail(snap))
        elif i < 8:
            # Tier 2: One-line summary
            lines.append(_format_summary(snap))
        else:
            break  # Tier 3 handles the rest

    # Tier 3: Aggregate remaining snapshots into 4-week blocks
    if len(snapshots) > 8:
        blocks = _aggregate_blocks(snapshots[8:], block_size=4)
        for block in blocks:
            lines.append(block)

    return "\n".join(lines)


def _format_full_detail(snap: WeeklySnapshot) -> str:
    """Tier 1: Full detail for recent weeks."""
    parts: list[str] = []
    week_label = f"  Week of {snap.week_start}"
    if snap.semester_week_number is not None:
        week_label += f" (wk {snap.semester_week_number})"

    work_hours = round(snap.total_work_minutes / 60, 1) if snap.total_work_minutes else 0
    stats = f"{snap.tasks_completed} done, {snap.tasks_slipped} slipped, {work_hours}h worked"

    # Life context
    try:
        contexts = json.loads(snap.life_contexts_json)
        if contexts:
            seasons = [c.get("season", "") for c in contexts if c.get("season")]
            if seasons:
                stats += f". {', '.join(s.title() for s in seasons)} active"
    except (json.JSONDecodeError, TypeError):
        pass

    parts.append(f"{week_label}: {stats}")

    # Health signals
    try:
        health = json.loads(snap.health_signals_json)
        signals: list[str] = []
        if health.get("late_night_days"):
            signals.append(f"{health['late_night_days']} late nights")
        if health.get("zero_activity_days", 0) >= 2:
            signals.append(f"{health['zero_activity_days']} zero-activity days")
        if signals:
            parts.append(f"    Health: {', '.join(signals)}")
    except (json.JSONDecodeError, TypeError):
        pass

    # Full narrative
    if snap.narrative.strip():
        parts.append(f"    \"{snap.narrative.strip()}\"")

    return "\n".join(parts)


def _format_summary(snap: WeeklySnapshot) -> str:
    """Tier 2: One-line summary for recent-ish weeks."""
    week_label = f"  Week of {snap.week_start}"
    if snap.semester_week_number is not None:
        week_label += f" (wk {snap.semester_week_number})"

    work_hours = round(snap.total_work_minutes / 60, 1) if snap.total_work_minutes else 0
    stats = f"{snap.tasks_completed} done, {snap.tasks_slipped} slipped, {work_hours}h worked"

    # Truncated narrative
    narrative = snap.narrative.strip()
    if narrative:
        excerpt = narrative[:80].rsplit(" ", 1)[0] if len(narrative) > 80 else narrative
        if len(narrative) > 80:
            excerpt += "..."
        return f"{week_label}: {stats}. \"{excerpt}\""
    return f"{week_label}: {stats}"


def _aggregate_blocks(snapshots: list[WeeklySnapshot], block_size: int = 4) -> list[str]:
    """Tier 3: Aggregate snapshots into N-week blocks."""
    blocks: list[str] = []

    for i in range(0, len(snapshots), block_size):
        chunk = snapshots[i : i + block_size]
        if not chunk:
            break

        total_done = sum(s.tasks_completed for s in chunk)
        total_slipped = sum(s.tasks_slipped for s in chunk)
        total_minutes = sum(s.total_work_minutes or 0 for s in chunk)
        avg_done = total_done / len(chunk)
        work_hours = round(total_minutes / 60, 1)

        # Date range
        start = chunk[-1].week_start  # oldest in chunk (snapshots are desc)
        end = chunk[0].week_end

        # Collect active seasons across the block
        seasons: set[str] = set()
        for snap in chunk:
            try:
                contexts = json.loads(snap.life_contexts_json)
                for c in contexts:
                    if s := c.get("season"):
                        seasons.add(s.title())
            except (json.JSONDecodeError, TypeError):
                pass

        summary = f"  Weeks {start} to {end}: avg {avg_done:.1f} tasks/week, "
        summary += f"{total_done} done, {total_slipped} slipped, {work_hours}h total"
        if seasons:
            summary += f". {', '.join(sorted(seasons))} active"

        blocks.append(summary)

    return blocks
