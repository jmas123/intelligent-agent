"""A/B evaluation harness for comparing base vs fine-tuned models."""

import json
import logging
from pathlib import Path
from typing import Any

from deadline_agent.config import settings
from deadline_agent.extraction.extractor import EXTRACTION_SYSTEM_PROMPT
from deadline_agent.extraction.schemas import ExtractedTask

logger = logging.getLogger(__name__)


async def evaluate_model(
    model_tag: str,
    test_pairs: list[dict[str, str]],
    base_url: str | None = None,
) -> dict[str, Any]:
    """Run a model against held-out test pairs and compute metrics.

    Each test pair must have {"input": str, "output": str} where output is
    the ground truth ExtractedTask JSON.

    Returns metrics dict with schema validity, confidence, and field accuracy.
    """
    from ollama import AsyncClient

    client = AsyncClient(host=base_url or settings.ollama_base_url)
    total = len(test_pairs)
    valid_json = 0
    schema_valid = 0
    confidences: list[float] = []
    field_matches: dict[str, int] = {
        "title": 0, "due_date_iso": 0, "source": 0,
        "type": 0, "course": 0, "urgency_score": 0,
    }

    for pair in test_pairs:
        try:
            response = await client.chat(
                model=model_tag,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": pair["input"]},
                ],
                format=ExtractedTask.model_json_schema(),
            )
            content = response.message.content or ""
        except Exception:
            logger.warning("Model %s failed on input", model_tag, exc_info=True)
            continue

        # Check JSON validity
        try:
            parsed = json.loads(content)
            valid_json += 1
        except json.JSONDecodeError:
            continue

        # Check schema validity
        try:
            predicted = ExtractedTask.model_validate(parsed)
            schema_valid += 1
            confidences.append(predicted.confidence)
        except Exception:
            continue

        # Compare fields against ground truth
        try:
            ground_truth = ExtractedTask.model_validate_json(pair["output"])
            for field_name in field_matches:
                if getattr(predicted, field_name) == getattr(ground_truth, field_name):
                    field_matches[field_name] += 1
        except Exception:
            pass

    field_accuracy = {
        k: round(v / total, 3) if total > 0 else 0.0
        for k, v in field_matches.items()
    }

    return {
        "model": model_tag,
        "total": total,
        "valid_json": valid_json,
        "schema_valid": schema_valid,
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "field_accuracy": field_accuracy,
    }


async def compare_models(
    base_model: str,
    finetuned_model: str,
    test_pairs: list[dict[str, str]],
    base_url: str | None = None,
) -> dict[str, Any]:
    """Run A/B evaluation between base and fine-tuned models.

    Returns side-by-side metrics for comparison.
    """
    base_results = await evaluate_model(base_model, test_pairs, base_url)
    finetuned_results = await evaluate_model(finetuned_model, test_pairs, base_url)

    return {
        "base": base_results,
        "finetuned": finetuned_results,
        "improvements": {
            "valid_json_delta": finetuned_results["valid_json"] - base_results["valid_json"],
            "schema_valid_delta": finetuned_results["schema_valid"] - base_results["schema_valid"],
            "field_accuracy_delta": {
                k: round(
                    finetuned_results["field_accuracy"].get(k, 0)
                    - base_results["field_accuracy"].get(k, 0),
                    3,
                )
                for k in base_results["field_accuracy"]
            },
        },
    }


def load_test_pairs(path: str | Path) -> list[dict[str, str]]:
    """Load test pairs from a JSONL file."""
    pairs: list[dict[str, str]] = []
    with Path(path).open() as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs
