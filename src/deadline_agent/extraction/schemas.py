"""Pydantic schema for AI-extracted tasks."""

from pydantic import BaseModel, Field


class ExtractedTask(BaseModel):
    """Schema for AI-extracted task data. Matches docs/schemas.md."""

    title: str = Field(min_length=1, description="Short human-readable name")
    due_date_iso: str | None = Field(default=None, description="ISO 8601 datetime or null")
    source: str = Field(description="gmail | moodle | gcal | slack | notion")
    type: str = Field(description="assignment | exam | meeting | reminder | announcement")
    course: str | None = Field(default=None, description="Course name or null")
    urgency_score: int = Field(ge=1, le=5, description="1=informational, 5=exam tomorrow")
    confidence: float = Field(ge=0.0, le=1.0, description="Extraction confidence")
    raw_hash: str = Field(description="sha256 of source content for dedup")
