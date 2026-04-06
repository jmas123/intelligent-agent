"""Heuristic file-to-task linking via string matching."""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import FileActivity, Task

logger = logging.getLogger(__name__)

# Words too common to be useful signals
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "is",
        "at",
        "by",
        "submit",
        "upload",
        "turn",
        "assignment",
        "homework",
        "project",
        "exam",
        "quiz",
    }
)


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase alphanumeric tokens, removing stop words."""
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return tokens - _STOP_WORDS


def _course_in_path(course: str, directory: str) -> bool:
    """Check if course name tokens appear in the directory path."""
    course_tokens = _tokenize(course)
    if not course_tokens:
        return False
    dir_lower = directory.lower()
    # Check for concatenated form (e.g., "CS101" in path)
    course_compact = re.sub(r"\s+", "", course.lower())
    if course_compact and course_compact in dir_lower:
        return True
    # Check for spaced form with all tokens present
    dir_tokens = _tokenize(directory)
    return course_tokens.issubset(dir_tokens)


def _title_keywords_in_filename(title: str, filename: str) -> float:
    """Score overlap between task title keywords and filename tokens."""
    title_tokens = _tokenize(title)
    if not title_tokens:
        return 0.0
    filename_tokens = _tokenize(filename)
    if not filename_tokens:
        return 0.0
    overlap = title_tokens & filename_tokens
    if not overlap:
        return 0.0
    return len(overlap) / len(title_tokens)


class TaskLinker:
    """Match file activity to existing tasks via string heuristics."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_matching_tasks(self, activity: FileActivity) -> list[tuple[Task, float, str]]:
        """Return (task, confidence, method) tuples for tasks matching this file.

        Only returns matches with confidence > 0.
        """
        stmt = select(Task).where(Task.status == "pending").where(Task.due_date_iso.is_not(None))
        tasks = list(self._session.scalars(stmt).all())

        matches: list[tuple[Task, float, str]] = []
        for task in tasks:
            score = self._string_match(activity, task)
            if score > 0:
                matches.append((task, score, "string"))

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    def _string_match(self, activity: FileActivity, task: Task) -> float:
        """Score 0.0-1.0 based on string overlap between file and task."""
        score = 0.0

        # Course name in directory path is a strong signal
        if task.course and _course_in_path(task.course, activity.directory):
            score = 0.7

        # Title keywords in filename add a bonus
        title_overlap = _title_keywords_in_filename(task.title, activity.filename)
        if title_overlap > 0:
            score += 0.3 * title_overlap

        return min(score, 1.0)
