"""Connecting to iCloud, and keeping the daemon running.

Authentication is slow and blocking, so it never runs on the GUI thread. That
creates a problem: a 2FA code has to come from a dialog, which can only be shown
*on* the GUI thread. ``TwoFactorPrompt`` bridges the two — the worker asks, the
GUI answers, the worker resumes.
"""
import threading

from PySide6.QtCore import QObject, Qt, Signal

import sync_engine
from auth import TwoFactorRequired, authenticate, find_vault_root, get_password
from watcher import VaultWatcher

_CODE_TIMEOUT_SECONDS = 300


class TwoFactorPrompt(QObject):
    """Lets a worker thread ask the GUI thread for a 2FA code and block on it.

    ``codeRequested`` is emitted across threads, so Qt queues it and the slot runs
    on the GUI thread. The worker then waits on an Event until the GUI calls
    ``provide()``.
    """

    codeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._event = threading.Event()
        self._code = None

    def request(self, api=None):
        """Called from a worker thread. Returns the code, or None if cancelled."""
        self._code = None
        self._event.clear()
        self.codeRequested.emit()
        if not self._event.wait(timeout=_CODE_TIMEOUT_SECONDS):
            return None
        return self._code

    def provide(self, code):
        """Called from the GUI thread once the user has entered (or cancelled)."""
        self._code = code or None
        self._event.set()


class SessionManager(QObject):
    """Owns the iCloud session and the daemon threads."""

    connected = Signal(object, object)      # api, vault_node
    failed = Signal(str)
    twoFactorNeeded = Signal()
    daemonStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.prompt = TwoFactorPrompt(self)
        self.prompt.codeRequested.connect(
            self.twoFactorNeeded, Qt.QueuedConnection)
        self.api = None
        self.vault_node = None
        self._watcher = None
        self._daemon_thread = None

    # ── connecting ──────────────────────────────────

    def connect_async(self, cfg):
        """Authenticate and resolve the vault, off the GUI thread."""
        threading.Thread(
            target=self._connect, args=(cfg,), name="icloud-connect", daemon=True
        ).start()

    def _connect(self, cfg):
        try:
            sync_engine.log("DEBUG", f"Authenticating as {cfg.get('apple_id','?')}…")
            api = authenticate(
                cfg["apple_id"],
                get_password(cfg["apple_id"]),
                interactive=False,
                twofa_callback=self.prompt.request,
            )
            sync_engine.log("DEBUG", "Authenticated; locating the vault…")
            vault_node = find_vault_root(api, cfg["vault_name"])
            if vault_node is None:
                self.failed.emit(
                    f"Vault '{cfg['vault_name']}' was not found on iCloud Drive.")
                return
            self.api = api
            self.vault_node = vault_node
            sync_engine.log("INFO", "Connected to iCloud")
            self.connected.emit(api, vault_node)
        except TwoFactorRequired as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            sync_engine.log("ERROR", f"Connection failed: {exc}")
            self.failed.emit(str(exc))

    # ── daemon ──────────────────────────────────────

    def start_daemon(self, cfg):
        """Start the sync loop and the filesystem watcher."""
        import os

        if self._daemon_thread and self._daemon_thread.is_alive():
            return

        self._daemon_thread = threading.Thread(
            target=sync_engine.daemon_loop,
            args=(self.api, self.vault_node, cfg),
            name="sync-daemon",
            daemon=True,
        )
        self._daemon_thread.start()

        local_path = cfg.get("local_path", "")
        if os.path.isdir(local_path):
            self._watcher = VaultWatcher(local_path)
            self._watcher.start()

        self.daemonStarted.emit()

    def shutdown(self):
        """Stop the daemon cooperatively. Safe to call more than once."""
        sync_engine.shutdown()
        if self._watcher:
            try:
                self._watcher.stop()
            except Exception:
                pass
            self._watcher = None
        if self._daemon_thread:
            self._daemon_thread.join(timeout=10)
            self._daemon_thread = None
