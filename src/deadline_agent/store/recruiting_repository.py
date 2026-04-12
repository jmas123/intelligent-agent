"""Repository for recruiting application tracking."""

import json
import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import RecruitingApplication

logger = logging.getLogger(__name__)

STATUS_ORDER = ["applied", "response", "interview", "offer", "closed"]

# Common company name suffixes to strip for fuzzy matching
_COMPANY_SUFFIXES = re.compile(
    r"\s+(?:inc|llc|ltd|corp|co|group|labs|ai|io|hq|technologies|technology|software)\.?$",
    re.IGNORECASE,
)


def _normalize_company(name: str) -> str:
    """Normalize company name for dedup: lowercase, strip whitespace."""
    return name.strip().lower()


def _fuzzy_normalize(name: str) -> str:
    """Aggressive normalization for fuzzy matching: strip suffixes, punctuation."""
    n = name.strip().lower()
    n = _COMPANY_SUFFIXES.sub("", n)
    # Remove trailing punctuation and whitespace
    n = n.strip(" .")
    return n


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
        signal_at: datetime | None = None,
    ) -> RecruitingApplication:
        """Find or create an application by company name.

        Args:
            signal_at: When the signal actually occurred. If None, uses
                       now (real-time ingestion). Pass the original date
                       during backfill/refresh to preserve accurate
                       staleness tracking.
        """
        normalized = _normalize_company(company_name)
        app = self.find_by_company(normalized)
        effective_signal_at = signal_at or datetime.utcnow()

        if app is None:
            app = RecruitingApplication(
                company_name=company_name.strip(),
                company_normalized=normalized,
                source=source,
                applied_at=applied_at,
                last_signal_at=effective_signal_at,
            )
            # Phase 25: populate temporal fields from applied_at
            if applied_at:
                app.applied_day_of_week = applied_at.weekday()
                app.applied_hour = applied_at.hour
            if signal:
                app.signals_json = json.dumps([signal])
            self._session.add(app)
            self._session.flush()
            logger.info(
                "New application tracked: %s (source=%s)",
                company_name,
                source,
            )
        else:
            if signal:
                signals = json.loads(app.signals_json)
                signals.append(signal)
                app.signals_json = json.dumps(signals)
            # Only advance last_signal_at, never backdate it
            if effective_signal_at.tzinfo:
                effective_naive = effective_signal_at.replace(
                    tzinfo=None
                )
            else:
                effective_naive = effective_signal_at
            existing_signal = app.last_signal_at
            if existing_signal is None or effective_naive > existing_signal:
                app.last_signal_at = effective_naive
            if applied_at:
                applied_naive = (
                    applied_at.replace(tzinfo=None)
                    if applied_at.tzinfo
                    else applied_at
                )
                existing_naive = (
                    app.applied_at.replace(tzinfo=None)
                    if app.applied_at and app.applied_at.tzinfo
                    else app.applied_at
                )
                if existing_naive is None or applied_naive < existing_naive:
                    app.applied_at = applied_naive

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
        """Find an application by normalized company name.

        Tries exact match first, then fuzzy matching (suffix-stripped,
        substring containment) to merge "Gray Swan" with "Gray Swan AI".
        """
        # Exact match
        stmt = select(RecruitingApplication).where(
            RecruitingApplication.company_normalized == normalized
        )
        result = self._session.scalar(stmt)
        if result is not None:
            return result

        # Fuzzy match: strip suffixes and try again
        fuzzy = _fuzzy_normalize(normalized)
        if fuzzy != normalized:
            stmt = select(RecruitingApplication).where(
                RecruitingApplication.company_normalized == fuzzy
            )
            result = self._session.scalar(stmt)
            if result is not None:
                return result

        # Substring containment: "gray swan" matches "gray swan ai"
        # Check both directions: new name contained in existing, or existing contained in new
        stmt = select(RecruitingApplication).order_by(
            RecruitingApplication.last_signal_at.desc()
        )
        all_apps = list(self._session.scalars(stmt).all())
        for app in all_apps:
            existing = app.company_normalized
            existing_fuzzy = _fuzzy_normalize(existing)
            if fuzzy and existing_fuzzy and (
                fuzzy in existing_fuzzy or existing_fuzzy in fuzzy
            ):
                return app

        return None

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

    def count_all(self) -> int:
        """Count all applications regardless of status."""
        from sqlalchemy import func

        stmt = select(func.count(RecruitingApplication.id))
        return self._session.execute(stmt).scalar() or 0

    def list_by_tier(self, tier: str) -> list[RecruitingApplication]:
        """List applications for a given company tier."""
        stmt = (
            select(RecruitingApplication)
            .where(RecruitingApplication.company_tier == tier)
            .order_by(RecruitingApplication.last_signal_at.desc())
        )
        return list(self._session.scalars(stmt).all())

    def list_by_status(self, status: str) -> list[RecruitingApplication]:
        """List applications with a given status."""
        stmt = (
            select(RecruitingApplication)
            .where(RecruitingApplication.status == status)
            .order_by(RecruitingApplication.last_signal_at.desc())
        )
        return list(self._session.scalars(stmt).all())

    def count_by_tier(self) -> dict[str, int]:
        """Count applications grouped by company tier."""
        from sqlalchemy import func

        stmt = (
            select(RecruitingApplication.company_tier, func.count(RecruitingApplication.id))
            .group_by(RecruitingApplication.company_tier)
        )
        results = self._session.execute(stmt).all()
        return {tier or "unclassified": count for tier, count in results}

    def count_by_status(self) -> dict[str, int]:
        """Count applications grouped by status."""
        from sqlalchemy import func

        stmt = (
            select(RecruitingApplication.status, func.count(RecruitingApplication.id))
            .group_by(RecruitingApplication.status)
        )
        results = self._session.execute(stmt).all()
        return {status: count for status, count in results}
