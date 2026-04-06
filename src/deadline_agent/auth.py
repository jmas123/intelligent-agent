"""Google OAuth2 token management."""

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from deadline_agent.config import settings

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class TokenManager:
    """Manages Google OAuth2 tokens with automatic refresh."""

    def __init__(
        self,
        credentials_path: str | None = None,
        token_path: str | None = None,
    ) -> None:
        self._credentials_path = Path(credentials_path or settings.google_credentials_path)
        self._token_path = Path(token_path or settings.google_token_path)
        self._token_data: dict[str, Any] | None = None
        self._credentials: dict[str, Any] | None = None

    def _load_credentials(self) -> dict[str, Any] | None:
        """Load OAuth client credentials from file."""
        if self._credentials is not None:
            return self._credentials
        if not self._credentials_path.exists():
            logger.error("Google credentials not found: %s", self._credentials_path)
            return None
        data = json.loads(self._credentials_path.read_text())
        # Handle both "installed" and "web" credential types
        self._credentials = data.get("installed") or data.get("web") or data
        return self._credentials

    def _load_token(self) -> dict[str, Any] | None:
        """Load stored token from file."""
        if self._token_data is not None:
            return self._token_data
        if not self._token_path.exists():
            return None
        self._token_data = json.loads(self._token_path.read_text())
        return self._token_data

    def _save_token(self, token_data: dict[str, Any]) -> None:
        """Persist token data to file."""
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps(token_data, indent=2))
        self._token_data = token_data

    def _is_expired(self, token_data: dict[str, Any]) -> bool:
        """Check if the access token is expired (with 5-minute buffer)."""
        expires_at: float = token_data.get("expires_at", 0)
        return time.time() >= (expires_at - 300)

    async def get_valid_token(self) -> str | None:
        """Return a valid access token, refreshing if necessary."""
        token_data = self._load_token()
        if token_data is None:
            logger.error("No Google token found. Run 'deadline-agent auth google' first.")
            return None

        if not self._is_expired(token_data):
            access_token: str | None = token_data.get("access_token")
            return access_token

        # Refresh the token
        credentials = self._load_credentials()
        if credentials is None:
            return None

        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            logger.error("No refresh token available. Re-run 'deadline-agent auth google'.")
            return None

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": credentials["client_id"],
                        "client_secret": credentials["client_secret"],
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=10.0,
                )
                resp.raise_for_status()
                new_data = resp.json()
        except httpx.HTTPError:
            logger.exception("Failed to refresh Google token")
            return None

        # Update stored token
        token_data["access_token"] = new_data["access_token"]
        token_data["expires_at"] = time.time() + new_data.get("expires_in", 3600)
        if "refresh_token" in new_data:
            token_data["refresh_token"] = new_data["refresh_token"]
        self._save_token(token_data)

        logger.info("Google token refreshed successfully")
        result: str = token_data["access_token"]
        return result

    async def exchange_code(
        self, code: str, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob"
    ) -> bool:
        """Exchange an authorization code for tokens (one-time setup)."""
        credentials = self._load_credentials()
        if credentials is None:
            return False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": credentials["client_id"],
                        "client_secret": credentials["client_secret"],
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": redirect_uri,
                    },
                    timeout=10.0,
                )
                resp.raise_for_status()
                token_data = resp.json()
        except httpx.HTTPError:
            logger.exception("Failed to exchange authorization code")
            return False

        token_data["expires_at"] = time.time() + token_data.get("expires_in", 3600)
        self._save_token(token_data)
        logger.info("Google token saved successfully")
        return True
