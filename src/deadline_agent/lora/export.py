"""Export training data pairs for LoRA fine-tuning."""

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.extraction.extractor import EXTRACTION_SYSTEM_PROMPT
from deadline_agent.extraction.schemas import ExtractedTask
from deadline_agent.models import DigestLog, ExtractionLog, Task

logger = logging.getLogger(__name__)


def export_extraction_pairs(
    session: Session,
    min_confidence: float = 0.75,
    only_accepted: bool = True,
) -> list[dict[str, str]]:
    """Export extraction training pairs from ExtractionLog table.

    Returns Alpaca-format dicts: {"instruction", "input", "output"}.
    """
    stmt = select(ExtractionLog).where(ExtractionLog.confidence >= min_confidence)
    if only_accepted:
        stmt = stmt.where(ExtractionLog.accepted.is_(True))
    stmt = stmt.order_by(ExtractionLog.created_at.asc())

    logs = session.scalars(stmt).all()
    pairs: list[dict[str, str]] = []
    for log in logs:
        pairs.append({
            "instruction": EXTRACTION_SYSTEM_PROMPT,
            "input": log.input_text,
            "output": log.output_json,
        })
    return pairs


def export_digest_pairs(
    session: Session,
    prompt_type: str | None = None,
) -> list[dict[str, str]]:
    """Export digest/reasoning training pairs from DigestLog table.

    Returns Alpaca-format dicts: {"instruction", "input", "output"}.
    """
    from deadline_agent.reasoning.engine import DIGEST_SYSTEM_PROMPT, REASONING_SYSTEM_PROMPT

    stmt = select(DigestLog).order_by(DigestLog.created_at.asc())
    if prompt_type is not None:
        stmt = stmt.where(DigestLog.prompt_type == prompt_type)

    logs = session.scalars(stmt).all()
    pairs: list[dict[str, str]] = []

    system_prompts = {
        "digest": DIGEST_SYSTEM_PROMPT,
        "insight": REASONING_SYSTEM_PROMPT,
        "query": "",  # query system prompt varies
    }

    for log in logs:
        pairs.append({
            "instruction": system_prompts.get(log.prompt_type, ""),
            "input": log.input_prompt,
            "output": log.output_text,
        })
    return pairs


def export_synthetic_pairs(session: Session) -> list[dict[str, str]]:
    """Generate synthetic training pairs from existing Task rows.

    Since historical tasks lack the original input text, this reconstructs
    approximate inputs from task metadata. Useful as a bootstrap before
    real logged data accumulates.
    """
    stmt = (
        select(Task)
        .where(Task.confidence >= 0.75)
        .where(Task.status != "dismissed")
        .order_by(Task.created_at.asc())
    )
    tasks = session.scalars(stmt).all()
    pairs: list[dict[str, str]] = []

    for task in tasks:
        # Reconstruct an approximate input from task fields
        parts: list[str] = []
        if task.title:
            parts.append(f"Subject: {task.title}")
        if task.course:
            parts.append(f"Course: {task.course}")
        if task.due_date_iso:
            parts.append(f"Due date hint: {task.due_date_iso}")
        parts.append(f"Content:\n{task.title}")
        synthetic_input = "\n".join(parts)

        # Build the expected output as ExtractedTask JSON
        output = ExtractedTask(
            title=task.title,
            due_date_iso=task.due_date_iso,
            source=task.source,
            type=task.type,
            course=task.course,
            urgency_score=task.urgency_score,
            confidence=task.confidence,
            raw_hash=task.raw_hash,
        )

        pairs.append({
            "instruction": EXTRACTION_SYSTEM_PROMPT,
            "input": synthetic_input,
            "output": output.model_dump_json(),
        })

    logger.info("Generated %d synthetic training pairs from Task table", len(pairs))
    return pairs


def write_jsonl(pairs: list[dict[str, Any]], output_path: str | Path) -> int:
    """Write training pairs to a JSONL file. Returns number of pairs written."""
    path = Path(output_path)
    with path.open("w") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
    logger.info("Wrote %d training pairs to %s", len(pairs), path)
    return len(pairs)


def get_training_stats(session: Session) -> dict[str, Any]:
    """Return statistics about available training data."""
    from sqlalchemy import func

    extraction_total = session.scalar(select(func.count(ExtractionLog.id))) or 0
    extraction_accepted = session.scalar(
        select(func.count(ExtractionLog.id)).where(ExtractionLog.accepted.is_(True))
    ) or 0
    extraction_rejected = extraction_total - extraction_accepted
    avg_confidence = session.scalar(
        select(func.avg(ExtractionLog.confidence)).where(ExtractionLog.accepted.is_(True))
    )

    digest_total = session.scalar(select(func.count(DigestLog.id))) or 0
    digest_by_type: dict[str, int] = {}
    rows = session.execute(
        select(DigestLog.prompt_type, func.count(DigestLog.id)).group_by(DigestLog.prompt_type)
    ).all()
    for ptype, count in rows:
        digest_by_type[ptype] = count

    # Count synthetic-eligible tasks
    synthetic_eligible = session.scalar(
        select(func.count(Task.id))
        .where(Task.confidence >= 0.75)
        .where(Task.status != "dismissed")
    ) or 0

    return {
        "extraction_logs": {
            "total": extraction_total,
            "accepted": extraction_accepted,
            "rejected": extraction_rejected,
            "avg_confidence": round(avg_confidence, 3) if avg_confidence else None,
        },
        "digest_logs": {
            "total": digest_total,
            "by_type": digest_by_type,
        },
        "synthetic_eligible_tasks": synthetic_eligible,
    }
