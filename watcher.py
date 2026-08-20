"""Filesystem watching, so a local edit does not wait for the next poll.

watchdog subscribes to the kernel's filesystem notifications (inotify on Linux),
which is what makes a local edit visible to the daemon immediately. Two limits
sit on top of that, and they solve different problems:

* A **quiet period** — the trigger is deferred until writing stops. An editor
  saves repeatedly while you type, and syncing a file mid-write uploads a
  half-finished note.
* A **minimum interval** — at most one watcher-driven cycle per 30 seconds, so
  a long editing session cannot turn into a continuous upload loop. Editing for
  an hour therefore uploads about twice a minute, rather than either constantly
  or not until you stop.

Events are filtered through the same ignore patterns the scans use. Skipping
that filter is not merely wasteful: an ignored file still counts as "something
changed", so Obsidian rewriting `.obsidian/workspace.json` spends the one
trigger per 30 seconds on a cycle with nothing to do, and the real note edit
that follows is dropped and left to the poll.
"""
import os
import time
import threading

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import sync_engine
from filters import should_ignore
from paths import to_key
from sync_engine import log, _sync_running, _sync_trigger

# How long writing must stop before a change counts as settled.
_QUIET_PERIOD_SECONDS = 3.0

# The floor between two watcher-driven cycles.
_MIN_INTERVAL_SECONDS = 30

# How often to re-check while a cycle is running. A cycle has no predictable
# length — a cached remote listing makes it instant, a forced refresh takes
# roughly a second per thirty files — so there is nothing better to wait on.
_RETRY_SECONDS = 1.0


class VaultEventHandler(FileSystemEventHandler):
    def __init__(self, vault_path=None, ignore_patterns=None):
        super().__init__()
        self._timer = None
        self._lock = threading.Lock()
        self._vault_path = vault_path
        self._ignore_patterns = ignore_patterns or []
        self._last_trigger = 0.0

    def _is_ignored(self, raw_path):
        """True when this path is one the scans would filter out anyway."""
        if not raw_path:
            return False
        if not self._vault_path:
            # No vault root to make the path relative to, so no reliable key to
            # match against. Watching too much is safe; missing an edit is not.
            return False
        try:
            rel = os.path.relpath(_fspath(raw_path), self._vault_path)
        except ValueError:              # different drive on Windows
            return False
        if rel.startswith(".."):        # outside the vault entirely
            return False
        return should_ignore(to_key(rel), self._ignore_patterns)

    def _debounce_trigger(self):
        self._schedule(_QUIET_PERIOD_SECONDS)

    def _schedule(self, delay):
        """Arm the single pending timer, replacing whatever was on it."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(max(delay, 0.05), self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _blocked_until(self, now):
        """When the next trigger may fire, or None if it may fire now."""
        if _sync_running.is_set():
            # A cycle gives no notice of when it will end, so re-check soon.
            return now + _RETRY_SECONDS
        if now < sync_engine._watchdog_suppress_until:
            return sync_engine._watchdog_suppress_until
        if now - self._last_trigger < _MIN_INTERVAL_SECONDS:
            return self._last_trigger + _MIN_INTERVAL_SECONDS
        return None

    def _fire(self):
        # A blocked change is deferred, never dropped. Returning here instead
        # discarded it: an edit made while a cycle was running — and a forced
        # remote scan takes far longer than the interval below — was forgotten
        # until the next poll, up to poll_interval later.
        now = time.time()
        blocked_until = self._blocked_until(now)
        if blocked_until is not None:
            self._schedule(blocked_until - now)
            return
        self._last_trigger = now
        log("DEBUG", "Local change detected — triggering sync")
        _sync_trigger.set()

    def cancel_pending(self):
        """Drop any armed timer, so a stopped watcher fires nothing."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _handle(self, event, *paths):
        if event.is_directory:
            return
        # A rename counts if either end is a file we sync: moving a note into
        # .trash/ is a deletion, and moving one out of it is a creation.
        if all(self._is_ignored(path) for path in paths if path):
            return
        self._debounce_trigger()

    def on_modified(self, event):
        self._handle(event, event.src_path)

    def on_created(self, event):
        self._handle(event, event.src_path)

    def on_deleted(self, event):
        self._handle(event, event.src_path)

    def on_moved(self, event):
        self._handle(event, event.src_path, getattr(event, "dest_path", None))


def _fspath(path):
    """watchdog reports bytes when the watched path was given as bytes."""
    return path.decode("utf-8", "surrogateescape") if isinstance(path, bytes) else path


class VaultWatcher:
    def __init__(self, vault_path, ignore_patterns=None):
        self._vault_path = vault_path
        self._observer = Observer()
        self._handler = VaultEventHandler(vault_path, ignore_patterns)

    def start(self):
        self._observer.schedule(
            self._handler, self._vault_path, recursive=True
        )
        self._observer.start()
        log("INFO", f"Watchdog monitoring: {self._vault_path}")

    def stop(self):
        self._handler.cancel_pending()
        self._observer.stop()
        self._observer.join()
        log("INFO", "Watchdog stopped")
