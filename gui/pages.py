"""The four pages of the main window.

Every page is a plain QWidget driven by signals from ``gui.bridge.EngineBridge``.
No page reaches into the engine's internals for live data; it either receives a
signal or calls one of the engine's public functions in response to a click.

Actions that talk to iCloud need an authenticated session, which lives on the
controller. When there is none, those controls are disabled rather than hidden,
so the UI does not reshuffle when a session expires.
"""
import os
import weakref

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPalette, QTextCharFormat, QTextCursor,
)
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

# How many lines the dashboard's activity panel keeps. It answers "what is it
# doing right now" at a glance; the Logs page is where history belongs.
_ACTIVITY_LINES = 10

# Only the levels that mean something went wrong get a colour. Colouring INFO
# too would make the panel loud and leave nothing to stand out.
_ACTIVITY_COLOURS = {"WARN": "#b26a00", "ERROR": "#c0392b"}

# How far a secondary label is blended toward the background. 0 is full contrast,
# 1 is invisible; 0.4 stays comfortably readable in both light and dark themes.
_SECONDARY_BLEND = 0.4


_SECONDARY_WIDGETS = weakref.WeakSet()


def refresh_secondary():
    """Recompute muted colours after the system palette changes."""
    for widget in list(_SECONDARY_WIDGETS):
        try:
            widget.setPalette(widget.parentWidget().palette()
                              if widget.parentWidget() else QPalette())
            _apply_secondary(widget)
        except RuntimeError:
            pass          # widget already destroyed


def make_secondary(widget, blend=_SECONDARY_BLEND):
    """De-emphasise a label without disabling it.

    setEnabled(False) is the obvious way to grey text out and the wrong one: it
    means "this control is unavailable", so Qt renders it at minimal contrast
    and assistive tools report it as inactive. Blending the theme's own text
    colour toward its background keeps the text legible, keeps it enabled, and
    follows whatever palette the system supplies — light or dark.
    """
    _SECONDARY_WIDGETS.add(widget)
    return _apply_secondary(widget, blend)


