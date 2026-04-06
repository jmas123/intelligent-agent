"""Tests for OAuth token management."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.auth import TokenManager


@pytest.fixture
def tmp_auth(tmp_path: Path) -> tuple[Path, Path]:
    """Create temporary credential and token files."""
    creds_path = tmp_path / "credentials.json"
    token_path = tmp_path / "token.json"

    creds_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test-client-id",
                    "client_secret": "test-client-secret",
                }
            }
        )
    )
    return creds_path, token_path


class TestTokenManager:
    def test_load_credentials(self, tmp_auth: tuple[Path, Path]) -> None:
        creds_path, token_path = tmp_auth
        tm = TokenManager(str(creds_path), str(token_path))
        creds = tm._load_credentials()
        assert creds is not None
        assert creds["client_id"] == "test-client-id"

    def test_missing_credentials(self, tmp_path: Path) -> None:
        tm = TokenManager(str(tmp_path / "missing.json"), str(tmp_path / "token.json"))
        assert tm._load_credentials() is None

    @pytest.mark.asyncio
    async def test_get_valid_token_not_expired(self, tmp_auth: tuple[Path, Path]) -> None:
        creds_path, token_path = tmp_auth
        token_path.write_text(
            json.dumps(
                {
                    "access_token": "valid-token",
                    "refresh_token": "refresh-token",
                    "expires_at": time.time() + 3600,
                }
            )
        )
        tm = TokenManager(str(creds_path), str(token_path))
        token = await tm.get_valid_token()
        assert token == "valid-token"

    @pytest.mark.asyncio
    async def test_get_valid_token_expired_refreshes(self, tmp_auth: tuple[Path, Path]) -> None:
        creds_path, token_path = tmp_auth
        token_path.write_text(
            json.dumps(
                {
                    "access_token": "expired-token",
                    "refresh_token": "refresh-token",
                    "expires_at": time.time() - 100,
                }
            )
        )
        tm = TokenManager(str(creds_path), str(token_path))

        mock_response = AsyncMock()
        mock_response.json = lambda: {
            "access_token": "new-token",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = lambda: None

        with patch("deadline_agent.auth.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            token = await tm.get_valid_token()

        assert token == "new-token"
        # Verify token was saved
        saved = json.loads(token_path.read_text())
        assert saved["access_token"] == "new-token"

    @pytest.mark.asyncio
    async def test_no_token_file(self, tmp_auth: tuple[Path, Path]) -> None:
        creds_path, token_path = tmp_auth
        tm = TokenManager(str(creds_path), str(token_path))
        token = await tm.get_valid_token()
        assert token is None

    @pytest.mark.asyncio
    async def test_exchange_code(self, tmp_auth: tuple[Path, Path]) -> None:
        creds_path, token_path = tmp_auth
        tm = TokenManager(str(creds_path), str(token_path))

        mock_response = AsyncMock()
        mock_response.json = lambda: {
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = lambda: None

        with patch("deadline_agent.auth.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            success = await tm.exchange_code("test-code")

        assert success
        saved = json.loads(token_path.read_text())
        assert saved["access_token"] == "new-token"
        assert saved["refresh_token"] == "new-refresh"
