"""Pre-filter: drops ~85% of messages that aren't deadline-related."""

import re

from deadline_agent.pipeline import IngestItem

DEADLINE_KEYWORDS: set[str] = {
    "deadline",
    "due",
    "due date",
    "submit",
    "submission",
    "assignment",
    "exam",
    "midterm",
    "final",
    "quiz",
    "project",
    "homework",
    "hw",
    "lab report",
    "by end of day",
    "by eod",
    "before midnight",
    "turn in",
    "hand in",
    "reminder",
    "overdue",
    "rubric",
    "syllabus",
    "presentation",
    "paper",
    "essay",
    "thesis",
    # Recruiting / professional
    "interview",
    "phone screen",
    "technical screen",
    "onsite",
    "on-site",
    "final round",
    "superday",
    "offer",
    "recruiter",
    "scheduled",
    "confirmed",
    "application",
    "coding challenge",
    "take-home",
    "assessment",
    # Job application tracking
    "applied",
    "application received",
    "application submitted",
    "thank you for applying",
    "we received your application",
    "application status",
    "application update",
    "moved forward",
    "next steps",
    "under review",
    "shortlisted",
    "rejected",
    "not moving forward",
    "unfortunately",
    "we regret",
    "position has been filled",
    "background check",
    "offer letter",
    "onboarding",
    "start date",
    "hiring manager",
}

ACADEMIC_SENDER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.edu$", re.IGNORECASE),
    re.compile(r"blackboard|moodle|brightspace", re.IGNORECASE),
    re.compile(r"instructure\.com$", re.IGNORECASE),
    re.compile(r"no-?reply.*university", re.IGNORECASE),
]

DATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}(?:,?\s*\d{4})?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:today|tomorrow|tonight|this\s+(?:friday|monday|week))\b", re.IGNORECASE),
    re.compile(r"\bby\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.IGNORECASE),
]


def has_deadline_keywords(text: str) -> bool:
    """Check if text contains any deadline-related keywords."""
    lower = text.lower()
    return any(kw in lower for kw in DEADLINE_KEYWORDS)


def matches_academic_sender(sender: str) -> bool:
    """Check if sender matches academic/LMS patterns."""
    return any(p.search(sender) for p in ACADEMIC_SENDER_PATTERNS)


def has_date_reference(text: str) -> bool:
    """Check if text contains date-like patterns."""
    return any(p.search(text) for p in DATE_PATTERNS)


def should_process(item: IngestItem) -> bool:
    """Main filter entry point. Returns True if item should proceed to extraction.

    Pass if keyword match found OR (academic sender AND date reference).
    """
    text = f"{item.metadata.get('subject', '')} {item.raw_content}"
    sender = item.metadata.get("sender", "")

    if has_deadline_keywords(text):
        return True
    if matches_academic_sender(sender) and has_date_reference(text):
        return True
    return False
