"""Repository for the personal knowledge graph."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from deadline_agent.models import EntityRelationship, KnowledgeEntity


class KnowledgeRepository:
    """CRUD for knowledge graph entities and relationships."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeEntity:
        """Insert or update an entity. Increments mention_count on update."""
        normalized = name.strip().lower()
        stmt = select(KnowledgeEntity).where(
            KnowledgeEntity.entity_type == entity_type,
            KnowledgeEntity.normalized_name == normalized,
        )
        existing = self._session.scalars(stmt).first()

        if existing is not None:
            existing.mention_count += 1
            existing.last_seen_at = datetime.now(UTC)
            if metadata:
                try:
                    current = json.loads(existing.metadata_json)
                except (json.JSONDecodeError, TypeError):
                    current = {}
                current.update(metadata)
                existing.metadata_json = json.dumps(current)
            self._session.flush()
            return existing

        entity = KnowledgeEntity(
            entity_type=entity_type,
            name=name.strip(),
            normalized_name=normalized,
            metadata_json=json.dumps(metadata or {}),
        )
        self._session.add(entity)
        self._session.flush()
        return entity

    def get_entity(self, entity_type: str, name: str) -> KnowledgeEntity | None:
        """Look up an entity by type and name."""
        normalized = name.strip().lower()
        stmt = select(KnowledgeEntity).where(
            KnowledgeEntity.entity_type == entity_type,
            KnowledgeEntity.normalized_name == normalized,
        )
        return self._session.scalars(stmt).first()

    def list_entities(
        self,
        entity_type: str | None = None,
        min_mentions: int = 1,
        limit: int = 100,
    ) -> list[KnowledgeEntity]:
        """List entities, optionally filtered by type."""
        stmt = (
            select(KnowledgeEntity)
            .where(KnowledgeEntity.mention_count >= min_mentions)
            .order_by(KnowledgeEntity.mention_count.desc())
            .limit(limit)
        )
        if entity_type is not None:
            stmt = stmt.where(KnowledgeEntity.entity_type == entity_type)
        return list(self._session.scalars(stmt).all())

    def upsert_relationship(
        self,
        source_id: int,
        target_id: int,
        relationship_type: str,
        evidence: str | None = None,
    ) -> EntityRelationship:
        """Insert or update a relationship between entities."""
        stmt = select(EntityRelationship).where(
            EntityRelationship.source_entity_id == source_id,
            EntityRelationship.target_entity_id == target_id,
            EntityRelationship.relationship_type == relationship_type,
        )
        existing = self._session.scalars(stmt).first()

        if existing is not None:
            existing.confidence = min(1.0, existing.confidence + 0.1)
            if evidence:
                try:
                    ev_list = json.loads(existing.evidence_json)
                except (json.JSONDecodeError, TypeError):
                    ev_list = []
                ev_list.append(evidence)
                existing.evidence_json = json.dumps(ev_list[-10:])  # keep last 10
            self._session.flush()
            return existing

        rel = EntityRelationship(
            source_entity_id=source_id,
            target_entity_id=target_id,
            relationship_type=relationship_type,
            evidence_json=json.dumps([evidence] if evidence else []),
        )
        self._session.add(rel)
        self._session.flush()
        return rel

    def get_relationships(self, entity_id: int) -> list[EntityRelationship]:
        """Get all relationships involving an entity."""
        stmt = select(EntityRelationship).where(
            (EntityRelationship.source_entity_id == entity_id)
            | (EntityRelationship.target_entity_id == entity_id)
        )
        return list(self._session.scalars(stmt).all())

    def get_graph_summary(self) -> dict[str, Any]:
        """Summarize the knowledge graph for identity synthesis."""
        entities = self.list_entities(min_mentions=1, limit=50)

        by_type: dict[str, list[str]] = {}
        for e in entities:
            by_type.setdefault(e.entity_type, []).append(e.name)

        total_relationships = self._session.scalar(
            select(func.count(EntityRelationship.id))
        ) or 0

        return {
            "entity_count": len(entities),
            "relationship_count": total_relationships,
            "by_type": by_type,
        }
