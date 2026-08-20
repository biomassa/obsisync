"""Main window: navigation, page wiring, and the controller the pages act through."""
import threading

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QStackedWidget, QStatusBar, QSystemTrayIcon, QWidget,
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
    """The window.

    The window is **disposable**. Closing it destroys the widget tree so the
    memory and the compositor's surface buffers are released; opening it builds
    a fresh one. Hiding alone frees nothing — Qt keeps every widget allocated
    and the compositor keeps the buffers while the surface exists.

    Nothing durable may live here as a result. The engine bridge and the iCloud
    session belong to the application and are passed in, so they survive the
    window. Everything the pages show is rebuilt from the config file and the
    database, so a rebuilt window loses no state.
    """

    hidden_to_tray = Signal()

    def __init__(self, bridge=None, controller=None, parent=None):
        super().__init__(parent)
        from paths import active_profile
        profile = active_profile()
        # Two instances on one vault would fight, and a tester will have both
        # open, so a profile window must never look like the real one.
        self.setWindowTitle(
            f"obsisync — profile: {profile}" if profile else "obsisync")
        self.resize(940, 640)
        self._really_quit = False

        # Owned by the application when supplied; created here only so the
        # window remains usable on its own, which the tests rely on.
        self._owns_bridge = bridge is None
        self.controller = controller if controller is not None else Controller(self)
        self.bridge = bridge if bridge is not None else EngineBridge(self)
        if controller is not None:
            self.controller.window = self

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
        self.bridge.logReceived.connect(self.dashboard.on_log)
        self.bridge.statusChanged.connect(self.dashboard.on_status)
        self.bridge.statusChanged.connect(self._on_status)
        self.bridge.pendingDeletionsChanged.connect(self.dashboard.on_pending_deletions)
        self.bridge.pendingIgnoredChanged.connect(self.dashboard.on_pending_ignored)
        self.bridge.pendingFirstRunChanged.connect(self.dashboard.on_pending_first_run)
        self.settings.saved.connect(self.conflicts.refresh)
        self.logs.cleared.connect(self.dashboard.activity.clear)

        # Read the persisted log, not the in-memory ring: the ring is empty at
        # startup, which made the view look as though nothing had ever happened.
        history = sync_engine.get_log_history(limit=500)
        self.logs.prime(history)
        # The panel keeps the last few lines, so priming it with the tail is
        # enough and avoids re-rendering the whole history into a 10-line box.
        self.dashboard.prime(history[-50:])

        # Fill the tiles now rather than waiting for the next poll, which only
        # fires on a change and would otherwise leave a rebuilt window blank.
        self.bridge.push_current()

    def _on_status(self, status):
        conflicts = status.get("conflicts", 0)
        self.statusBar().showMessage(
            f"{status.get('files', 0):,} files tracked"
            + (f" · {conflicts} conflicts" if conflicts else "")
        )

    def prepare_quit(self):
        """Allow the next close to actually exit."""
        self._really_quit = True

    def changeEvent(self, event):
        # The muted colours are derived from the palette, so they must be
        # recomputed when the system switches between light and dark.
        if event.type() == QEvent.PaletteChange:
            from gui.pages import refresh_secondary
            refresh_secondary()
        super().changeEvent(event)

    def detach(self):
        """Disconnect from the shared bridge before this window is destroyed."""
        for signal in (self.bridge.logReceived, self.bridge.statusChanged,
                       self.bridge.pendingDeletionsChanged,
                       self.bridge.pendingIgnoredChanged,
                       self.bridge.pendingFirstRunChanged):
            try:
                signal.disconnect(self)
            except (RuntimeError, TypeError):
                pass          # nothing was connected to this receiver

    def closeEvent(self, event):
        # Closing must not stop syncing: this is a background daemon with a window.
        if self._really_quit:
            if self._owns_bridge:
                self.bridge.shutdown()
            event.accept()
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            # With no tray there would be no way to get the window back, so
            # closing has to mean quitting rather than vanishing.
            if self._owns_bridge:
                self.bridge.shutdown()
            event.accept()
            QApplication.quit()
            return
        event.ignore()
        self.hide()
        self.hidden_to_tray.emit()
