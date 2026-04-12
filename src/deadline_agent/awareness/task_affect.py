"""Task affect inference: classify tasks by engagement pattern and infer energy proxy.

Engagement patterns:
  - enjoyment: early start + high completion rate
  - neutral: moderate timing
  - avoidance: consistently started in final 10% of available time
  - anxiety: frequently skipped or started after deadline

Energy proxy (post-task state):
  - energizing: short gap before next task (<30 min)
  - draining: long gap after session (>2 h)
  - neutral: everything else
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import BehavioralPattern, Task, WorkSession
from deadline_agent.store.pattern_repository import PatternRepository

logger = logging.getLogger(__name__)

AFFECT_LABELS = ("enjoyment", "neutral", "avoidance", "anxiety")

INTERVENTIONS: dict[str, str] = {
    "avoidance": "Start with just 10 minutes to break the seal",
    "anxiety": "What specifically feels hard about this?",
    "enjoyment": "You tend to enjoy this kind of work — lean into that momentum",
    "neutral": "",
}

# Canonical task types — imported from analyzer to stay consistent.
_CANONICAL_TYPES = {
    "assignment", "exam", "meeting", "reminder", "announcement",
    "recruiting", "project", "interview_prep", "networking", "personal",
}
_TYPE_ALIASES: dict[str, str] = {
    "homework": "assignment", "discussion": "assignment", "lesson": "assignment",
    "assessment": "exam", "quiz": "exam", "due_date_hint": "assignment",
    "deadline/assignment": "assignment", "action item": "reminder",
    "task": "assignment", "interview": "interview_prep",
    "coffee chat": "networking", "info session": "networking",
}


def _normalize_type(raw: str) -> str | None:
    lower = raw.lower().strip()
    if lower in _CANONICAL_TYPES:
        return lower
    return _TYPE_ALIASES.get(lower)


def _confidence(n: int, threshold: int = 10) -> float:
    return min(1.0, n / threshold)


@dataclass
class TaskAffectResult:
    """In-memory affect classification result."""

    task_type: str
    affect_label: str  # enjoyment | neutral | avoidance | anxiety
    confidence: float
    evidence: dict  # type: ignore[type-arg]


@dataclass
class EnergyProxyResult:
    """Post-task energy inference."""

    task_type: str
    energy_label: str  # energizing | neutral | draining
    confidence: float
    evidence: dict  # type: ignore[type-arg]


# ── Core classification ──────────────────────────────────────


def classify_task_affect(session: Session) -> list[BehavioralPattern]:
    """Classify each task type by engagement pattern. Stores as BehavioralPattern records.

    Algorithm per task type (minimum 3 samples):
      1. Compute start_lag_pct = (first_session - created_at) / (due_date - created_at)
      2. Compute completion_rate = done_count / total_count
      3. Compute no_work_rate = tasks with zero sessions / total_count
      4. Classify using thresholds (see below)
    """
    repo = PatternRepository(session)

    # Fetch all tasks with a due date and a normalized type
    stmt = select(Task).where(Task.due_date_iso.isnot(None))
    tasks = list(session.scalars(stmt).all())

    # Group by normalized type
    by_type: dict[str, list[Task]] = defaultdict(list)
    for t in tasks:
        ntype = _normalize_type(t.type)
        if ntype:
            by_type[ntype].append(t)

    # Pre-fetch all work sessions keyed by task_id (earliest per task)
    ws_stmt = (
        select(WorkSession.task_id, WorkSession.started_at)
        .order_by(WorkSession.task_id, WorkSession.started_at.asc())
    )
    ws_rows = session.execute(ws_stmt).all()
    first_session_by_task: dict[int, datetime] = {}
    for task_id, started_at in ws_rows:
        if task_id not in first_session_by_task:
            first_session_by_task[task_id] = started_at

    patterns: list[BehavioralPattern] = []

    for task_type, type_tasks in by_type.items():
        if len(type_tasks) < 3:
            continue

        start_lag_pcts: list[float] = []
        done_count = 0
        no_work_count = 0
        total = len(type_tasks)

        for t in type_tasks:
            if t.status == "done":
                done_count += 1

            first_ws = first_session_by_task.get(t.id)
            if first_ws is None:
                no_work_count += 1
                continue

            try:
                due = datetime.fromisoformat(t.due_date_iso)  # type: ignore[arg-type]
                created = t.created_at
                # Ensure both are naive or both are aware for subtraction
                if due.tzinfo and not created.tzinfo:
                    due = due.replace(tzinfo=None)
                elif created.tzinfo and not due.tzinfo:
                    created = created.replace(tzinfo=None)
                if first_ws.tzinfo and not created.tzinfo:
                    first_ws = first_ws.replace(tzinfo=None)
                elif created.tzinfo and not first_ws.tzinfo:
                    created = created.replace(tzinfo=None)

                available = (due - created).total_seconds()
                if available <= 0:
                    continue
                lag = (first_ws - created).total_seconds()
                pct = max(0.0, min(1.0, lag / available))
                start_lag_pcts.append(pct)
            except (ValueError, TypeError):
                continue

        completion_rate = done_count / total if total else 0
        no_work_rate = no_work_count / total if total else 0
        mean_lag = sum(start_lag_pcts) / len(start_lag_pcts) if start_lag_pcts else 1.0

        # Classification thresholds
        if no_work_rate > 0.5:
            label = "anxiety"
        elif mean_lag > 0.9:
            label = "avoidance"
        elif mean_lag < 0.3 and completion_rate > 0.8:
            label = "enjoyment"
        else:
            label = "neutral"

        evidence = {
            "mean_start_lag_pct": round(mean_lag, 3),
            "completion_rate": round(completion_rate, 3),
            "no_work_rate": round(no_work_rate, 3),
            "sample_count": total,
            "tasks_with_sessions": len(start_lag_pcts),
        }

        p = repo.upsert(
            pattern_type="task_affect",
            pattern_key=task_type,
            value=json.dumps(evidence),
            sample_count=total,
            confidence=_confidence(total),
        )
        patterns.append(p)

    logger.info("Task affect analysis: %d type(s) classified", len(patterns))
    return patterns


def compute_energy_proxy(session: Session) -> list[BehavioralPattern]:
    """Infer post-task energy from inter-session gaps, aggregated by task type.

    For each completed work session, measure the gap to the next session (any task).
      - gap < 30 min AND next session exists → energizing
      - gap > 120 min → draining
      - otherwise → neutral
    """
    repo = PatternRepository(session)

    # Fetch all sessions ordered by ended_at
    stmt = select(WorkSession).order_by(WorkSession.ended_at.asc())
    all_sessions = list(session.scalars(stmt).all())

    if len(all_sessions) < 2:
        return []

    # Map task_id → normalized type
    task_ids = {ws.task_id for ws in all_sessions}
    tasks = session.execute(
        select(Task.id, Task.type).where(Task.id.in_(task_ids))
    ).all()
    task_type_map: dict[int, str | None] = {
        tid: _normalize_type(ttype) for tid, ttype in tasks
    }

    # Compute gaps per task type
    gaps_by_type: dict[str, list[float]] = defaultdict(list)

    for i in range(len(all_sessions) - 1):
        current = all_sessions[i]
        next_ws = all_sessions[i + 1]
        ntype = task_type_map.get(current.task_id)
        if not ntype:
            continue

        ended = current.ended_at
        started = next_ws.started_at
        # Normalize tz
        if ended.tzinfo and not started.tzinfo:
            ended = ended.replace(tzinfo=None)
        elif started.tzinfo and not ended.tzinfo:
            started = started.replace(tzinfo=None)

        gap_minutes = (started - ended).total_seconds() / 60
        if gap_minutes >= 0:  # Ignore overlapping sessions
            gaps_by_type[ntype].append(gap_minutes)

    patterns: list[BehavioralPattern] = []

    for task_type, gaps in gaps_by_type.items():
        if len(gaps) < 3:
            continue

        avg_gap = sum(gaps) / len(gaps)
        short_gaps = sum(1 for g in gaps if g < 30)
        long_gaps = sum(1 for g in gaps if g > 120)
        total = len(gaps)

        short_rate = short_gaps / total
        long_rate = long_gaps / total

        if long_rate > 0.5:
            energy_label = "draining"
        elif short_rate > 0.5:
            energy_label = "energizing"
        else:
            energy_label = "neutral"

        evidence = {
            "avg_gap_minutes": round(avg_gap, 1),
            "short_gap_rate": round(short_rate, 3),
            "long_gap_rate": round(long_rate, 3),
            "sample_count": total,
            "energy_label": energy_label,
        }

        p = repo.upsert(
            pattern_type="energy_proxy",
            pattern_key=task_type,
            value=json.dumps(evidence),
            sample_count=total,
            confidence=_confidence(total),
        )
        patterns.append(p)

    logger.info("Energy proxy analysis: %d type(s) classified", len(patterns))
    return patterns


# ── Query helpers ────────────────────────────────────────────


def get_task_affect_map(session: Session) -> dict[str, TaskAffectResult]:
    """Return the current affect map from stored patterns."""
    repo = PatternRepository(session)
    patterns = repo.get_by_type("task_affect")
    result: dict[str, TaskAffectResult] = {}

    for p in patterns:
        try:
            evidence = json.loads(p.value)
        except (json.JSONDecodeError, TypeError):
            evidence = {}

        # Derive label from evidence thresholds
        no_work_rate = evidence.get("no_work_rate", 0)
        mean_lag = evidence.get("mean_start_lag_pct", 0.5)
        completion_rate = evidence.get("completion_rate", 0)

        if no_work_rate > 0.5:
            label = "anxiety"
        elif mean_lag > 0.9:
            label = "avoidance"
        elif mean_lag < 0.3 and completion_rate > 0.8:
            label = "enjoyment"
        else:
            label = "neutral"

        result[p.pattern_key] = TaskAffectResult(
            task_type=p.pattern_key,
            affect_label=label,
            confidence=p.confidence,
            evidence=evidence,
        )

    return result


def get_affect_for_task(session: Session, task: Task) -> TaskAffectResult | None:
    """Look up affect for a single task by its normalized type."""
    ntype = _normalize_type(task.type)
    if not ntype:
        return None
    affect_map = get_task_affect_map(session)
    return affect_map.get(ntype)


def get_energy_for_type(session: Session, task_type: str) -> EnergyProxyResult | None:
    """Look up energy proxy for a task type."""
    repo = PatternRepository(session)
    p = repo.get_one("energy_proxy", task_type)
    if not p:
        return None
    try:
        evidence = json.loads(p.value)
    except (json.JSONDecodeError, TypeError):
        evidence = {}
    return EnergyProxyResult(
        task_type=task_type,
        energy_label=evidence.get("energy_label", "neutral"),
        confidence=p.confidence,
        evidence=evidence,
    )


def get_intervention(affect_label: str) -> str:
    """Return the appropriate intervention text for an affect label."""
    return INTERVENTIONS.get(affect_label, "")