def _apply_secondary(widget, blend=_SECONDARY_BLEND):
    palette = widget.palette()
    fg = palette.color(QPalette.Active, QPalette.WindowText)
    bg = palette.color(QPalette.Active, QPalette.Window)
    muted = QColor(
        round(fg.red() * (1 - blend) + bg.red() * blend),
        round(fg.green() * (1 - blend) + bg.green() * blend),
        round(fg.blue() * (1 - blend) + bg.blue() * blend),
    )
    for role in (QPalette.WindowText, QPalette.Text):
        palette.setColor(QPalette.Active, role, muted)
        palette.setColor(QPalette.Inactive, role, muted)
    widget.setPalette(palette)
    return widget


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
        make_secondary(cap)
        self.caption = cap

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
        make_secondary(self.last_sync)
        self.sync_now = QPushButton("Sync now")
        self.pause = QPushButton("Pause")
        self.clear_stats = QPushButton("Clear stats")
        self.clear_stats.setToolTip(
            "Reset the uploaded, downloaded, conflicts, errors and deleted "
            "totals. The tracked file count is not affected.")
        self.close_window = QPushButton("Close window")
        self.close_window.setToolTip(
            "Hide the window. Syncing carries on in the background — "
            "quit from the tray icon to stop it.")
        row.addWidget(self.status_label)
        row.addWidget(self.last_sync)
        row.addStretch()
        row.addWidget(self.sync_now)
        row.addWidget(self.pause)
        row.addWidget(self.clear_stats)
        row.addWidget(self.close_window)
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

        from paths import active_profile
        profile = active_profile()
        if profile:
            self.profile_notice = AlertBanner(
                "Running against a test profile",
                "Config and sync state are under the directory below, not the "
                "usual locations. Do not point this at a vault another instance "
                "is already syncing.",
                [],
            )
            self.profile_notice.set_paths([profile])
            layout.addWidget(self.profile_notice)

        self.first_run = AlertBanner(
            "First sync — nothing tracked yet",
            "Files exist both here and on iCloud, but nothing is tracked, so "
            "obsisync cannot tell which copy to keep. Sync is paused until you choose.",
            [("adopt", "They already match — start tracking"),
             ("prefer-remote", "Trust iCloud"),
             ("prefer-local", "Trust this computer")],
        )
        layout.addWidget(self.first_run)

        self.ignored = AlertBanner(
            "Tracked files now match an ignore pattern",
            "These files were being synced but are now excluded by your ignore patterns. "
            "Nothing has been changed yet.",
            [("untrack", "Stop syncing, keep both copies"),
             ("delete_remote", "Stop syncing, delete from iCloud"),
             ("keep", "Keep syncing (drop the pattern)")],
        )
        layout.addWidget(self.ignored)

        activity = QGroupBox("recent activity")
        # Without this the box takes every spare pixel in the window and the
        # text sits marooned in the middle of it.
        activity.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(6, 6, 6, 6)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        # One block per line, so the cap is exactly the number of visible lines
        # and old entries fall off the top without any bookkeeping here.
        self.activity.setMaximumBlockCount(_ACTIVITY_LINES)
        self.activity.setFont(QFont("monospace"))
        self.activity.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.activity.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.activity.setFrameShape(QFrame.NoFrame)
        self.activity.setPlaceholderText("waiting for the first sync…")
        # Size to the line count instead of stretching: the panel sits under the
        # alert banners and must not push them off a short window.
        # Ten rows tall. A long message wraps onto a second row and so shows
        # fewer than ten entries at once; the view stays pinned to the newest
        # line, and the Logs page holds everything.
        rows = QFontMetrics(self.activity.font()).lineSpacing() * _ACTIVITY_LINES
        self.activity.setFixedHeight(rows + 12)
        activity_layout.addWidget(self.activity)
        layout.addWidget(activity)

        layout.addStretch()

        self.sync_now.clicked.connect(sync_engine.trigger_sync)
        self.pause.clicked.connect(self._toggle_pause)
        self.clear_stats.clicked.connect(self._clear_stats)
        self.close_window.clicked.connect(self._close_window)
        self._wire_alerts()

    def _clear_stats(self):
        sync_engine.reset_counters()
        for key in sync_engine.COUNTER_KEYS:
            if key in self.tiles:
                self.tiles[key].set_value(0)

    def _close_window(self):
        window = getattr(self._controller, "window", None)
        if window is not None:
            window.close()          # hides to tray; see MainWindow.closeEvent

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

        for mode in ("adopt", "prefer-remote", "prefer-local"):
            self.first_run.button(mode).clicked.connect(
                lambda _=False, m=mode: self._resolve_first_run(m))

    def _resolve_first_run(self, mode):
        if mode != "adopt":
            side = "iCloud" if mode == "prefer-remote" else "this computer"
            other = "local" if mode == "prefer-remote" else "iCloud"
            if QMessageBox.question(
                    self, "Replace differing files?",
                    f"Files that differ will be replaced with the copy from {side}, "
                    f"overwriting the {other} version. Continue?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self._controller.run_icloud_action(
            lambda api, node, cfg: sync_engine.reconcile_first_run(api, node, cfg, mode),
            self.first_run)

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

    def on_log(self, entry):
        """Append one line. DEBUG is dropped: this panel is a summary, not a log."""
        if entry.get("level") == "DEBUG":
            return
        level = entry.get("level", "")
        stamp = (entry.get("timestamp") or "")[-8:]        # time, not the date
        line = f"{stamp}  {level:<5}  {entry.get('message', '')}"
        self._append_activity(line, _ACTIVITY_COLOURS.get(level))

    def _append_activity(self, line, colour=None):
        """Append one line, coloured only when the level asks for it.

        The format is applied per insertion rather than through appendHtml.
        HTML leaves its colour as the document's current character format, so
        every plain line appended afterwards inherits it and the whole panel
        turns red after the first error.

        Leaving the format empty for a normal line is deliberate: the text then
        follows the palette, and stays right when the system theme changes.
        """
        document = self.activity.document()
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.End)
        if not document.isEmpty():
            cursor.insertBlock()
        fmt = QTextCharFormat()
        if colour:
            fmt.setForeground(QColor(colour))
        cursor.insertText(line, fmt)
        bar = self.activity.verticalScrollBar()
        bar.setValue(bar.maximum())

    def prime(self, entries):
        """Fill the panel from stored history so a fresh window is not blank."""
        for entry in entries:
            self.on_log(entry)

    def on_pending_deletions(self, paths):
        self.deletions.set_paths(paths)

    def on_pending_ignored(self, paths):
        self.ignored.set_paths(paths)

    def on_pending_first_run(self, info):
        if not info:
            self.first_run.set_paths([])
            return
        self.first_run.set_paths([
            f"{info.get('both', 0)} file(s) on both sides",
            f"{info.get('local_only', 0)} only here",
            f"{info.get('remote_only', 0)} only on iCloud",
        ])


