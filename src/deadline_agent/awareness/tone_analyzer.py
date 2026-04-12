"""Outgoing communication tone analysis.

Analyzes sent Gmail messages for behavioral signals:
- Message length trends (shorter replies = overloaded)
- Response latency shifts (delayed replies = avoidance)
- Contact frequency changes (who you're talking to more/less)

Privacy: Only metadata is stored (length, latency, recipient hash).
Never stores raw message content.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from deadline_agent.store.pattern_repository import PatternRepository

logger = logging.getLogger(__name__)


async def analyze_outgoing_tone(session: Session) -> list[str]:
    """Fetch sent messages and compute tone signals.

    Returns list of signal descriptions for logging.
    Stores patterns via PatternRepository.
    """
    from deadline_agent.config import settings

    if not settings.enable_tone_analysis:
        return []

    try:
        from deadline_agent.auth import TokenManager
    except ImportError:
        logger.debug("Auth module not available, skipping tone analysis")
        return []

    tm = TokenManager()
    credentials = await tm.get_valid_token()
    if credentials is None:
        logger.debug("No valid Google token, skipping tone analysis")
        return []

    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=credentials)
    except Exception:
        logger.debug("Gmail API not available, skipping tone analysis")
        return []

    now = datetime.now(UTC)
    six_hours_ago = now - timedelta(hours=6)
    query = f"in:sent after:{int(six_hours_ago.timestamp())}"

    try:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=50
        ).execute()
    except Exception:
        logger.exception("Failed to list sent messages")
        return []

    messages = results.get("messages", [])
    if not messages:
        return []

    signals: list[str] = []
    snippet_lengths: list[int] = []
    response_latencies: list[float] = []
    recipients: dict[str, int] = {}

    for msg_ref in messages:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["To", "In-Reply-To", "Subject"],
            ).execute()

            snippet = msg.get("snippet", "")
            snippet_lengths.append(len(snippet))

            # Extract recipient
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            to_addr = headers.get("To", "")
            if to_addr:
                # Hash for privacy
                addr_hash = hashlib.sha256(to_addr.lower().encode()).hexdigest()[:12]
                recipients[addr_hash] = recipients.get(addr_hash, 0) + 1

            # Compute response latency if this is a reply
            if headers.get("In-Reply-To"):
                internal_date = int(msg.get("internalDate", 0)) / 1000
                thread_id = msg.get("threadId")
                if thread_id:
                    latency = _compute_reply_latency(service, thread_id, internal_date)
                    if latency is not None:
                        response_latencies.append(latency)

        except Exception:
            logger.debug("Failed to process message %s", msg_ref.get("id"))
            continue

    # Store patterns
    repo = PatternRepository(session)

    if snippet_lengths:
        avg_length = sum(snippet_lengths) / len(snippet_lengths)
        value = json.dumps({
            "avg_snippet_length": round(avg_length, 1),
            "message_count": len(snippet_lengths),
            "period": "6h",
        })
        repo.upsert(
            "outgoing_tone", "message_length",
            value, len(snippet_lengths),
            min(1.0, len(snippet_lengths) / 10),
        )
        signals.append(f"Avg message length: {avg_length:.0f} chars ({len(snippet_lengths)} messages)")

    if response_latencies:
        avg_latency_hours = sum(response_latencies) / len(response_latencies) / 3600
        value = json.dumps({
            "avg_response_latency_hours": round(avg_latency_hours, 2),
            "reply_count": len(response_latencies),
        })
        repo.upsert(
            "outgoing_tone", "response_latency",
            value, len(response_latencies),
            min(1.0, len(response_latencies) / 5),
        )
        signals.append(f"Avg response latency: {avg_latency_hours:.1f}h ({len(response_latencies)} replies)")

    return signals


def _compute_reply_latency(
    service: Any, thread_id: str, reply_timestamp: float
) -> float | None:
    """Compute seconds between the previous message in thread and our reply."""
    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="minimal",
        ).execute()

        messages = thread.get("messages", [])
        if len(messages) < 2:
            return None

        # Find the message just before our reply
        for i, msg in enumerate(messages):
            msg_ts = int(msg.get("internalDate", 0)) / 1000
            if abs(msg_ts - reply_timestamp) < 1:
                # This is our reply, get the previous message
                if i > 0:
                    prev_ts = int(messages[i - 1].get("internalDate", 0)) / 1000
                    latency = reply_timestamp - prev_ts
                    if 0 < latency < 7 * 86400:  # Within 7 days
                        return latency
                break

    except Exception:
        pass

    return None
