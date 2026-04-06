"""Background processor for file system events."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from deadline_agent.awareness.watcher import FileEvent
from deadline_agent.config import settings

logger = logging.getLogger(__name__)


async def process_file_events(session_factory: Any, event_queue: asyncio.Queue[FileEvent]) -> None:
    """Dequeue file events, record activity, and link to tasks.

    Mirrors the run_pipeline pattern: infinite async loop processing queued items.
    """
    from deadline_agent.awareness.linker import TaskLinker
    from deadline_agent.store.file_repository import FileActivityRepository

    logger.info("File event processor started")

    while True:
        event = await event_queue.get()
        try:
            path = Path(event.path)
            if not path.exists():
                logger.debug("File no longer exists, skipping: %s", event.path)
                continue

            stat = path.stat()
            activity_data = {
                "path": str(path),
                "filename": path.name,
                "directory": str(path.parent),
                "size_bytes": stat.st_size,
                "modified_at": event.timestamp,
                "event_type": event.event_type,
            }

            with session_factory() as session:
                repo = FileActivityRepository(session)
                activity = repo.record_activity(activity_data)

                # Classify life track before linking
                from deadline_agent.awareness.classifier import classify_file

                track = classify_file(activity.path, activity.filename, activity.directory)
                if track is not None:
                    activity.life_track = track
                    session.commit()

                # Track recruiting applications from file names
                if track == "recruiting":
                    from deadline_agent.awareness.recruiting_extractor import (
                        extract_company_from_filename,
                    )
                    from deadline_agent.store.recruiting_repository import (
                        RecruitingRepository,
                    )

                    company = extract_company_from_filename(activity.filename)
                    if company:
                        recruiting_repo = RecruitingRepository(session)
                        recruiting_repo.upsert_application(
                            company_name=company,
                            source="file",
                            signal={
                                "type": "file",
                                "date": event.timestamp.isoformat(),
                                "summary": activity.filename,
                            },
                            applied_at=event.timestamp,
                        )

                linker = TaskLinker(session)
                matches = linker.find_matching_tasks(activity)

                linked = 0
                for task, confidence, method in matches:
                    if confidence >= settings.file_link_threshold:
                        link = repo.link_to_task(activity.id, task.id, confidence, method)
                        if link is not None:
                            linked += 1
                            logger.info(
                                "Linked %s → %s (%.2f, %s)",
                                path.name,
                                task.title,
                                confidence,
                                method,
                            )

                if linked > 0:
                    logger.debug("File %s linked to %d task(s)", path.name, linked)

        except Exception:
            logger.exception("Error processing file event: %s", event.path)
        finally:
            event_queue.task_done()
