"""Near-duplicate detection via embedding similarity."""

import json
import logging
import math
from datetime import datetime, timedelta

from ollama import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import Task

logger = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def generate_embedding(text: str) -> list[float]:
    """Generate an embedding vector using Ollama."""
    client = AsyncClient(host=settings.ollama_base_url)
    response = await client.embed(model=settings.embedding_model, input=text)
    return list(response.embeddings[0])


def find_near_duplicates(
    session: Session,
    embedding: list[float],
    threshold: float | None = None,
    window_days: int | None = None,
) -> list[tuple[Task, float]]:
    """Find tasks with similar embeddings within a time window.

    Returns list of (task, similarity_score) tuples above threshold.
    """
    threshold = threshold if threshold is not None else settings.similarity_threshold
    window_days = window_days if window_days is not None else settings.dedup_window_days

    cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()
    stmt = (
        select(Task)
        .where(Task.embedding.is_not(None))
        .where(Task.created_at >= cutoff)
        .where(Task.status != "dismissed")
    )
    tasks = list(session.scalars(stmt).all())

    matches: list[tuple[Task, float]] = []
    for task in tasks:
        stored_embedding: list[float] = json.loads(task.embedding)  # type: ignore[arg-type]
        score = cosine_similarity(embedding, stored_embedding)
        if score >= threshold:
            matches.append((task, score))

    matches.sort(key=lambda x: x[1], reverse=True)
    return matches