class LogsPage(QWidget):
    """Append-only log view with a level filter.

    The engine filters by level before a message is ever emitted, so this filter
    is a second, view-only pass — raising it here hides messages already in the
    buffer without touching what the engine records.
    """

    cleared = Signal()

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
        self.clear_btn = QPushButton("Clear logs")
        self.clear_btn.setToolTip("Delete the stored log, not just this view.")
        row.addWidget(self.clear_btn)
        layout.addLayout(row)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(_MAX_LOG_BLOCKS)
        self.view.setFont(QFont("monospace"))
        layout.addWidget(self.view)

        self.clear_btn.clicked.connect(self._clear_logs)

    def _clear_logs(self):
        if QMessageBox.question(
                self, "Clear logs?",
                "Delete the stored log history? This cannot be undone.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if sync_engine.clear_log_history():
            self.view.clear()
            self.cleared.emit()
            sync_engine.log("INFO", "Log cleared")

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
        make_secondary(self.empty)
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

        self.force_ipv4 = QCheckBox("Connect to iCloud over IPv4 only")
        self.force_ipv4.setToolTip(
            "Some routers advertise IPv6 but route none of it, which makes every "
            "connection hang. iCloud is reachable over IPv4 everywhere, so leave "
            "this on unless you are on an IPv6-only network.")
        form.addRow("", self.force_ipv4)

        layout.addLayout(form)

        layout.addWidget(QLabel("Ignore patterns (one per line)"))
        self.ignore = QPlainTextEdit()
        self.ignore.setMaximumHeight(140)
        layout.addWidget(self.ignore)

        note = QLabel(
            "Adding a pattern that matches already-synced files will raise a prompt "
            "on the dashboard rather than deleting anything.")
        note.setWordWrap(True)
        make_secondary(note)
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
        self.force_ipv4.setChecked(bool(cfg.get("force_ipv4", True)))
        self.ignore.setPlainText("\n".join(cfg.get("ignore_patterns", [])))
        from gui import autostart as _autostart
        if _autostart.supported():
            # Read the real OS state, not a config field that could drift from it.
            self.autostart.setChecked(_autostart.is_enabled())

    def _save(self):
        cfg = config.load()
        new_path = self.local_path.text().strip()
        if not new_path:
            QMessageBox.warning(self, "No vault folder", "Choose a vault folder.")
            return
        if not os.path.isdir(new_path):
            # Saving a path that does not exist would make the next sync treat
            # the vault as missing and download the whole thing afresh.
            if QMessageBox.question(
                    self, "Folder does not exist",
                    f"{new_path} does not exist.\n\nIf you save this, the next sync "
                    "will treat the vault as missing and download everything from "
                    "iCloud into it. Save anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        cfg["local_path"] = new_path
        cfg["poll_interval"] = self.poll_interval.value()
        cfg["conflict_strategy"] = self.strategy.currentText()
        cfg["log_level"] = self.log_level.currentText()
        cfg["sync_deletes"] = self.sync_deletes.isChecked()
        cfg["notifications"] = self.notifications.isChecked()
        cfg["force_ipv4"] = self.force_ipv4.isChecked()
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
        # Takes effect on the next connection, which is what the user expects
        # after changing it: no restart, no reconnect.
        from icloudlite.ipv4 import force_ipv4 as _force_ipv4
        _force_ipv4(cfg["force_ipv4"])
        sync_engine.log("INFO", "Settings saved")
        self.saved.emit()
