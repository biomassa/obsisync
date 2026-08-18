import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import sync_engine
from sync_engine import log, _sync_running, _sync_trigger

_DEBOUNCE_SECONDS = 0.3


class VaultEventHandler(FileSystemEventHandler):
    def __init__(self, ignore_patterns=None):
        super().__init__()
        self._timer = None
        self._lock = threading.Lock()
        self._ignore_patterns = ignore_patterns or []
        self._last_trigger = 0.0

    def _debounce_trigger(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._fire)
            self._timer.start()

    def _fire(self):
        if _sync_running.is_set():
            return
        if time.time() < sync_engine._watchdog_suppress_until:
            return
        now = time.time()
        if now - self._last_trigger < 30:
            return
        self._last_trigger = now
        log("DEBUG", "Local change detected — triggering sync")
        _sync_trigger.set()

    def on_modified(self, event):
        if not event.is_directory:
            self._debounce_trigger()

    def on_created(self, event):
        if not event.is_directory:
            self._debounce_trigger()

    def on_deleted(self, event):
        if not event.is_directory:
            self._debounce_trigger()

    def on_moved(self, event):
        if not event.is_directory:
            self._debounce_trigger()


class VaultWatcher:
    def __init__(self, vault_path):
        self._vault_path = vault_path
        self._observer = Observer()
        self._handler = VaultEventHandler()

    def start(self):
        self._observer.schedule(
            self._handler, self._vault_path, recursive=True
        )
        self._observer.start()
        log("INFO", f"Watchdog monitoring: {self._vault_path}")

    def stop(self):
        self._observer.stop()
        self._observer.join()
        log("INFO", "Watchdog stopped")
