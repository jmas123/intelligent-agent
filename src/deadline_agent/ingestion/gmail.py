"""Gmail Pub/Sub webhook ingester."""

import base64
import hashlib
import json
import logging
from typing import Any

import httpx

from deadline_agent.auth import TokenManager
from deadline_agent.ingestion.base import BaseIngester
from deadline_agent.pipeline import IngestItem, pipeline_queue

logger = logging.getLogger(__name__)


class GmailIngester(BaseIngester):
    """Handles Gmail Pub/Sub push notifications.

    Flow: receive Pub/Sub envelope → decode historyId → fetch messages via Gmail API → enqueue.
    """

    def __init__(self, token_manager: TokenManager | None = None) -> None:
        self._token_manager = token_manager or TokenManager()
        self._api_base = "https://gmail.googleapis.com/gmail/v1"

    async def validate_payload(self, payload: dict[str, Any]) -> bool:
        """Validate Pub/Sub envelope structure."""
        msg = payload.get("message")
        if not isinstance(msg, dict):
            return False
        return "data" in msg and "messageId" in msg

    async def handle_webhook(self, payload: dict[str, Any]) -> None:
        """Decode Pub/Sub message, fetch email via Gmail API, enqueue."""
        data_b64 = payload["message"]["data"]
        decoded = base64.b64decode(data_b64)
        logger.info("Gmail push decoded data: %s", decoded[:200])
        data = json.loads(decoded)
        email_address = data.get("emailAddress", "me")
        history_id = data.get("historyId")

        logger.info("Gmail push: emailAddress=%s, historyId=%s", email_address, history_id)

        if not history_id:
            logger.warning("No historyId in Pub/Sub message")
            return

        messages = await self._fetch_messages_since(email_address, history_id)
        logger.info("Gmail: fetched %d message(s) from history", len(messages))
        for msg in messages:
            raw_hash = hashlib.sha256(msg["id"].encode()).hexdigest()
            item = IngestItem(
                source="gmail",
                raw_content=msg.get("snippet", ""),
                metadata={
                    "subject": msg.get("subject", ""),
                    "sender": msg.get("from", ""),
                    "gmail_id": msg["id"],
                    "raw_hash": raw_hash,
                },
            )
            await pipeline_queue.put(item)

    async def _fetch_messages_since(self, email: str, history_id: str) -> list[dict[str, Any]]:
        """Fetch new messages since historyId via Gmail API.

        Returns list of dicts with keys: id, snippet, subject, from.
        """
        token = await self._token_manager.get_valid_token()
        if not token:
            logger.error("No valid Google token available")
            return []

        headers = {"Authorization": f"Bearer {token}"}
        messages: list[dict[str, Any]] = []

        async with httpx.AsyncClient() as client:
            history_resp = await client.get(
                f"{self._api_base}/users/{email}/history",
                params={"startHistoryId": history_id, "historyTypes": "messageAdded"},
                headers=headers,
                timeout=10.0,
            )
            if history_resp.status_code != 200:
                logger.error("Gmail history API error: %s", history_resp.text)
                return []

            history_data = history_resp.json()
            message_ids: list[str] = []
            for record in history_data.get("history", []):
                for added in record.get("messagesAdded", []):
                    msg_id = added.get("message", {}).get("id")
                    if msg_id:
                        message_ids.append(msg_id)

            for msg_id in message_ids:
                msg_resp = await client.get(
                    f"{self._api_base}/users/{email}/messages/{msg_id}",
                    params={"format": "metadata", "metadataHeaders": ["Subject", "From"]},
                    headers=headers,
                    timeout=10.0,
                )
                if msg_resp.status_code != 200:
                    continue

                msg_data = msg_resp.json()
                headers_list = msg_data.get("payload", {}).get("headers", [])
                header_map = {h["name"].lower(): h["value"] for h in headers_list}

                messages.append(
                    {
                        "id": msg_id,
                        "snippet": msg_data.get("snippet", ""),
                        "subject": header_map.get("subject", ""),
                        "from": header_map.get("from", ""),
                    }
                )

        return messages
