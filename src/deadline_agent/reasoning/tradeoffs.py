"""Cross-domain tradeoff reasoning: school vs recruiting vs personal."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.models import LifeContext, RecruitingApplication, Task

logger = logging.getLogger(__name__)

# Approximate grade weight by task type (rough heuristic)
_GRADE_WEIGHT: dict[str, str] = {
    "exam": "~20-30% of grade",
    "assignment": "~5-10% of grade",
    "meeting": "attendance",
    "reminder": "low weight",
}


def compute_tradeoff_context(
    tasks: list[Task],
    life_contexts: list[LifeContext],
    session: Session,
) -> list[str]:
    """Generate cross-domain priority comparisons.

    Compares recruiting opportunity rarity against academic task weight,
    factoring in active life contexts.
    """
    statements: list[str] = []

    # Count recruiting applications
    total_apps = session.execute(
        select(func.count(RecruitingApplication.id))
    ).scalar() or 0
    active_apps = session.execute(
        select(func.count(RecruitingApplication.id)).where(
            RecruitingApplication.status != "closed"
        )
    ).scalar() or 0

    if total_apps == 0:
        return statements

    # Find active recruiting stages (response, interview, offer)
    advanced_apps = list(
        session.scalars(
            select(RecruitingApplication).where(
                RecruitingApplication.status.in_(["response", "interview", "offer"])
            )
        ).all()
    )

    # Check for recruiting life context
    recruiting_active = any(c.season == "recruiting" for c in life_contexts)
    exam_active = any(c.season == "exams" for c in life_contexts)

    # Generate tradeoff statements
    if advanced_apps and total_apps > 5:
        # Few responses out of many applications = high value
        for app in advanced_apps:
            if app.status in ("interview", "offer"):
                weight_desc = _GRADE_WEIGHT.get("assignment", "~5-10% of grade")
                # Find competing academic tasks
                competing = [
                    t for t in tasks
                    if t.type in ("assignment", "meeting", "reminder")
                    and t.urgency_score <= 3
                ]
                if competing:
                    low_priority = competing[0]
                    course_note = f" ({low_priority.course})" if low_priority.course else ""
                    statements.append(
                        f"{app.company_name} is one of {len(advanced_apps)} active "
                        f"response(s) in {total_apps} applications — prioritize "
                        f"interview prep over {low_priority.title}{course_note} "
                        f"({weight_desc})."
                    )
                    break  # One tradeoff statement per cycle

    # Recruiting vs exam season conflict
    if recruiting_active and exam_active:
        interview_tasks = [t for t in tasks if t.type == "interview_prep"]
        exam_tasks = [t for t in tasks if t.type == "exam"]
        if interview_tasks and exam_tasks:
            statements.append(
                f"Recruiting and exams overlap: {len(interview_tasks)} interview prep "
                f"task(s) competing with {len(exam_tasks)} exam(s). Exams are "
                f"non-negotiable deadlines — schedule interview prep around them."
            )

    # Recruiting season with no advanced applications
    if recruiting_active and not advanced_apps and total_apps > 10:
        statements.append(
            f"{total_apps} applications sent, 0 active responses. "
            f"Consider pausing new applications and focusing on academics "
            f"until you hear back."
        )

    return statements
