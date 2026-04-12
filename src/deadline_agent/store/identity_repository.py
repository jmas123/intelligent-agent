"""Repository for the durable identity document."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import IdentityDocument


class IdentityRepository:
    """Single-row identity document that evolves via version increments."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_current(self) -> IdentityDocument | None:
        """Return the current identity document (highest version)."""
        stmt = select(IdentityDocument).order_by(IdentityDocument.version.desc()).limit(1)
        return self._session.scalars(stmt).first()

    def upsert(
        self,
        document_json: str,
        document_markdown: str,
        synthesis_sources_json: str = "{}",
    ) -> IdentityDocument:
        """Create or update the identity document, incrementing version."""
        current = self.get_current()
        if current is not None:
            current.version += 1
            current.document_json = document_json
            current.document_markdown = document_markdown
            current.synthesis_sources_json = synthesis_sources_json
            current.last_synthesis_at = datetime.now(UTC)
            self._session.commit()
            return current

        doc = IdentityDocument(
            version=1,
            document_json=document_json,
            document_markdown=document_markdown,
            synthesis_sources_json=synthesis_sources_json,
        )
        self._session.add(doc)
        self._session.commit()
        return doc

    def get_markdown(self) -> str:
        """Return the portable markdown rendering, or empty string."""
        current = self.get_current()
        return current.document_markdown if current else ""
