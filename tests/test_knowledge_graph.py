"""Tests for the personal knowledge graph."""

from sqlalchemy.orm import Session

from deadline_agent.models import KnowledgeEntity, RecruitingApplication, Task


class TestKnowledgeRepository:
    def test_upsert_entity_creates(self, session: Session) -> None:
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        repo = KnowledgeRepository(session)
        entity = repo.upsert_entity("course", "CS 101")
        assert entity.id is not None
        assert entity.name == "CS 101"
        assert entity.normalized_name == "cs 101"
        assert entity.mention_count == 1

    def test_upsert_entity_increments_mentions(self, session: Session) -> None:
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        repo = KnowledgeRepository(session)
        repo.upsert_entity("course", "CS 101")
        entity = repo.upsert_entity("course", "CS 101")
        assert entity.mention_count == 2

    def test_upsert_entity_case_insensitive(self, session: Session) -> None:
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        repo = KnowledgeRepository(session)
        repo.upsert_entity("course", "CS 101")
        entity = repo.upsert_entity("course", "cs 101")
        assert entity.mention_count == 2

    def test_upsert_entity_merges_metadata(self, session: Session) -> None:
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        repo = KnowledgeRepository(session)
        repo.upsert_entity("company", "Google", {"status": "applied"})
        entity = repo.upsert_entity("company", "Google", {"status": "interview"})

        import json
        metadata = json.loads(entity.metadata_json)
        assert metadata["status"] == "interview"

    def test_get_entity(self, session: Session) -> None:
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        repo = KnowledgeRepository(session)
        repo.upsert_entity("person", "Prof. Smith")
        found = repo.get_entity("person", "Prof. Smith")
        assert found is not None
        assert found.name == "Prof. Smith"

    def test_list_entities_by_type(self, session: Session) -> None:
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        repo = KnowledgeRepository(session)
        repo.upsert_entity("course", "CS 101")
        repo.upsert_entity("course", "MATH 200")
        repo.upsert_entity("company", "Google")
        session.flush()

        courses = repo.list_entities(entity_type="course")
        assert len(courses) == 2

        companies = repo.list_entities(entity_type="company")
        assert len(companies) == 1

    def test_upsert_relationship(self, session: Session) -> None:
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        repo = KnowledgeRepository(session)
        prof = repo.upsert_entity("person", "Prof. Smith")
        course = repo.upsert_entity("course", "CS 101")
        session.flush()

        rel = repo.upsert_relationship(prof.id, course.id, "teaches", "Task: HW5")
        assert rel.relationship_type == "teaches"
        assert rel.confidence == 0.5

        # Upsert again increases confidence
        rel2 = repo.upsert_relationship(prof.id, course.id, "teaches", "Task: HW6")
        assert rel2.confidence == 0.6

    def test_get_graph_summary(self, session: Session) -> None:
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        repo = KnowledgeRepository(session)
        repo.upsert_entity("course", "CS 101")
        repo.upsert_entity("company", "Google")
        session.flush()

        summary = repo.get_graph_summary()
        assert summary["entity_count"] == 2
        assert "course" in summary["by_type"]
        assert "company" in summary["by_type"]


class TestKnowledgeExtractor:
    def test_extracts_courses_from_tasks(self, session: Session) -> None:
        from deadline_agent.memory.knowledge_extractor import extract_entities_from_tasks
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        session.add(Task(
            title="HW5", source="gmail", type="assignment", course="CS 101",
            urgency_score=3, confidence=0.9, raw_hash="h1",
        ))
        session.add(Task(
            title="Exam 1", source="gmail", type="exam", course="MATH 200",
            urgency_score=5, confidence=0.9, raw_hash="h2",
        ))
        session.flush()

        repo = KnowledgeRepository(session)
        count = extract_entities_from_tasks(session, repo)
        assert count >= 2

        cs = repo.get_entity("course", "CS 101")
        assert cs is not None

    def test_extracts_professors_from_titles(self, session: Session) -> None:
        from deadline_agent.memory.knowledge_extractor import extract_entities_from_tasks
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        session.add(Task(
            title="Prof. Smith's Discussion Post 3", source="gmail", type="assignment",
            course="CS 101", urgency_score=2, confidence=0.8, raw_hash="h3",
        ))
        session.flush()

        repo = KnowledgeRepository(session)
        extract_entities_from_tasks(session, repo)

        prof = repo.get_entity("person", "Prof. Smith")
        assert prof is not None

    def test_extracts_companies_from_recruiting(self, session: Session) -> None:
        from deadline_agent.memory.knowledge_extractor import extract_entities_from_recruiting
        from deadline_agent.store.knowledge_repository import KnowledgeRepository

        session.add(RecruitingApplication(
            company_name="Google", company_normalized="google",
            status="interview", source="email",
        ))
        session.flush()

        repo = KnowledgeRepository(session)
        count = extract_entities_from_recruiting(session, repo)
        assert count == 1

        google = repo.get_entity("company", "Google")
        assert google is not None

    def test_run_entity_extraction(self, session: Session) -> None:
        from deadline_agent.memory.knowledge_extractor import run_entity_extraction

        session.add(Task(
            title="Final Exam", source="moodle", type="exam", course="CS 101",
            urgency_score=5, confidence=0.9, raw_hash="h4",
        ))
        session.flush()

        count = run_entity_extraction(session)
        assert count >= 1
