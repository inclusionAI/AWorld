"""
File watcher module using watchdog.
Monitors memory files for changes and triggers synchronization.
"""

import time
import logging
from pathlib import Path
from typing import Callable, Set, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger(__name__)


class MemoryFileHandler(FileSystemEventHandler):
    """Handler for memory file system events."""

    def __init__(self, on_change: Callable[[], None], debounce_ms: int = 1500):
        """Initialize handler with change callback."""
        self.on_change = on_change
        self.debounce_ms = debounce_ms
        self.last_trigger_time = 0
        self.pending_timer = None

    def _should_ignore(self, path: str) -> bool:
        """Check if path should be ignored."""
        path_obj = Path(path)
        
        # Ignore hidden files
        if any(part.startswith('.') for part in path_obj.parts):
            return True
        
        # Ignore specific directories
        ignore_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'venv'}
        if any(part in ignore_dirs for part in path_obj.parts):
            return True
        
        # Only watch .md files
        if path_obj.suffix.lower() != '.md':
            return True
        
        return False

    def _trigger_change(self):
        """Trigger change callback with debouncing."""
        current_time = time.time() * 1000  # Convert to milliseconds
        
        # If enough time has passed since last trigger, trigger immediately
        if current_time - self.last_trigger_time >= self.debounce_ms:
            self.last_trigger_time = current_time
            self.on_change()
        else:
            # Otherwise, schedule a delayed trigger
            # This will be overwritten if more changes come in
            pass

    def on_created(self, event: FileSystemEvent):
        """Handle file creation."""
        if event.is_directory or self._should_ignore(event.src_path):
            return
        logger.debug(f"Memory file created: {event.src_path}")
        self._trigger_change()

    def on_modified(self, event: FileSystemEvent):
        """Handle file modification."""
        if event.is_directory or self._should_ignore(event.src_path):
            return
        logger.debug(f"Memory file modified: {event.src_path}")
        self._trigger_change()

    def on_deleted(self, event: FileSystemEvent):
        """Handle file deletion."""
        if event.is_directory or self._should_ignore(event.src_path):
            return
        logger.debug(f"Memory file deleted: {event.src_path}")
        self._trigger_change()


class MemoryWatcher:
    """Watches memory files for changes."""

    def __init__(
        self,
        workspace_dir: str,
        on_change: Callable[[], None],
        debounce_ms: int = 1500,
        extra_paths: Optional[List[str]] = None,
    ):
        """Initialize watcher."""
        self.workspace_dir = Path(workspace_dir)
        self.on_change = on_change
        self.debounce_ms = debounce_ms
        self.extra_paths = extra_paths or []
        self.observer: Optional[Observer] = None
        self.handler: Optional[MemoryFileHandler] = None
        self._watch_paths: Set[Path] = set()

    def start(self):
        """Start watching files."""
        if self.observer:
            logger.warning("Watcher already started")
            return

        # Build watch paths
        self._watch_paths = {
            self.workspace_dir,  # Watch root for MEMORY.md
            self.workspace_dir / "memory",  # Watch memory directory
        }
        
        # Add extra paths
        for extra_path in self.extra_paths:
            path = Path(extra_path).expanduser().resolve()
            if path.exists():
                self._watch_paths.add(path)

        # Create handler and observer
        self.handler = MemoryFileHandler(self.on_change, self.debounce_ms)
        self.observer = Observer()

        # Schedule watches
        for watch_path in self._watch_paths:
            if watch_path.exists():
                self.observer.schedule(self.handler, str(watch_path), recursive=True)
                logger.info(f"Watching memory path: {watch_path}")

        self.observer.start()
        logger.info("Memory file watcher started")

    def stop(self):
        """Stop watching files."""
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
            logger.info("Memory file watcher stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
