"""Tests for LoRA training data export."""

import json
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from deadline_agent.extraction.extractor import EXTRACTION_SYSTEM_PROMPT
from deadline_agent.models import DigestLog, ExtractionLog, Task


def _make_extraction_log(
    session: Session,
    input_text: str = "Subject: HW5\nContent: Due Friday",
    output_json: str = '{"title":"HW5","due_date_iso":"2026-04-10","source":"gmail","type":"assignment","course":"CS101","urgency_score":3,"confidence":0.9,"raw_hash":"abc123"}',
    confidence: float = 0.9,
    accepted: bool = True,
    task_id: int | None = None,
) -> ExtractionLog:
    log = ExtractionLog(
        input_text=input_text,
        output_json=output_json,
        model_used="llama3.2:3b",
        confidence=confidence,
        task_id=task_id,
        accepted=accepted,
    )
    session.add(log)
    session.flush()
    return log


def _make_task(
    session: Session,
    title: str = "HW5",
    confidence: float = 0.9,
    status: str = "pending",
) -> Task:
    task = Task(
        title=title,
        due_date_iso="2026-04-10T23:59:00",
        source="gmail",
        type="assignment",
        course="CS101",
        urgency_score=3,
        confidence=confidence,
        raw_hash=f"hash_{title}_{confidence}",
        status=status,
    )
    session.add(task)
    session.flush()
    return task


class TestExportExtractionPairs:
    def test_exports_accepted_high_confidence(self, session: Session) -> None:
        from deadline_agent.lora.export import export_extraction_pairs

        _make_extraction_log(session, confidence=0.9, accepted=True)
        _make_extraction_log(session, confidence=0.85, accepted=True)

        pairs = export_extraction_pairs(session)
        assert len(pairs) == 2
        assert pairs[0]["instruction"] == EXTRACTION_SYSTEM_PROMPT
        assert "HW5" in pairs[0]["input"]

    def test_filters_low_confidence(self, session: Session) -> None:
        from deadline_agent.lora.export import export_extraction_pairs

        _make_extraction_log(session, confidence=0.6, accepted=True)
        _make_extraction_log(session, confidence=0.9, accepted=True)

        pairs = export_extraction_pairs(session, min_confidence=0.75)
        assert len(pairs) == 1

    def test_filters_rejected(self, session: Session) -> None:
        from deadline_agent.lora.export import export_extraction_pairs

        _make_extraction_log(session, confidence=0.9, accepted=False)
        _make_extraction_log(session, confidence=0.9, accepted=True)

        pairs = export_extraction_pairs(session, only_accepted=True)
        assert len(pairs) == 1

    def test_includes_rejected_when_flag_false(self, session: Session) -> None:
        from deadline_agent.lora.export import export_extraction_pairs

        _make_extraction_log(session, confidence=0.9, accepted=False)
        _make_extraction_log(session, confidence=0.9, accepted=True)

        pairs = export_extraction_pairs(session, only_accepted=False)
        assert len(pairs) == 2


class TestExportDigestPairs:
    def test_exports_digest_pairs(self, session: Session) -> None:
        from deadline_agent.lora.export import export_digest_pairs

        session.add(DigestLog(
            input_prompt="state snapshot",
            output_text="morning briefing text",
            model_used="llama3.2:3b",
            prompt_type="digest",
        ))
        session.flush()

        pairs = export_digest_pairs(session)
        assert len(pairs) == 1
        assert pairs[0]["input"] == "state snapshot"
        assert pairs[0]["output"] == "morning briefing text"

    def test_filters_by_prompt_type(self, session: Session) -> None:
        from deadline_agent.lora.export import export_digest_pairs

        session.add(DigestLog(
            input_prompt="p1", output_text="o1", model_used="m", prompt_type="digest",
        ))
        session.add(DigestLog(
            input_prompt="p2", output_text="o2", model_used="m", prompt_type="insight",
        ))
        session.flush()

        pairs = export_digest_pairs(session, prompt_type="digest")
        assert len(pairs) == 1
        assert pairs[0]["input"] == "p1"


class TestExportSyntheticPairs:
    def test_generates_pairs_from_tasks(self, session: Session) -> None:
        from deadline_agent.lora.export import export_synthetic_pairs

        _make_task(session, title="Final Exam", confidence=0.9, status="pending")
        _make_task(session, title="HW6", confidence=0.85, status="done")

        pairs = export_synthetic_pairs(session)
        assert len(pairs) == 2
        # Verify output is valid ExtractedTask JSON
        for pair in pairs:
            data = json.loads(pair["output"])
            assert "title" in data
            assert "source" in data

    def test_excludes_dismissed_tasks(self, session: Session) -> None:
        from deadline_agent.lora.export import export_synthetic_pairs

        _make_task(session, title="Bad", confidence=0.9, status="dismissed")
        _make_task(session, title="Good", confidence=0.9, status="pending")

        pairs = export_synthetic_pairs(session)
        assert len(pairs) == 1

    def test_excludes_low_confidence_tasks(self, session: Session) -> None:
        from deadline_agent.lora.export import export_synthetic_pairs

        _make_task(session, title="Low", confidence=0.5)
        _make_task(session, title="High", confidence=0.9)

        pairs = export_synthetic_pairs(session)
        assert len(pairs) == 1


class TestWriteJsonl:
    def test_writes_valid_jsonl(self, session: Session) -> None:
        from deadline_agent.lora.export import write_jsonl

        pairs = [
            {"instruction": "sys", "input": "in1", "output": "out1"},
            {"instruction": "sys", "input": "in2", "output": "out2"},
        ]

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        count = write_jsonl(pairs, path)
        assert count == 2

        lines = Path(path).read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "instruction" in data
            assert "input" in data
            assert "output" in data

        Path(path).unlink()


class TestTrainingStats:
    def test_returns_stats(self, session: Session) -> None:
        from deadline_agent.lora.export import get_training_stats

        _make_extraction_log(session, confidence=0.9, accepted=True)
        _make_extraction_log(session, confidence=0.8, accepted=False)
        _make_task(session, title="T1", confidence=0.9)

        stats = get_training_stats(session)
        assert stats["extraction_logs"]["total"] == 2
        assert stats["extraction_logs"]["accepted"] == 1
        assert stats["extraction_logs"]["rejected"] == 1
        assert stats["synthetic_eligible_tasks"] == 1
