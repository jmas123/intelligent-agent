"""Simple TTL cache for LLM reasoning results."""

import hashlib
import time
from dataclasses import dataclass, field


@dataclass
class _CacheEntry:
    value: str
    expires_at: float


class TTLCache:
    """In-memory cache with per-key TTL. Thread-safe enough for async use."""

    def __init__(self, default_ttl_seconds: int = 1800) -> None:
        self._default_ttl = default_ttl_seconds
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None or time.monotonic() > entry.expires_at:
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self._store[key] = _CacheEntry(
            value=value,
            expires_at=time.monotonic() + (ttl or self._default_ttl),
        )

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


# Shared cache instances
digest_cache = TTLCache(default_ttl_seconds=1800)  # 30 min
weekly_review_cache = TTLCache(default_ttl_seconds=3600)  # 60 min


def snapshot_hash(prompt_text: str) -> str:
    """Hash a state snapshot prompt to detect changes."""
    return hashlib.sha256(prompt_text.encode()).hexdigest()[:16]
