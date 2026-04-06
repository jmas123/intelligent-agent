"""FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI

from deadline_agent.config import settings
from deadline_agent.ingestion.gcal import GCalIngester
from deadline_agent.ingestion.gmail import GmailIngester
from deadline_agent.ingestion.moodle import MoodleIngester
from deadline_agent.pipeline import run_pipeline
from deadline_agent.store.session import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

gmail_ingester = GmailIngester()
gcal_ingester = GCalIngester()


async def alert_scheduler() -> None:
    """Periodically check for tasks approaching deadlines and send alerts."""
    from deadline_agent.notifications.alerts import check_and_send_alerts
    from deadline_agent.notifications.no_work_alerts import check_no_work_alerts

    interval = settings.alert_check_interval_minutes * 60
    logger.info("Alert scheduler started (every %d minutes)", settings.alert_check_interval_minutes)

    while True:
        await asyncio.sleep(interval)
        if not settings.enable_notifications:
            continue
        try:
            with SessionLocal() as session:
                count = check_and_send_alerts(session)
                if count > 0:
                    logger.info("Sent %d pre-deadline alert(s)", count)
                no_work_count = check_no_work_alerts(session)
                if no_work_count > 0:
                    logger.info("Sent %d no-work alert(s)", no_work_count)
        except Exception:
            logger.exception("Alert scheduler error")


async def digest_scheduler() -> None:
    """Send a morning digest notification once daily at the configured time."""
    from deadline_agent.notifications.digest import send_digest, send_digest_v2

    logger.info("Digest scheduler started (daily at %s)", settings.digest_time)

    _est = ZoneInfo("America/New_York")

    while True:
        # Calculate seconds until next digest time
        now = datetime.now(_est)
        hour, minute = (int(x) for x in settings.digest_time.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            from datetime import timedelta

            target += timedelta(days=1)

        delay = (target - now).total_seconds()
        await asyncio.sleep(delay)

        if not settings.enable_notifications:
            continue
        try:
            with SessionLocal() as session:
                sent = await send_digest_v2(session)
                if not sent:
                    send_digest(session)
        except Exception:
            logger.exception("Digest scheduler error")


async def reasoning_scheduler() -> None:
    """Periodically generate proactive insights and propose actions."""
    from deadline_agent.reasoning.engine import generate_insights
    from deadline_agent.reasoning.scheduler import propose_smart_schedule
    from deadline_agent.reasoning.state import build_unified_context

    interval = settings.reasoning_interval_minutes * 60
    logger.info("Reasoning scheduler started (every %d min)", settings.reasoning_interval_minutes)

    while True:
        await asyncio.sleep(interval)
        try:
            with SessionLocal() as session:
                insights = await generate_insights(session)
                if insights:
                    logger.info("Generated %d insight(s)", len(insights))

                # Propose calendar blocks for unworked tasks
                context = await build_unified_context(session)
                proposals = propose_smart_schedule(context, session)
                if proposals:
                    logger.info("Proposed %d action(s)", len(proposals))

                # Expire stale negotiation sessions
                from deadline_agent.store.negotiation_repository import (
                    NegotiationRepository,
                )

                neg_repo = NegotiationRepository(session)
                expired = neg_repo.expire_stale(hours=24)
                if expired:
                    logger.info("Expired %d stale negotiation session(s)", expired)
        except Exception:
            logger.exception("Reasoning scheduler error")


async def weekly_review_scheduler() -> None:
    """Send a weekly review notification every Sunday at digest_time."""
    from deadline_agent.notifications.macos import send_notification
    from deadline_agent.reasoning.weekly_review import generate_weekly_review

    logger.info("Weekly review scheduler started (Sundays at %s)", settings.digest_time)

    _est = ZoneInfo("America/New_York")

    while True:
        # Calculate seconds until next Sunday at digest_time
        now = datetime.now(_est)
        hour, minute = (int(x) for x in settings.digest_time.split(":"))
        # Days until Sunday (weekday 6)
        days_ahead = (6 - now.weekday()) % 7
        if days_ahead == 0 and now.hour >= hour:
            days_ahead = 7
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        from datetime import timedelta

        target += timedelta(days=days_ahead)
        delay = (target - now).total_seconds()
        await asyncio.sleep(delay)

        if not settings.enable_notifications:
            continue
        try:
            with SessionLocal() as session:
                review = await generate_weekly_review(session)
                send_notification(
                    title="Deadline Agent — Weekly Review",
                    body=review[:500],
                )
        except Exception:
            logger.exception("Weekly review scheduler error")


async def behavioral_analysis_scheduler() -> None:
    """Periodically infer work sessions and analyze behavioral patterns."""
    from deadline_agent.behavioral.analyzer import run_all_analyses
    from deadline_agent.behavioral.session_inference import infer_sessions

    interval = settings.behavioral_analysis_interval_hours * 3600
    logger.info(
        "Behavioral analysis scheduler started (every %d hours)",
        settings.behavioral_analysis_interval_hours,
    )

    while True:
        await asyncio.sleep(interval)
        try:
            with SessionLocal() as session:
                sessions = infer_sessions(session)
                if sessions:
                    logger.info("Inferred %d work session(s)", len(sessions))
                patterns = run_all_analyses(session)
                if patterns:
                    logger.info("Updated %d behavioral pattern(s)", len(patterns))
        except Exception:
            logger.exception("Behavioral analysis scheduler error")


async def gmail_watch_scheduler() -> None:
    """Renew Gmail Pub/Sub watch before the 7-day expiry."""
    if not settings.gmail_pubsub_topic:
        logger.info("Gmail watch scheduler skipped — no pubsub topic configured")
        return

    from deadline_agent.auth import TokenManager

    interval = settings.gmail_watch_renew_days * 86400  # days → seconds
    tm = TokenManager()
    logger.info("Gmail watch scheduler started (every %d days)", settings.gmail_watch_renew_days)

    while True:
        try:
            token = await tm.get_valid_token()
            if token:
                import httpx

                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://gmail.googleapis.com/gmail/v1/users/me/watch",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "topicName": settings.gmail_pubsub_topic,
                            "labelIds": ["INBOX"],
                        },
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        logger.info("Gmail watch renewed, expires: %s", data.get("expiration"))
                    else:
                        logger.error("Gmail watch renewal failed: %s", resp.text)
            else:
                logger.error("Gmail watch renewal skipped — no valid token")
        except Exception:
            logger.exception("Gmail watch scheduler error")
        await asyncio.sleep(interval)


async def moodle_scheduler() -> None:
    """Periodically fetch Moodle iCal feeds for new/updated deadlines."""
    if not settings.moodle_ical_urls:
        logger.info("Moodle scheduler skipped — no iCal URLs configured")
        return

    interval = settings.moodle_fetch_interval_minutes * 60
    ingester = MoodleIngester(settings.moodle_ical_urls)
    logger.info(
        "Moodle scheduler started (every %d minutes, %d feeds)",
        settings.moodle_fetch_interval_minutes,
        len(settings.moodle_ical_urls),
    )

    # Fetch immediately on startup, then on interval
    while True:
        try:
            count = await ingester.fetch_and_enqueue()
            if count > 0:
                logger.info("Moodle fetch: enqueued %d items", count)
        except Exception:
            logger.exception("Moodle scheduler error")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start pipeline worker, schedulers, and file watcher on startup."""
    init_db()
    tasks = [
        asyncio.create_task(run_pipeline(SessionLocal)),
        asyncio.create_task(alert_scheduler()),
        asyncio.create_task(digest_scheduler()),
        asyncio.create_task(reasoning_scheduler()),
        asyncio.create_task(weekly_review_scheduler()),
        asyncio.create_task(behavioral_analysis_scheduler()),
        asyncio.create_task(gmail_watch_scheduler()),
        asyncio.create_task(moodle_scheduler()),
    ]

    watcher = None
    if settings.enable_file_watcher and settings.watch_directories:
        from deadline_agent.awareness.processor import process_file_events
        from deadline_agent.awareness.watcher import FileEvent, FileWatcher

        loop = asyncio.get_running_loop()
        file_event_queue: asyncio.Queue[FileEvent] = asyncio.Queue()
        watcher = FileWatcher(settings.watch_directories, file_event_queue, loop)
        watcher.start()
        tasks.append(asyncio.create_task(process_file_events(SessionLocal, file_event_queue)))
        logger.info("File watcher started for %d directories", len(settings.watch_directories))

    logger.info("Pipeline worker and schedulers started")
    yield
    if watcher is not None:
        watcher.stop()
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Deadline Agent", lifespan=lifespan)

