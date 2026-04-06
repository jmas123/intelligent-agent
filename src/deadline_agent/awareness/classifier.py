"""File classifier: infer life track (school, recruiting, project) from file metadata."""

import fnmatch
import os
from pathlib import Path

from deadline_agent.config import settings

# Substrings in filename that signal recruiting activity
_RECRUITING_SIGNALS = [
    "resume",
    "cv",
    "cover letter",
    "cover_letter",
    "coverletter",
    "offer letter",
    "offer_letter",
    "offerletter",
    "contract",
    "interview",
    "application",
    "job",
    "recruiter",
    "hiring",
    "salary",
    "nda",
    "background check",
    "onboarding",
]

# Files that indicate a directory is a software project root
_PROJECT_INDICATORS = {
    ".git",
    "Cargo.toml",
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Makefile",
    "CMakeLists.txt",
    "pom.xml",
    "build.gradle",
    ".xcodeproj",
}


def classify_file(path: str, filename: str, directory: str) -> str | None:
    """Classify a file into a life track based on name and location.

    Returns "recruiting", "project", or None.
    School classification is handled by the task linker, not here.
    """
    # 1. Recruiting: keyword match on filename
    name_lower = filename.lower()
    for signal in _RECRUITING_SIGNALS:
        if signal in name_lower:
            return "recruiting"

    # 1b. Recruiting: user-configured filename patterns (e.g. "JudeElMasri*.pdf")
    for pattern in settings.recruiting_patterns:
        if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(name_lower, pattern.lower()):
            return "recruiting"

    # 2. Project: file is inside a project directory
    if _is_in_project(directory):
        return "project"

    return None


def _is_in_project(directory: str) -> bool:
    """Check if a directory is inside a software project root."""
    expanded = os.path.expanduser(directory)
    current = Path(expanded)

    # Don't walk above home directory
    home = Path.home()

    # Check configured project directories first
    project_dirs = [
        Path(os.path.expanduser(d)).resolve()
        for d in settings.project_directories
    ]
    resolved = current.resolve()
    for proj_dir in project_dirs:
        if resolved == proj_dir or proj_dir in resolved.parents:
            return True

    # Walk up looking for project indicators (max 5 levels)
    for _ in range(5):
        if current == home or current == current.parent:
            break
        for indicator in _PROJECT_INDICATORS:
            if (current / indicator).exists():
                return True
        current = current.parent

    return False
