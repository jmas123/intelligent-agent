"""FSEvents-based directory watcher using watchdog."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from deadline_agent.config import settings

logger = logging.getLogger(__name__)


@dataclass
class FileEvent:
    """Lightweight representation of a file system event."""

    path: str
    event_type: str  # created | modified | moved
    timestamp: datetime


class _Handler(FileSystemEventHandler):
    """Watchdog event handler that filters and enqueues events."""

    def __init__(
        self,
        queue: asyncio.Queue[FileEvent],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__()
        self._queue = queue
        self._loop = loop
        self._last_seen: dict[str, float] = {}
        self._debounce_seconds = 5.0

    def _should_process(self, path: str) -> bool:
        """Check extension whitelist and ignore patterns."""
        p = Path(path)
        if not p.suffix or p.suffix.lower() not in settings.watch_extensions:
            return False
        for part in p.parts:
            for pattern in settings.watch_ignore_patterns:
                if fnmatch(part, pattern):
                    return False
        return True

    def _is_debounced(self, path: str) -> bool:
        """Skip if same path was seen within debounce window."""
        now = time.monotonic()
        last = self._last_seen.get(path)
        if last is not None and (now - last) < self._debounce_seconds:
            return True
        self._last_seen[path] = now
        return False

    def _enqueue(self, path: str, event_type: str) -> None:
        if not self._should_process(path):
            return
        if self._is_debounced(path):
            return
        event = FileEvent(path=path, event_type=event_type, timestamp=datetime.now())
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        else:
            self._queue.put_nowait(event)
        logger.debug("File event enqueued: %s %s", event_type, path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(str(event.src_path), "created")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(str(event.src_path), "modified")

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(str(event.dest_path), "moved")


class FileWatcher:
    """Watch directories for file changes via FSEvents."""

    def __init__(
        self,
        directories: list[str],
        queue: asyncio.Queue[FileEvent],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._directories = directories
        self._observer = Observer()
        self._handler = _Handler(queue, loop)

    def start(self) -> None:
        """Start watching all configured directories."""
        for dir_str in self._directories:
            path = Path(dir_str).expanduser()
            if not path.is_dir():
                logger.warning("Watch directory does not exist: %s", path)
                continue
            self._observer.schedule(self._handler, str(path), recursive=True)
            logger.info("Watching directory: %s", path)
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        """Stop the watcher."""
        self._observer.stop()
        self._observer.join(timeout=5)