# Chat API for Open WebUI integration
from deadline_agent.api.chat import router as chat_router

app.include_router(chat_router, prefix="/api/chat")


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


from fastapi import Request

@app.post("/webhooks/gmail", status_code=200)
async def gmail_webhook(
    request: Request,
) -> dict[str, str]:
    """Receive Gmail Pub/Sub push notification."""
    body = await request.body()
    logger.info("Gmail webhook raw body: %s", body[:500])
    try:
        import json as _json
        payload: dict[str, Any] = _json.loads(body)
    except Exception:
        logger.warning("Gmail webhook: failed to parse body as JSON")
        return {"status": "invalid"}
    if not await gmail_ingester.validate_payload(payload):
        logger.warning("Gmail webhook: invalid payload — validation failed")
        return {"status": "invalid"}

    logger.info("Gmail webhook: valid payload, calling handler")
    try:
        await gmail_ingester.handle_webhook(payload)
    except Exception:
        logger.exception("Gmail webhook handler failed")
    return {"status": "received"}


@app.post("/webhooks/gcal", status_code=200)
async def gcal_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Receive Google Calendar push notification.

    Returns 200 immediately per ingestion rules.
    """
    if not await gcal_ingester.validate_payload(payload):
        return {"status": "invalid"}
    background_tasks.add_task(gcal_ingester.handle_webhook, payload)
    return {"status": "received"}
