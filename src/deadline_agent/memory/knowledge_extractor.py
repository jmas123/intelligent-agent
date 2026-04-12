"""Extract entities and relationships from existing data for the knowledge graph."""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import LifeContext, RecruitingApplication, Task
from deadline_agent.store.knowledge_repository import KnowledgeRepository

logger = logging.getLogger(__name__)


def extract_entities_from_tasks(session: Session, repo: KnowledgeRepository) -> int:
    """Extract course and project entities from tasks."""
    tasks = list(session.scalars(select(Task)).all())
    count = 0

    seen_courses: set[str] = set()
    for task in tasks:
        # Courses
        if task.course and task.course.lower() not in seen_courses:
            seen_courses.add(task.course.lower())
            repo.upsert_entity("course", task.course)
            count += 1

    # Extract people from task titles (e.g., "Prof. Smith", "Dr. Jones")
    prof_pattern = re.compile(r"\b(?:Prof\.?|Dr\.?|Professor)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.IGNORECASE)
    seen_people: set[str] = set()
    for task in tasks:
        for match in prof_pattern.finditer(task.title):
            name = match.group(0).strip()
            if name.lower() not in seen_people:
                seen_people.add(name.lower())
                entity = repo.upsert_entity("person", name, {"role": "professor"})
                count += 1

                # Link professor to course if task has one
                if task.course:
                    course_entity = repo.get_entity("course", task.course)
                    if course_entity:
                        repo.upsert_relationship(
                            entity.id, course_entity.id, "teaches",
                            evidence=f"Task: {task.title}",
                        )

    return count


def extract_entities_from_recruiting(session: Session, repo: KnowledgeRepository) -> int:
    """Extract company entities from recruiting applications."""
    apps = list(session.scalars(select(RecruitingApplication)).all())
    count = 0

    for app in apps:
        entity = repo.upsert_entity(
            "company",
            app.company_name,
            {"status": app.status, "source": app.source},
        )
        count += 1

    return count


def extract_entities_from_contexts(session: Session, repo: KnowledgeRepository) -> int:
    """Extract institutional/seasonal entities from life contexts."""
    contexts = list(session.scalars(select(LifeContext)).all())
    count = 0

    for ctx in contexts:
        if ctx.season in ("exams", "recruiting") and ctx.label:
            repo.upsert_entity(
                "institution" if ctx.season == "exams" else "company",
                ctx.label,
                {"season": ctx.season, "period": f"{ctx.start_date} to {ctx.end_date}"},
            )
            count += 1

    return count


def run_entity_extraction(session: Session) -> int:
    """Run all entity extractors and commit."""
    repo = KnowledgeRepository(session)
    total = 0

    total += extract_entities_from_tasks(session, repo)
    total += extract_entities_from_recruiting(session, repo)
    total += extract_entities_from_contexts(session, repo)

    session.commit()
    logger.info("Knowledge graph: extracted/updated %d entities", total)
    return total
