"""Seed life contexts from config.toml on startup."""

import logging

from sqlalchemy.orm import Session

from deadline_agent.store.context_repository import LifeContextRepository

logger = logging.getLogger(__name__)


def seed_from_config(session: Session) -> int:
    """Create manual LifeContext records from config.toml life_contexts setting.

    Idempotent: skips contexts that already exist with matching season + dates.
    """
    from deadline_agent.config import settings

    if not settings.life_contexts:
        return 0

    repo = LifeContextRepository(session)
    count = 0

    for ctx_def in settings.life_contexts:
        season = ctx_def.get("season", "")
        start = ctx_def.get("start", "")
        end = ctx_def.get("end", "")
        label = ctx_def.get("label")

        if not season or not start or not end:
            logger.warning("Skipping incomplete life_context definition: %s", ctx_def)
            continue

        repo.set_manual(season, start, end, label)
        count += 1

    if count:
        logger.info("Seeded %d life context(s) from config", count)

    return count
