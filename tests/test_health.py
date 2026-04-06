"""Smoke tests for the FastAPI app."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_gmail_webhook_invalid_payload(client: AsyncClient) -> None:
    response = await client.post("/webhooks/gmail", json={"message": "test"})
    assert response.status_code == 200
    assert response.json() == {"status": "invalid"}


@pytest.mark.asyncio
async def test_gmail_webhook_valid_payload(client: AsyncClient) -> None:
    payload = {
        "message": {"data": "eyJlbWFpbCI6ICJ0ZXN0In0=", "messageId": "123"},
        "subscription": "test",
    }
    response = await client.post("/webhooks/gmail", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "received"}
