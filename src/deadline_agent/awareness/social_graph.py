"""Social graph depth: relationship trajectory tracking.

Builds relationship records from email and calendar data.
Computes trends (growing/stable/atrophying) by comparing
recent interaction frequency against prior periods.
"""

import email.utils
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from deadline_agent.store.relationship_repository import RelationshipRepository

logger = logging.getLogger(__name__)


def compute_relationship_alerts(session: Session) -> list[str]:
    """Generate alerts for atrophying or notable relationship changes.

    Returns list of human-readable alert strings for StateSnapshot.
    """
    from deadline_agent.config import settings

    if not settings.enable_social_graph:
        return []

    repo = RelationshipRepository(session)
    alerts: list[str] = []

    # Check atrophying relationships
    atrophying = repo.list_atrophying()
    for rel in atrophying:
        if rel.last_interaction_at:
            days = (datetime.now(UTC) - rel.last_interaction_at.replace(tzinfo=UTC)).days
            alerts.append(
                f"Atrophying: no interaction with {rel.person} ({rel.channel}) "
                f"in {days} days"
            )

    # Check stale relationships (14+ days no interaction)
    stale = repo.list_stale(days=14)
    stale_ids = {r.id for r in atrophying}  # Don't duplicate
    for rel in stale:
        if rel.id not in stale_ids and rel.interaction_count >= 3:
            if rel.last_interaction_at:
                days = (datetime.now(UTC) - rel.last_interaction_at.replace(tzinfo=UTC)).days
                alerts.append(
                    f"Stale: {rel.person} ({rel.channel}) — "
                    f"was active ({rel.interaction_count} interactions) but "
                    f"silent for {days} days"
                )

    # Contact frequency shifts
    try:
        shifts = compute_frequency_shifts(session)
        for s in shifts:
            if s["delta_pct"] < -50:
                alerts.append(
                    f"Frequency drop: {s['person']} ({s['channel']}) — "
                    f"{s['baseline_count']}→{s['current_count']} interactions "
                    f"({s['delta_pct']:+.0f}%)"
                )
    except Exception:
        logger.debug("Failed to compute frequency shifts", exc_info=True)

    # Deprioritization detection
    try:
        deprioritized = detect_deprioritized_contacts(session)
        alerts.extend(deprioritized)
    except Exception:
        logger.debug("Failed to detect deprioritized contacts", exc_info=True)

    return alerts


def update_relationship_trends(session: Session) -> int:
    """Recompute trends for all relationships.

    Compares interaction patterns to determine growing/stable/atrophying.
    Returns count of relationships updated.
    """
    from deadline_agent.config import settings

    if not settings.enable_social_graph:
        return 0

    repo = RelationshipRepository(session)
    all_rels = repo.list_all()
    updated = 0

    now = datetime.now(UTC)

    for rel in all_rels:
        if rel.last_interaction_at is None:
            continue

        last_dt = rel.last_interaction_at
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)

        days_since = (now - last_dt).days

        # Simple trend heuristic based on recency and frequency
        if rel.interaction_count < 2:
            new_trend = "stable"  # Not enough data
        elif days_since > 21 and rel.interaction_count >= 3:
            new_trend = "atrophying"
        elif days_since < 7 and rel.interaction_count >= 5:
            new_trend = "growing"
        else:
            new_trend = "stable"

        if new_trend != rel.trend:
            repo.update_trend(rel.id, new_trend)
            updated += 1

    return updated


# ── Social graph builder ─────────────────────────────────────────────


async def build_social_graph(session: Session) -> int:
    """Populate relationships from Gmail recipients and Calendar attendees.

    Returns the count of relationships upserted.
    """
    from deadline_agent.config import settings

    if not settings.enable_social_graph:
        return 0

    repo = RelationshipRepository(session)
    upserted = 0

    upserted += await _ingest_gmail_recipients(repo)
    upserted += await _ingest_calendar_attendees(repo)

    # Update trends after ingesting new data
    update_relationship_trends(session)

    return upserted


async def _ingest_gmail_recipients(repo: RelationshipRepository) -> int:
    """Fetch sent messages from the last 14 days and upsert recipients."""
    try:
        from deadline_agent.auth import TokenManager
    except ImportError:
        logger.debug("Auth module not available, skipping Gmail recipient ingestion")
        return 0

    tm = TokenManager()
    token = await tm.get_valid_token()
    if token is None:
        return 0

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials(token=token)
        service = build("gmail", "v1", credentials=credentials)
    except Exception:
        logger.debug("Gmail API not available")
        return 0

    now = datetime.now(UTC)
    fourteen_days_ago = now - timedelta(days=14)
    query = f"in:sent after:{int(fourteen_days_ago.timestamp())}"

    try:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=200
        ).execute()
    except Exception:
        logger.exception("Failed to list sent messages for social graph")
        return 0

    messages = results.get("messages", [])
    upserted = 0

    for msg_ref in messages:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["To"],
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            to_raw = headers.get("To", "")
            if not to_raw:
                continue

            internal_date = datetime.fromtimestamp(
                int(msg.get("internalDate", 0)) / 1000, tz=UTC
            )

            # Parse potentially multiple recipients
            for addr_str in to_raw.split(","):
                name, addr = email.utils.parseaddr(addr_str.strip())
                if not addr:
                    continue
                display = name if name else addr
                repo.upsert(display, "email", internal_date)
                upserted += 1

        except Exception:
            logger.debug("Failed to process message %s", msg_ref.get("id"))
            continue

    return upserted


