"""Thread bridge between the sync engine and the Qt GUI.

The engine runs on plain background threads and knows nothing about Qt. Qt in turn
forbids touching a widget from any thread but the GUI one. This module is the only
place the two meet.

Two mechanisms feed the GUI:

* **Log entries** are pushed by the engine into a ``queue.Queue`` registered through
  ``sync_engine.subscribe_logs``. A worker thread blocks on that queue and re-emits
  each entry as a Qt signal. Signals delivered across threads use a queued connection
  by default, so slots always run on the GUI thread.
* **Status** has no push mechanism in the engine, so a timer polls it. Polling is
  fine here: it is a handful of cheap in-memory reads, and the previous web UI did
  exactly the same over its status WebSocket.
"""
import queue
import threading

from PySide6.QtCore import QObject, QTimer, Signal

import sync_engine

# Matches the cadence the old /ws/status WebSocket used.
_STATUS_INTERVAL_MS = 2000


class EngineBridge(QObject):
    """Surfaces engine activity as Qt signals. Create one, on the GUI thread."""

    logReceived = Signal(dict)          # {timestamp, level, message}
    statusChanged = Signal(dict)        # stats + paused + pending counts
    conflictsChanged = Signal(int)      # unresolved conflict count
    pendingDeletionsChanged = Signal(list)
    pendingIgnoredChanged = Signal(list)
    authExpired = Signal(str)           # reason; GUI should prompt for re-auth

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._last = {}

        sync_engine.subscribe_logs(self._queue)
        self._thread = threading.Thread(
            target=self._drain_logs, name="engine-log-bridge", daemon=True
        )
        self._thread.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_status)
        self._timer.start(_STATUS_INTERVAL_MS)

    # ── log pump ────────────────────────────────────

    def _drain_logs(self):
        while not self._stop.is_set():
            try:
                entry = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            # Emitted from a worker thread; Qt marshals this onto the GUI thread.
            self.logReceived.emit(entry)

    # ── status polling ──────────────────────────────

    def _poll_status(self):
        stats = sync_engine._load_stats()
        pending_deletions = sync_engine.get_pending_deletions()
        pending_ignored = sync_engine.get_pending_ignored()

        status = dict(stats)
        status["paused"] = sync_engine.is_paused()
        status["running"] = sync_engine.is_running()
        status["pending_deletions"] = len(pending_deletions)
        status["pending_ignored"] = len(pending_ignored)

        if status != self._last.get("status"):
            self._last["status"] = status
            self.statusChanged.emit(status)

        if pending_deletions != self._last.get("deletions"):
            self._last["deletions"] = pending_deletions
            self.pendingDeletionsChanged.emit(pending_deletions)

        if pending_ignored != self._last.get("ignored"):
            self._last["ignored"] = pending_ignored
            self.pendingIgnoredChanged.emit(pending_ignored)

    # ── lifecycle ───────────────────────────────────

    def shutdown(self):
        """Detach from the engine. Safe to call more than once."""
        self._timer.stop()
        self._stop.set()
        sync_engine.unsubscribe_logs(self._queue)
        self._thread.join(timeout=2)
