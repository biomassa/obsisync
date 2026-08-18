"""The four pages of the main window.

Every page is a plain QWidget driven by signals from ``gui.bridge.EngineBridge``.
No page reaches into the engine's internals for live data; it either receives a
signal or calls one of the engine's public functions in response to a click.

Actions that talk to iCloud need an authenticated session, which lives on the
controller. When there is none, those controls are disabled rather than hidden,
so the UI does not reshuffle when a session expires.
"""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import config
import sync_engine
from state_db import unresolved_conflicts

_LOG_LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]
_MAX_LOG_BLOCKS = 2000


class StatTile(QFrame):
    """One number with a caption."""

    def __init__(self, caption, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QVBoxLayout(self)
        layout.setSpacing(2)

        self.value = QLabel("—")
        font = self.value.font()
        font.setPointSize(font.pointSize() + 8)
        font.setWeight(QFont.Bold)
        self.value.setFont(font)
        self.value.setAlignment(Qt.AlignCenter)

        cap = QLabel(caption)
        cap.setAlignment(Qt.AlignCenter)
        cap.setEnabled(False)

        layout.addWidget(self.value)
        layout.addWidget(cap)

    def set_value(self, n):
        self.value.setText(f"{n:,}" if isinstance(n, int) else str(n))


class AlertBanner(QGroupBox):
    """A prompt that needs a decision, with one button per available action.

    Used for the two cases the engine parks rather than resolving on its own:
    a suspicious bulk deletion, and tracked files that started matching an
    ignore pattern.
    """

    def __init__(self, title, explanation, actions, parent=None):
        super().__init__(title, parent)
        self._actions = {}
        # Hug the content: without this the group box expands to fill the page
        # and the explanation floats in the middle of a large empty box.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QVBoxLayout(self)

        self.explanation = QLabel(explanation)
        self.explanation.setWordWrap(True)
        self.explanation.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(self.explanation)

        self.items = QListWidget()
        self.items.setMaximumHeight(96)
        self.items.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(self.items)

        row = QHBoxLayout()
        for key, label in actions:
            btn = QPushButton(label)
            self._actions[key] = btn
            row.addWidget(btn)
        row.addStretch()
        layout.addLayout(row)
        self.hide()

    def button(self, key):
        return self._actions[key]

    def set_paths(self, paths):
        self.items.clear()
        self.items.addItems(paths)
        self.setVisible(bool(paths))

    def set_busy(self, busy):
        for btn in self._actions.values():
            btn.setEnabled(not busy)


class DashboardPage(QWidget):
    """Counters, daemon controls, and the two decision prompts."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        layout = QVBoxLayout(self)

        grid = QGridLayout()
        self.tiles = {}
        for col, key in enumerate(
            ["files", "uploaded", "downloaded", "conflicts", "errors", "deleted"]
        ):
            tile = StatTile(key)
            self.tiles[key] = tile
            grid.addWidget(tile, 0, col)
        layout.addLayout(grid)

        controls = QGroupBox("daemon")
        controls.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        row = QHBoxLayout(controls)
        self.status_label = QLabel("connecting…")
        self.last_sync = QLabel("")
        self.last_sync.setEnabled(False)
        self.sync_now = QPushButton("Sync now")
        self.pause = QPushButton("Pause")
        row.addWidget(self.status_label)
        row.addWidget(self.last_sync)
        row.addStretch()
        row.addWidget(self.sync_now)
        row.addWidget(self.pause)
        layout.addWidget(controls)

        self.deletions = AlertBanner(
            "Bulk deletion needs confirmation",
            "These tracked files vanished from iCloud in one cycle. Sync is paused. "
            "Confirm only if you deleted them deliberately.",
            [("confirm", "Confirm deletions"),
             ("upload", "Re-upload local copies"),
             ("cancel", "Skip this batch")],
        )
        layout.addWidget(self.deletions)

        self.ignored = AlertBanner(
            "Tracked files now match an ignore pattern",
            "These files were being synced but are now excluded by your ignore patterns. "
            "Nothing has been changed yet.",
            [("untrack", "Stop syncing, keep both copies"),
             ("delete_remote", "Stop syncing, delete from iCloud"),
             ("keep", "Keep syncing (drop the pattern)")],
        )
        layout.addWidget(self.ignored)

        layout.addStretch()

        self.sync_now.clicked.connect(sync_engine.trigger_sync)
        self.pause.clicked.connect(self._toggle_pause)
        self._wire_alerts()

    def _toggle_pause(self):
        if sync_engine.is_paused():
            sync_engine.resume()
        else:
            sync_engine.pause()

    def _wire_alerts(self):
        c = self._controller
        self.deletions.button("confirm").clicked.connect(
            lambda: c.run_icloud_action(sync_engine.confirm_pending_deletions, self.deletions))
        self.deletions.button("upload").clicked.connect(
            lambda: c.run_icloud_action(sync_engine.upload_pending_deletions, self.deletions))
        self.deletions.button("cancel").clicked.connect(
            lambda: c.run_local_action(sync_engine.cancel_pending_deletions, self.deletions))

        self.ignored.button("untrack").clicked.connect(
            lambda: c.run_local_action(sync_engine.untrack_ignored, self.ignored))
        self.ignored.button("keep").clicked.connect(
            lambda: c.run_local_action(sync_engine.unignore_pending, self.ignored))
        self.ignored.button("delete_remote").clicked.connect(self._confirm_delete_remote)

    def _confirm_delete_remote(self):
        answer = QMessageBox.question(
            self, "Delete from iCloud?",
            "Remove these files from iCloud Drive? The local copies are kept.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._controller.run_icloud_action(
                sync_engine.delete_remote_ignored, self.ignored)

    # ── signal handlers ─────────────────────────────

    def on_status(self, status):
        for key, tile in self.tiles.items():
            tile.set_value(status.get(key, 0))
        paused = status.get("paused")
        running = status.get("running")
        self.status_label.setText(
            "paused" if paused else ("syncing…" if running else "idle"))
        self.pause.setText("Resume" if paused else "Pause")
        last = status.get("last_sync") or "never"
        self.last_sync.setText(f"last sync: {last}")

    def on_pending_deletions(self, paths):
        self.deletions.set_paths(paths)

    def on_pending_ignored(self, paths):
        self.ignored.set_paths(paths)


class LogsPage(QWidget):
    """Append-only log view with a level filter.

    The engine filters by level before a message is ever emitted, so this filter
    is a second, view-only pass — raising it here hides messages already in the
    buffer without touching what the engine records.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Show level:"))
        self.level = QComboBox()
        self.level.addItems(_LOG_LEVELS)
        self.level.setCurrentText("INFO")
        row.addWidget(self.level)
        row.addStretch()
        self.clear_btn = QPushButton("Clear view")
        row.addWidget(self.clear_btn)
        layout.addLayout(row)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(_MAX_LOG_BLOCKS)
        self.view.setFont(QFont("monospace"))
        layout.addWidget(self.view)

        self.clear_btn.clicked.connect(self.view.clear)

    def on_log(self, entry):
        threshold = _LOG_LEVELS.index(self.level.currentText())
        try:
            level_rank = _LOG_LEVELS.index(entry.get("level", "INFO"))
        except ValueError:
            level_rank = len(_LOG_LEVELS)      # unknown levels are never hidden
        if level_rank < threshold:
            return
        self.view.appendPlainText(
            f"{entry.get('timestamp','')}  [{entry.get('level','')}]  {entry.get('message','')}")

    def prime(self, entries):
        for entry in entries:
            self.on_log(entry)


class ConflictsPage(QWidget):
    """Unresolved conflicts, resolvable per row."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        layout = QVBoxLayout(self)

        self.empty = QLabel("No unresolved conflicts.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setEnabled(False)
        layout.addWidget(self.empty)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["path", "local modified", "remote modified", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        rows = unresolved_conflicts()
        self.table.setRowCount(0)
        self.empty.setVisible(not rows)
        self.table.setVisible(bool(rows))
        for row in rows:
            self._add_row(dict(row))

    def _add_row(self, row):
        import datetime
        r = self.table.rowCount()
        self.table.insertRow(r)

        def stamp(value):
            try:
                return datetime.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                return "—"

        self.table.setItem(r, 0, QTableWidgetItem(row.get("path", "")))
        self.table.setItem(r, 1, QTableWidgetItem(stamp(row.get("local_mtime"))))
        self.table.setItem(r, 2, QTableWidgetItem(stamp(row.get("remote_mtime"))))

        holder = QWidget()
        box = QHBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        for action, label in [("local", "Keep local"), ("remote", "Keep remote"),
                              ("keep-both", "Keep both")]:
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _=False, p=row.get("path", ""), a=action: self._resolve(p, a))
            box.addWidget(btn)
        self.table.setCellWidget(r, 3, holder)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def _resolve(self, path, action):
        from conflict import resolve_manually
        cfg = config.load()
        try:
            resolve_manually(path, action, cfg["local_path"])
            sync_engine.log("INFO", f"Conflict resolved ({action}): {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Could not resolve", str(exc))
        self.refresh()
        sync_engine.trigger_sync()


class SettingsPage(QWidget):
    """Edits the same JSON config the CLI uses."""

    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.vault_row = QHBoxLayout()
        self.local_path = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.vault_row.addWidget(self.local_path)
        self.vault_row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(self.vault_row)
        form.addRow("Local vault", holder)

        self.poll_interval = QSpinBox()
        self.poll_interval.setRange(30, 3600)
        self.poll_interval.setSuffix(" s")
        form.addRow("Poll interval", self.poll_interval)

        self.strategy = QComboBox()
        from conflict import STRATEGIES
        self.strategy.addItems(list(STRATEGIES))
        form.addRow("Conflict strategy", self.strategy)

        self.log_level = QComboBox()
        self.log_level.addItems(_LOG_LEVELS)
        form.addRow("Log level", self.log_level)

        self.sync_deletes = QCheckBox("Propagate deletions between local and iCloud")
        form.addRow("", self.sync_deletes)

        self.autostart = QCheckBox("Start obsisync when I log in")
        from gui import autostart as _autostart
        self.autostart.setEnabled(_autostart.supported())
        if not _autostart.supported():
            self.autostart.setToolTip("Not supported on this platform")
        form.addRow("", self.autostart)

        self.notifications = QCheckBox("Show desktop notifications")
        form.addRow("", self.notifications)

        layout.addLayout(form)

        layout.addWidget(QLabel("Ignore patterns (one per line)"))
        self.ignore = QPlainTextEdit()
        self.ignore.setMaximumHeight(140)
        layout.addWidget(self.ignore)

        note = QLabel(
            "Adding a pattern that matches already-synced files will raise a prompt "
            "on the dashboard rather than deleting anything.")
        note.setWordWrap(True)
        note.setEnabled(False)
        layout.addWidget(note)

        row = QHBoxLayout()
        row.addStretch()
        self.save_btn = QPushButton("Save")
        row.addWidget(self.save_btn)
        layout.addLayout(row)

        self.save_btn.clicked.connect(self._save)
        self.load()

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose your Obsidian vault", self.local_path.text() or os.path.expanduser("~"))
        if chosen:
            self.local_path.setText(chosen)

    def load(self):
        cfg = config.load()
        self.local_path.setText(cfg.get("local_path", ""))
        self.poll_interval.setValue(int(cfg.get("poll_interval", 120)))
        self.strategy.setCurrentText(cfg.get("conflict_strategy", "last-writer-wins"))
        self.log_level.setCurrentText(cfg.get("log_level", "INFO"))
        self.sync_deletes.setChecked(bool(cfg.get("sync_deletes", True)))
        self.notifications.setChecked(bool(cfg.get("notifications", True)))
        self.ignore.setPlainText("\n".join(cfg.get("ignore_patterns", [])))
        from gui import autostart as _autostart
        if _autostart.supported():
            # Read the real OS state, not a config field that could drift from it.
            self.autostart.setChecked(_autostart.is_enabled())

    def _save(self):
        cfg = config.load()
        cfg["local_path"] = self.local_path.text().strip()
        cfg["poll_interval"] = self.poll_interval.value()
        cfg["conflict_strategy"] = self.strategy.currentText()
        cfg["log_level"] = self.log_level.currentText()
        cfg["sync_deletes"] = self.sync_deletes.isChecked()
        cfg["notifications"] = self.notifications.isChecked()
        cfg["ignore_patterns"] = [
            line.strip() for line in self.ignore.toPlainText().splitlines() if line.strip()
        ]
        config.save(cfg)
        from gui import autostart as _autostart
        if _autostart.supported():
            try:
                _autostart.set_enabled(self.autostart.isChecked())
            except Exception as exc:
                QMessageBox.warning(self, "Could not change start-on-login", str(exc))
        sync_engine.set_log_level(cfg["log_level"])
        sync_engine.log("INFO", "Settings saved")
        self.saved.emit()