async def _ingest_calendar_attendees(repo: RelationshipRepository) -> int:
    """Fetch calendar events from the last 14 days and upsert attendees."""
    try:
        from deadline_agent.auth import TokenManager
    except ImportError:
        logger.debug("Auth module not available, skipping calendar attendee ingestion")
        return 0

    tm = TokenManager()
    token = await tm.get_valid_token()
    if token is None:
        return 0

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials(token=token)
        service = build("calendar", "v3", credentials=credentials)
    except Exception:
        logger.debug("Calendar API not available")
        return 0

    now = datetime.now(UTC)
    fourteen_days_ago = now - timedelta(days=14)

    try:
        # Get user's email to exclude self
        profile = service.calendars().get(calendarId="primary").execute()
        my_email = (profile.get("id") or "").lower()
    except Exception:
        my_email = ""

    try:
        events_result = service.events().list(
            calendarId="primary",
            timeMin=fourteen_days_ago.isoformat(),
            timeMax=now.isoformat(),
            maxResults=200,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except Exception:
        logger.exception("Failed to list calendar events for social graph")
        return 0

    events = events_result.get("items", [])
    upserted = 0

    for event in events:
        attendees = event.get("attendees", [])
        start_str = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        try:
            event_time = datetime.fromisoformat(start_str) if start_str else now
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            event_time = now

        for attendee in attendees:
            attendee_email = (attendee.get("email") or "").lower()
            if not attendee_email or attendee_email == my_email:
                continue
            display = attendee.get("displayName") or attendee_email
            repo.upsert(display, "calendar", event_time)
            upserted += 1

    return upserted


# ── Contact frequency shift ──────────────────────────────────────────


def compute_frequency_shifts(session: Session) -> list[dict[str, Any]]:
    """Compare per-recipient message frequency between two 2-week windows.

    Window A (baseline): 14-28 days ago
    Window B (current):  0-14 days ago

    Returns list of dicts with person, channel, baseline/current counts, delta.
    """
    repo = RelationshipRepository(session)
    all_rels = repo.list_all(limit=200)

    if not all_rels:
        return []

    # Use relationship data: approximate frequency from interaction_count and
    # last_interaction_at. For contacts with last interaction in each window,
    # estimate counts based on overall rate.
    now = datetime.now(UTC)
    cutoff_recent = now - timedelta(days=14)
    cutoff_baseline = now - timedelta(days=28)

    shifts: list[dict[str, Any]] = []

    for rel in all_rels:
        if rel.interaction_count < 3:
            continue  # Not enough data

        last_dt = rel.last_interaction_at
        if last_dt is None:
            continue
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)

        created = rel.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)

        # Calculate overall rate (interactions per day)
        total_days = max((now - created).days, 1)
        rate_per_day = rel.interaction_count / total_days

        # Estimate window counts based on recency
        if last_dt >= cutoff_recent:
            # Active in current window
            current_count = max(1, round(rate_per_day * 14))
            baseline_count = max(1, round(rate_per_day * 14))
        elif last_dt >= cutoff_baseline:
            # Active in baseline but not current
            current_count = 0
            baseline_count = max(1, round(rate_per_day * 14))
        else:
            continue  # Too old

        if baseline_count == 0:
            continue

        delta_pct = ((current_count - baseline_count) / baseline_count) * 100

        if abs(delta_pct) > 30:  # Only report significant changes
            shifts.append({
                "person": rel.person,
                "channel": rel.channel,
                "baseline_count": baseline_count,
                "current_count": current_count,
                "delta_pct": round(delta_pct, 1),
                "direction": "increasing" if delta_pct > 0 else "decreasing",
            })

    return shifts


# ── Deprioritization detection ───────────────────────────────────────


def detect_deprioritized_contacts(session: Session) -> list[str]:
    """Flag contacts whose frequency dropped during active LifeContext seasons.

    Distinguishes intentional deprioritization (during demanding seasons)
    from genuine relationship atrophy.
    """
    try:
        from deadline_agent.store.life_context_repository import LifeContextRepository
    except ImportError:
        return []

    lc_repo = LifeContextRepository(session)
    active_contexts = lc_repo.get_active()

    if not active_contexts:
        return []

    # Check if any demanding season is active
    demanding_seasons = {"exams", "recruiting", "crunch_week"}
    active_demanding = [
        ctx for ctx in active_contexts if ctx.season in demanding_seasons
    ]

    if not active_demanding:
        return []

    season = active_demanding[0]
    shifts = compute_frequency_shifts(session)
    alerts: list[str] = []

    for s in shifts:
        if s["delta_pct"] < -40:
            end_str = ""
            if season.end_date:
                try:
                    end_dt = datetime.fromisoformat(str(season.end_date))
                    end_str = f" — consider reconnecting after {end_dt.strftime('%b %d')}"
                except (ValueError, TypeError):
                    pass

            alerts.append(
                f"Deprioritized during {season.season}: "
                f"{s['person']} ({s['channel']}) contact dropped {s['delta_pct']:+.0f}%"
                f"{end_str}"
            )

    return alerts
