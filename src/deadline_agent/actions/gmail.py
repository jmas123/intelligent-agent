"""Gmail draft creation. Never sends — only creates drafts."""

import base64
import logging
from email.mime.text import MIMEText
from typing import Any

import httpx

from deadline_agent.auth import TokenManager

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


async def create_gmail_draft(
    payload: dict[str, Any],
    token_manager: TokenManager | None = None,
) -> dict[str, Any] | None:
    """Create a Gmail draft (never sends).

    Payload: {to, subject, body}
    Returns created draft data or None on failure.
    """
    tm = token_manager or TokenManager()
    token = await tm.get_valid_token()
    if not token:
        logger.error("No valid Google token for Gmail draft")
        return None

    msg = MIMEText(payload["body"])
    msg["to"] = payload["to"]
    msg["subject"] = payload["subject"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    draft_body = {"message": {"raw": raw}}
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GMAIL_API}/drafts",
                json=draft_body,
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code not in (200, 201):
                logger.error("Gmail API error %d: %s", resp.status_code, resp.text)
                return None
            result: dict[str, Any] = resp.json()
            logger.info("Created Gmail draft: %s", result.get("id"))
            return result
    except httpx.HTTPError:
        logger.exception("Failed to create Gmail draft")
        return None
