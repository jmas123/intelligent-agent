"""Tests for file life track classifier."""

from deadline_agent.awareness.classifier import classify_file


def test_recruiting_resume():
    assert classify_file(
        "/Users/x/Downloads/Resume_2026.pdf",
        "Resume_2026.pdf",
        "/Users/x/Downloads",
    ) == "recruiting"


def test_recruiting_cover_letter():
    assert classify_file(
        "/Users/x/Downloads/CoverLetter_Google.pdf",
        "CoverLetter_Google.pdf",
        "/Users/x/Downloads",
    ) == "recruiting"


def test_recruiting_cover_letter_spaces():
    assert classify_file(
        "/Users/x/Downloads/Cover Letter - Stripe.docx",
        "Cover Letter - Stripe.docx",
        "/Users/x/Downloads",
    ) == "recruiting"


def test_recruiting_contract():
    assert classify_file(
        "/Users/x/Downloads/Contract_Gecko.pdf",
        "Contract_Gecko.pdf",
        "/Users/x/Downloads",
    ) == "recruiting"


def test_recruiting_interview():
    assert classify_file(
        "/Users/x/Downloads/interview_prep_notes.md",
        "interview_prep_notes.md",
        "/Users/x/Downloads",
    ) == "recruiting"


def test_recruiting_cv():
    assert classify_file(
        "/Users/x/Downloads/CV.pdf",
        "CV.pdf",
        "/Users/x/Downloads",
    ) == "recruiting"


def test_school_file_not_recruiting():
    """A normal school file should not be classified as recruiting."""
    result = classify_file(
        "/Users/x/Documents/CS101/homework3.py",
        "homework3.py",
        "/Users/x/Documents/CS101",
    )
    # Not recruiting — returns None (school classification is done by the linker)
    assert result != "recruiting"


def test_unclassifiable():
    """A random file in Downloads with no signals returns None."""
    assert classify_file(
        "/Users/x/Downloads/photo.jpg",
        "photo.jpg",
        "/Users/x/Downloads",
    ) is None


def test_project_detection(tmp_path):
    """A file inside a directory with .git should be classified as project."""
    # Create a fake project
    project = tmp_path / "my-side-project"
    project.mkdir()
    (project / ".git").mkdir()
    src = project / "src"
    src.mkdir()

    result = classify_file(
        str(src / "main.py"),
        "main.py",
        str(src),
    )
    assert result == "project"


def test_project_pyproject(tmp_path):
    """A file in a directory with pyproject.toml is a project."""
    project = tmp_path / "tool"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='tool'\n")

    result = classify_file(
        str(project / "app.py"),
        "app.py",
        str(project),
    )
    assert result == "project"
