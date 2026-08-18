"""Main window: navigation, page wiring, and the controller the pages act through."""
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QStackedWidget, QStatusBar, QWidget,
)

import sync_engine
from gui.bridge import EngineBridge
from gui.pages import ConflictsPage, DashboardPage, LogsPage, SettingsPage

_PAGES = ["Dashboard", "Logs", "Conflicts", "Settings"]


class Controller:
    """Owns the authenticated iCloud session and runs actions off the GUI thread.

    Anything that touches iCloud can block for seconds, so it must never run on
    the GUI thread. Local-only actions are cheap and run inline.
    """

    def __init__(self, window):
        self.window = window
        self.api = None
        self.vault_node = None
        self.cfg = None

    @property
    def connected(self):
        return self.api is not None and self.vault_node is not None

    def attach_session(self, api, vault_node, cfg):
        self.api = api
        self.vault_node = vault_node
        self.cfg = cfg

    def run_local_action(self, fn, banner=None):
        if banner:
            banner.set_busy(True)
        try:
            fn()
        except Exception as exc:
            QMessageBox.warning(self.window, "Action failed", str(exc))
        finally:
            if banner:
                banner.set_busy(False)
        sync_engine.trigger_sync()

    def run_icloud_action(self, fn, banner=None):
        if not self.connected:
            QMessageBox.information(
                self.window, "Not connected",
                "No iCloud session. This action needs one — sign in first.")
            return
        if banner:
            banner.set_busy(True)

        def work():
            try:
                fn(self.api, self.vault_node, self.cfg)
            except Exception as exc:
                sync_engine.log("ERROR", f"Action failed: {exc}")
            finally:
                if banner:
                    # set_busy only flips enabled state; safe enough to queue back.
                    banner.set_busy(False)
                sync_engine.trigger_sync()

        threading.Thread(target=work, name="icloud-action", daemon=True).start()


class MainWindow(QMainWindow):
    """The window. Closing it hides to tray rather than quitting."""

    hidden_to_tray = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("obsisync")
        self.resize(940, 640)
        self._really_quit = False

        self.controller = Controller(self)
        self.bridge = EngineBridge(self)

        central = QWidget()
        layout = QHBoxLayout(central)

        self.nav = QListWidget()
        self.nav.setMaximumWidth(160)
        for name in _PAGES:
            self.nav.addItem(QListWidgetItem(name))
        self.nav.setCurrentRow(0)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(self.controller)
        self.logs = LogsPage()
        self.conflicts = ConflictsPage(self.controller)
        self.settings = SettingsPage()
        for page in (self.dashboard, self.logs, self.conflicts, self.settings):
            self.stack.addWidget(page)
        layout.addWidget(self.stack)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.bridge.logReceived.connect(self.logs.on_log)
        self.bridge.statusChanged.connect(self.dashboard.on_status)
        self.bridge.statusChanged.connect(self._on_status)
        self.bridge.pendingDeletionsChanged.connect(self.dashboard.on_pending_deletions)
        self.bridge.pendingIgnoredChanged.connect(self.dashboard.on_pending_ignored)
        self.bridge.pendingFirstRunChanged.connect(self.dashboard.on_pending_first_run)
        self.settings.saved.connect(self.conflicts.refresh)

        # The ring buffer already holds recent history; show it rather than
        # starting the view empty.
        self.logs.prime(sync_engine.get_logs(limit=200))

    def _on_status(self, status):
        conflicts = status.get("conflicts", 0)
        self.statusBar().showMessage(
            f"{status.get('files', 0):,} files tracked"
            + (f" · {conflicts} conflicts" if conflicts else "")
        )

    def prepare_quit(self):
        """Allow the next close to actually exit."""
        self._really_quit = True

    def closeEvent(self, event):
        # Closing must not stop syncing: this is a background daemon with a window.
        if self._really_quit:
            self.bridge.shutdown()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.hidden_to_tray.emit()
