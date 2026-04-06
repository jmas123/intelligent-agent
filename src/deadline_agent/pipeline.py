"""Pipeline orchestration: async queue connecting filter → extract → store."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class IngestItem:
    """Unit of work moving through the pipeline. raw_content is ephemeral — never persisted."""

    source: str  # "gmail" | "moodle" | "gcal"
    raw_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


pipeline_queue: asyncio.Queue[IngestItem] = asyncio.Queue()


async def _process_item(
    item: IngestItem,
    session_factory: Any,
) -> bool:
    """Process a single IngestItem through filter → extract → dedup → store.

    Returns True if a task was stored.
    """
    from deadline_agent.extraction.fallback import FallbackExtractor
    from deadline_agent.filter.prefilter import should_process
    from deadline_agent.store.dedup import find_near_duplicates, generate_embedding
    from deadline_agent.store.repository import TaskRepository

    # Track recruiting applications from Gmail signals (before filter, so we catch all)
    if item.source == "gmail":
        from deadline_agent.awareness.recruiting_extractor import (
            extract_company_from_email,
            has_recruiting_signal,
            infer_status,
        )

        subject = item.metadata.get("subject", "")
        sender = item.metadata.get("sender", "")
        text = f"{subject} {item.raw_content}"
        if has_recruiting_signal(text):
            company = extract_company_from_email(sender, subject, item.raw_content)
            if company:
                from deadline_agent.store.recruiting_repository import (
                    RecruitingRepository,
                )

                with session_factory() as session:
                    repo = RecruitingRepository(session)
                    app = repo.upsert_application(
                        company_name=company,
                        source="email",
                        signal={
                            "type": "email",
                            "date": "",
                            "summary": subject[:100],
                        },
                    )
                    new_status = infer_status(app.status, text)
                    if new_status != app.status:
                        repo.advance_status(app.id, new_status, signal={
                            "type": "status_change",
                            "date": "",
                            "summary": f"{app.status} → {new_status}: {subject[:80]}",
                        })

    # Moodle iCal events are inherently deadline-relevant — skip pre-filter
    if item.source != "moodle" and not should_process(item):
        logger.info("Filtered out: %s", item.metadata.get("subject", ""))
        return False

    # Early dedup: skip AI extraction if we already have this content hash
    raw_hash = item.metadata.get("raw_hash")
    if raw_hash:
        with session_factory() as session:
            repo = TaskRepository(session)
            if repo.exists_by_hash(raw_hash):
                logger.debug("Already stored (hash match), skipping extraction: %s", item.metadata.get("subject", ""))
                return False

    extractor = FallbackExtractor()
    result = await extractor.extract(item)
    if result is None:
        return False

    # Generate embedding for near-dup detection
    embedding: list[float] | None = None
    try:
        embed_text = f"{result.title} {result.course or ''} {result.due_date_iso or ''}"
        embedding = await generate_embedding(embed_text)
    except Exception:
        logger.warning("Embedding generation failed, skipping near-dup check")

    # Check for near-duplicates
    if embedding is not None:
        with session_factory() as session:
            near_dupes = find_near_duplicates(session, embedding)
            for dup_task, score in near_dupes:
                logger.warning(
                    "Near-duplicate (%.2f): '%s' similar to existing '%s' (id=%d)",
                    score,
                    result.title,
                    dup_task.title,
                    dup_task.id,
                )

    # Store task (exact hash dedup still enforced by DB constraint)
    task_data = result.model_dump()
    if embedding is not None:
        task_data["embedding"] = json.dumps(embedding)

    with session_factory() as session:
        repo = TaskRepository(session)
        task = repo.create_task(task_data)
        if task is not None:
            logger.info("Stored task: %s (confidence=%.2f)", task.title, task.confidence)
            return True
        logger.debug("Duplicate skipped: %s", result.raw_hash)
        return False


async def run_pipeline(session_factory: Any) -> None:
    """Main pipeline loop: dequeue → filter → extract → dedup → store.

    Runs as a long-lived background task started from FastAPI lifespan.
    """
    logger.info("Pipeline worker started")

    while True:
        item = await pipeline_queue.get()
        try:
            await _process_item(item, session_factory)
        except Exception:
            logger.exception("Pipeline error for item from %s", item.source)
        finally:
            pipeline_queue.task_done()


async def drain_queue(session_factory: Any) -> int:
    """Process all items currently in the queue. For CLI use.

    Returns number of tasks stored.
    """
    stored = 0
    while not pipeline_queue.empty():
        item = pipeline_queue.get_nowait()
        try:
            if await _process_item(item, session_factory):
                stored += 1
        except Exception:
            logger.exception("Pipeline error for item from %s", item.source)
        finally:
            pipeline_queue.task_done()
    return stored
