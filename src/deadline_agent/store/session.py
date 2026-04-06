"""Database engine and session factory."""

import logging
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from deadline_agent.config import settings
from deadline_agent.models import Base

logger = logging.getLogger(__name__)

# Ensure the database directory exists
db_path = settings.database_url.replace("sqlite:///", "")
if db_path:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)


def _migrate_tasks_table() -> None:
    """Add columns to tasks table that were added after initial schema."""
    inspector = inspect(engine)
    if "tasks" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("tasks")}
    if "no_work_alert_sent" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN no_work_alert_sent BOOLEAN DEFAULT 0"))
        logger.info("Migrated tasks table: added no_work_alert_sent column")


def _migrate_file_activity_table() -> None:
    """Add columns to file_activity table that were added after initial schema."""
    inspector = inspect(engine)
    if "file_activity" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("file_activity")}
    if "life_track" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE file_activity ADD COLUMN life_track TEXT"))
        logger.info("Migrated file_activity table: added life_track column")


def init_db() -> None:
    """Create all tables if they don't exist, then run migrations."""
    Base.metadata.create_all(engine)
    _migrate_tasks_table()
    _migrate_file_activity_table()

    # Seed life contexts from config.toml
    try:
        from deadline_agent.awareness.life_context_seeder import seed_from_config

        with SessionLocal() as session:
            seed_from_config(session)
    except Exception:
        logger.exception("Failed to seed life contexts from config")


def get_session() -> Session:  # type: ignore[misc]
    """Yield a session, closing it when done."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
