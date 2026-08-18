"""System tray icon, menu, and desktop notifications.

The icon is drawn rather than loaded from a file so a compiled build has no asset
to lose, and so its colour can carry status: idle, syncing, paused, or needing
attention.
"""
from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

import sync_engine

_COLOURS = {
    "idle":      QColor("#3fb950"),
    "syncing":   QColor("#58a6ff"),
    "paused":    QColor("#d29922"),
    "attention": QColor("#f85149"),
    "offline":   QColor("#8b949e"),
}


def _make_icon(state, size=64):
    """Two arrows, coloured by state — the same motif as the app icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    colour = _COLOURS.get(state, _COLOURS["offline"])
    painter.setBrush(colour)
    painter.setPen(Qt.NoPen)

    s = size / 32.0
    up = QPainterPath()
    up.moveTo(9.5 * s, 12 * s); up.lineTo(12.5 * s, 12 * s)
    up.lineTo(12.5 * s, 25 * s); up.lineTo(9.5 * s, 25 * s); up.closeSubpath()
    up.moveTo(7 * s, 13 * s); up.lineTo(15 * s, 13 * s); up.lineTo(11 * s, 6 * s)
    up.closeSubpath()

    down = QPainterPath()
    down.moveTo(19.5 * s, 7 * s); down.lineTo(22.5 * s, 7 * s)
    down.lineTo(22.5 * s, 20 * s); down.lineTo(19.5 * s, 20 * s); down.closeSubpath()
    down.moveTo(17 * s, 19 * s); down.lineTo(25 * s, 19 * s); down.lineTo(21 * s, 26 * s)
    down.closeSubpath()

    painter.drawPath(up)
    painter.drawPath(down)
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    """Tray presence: status at a glance, quick actions, and notifications."""

    openRequested = Signal()
    quitRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._notified = set()

        self.icon = QSystemTrayIcon(_make_icon("offline"), self)
        self.icon.setToolTip("obsisync")

        menu = QMenu()
        self.open_action = QAction("Open obsisync")
        self.sync_action = QAction("Sync now")
        self.pause_action = QAction("Pause syncing")
        self.pause_action.setCheckable(True)
        self.quit_action = QAction("Quit")

        menu.addAction(self.open_action)
        menu.addSeparator()
        menu.addAction(self.sync_action)
        menu.addAction(self.pause_action)
        menu.addSeparator()
        menu.addAction(self.quit_action)
        self.icon.setContextMenu(menu)
        self._menu = menu                      # keep a reference; Qt will not

        self.open_action.triggered.connect(self.openRequested)
        self.quit_action.triggered.connect(self.quitRequested)
        self.sync_action.triggered.connect(sync_engine.trigger_sync)
        self.pause_action.toggled.connect(self._on_pause_toggled)
        self.icon.activated.connect(self._on_activated)

    def show(self):
        self.icon.show()

    def hide(self):
        self.icon.hide()

    @staticmethod
    def available():
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.openRequested.emit()

    def _on_pause_toggled(self, checked):
        if checked:
            sync_engine.pause()
        else:
            sync_engine.resume()

    # ── status ──────────────────────────────────────

    def set_state(self, state, tooltip=None):
        if state != self._state:
            self._state = state
            self.icon.setIcon(_make_icon(state))
        self.icon.setToolTip(tooltip or f"obsisync — {state}")

    def on_status(self, status):
        needs_attention = status.get("pending_deletions") or status.get("pending_ignored")
        if needs_attention:
            state = "attention"
        elif status.get("paused"):
            state = "paused"
        elif status.get("running"):
            state = "syncing"
        else:
            state = "idle"

        # Reflect engine-side pauses (the bulk-deletion guard sets one itself)
        # without re-triggering pause/resume.
        paused = bool(status.get("paused"))
        if self.pause_action.isChecked() != paused:
            self.pause_action.blockSignals(True)
            self.pause_action.setChecked(paused)
            self.pause_action.blockSignals(False)

        last = status.get("last_sync") or "never"
        self.set_state(state, f"obsisync — {state}\nlast sync: {last}")

    # ── notifications ───────────────────────────────

    def notify(self, title, message, key=None, level=QSystemTrayIcon.Information):
        """Show a desktop notification.

        ``key`` de-duplicates: the sync loop re-reports the same pending state
        every cycle, and notifying each time would be intolerable.
        """
        if key is not None:
            if key in self._notified:
                return
            self._notified.add(key)
        if self.icon.isVisible():
            self.icon.showMessage(title, message, level, 10000)

    def clear_notification(self, key):
        self._notified.discard(key)

    def on_pending_deletions(self, paths):
        if paths:
            self.notify(
                "Bulk deletion needs confirmation",
                f"{len(paths)} tracked files vanished from iCloud. Syncing is paused "
                "until you decide what to do.",
                key="deletions", level=QSystemTrayIcon.Critical)
        else:
            self.clear_notification("deletions")

    def on_pending_ignored(self, paths):
        if paths:
            self.notify(
                "Files now match an ignore pattern",
                f"{len(paths)} synced files are now excluded by your ignore patterns. "
                "Nothing has been changed yet.",
                key="ignored", level=QSystemTrayIcon.Warning)
        else:
            self.clear_notification("ignored")

    def on_auth_expired(self, reason):
        self.notify("iCloud sign-in needed", reason, key="auth",
                    level=QSystemTrayIcon.Critical)
