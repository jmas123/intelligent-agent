"""Tests for the file system watcher."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from deadline_agent.awareness.watcher import FileEvent, FileWatcher, _Handler


@pytest.fixture
def queue() -> asyncio.Queue[FileEvent]:
    return asyncio.Queue()


@pytest.fixture
def handler(queue: asyncio.Queue[FileEvent]) -> _Handler:
    return _Handler(queue)  # No loop = direct put_nowait for testing


def _make_event(src_path: str, is_directory: bool = False) -> MagicMock:
    event = MagicMock()
    event.src_path = src_path
    event.is_directory = is_directory
    return event


def test_handler_filters_by_extension(handler: _Handler, queue: asyncio.Queue[FileEvent]) -> None:
    event = _make_event("/Users/test/photo.jpg")
    handler.on_created(event)
    assert queue.empty()


def test_handler_accepts_valid_extension(
    handler: _Handler, queue: asyncio.Queue[FileEvent]
) -> None:
    event = _make_event("/Users/test/Documents/hw3.pdf")
    handler.on_created(event)
    assert not queue.empty()
    file_event = queue.get_nowait()
    assert file_event.event_type == "created"
    assert file_event.path == "/Users/test/Documents/hw3.pdf"


def test_handler_ignores_dotfiles(handler: _Handler, queue: asyncio.Queue[FileEvent]) -> None:
    event = _make_event("/Users/test/.hidden/secret.pdf")
    handler.on_created(event)
    assert queue.empty()


def test_handler_ignores_git_dir(handler: _Handler, queue: asyncio.Queue[FileEvent]) -> None:
    event = _make_event("/Users/test/project/.git/objects/abc.md")
    handler.on_created(event)
    assert queue.empty()


def test_handler_ignores_pycache(handler: _Handler, queue: asyncio.Queue[FileEvent]) -> None:
    event = _make_event("/Users/test/__pycache__/module.py")
    handler.on_created(event)
    assert queue.empty()


def test_handler_ignores_directories(handler: _Handler, queue: asyncio.Queue[FileEvent]) -> None:
    event = _make_event("/Users/test/Documents/CS101", is_directory=True)
    handler.on_created(event)
    assert queue.empty()


def test_handler_debounces_same_path(handler: _Handler, queue: asyncio.Queue[FileEvent]) -> None:
    event = _make_event("/Users/test/Documents/hw3.pdf")
    handler.on_modified(event)
    handler.on_modified(event)
    handler.on_modified(event)
    # Only first event should pass debounce
    assert queue.qsize() == 1


def test_handler_allows_after_debounce_window(
    handler: _Handler, queue: asyncio.Queue[FileEvent]
) -> None:
    handler._debounce_seconds = 0.0  # Disable debounce for this test
    event = _make_event("/Users/test/Documents/hw3.pdf")
    handler.on_modified(event)
    handler.on_modified(event)
    assert queue.qsize() == 2


def test_handler_on_moved(handler: _Handler, queue: asyncio.Queue[FileEvent]) -> None:
    event = MagicMock()
    event.is_directory = False
    event.dest_path = "/Users/test/Documents/hw3_final.pdf"
    handler.on_moved(event)
    assert not queue.empty()
    file_event = queue.get_nowait()
    assert file_event.event_type == "moved"
    assert file_event.path == "/Users/test/Documents/hw3_final.pdf"


def test_file_watcher_start_stop(queue: asyncio.Queue[FileEvent], tmp_path: object) -> None:
    with patch("deadline_agent.awareness.watcher.Observer") as mock_observer_cls:
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer

        watcher = FileWatcher([str(tmp_path)], queue)
        watcher.start()

        mock_observer.schedule.assert_called_once()
        mock_observer.start.assert_called_once()

        watcher.stop()
        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()


def test_file_watcher_skips_nonexistent_dir(
    queue: asyncio.Queue[FileEvent],
) -> None:
    with patch("deadline_agent.awareness.watcher.Observer") as mock_observer_cls:
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer

        watcher = FileWatcher(["/nonexistent/path"], queue)
        watcher.start()

        mock_observer.schedule.assert_not_called()
