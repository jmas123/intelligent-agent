"""Repository for recruiting application tracking."""

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import RecruitingApplication

logger = logging.getLogger(__name__)

STATUS_ORDER = ["applied", "response", "interview", "offer", "closed"]


def _normalize_company(name: str) -> str:
    """Normalize company name for dedup: lowercase, strip whitespace."""
    return name.strip().lower()


class RecruitingRepository:
    """CRUD operations for recruiting applications."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_application(
        self,
        company_name: str,
        source: str = "file",
        signal: dict[str, str] | None = None,
        applied_at: datetime | None = None,
    ) -> RecruitingApplication:
        """Find or create an application by company name. Appends signal if provided."""
        normalized = _normalize_company(company_name)
        app = self.find_by_company(normalized)

        if app is None:
            app = RecruitingApplication(
                company_name=company_name.strip(),
                company_normalized=normalized,
                source=source,
                applied_at=applied_at,
            )
            if signal:
                app.signals_json = json.dumps([signal])
            self._session.add(app)
            self._session.flush()
            logger.info("New application tracked: %s (source=%s)", company_name, source)
        else:
            if signal:
                signals = json.loads(app.signals_json)
                signals.append(signal)
                app.signals_json = json.dumps(signals)
            app.last_signal_at = datetime.utcnow()
            if applied_at and (app.applied_at is None or applied_at < app.applied_at):
                app.applied_at = applied_at

        self._session.commit()
        return app

    def advance_status(
        self,
        app_id: int,
        new_status: str,
        signal: dict[str, str] | None = None,
    ) -> RecruitingApplication | None:
        """Advance application status. Only moves forward in the hierarchy.

        Exception: 'closed' can override any status (rejection at any stage).
        """
        app = self._session.get(RecruitingApplication, app_id)
        if app is None:
            return None

        current_idx = STATUS_ORDER.index(app.status) if app.status in STATUS_ORDER else 0
        new_idx = STATUS_ORDER.index(new_status) if new_status in STATUS_ORDER else 0

        # Only advance, except 'closed' can override anything
        if new_status == "closed" or new_idx > current_idx:
            app.status = new_status
            app.last_signal_at = datetime.utcnow()

        if signal:
            signals = json.loads(app.signals_json)
            signals.append(signal)
            app.signals_json = json.dumps(signals)

        self._session.commit()
        return app

    def find_by_company(self, normalized: str) -> RecruitingApplication | None:
        """Find an application by normalized company name."""
        stmt = select(RecruitingApplication).where(
            RecruitingApplication.company_normalized == normalized
        )
        return self._session.scalar(stmt)

    def list_active(self) -> list[RecruitingApplication]:
        """List non-closed applications, ordered by last signal date."""
        stmt = (
            select(RecruitingApplication)
            .where(RecruitingApplication.status != "closed")
            .order_by(RecruitingApplication.last_signal_at.desc())
        )
        return list(self._session.scalars(stmt).all())

    def list_all(self) -> list[RecruitingApplication]:
        """List all applications, ordered by last signal date."""
        stmt = select(RecruitingApplication).order_by(
            RecruitingApplication.last_signal_at.desc()
        )
        return list(self._session.scalars(stmt).all())
