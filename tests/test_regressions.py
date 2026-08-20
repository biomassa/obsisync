"""Regressions from the first real-world run."""
import inspect, os, sqlite3, sys, tempfile, shutil
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

S = tempfile.mkdtemp(prefix="obsisync-regr-")

print("\n== a WAL-mode database survives the import ==")
# Reproduce iObsi's layout: an old main file plus a WAL holding everything
# recent. Copying only the main file yields "database disk image is malformed".
legacy = os.path.join(S, "legacy"); os.makedirs(legacy)
src = os.path.join(legacy, "sync_state.db")
c = sqlite3.connect(src)
c.execute("pragma journal_mode=WAL")
c.execute("create table file_states (path text primary key)")
c.commit()
c.executemany("insert into file_states values (?)", [(f"n{i}.md",) for i in range(500)])
c.commit()
check("the WAL sidecar exists (as it does for iObsi)", os.path.exists(src + "-wal"))

import migrate
naive = os.path.join(S, "naive.db")
shutil.copy2(src, naive)            # what the old code did
try:
    n = sqlite3.connect(naive).execute("select count(*) from file_states").fetchone()[0]
    naive_ok = (n == 500)
except sqlite3.DatabaseError:
    naive_ok = False
check("a plain file copy loses or corrupts the data (the reported bug)", not naive_ok)

good = os.path.join(S, "good.db")
migrate._copy_database(src, good)
conn = sqlite3.connect(good)
check("the WAL-aware copy passes integrity_check",
      conn.execute("pragma integrity_check").fetchone()[0] == "ok")
check("the WAL-aware copy has every row",
      conn.execute("select count(*) from file_states").fetchone()[0] == 500)
conn.close(); c.close()

print("\n== a corrupt database does not stop the app starting ==")
import state_db
state_db.DB_PATH = os.path.join(S, "corrupt.db")
with open(state_db.DB_PATH, "wb") as f:
    f.write(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 400)
state_db._local.conn = None
try:
    state_db.init(); started = True
except Exception as exc:
    started = False
check("init() recovers instead of raising", started)
check("the unreadable file was kept for inspection",
      any(f.startswith("corrupt.db.corrupt-") for f in os.listdir(S)), str(os.listdir(S)))
state_db.upsert_state("x.md", local_mtime=1, local_hash="h", remote_etag="e",
                      remote_mtime=1, remote_size=1, last_sync_hash="h")
check("the fresh database is usable", len(state_db.all_states()) == 1)

print("\n== settings will not silently point at a missing folder ==")
import config
config.CONFIG_DIR = S; config.CONFIG_FILE = os.path.join(S, "config.json")
state_db.DB_PATH = os.path.join(S, "ok.db"); state_db._local.conn = None; state_db.init()
config.save({"apple_id": "a@b.c", "vault_name": "V", "local_path": S,
             "poll_interval": 120, "conflict_strategy": "last-writer-wins",
             "sync_deletes": True, "log_level": "INFO", "ignore_patterns": []})
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])
from gui.pages import SettingsPage
page = SettingsPage()
page.local_path.setText(os.path.join(S, "does-not-exist"))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)   # user declines
page._save()
check("declining leaves the good path in place",
      config.load()["local_path"] == S, config.load()["local_path"])
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)  # user insists
page._save()
check("confirming still allows it", config.load()["local_path"].endswith("does-not-exist"))

print("\n== the window reloads settings after setup ==")
import inspect
from gui import app as gui_app
src_app = inspect.getsource(gui_app)
# The window is now built after the wizard rather than before it, so its
# settings page reads the post-setup config on construction. The earlier
# explicit reload existed only because the window was built too early.
_run = src_app[src_app.index("def run(self)"):src_app.index("return self.app.exec()")]
check("the window is built after setup, so its settings are current",
      _run.index("SetupDialog()") < _run.index("self._show_window()"))
check("a watchdog reports a stalled connection",
      "_connect_slow" in src_app and "45000" in src_app)

print("\n== secondary text is muted, not disabled ==")
from gui.pages import make_secondary, refresh_secondary, DashboardPage, LogsPage
from PySide6.QtWidgets import QLabel
from PySide6.QtGui import QPalette

lab = QLabel("files"); make_secondary(lab)
check("the label stays enabled (disabled text is unreadable and reads as inactive)",
      lab.isEnabled())

def _contrast(widget):
    p = widget.palette()
    fg = p.color(QPalette.Active, QPalette.WindowText)
    bg = p.color(QPalette.Active, QPalette.Window)
    def lum(c):
        def f(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(c.red()) + 0.7152 * f(c.green()) + 0.0722 * f(c.blue())
    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)

ratio = _contrast(lab)
check(f"contrast meets WCAG AA ({ratio:.2f}:1, needs 4.5)", ratio >= 4.5, f"{ratio:.2f}")
check("colour is derived from the palette, so it follows the system theme",
      "palette" in make_secondary.__doc__.lower())
check("a palette change can be re-applied", callable(refresh_secondary))

print("\n== dashboard close button ==")
class _Ctl:
    def __init__(self): self.window = None; self.connected = False
    def run_local_action(self, *a, **k): pass
    def run_icloud_action(self, *a, **k): pass
ctl = _Ctl()
dash = DashboardPage(ctl)
check("Close window button present", hasattr(dash, "close_window"))
check("its label says what it does", dash.close_window.text() == "Close window")
check("it explains that syncing continues",
      "background" in dash.close_window.toolTip().lower())
closed = []
class _Win:
    def close(self): closed.append(True)
ctl.window = _Win()
dash._close_window()
check("clicking it closes the window", closed == [True])

print("\n== the window is disposable ==")
import gui.app as _app_mod
_src = inspect.getsource(_app_mod)
check("the bridge belongs to the application, not the window",
      "self.bridge = EngineBridge()" in _src)
check("closing destroys the window rather than only hiding it",
      "_destroy_window" in _src and "deleteLater" in _src)
check("destruction is deferred out of the close event",
      "QTimer.singleShot(0, self._destroy_window)" in _src)
check("opening rebuilds a window when there is none",
      "if self.window is None:" in _src)
check("a missing window cannot crash the status bar",
      "_status_message" in _src)
check("a 2FA prompt reopens the window first",
      _src.index("def _on_twofa") < _src.index("dialog = TwoFactorDialog")
      and "self._show_window()" in _src[_src.index("def _on_twofa"):_src.index("def _on_quit")])

from gui.main_window import MainWindow, Controller
from gui.bridge import EngineBridge
shared_bridge = EngineBridge()
shared_ctl = Controller(None)
w3 = MainWindow(bridge=shared_bridge, controller=shared_ctl)
check("the window accepts an injected bridge", w3.bridge is shared_bridge)
check("it does not own an injected bridge", not w3._owns_bridge)
w3.detach()
check("detach leaves the shared bridge running", shared_bridge._thread.is_alive())
w4 = MainWindow(bridge=shared_bridge, controller=shared_ctl)
check("a second window can be built on the same bridge", w4.bridge is shared_bridge)
check("the controller points at the newest window", shared_ctl.window is w4)
shared_bridge.shutdown()

print("\n== a rebuilt window is populated at once ==")
from gui.bridge import EngineBridge as _EB
from gui.main_window import MainWindow as _MW, Controller as _Ctl
import sync_engine as _se2
_se2._save_stats(files=817, uploaded=59, downloaded=55, conflicts=0, errors=2, deleted=28)
_br = _EB(); _c = _Ctl(None)
_w1 = _MW(bridge=_br, controller=_c)
check("the first window shows the stats immediately, not after a poll",
      _w1.dashboard.tiles["files"].value.text() == "817",
      _w1.dashboard.tiles["files"].value.text())
_w1.detach()
_w2 = _MW(bridge=_br, controller=_c)
# The poll only emits on change, and the bridge outlives the window, so without
# an explicit push a reopened window stays blank until something happens.
check("a window rebuilt from the tray is populated too",
      _w2.dashboard.tiles["files"].value.text() == "817",
      _w2.dashboard.tiles["files"].value.text())
check("its daemon state is not stuck on connecting",
      _w2.dashboard.status_label.text() != "connecting…",
      _w2.dashboard.status_label.text())
_br.shutdown()

print("\n== log timestamps agree between memory and database ==")
_se2.log("INFO", "stamp check")
_ring = _se2.get_logs(1)[0]["timestamp"]
_db = [e for e in _se2.get_log_history(20) if e["message"] == "stamp check"][-1]["timestamp"]
check("stored and live entries use the same clock", _ring == _db, f"{_ring} vs {_db}")

print("\n== clear stats ==")
import sync_engine as _se
_se._save_stats(files=817, uploaded=59, downloaded=55, conflicts=3, errors=2, deleted=28)
dash2 = DashboardPage(ctl)
dash2.on_status(_se._load_stats())
check("Clear stats sits before Close window",
      dash2.clear_stats.text() == "Clear stats" and hasattr(dash2, "close_window"))
dash2._clear_stats()
stats = _se._load_stats()
check("counters are zeroed",
      all(stats[k] == 0 for k in _se.COUNTER_KEYS), str(stats))
check("the tracked file count is left alone", stats["files"] == 817, str(stats["files"]))
check("the tiles update immediately",
      dash2.tiles["uploaded"].value.text() == "0")

print("\n== logs persist and can be cleared ==")
import sync_engine
check("history is read from the database, not the in-memory ring",
      "recent_logs" in inspect.getsource(sync_engine.get_log_history))
import gui.main_window as _mw
check("the window primes from persisted history",
      "get_log_history" in inspect.getsource(_mw))
sync_engine.log("INFO", "persisted entry")
check("an entry reaches the database", any(
    e["message"] == "persisted entry" for e in sync_engine.get_log_history(50)))
logs_page = LogsPage()
check("the button clears stored logs, not just the view",
      logs_page.clear_btn.text() == "Clear logs")
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
logs_page._clear_logs()
remaining = [e for e in sync_engine.get_log_history(50)
             if e["message"] == "persisted entry"]
check("clearing removes the persisted entries", not remaining, str(len(remaining)))

print("\n== a black-holed IPv6 route cannot hang the app ==")
# A router can advertise IPv6 and route none of it. requests has no Happy
# Eyeballs, so it blocks on the first (IPv6) address for ever, and the vendored
# client passed no timeout — the connect thread wedged with no way back.
import socket
import urllib3.util.connection as _u3
from icloudlite.ipv4 import force_ipv4, ipv4_forced
from icloudlite.session import DEFAULT_TIMEOUT, PyiCloudSession

default_family = _u3.allowed_gai_family
force_ipv4(True)
check("forcing IPv4 makes urllib3 ask for AF_INET only",
      _u3.allowed_gai_family() == socket.AF_INET)
check("the state is reportable", ipv4_forced())
force_ipv4(False)
check("it can be turned off again for an IPv6-only network",
      _u3.allowed_gai_family() is default_family() or not ipv4_forced())
force_ipv4(True)

connect_timeout, read_timeout = DEFAULT_TIMEOUT
check("a connect timeout is set", 0 < connect_timeout <= 30, str(connect_timeout))
check("a read timeout is set and is longer than the connect timeout",
      read_timeout > connect_timeout, str(read_timeout))

kwargs = {"timeout": None}
PyiCloudSession._apply_default_timeout(kwargs)
check("a request without a timeout is given the default",
      kwargs["timeout"] == DEFAULT_TIMEOUT)
kwargs = {"timeout": 5}
PyiCloudSession._apply_default_timeout(kwargs)
check("a caller's own timeout is left alone", kwargs["timeout"] == 5)

import auth
check("authenticate applies the setting before the first request",
      "_apply_network_preferences" in inspect.getsource(auth.authenticate))
src_order = inspect.getsource(auth.authenticate)
check("it is applied before the service is constructed",
      src_order.index("_apply_network_preferences")
      < src_order.index("PyiCloudService("))
import config
check("the setting has a default and defaults to on",
      config.DEFAULT_CONFIG.get("force_ipv4") is True)

print("\n== the dashboard shows recent activity ==")
dash3 = DashboardPage(ctl)
check("the panel exists", hasattr(dash3, "activity"))
check("it is read-only", dash3.activity.isReadOnly())
check("it keeps ten lines", dash3.activity.maximumBlockCount() == 10)
dash3.on_log({"timestamp": "2026-08-20 10:35:54", "level": "INFO",
              "message": "Uploaded note.md"})
text = dash3.activity.toPlainText()
check("an entry appears", "Uploaded note.md" in text, text)
check("the date is trimmed to the time", "2026-08-20" not in text, text)
dash3.on_log({"timestamp": "2026-08-20 10:35:55", "level": "DEBUG",
              "message": "lifecycle chatter"})
check("DEBUG is left to the Logs page",
      "lifecycle chatter" not in dash3.activity.toPlainText())
for i in range(20):
    dash3.on_log({"timestamp": "2026-08-20 10:36:00", "level": "INFO",
                  "message": f"entry {i}"})
lines = [ln for ln in dash3.activity.toPlainText().splitlines() if ln.strip()]
check("it never grows past ten lines", len(lines) <= 10, str(len(lines)))
check("the oldest entries fall off the top", "Uploaded note.md" not in
      dash3.activity.toPlainText())
dash4 = DashboardPage(ctl)
dash4.prime([{"timestamp": "2026-08-20 10:00:00", "level": "INFO",
              "message": "primed"}])
check("a rebuilt window is primed from history",
      "primed" in dash4.activity.toPlainText())
check("the window primes the dashboard too",
      "self.dashboard.prime" in inspect.getsource(_mw))
check("clearing the logs clears the panel",
      "self.dashboard.activity.clear" in inspect.getsource(_mw))

from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy
from PySide6.QtCore import Qt as _Qt

# An error used to leave its colour as the document's current format, so every
# plain line appended afterwards inherited it and the panel turned wholly red.
dash5 = DashboardPage(ctl)
dash5.on_log({"timestamp": "2026-08-20 11:10:01", "level": "ERROR",
              "message": "Still connecting"})
dash5.on_log({"timestamp": "2026-08-20 11:11:01", "level": "INFO",
              "message": "Connected to iCloud"})

def _colour_of(widget, block_index):
    """The foreground colour actually stored on one line of the panel."""
    block = widget.document().findBlockByNumber(block_index)
    return block.begin().fragment().charFormat().foreground().color().name()

check("an error line is coloured",
      _colour_of(dash5.activity, 0).lower() == "#c0392b")
check("a normal line after an error is not coloured red",
      _colour_of(dash5.activity, 1).lower() != "#c0392b",
      _colour_of(dash5.activity, 1))
dash5.on_log({"timestamp": "2026-08-20 11:12:01", "level": "WARN",
              "message": "stale scan"})
dash5.on_log({"timestamp": "2026-08-20 11:13:01", "level": "INFO",
              "message": "back to normal"})
check("a normal line after a warning is not coloured amber",
      _colour_of(dash5.activity, 3).lower() != "#b26a00",
      _colour_of(dash5.activity, 3))

check("long lines wrap instead of scrolling sideways",
      dash5.activity.lineWrapMode() == QPlainTextEdit.WidgetWidth)
check("there is no horizontal scrollbar",
      dash5.activity.horizontalScrollBarPolicy() == _Qt.ScrollBarAlwaysOff)
check("the panel does not swallow the spare height of the window",
      dash5.activity.parentWidget().sizePolicy().verticalPolicy()
      == QSizePolicy.Maximum)

print("\n== a workspace write no longer eats the watcher's trigger ==")
# The watcher accepted ignore_patterns and never applied them. An ignored file
# still counted as "something changed", so Obsidian rewriting
# .obsidian/workspace.json spent the one trigger per 30s on a cycle with
# nothing to do, and the real note edit that followed was dropped.
import time as _time
import watcher as _watcher
from watchdog.events import FileModifiedEvent, FileMovedEvent
import sync_engine as _se

VAULT = "/home/dingus/obsi"
handler = _watcher.VaultEventHandler(VAULT, ["*.scratch"])

def _fires(event):
    _se._sync_trigger.clear()
    _se._watchdog_suppress_until = 0.0
    handler.on_modified(event) if isinstance(event, FileModifiedEvent) else \
        handler.on_moved(event)
    _time.sleep(_watcher._QUIET_PERIOD_SECONDS + 0.4)
    return _se._sync_trigger.is_set()

handler._last_trigger = 0.0
check("a default-ignored file does not trigger a sync",
      not _fires(FileModifiedEvent(f"{VAULT}/.obsidian/workspace.json")))
check("it does not consume the rate limit either", handler._last_trigger == 0.0)
check("a note edit straight afterwards still triggers",
      _fires(FileModifiedEvent(f"{VAULT}/Daily/note.md")))

handler._last_trigger = 0.0
check("a config-ignored file does not trigger",
      not _fires(FileModifiedEvent(f"{VAULT}/notes/tmp.scratch")))
handler._last_trigger = 0.0
check("a non-ignored .obsidian file still triggers",
      _fires(FileModifiedEvent(f"{VAULT}/.obsidian/appearance.json")))

handler._last_trigger = 0.0
check("moving a note into .trash/ still triggers (it is a deletion)",
      _fires(FileMovedEvent(f"{VAULT}/Daily/note.md", f"{VAULT}/.trash/note.md")))
handler._last_trigger = 0.0
check("a move between two ignored paths does not",
      not _fires(FileMovedEvent(f"{VAULT}/a.scratch", f"{VAULT}/b.scratch")))

handler._last_trigger = 0.0
check("a path outside the vault is not silently ignored",
      _fires(FileModifiedEvent("/somewhere/else/note.md")))

check("both callers pass the configured patterns",
      "ignore_patterns" in open("gui/session.py").read()
      and "ignore_patterns" in open("sync.py").read())

check("the quiet period is long enough to outlast an editor's autosave",
      _watcher._QUIET_PERIOD_SECONDS >= 2.0, str(_watcher._QUIET_PERIOD_SECONDS))
check("a long editing session is still rate limited",
      _watcher._MIN_INTERVAL_SECONDS >= 30, str(_watcher._MIN_INTERVAL_SECONDS))
_se._sync_trigger.clear()

print("\n== an edit during a cycle is deferred, not dropped ==")
# All three guards used to return, discarding the change. A forced remote scan
# takes far longer than the 30s floor, so an edit made while one was running was
# forgotten until the next poll — up to poll_interval later.
handler.cancel_pending()
_quiet, _floor, _retry = (_watcher._QUIET_PERIOD_SECONDS,
                          _watcher._MIN_INTERVAL_SECONDS, _watcher._RETRY_SECONDS)
_watcher._QUIET_PERIOD_SECONDS, _watcher._MIN_INTERVAL_SECONDS = 0.2, 3
_watcher._RETRY_SECONDS = 0.2
try:
    deferred = _watcher.VaultEventHandler(VAULT, [])
    _se._watchdog_suppress_until = 0.0
    _se._sync_trigger.clear()
    _se._sync_running.set()
    deferred.on_modified(FileModifiedEvent(f"{VAULT}/Daily/note.md"))
    _time.sleep(1.0)
    check("nothing fires while a cycle is running", not _se._sync_trigger.is_set())
    _se._sync_running.clear()
    _time.sleep(0.8)
    check("the edit fires once the cycle ends", _se._sync_trigger.is_set())

    _se._sync_trigger.clear()
    deferred._last_trigger = _time.time()
    deferred.on_modified(FileModifiedEvent(f"{VAULT}/Daily/note.md"))
    _time.sleep(1.0)
    check("nothing fires inside the minimum interval",
          not _se._sync_trigger.is_set())
    _time.sleep(2.6)
    check("the edit fires once the interval expires", _se._sync_trigger.is_set())

    _se._sync_trigger.clear()
    deferred._last_trigger = 0.0
    _se._watchdog_suppress_until = _time.time() + 1.0
    deferred.on_modified(FileModifiedEvent(f"{VAULT}/Daily/note.md"))
    _time.sleep(0.6)
    check("nothing fires inside the post-cycle window",
          not _se._sync_trigger.is_set())
    _time.sleep(1.2)
    check("the edit fires once that window expires", _se._sync_trigger.is_set())

    deferred.cancel_pending()
    _se._sync_trigger.clear()
    deferred.on_modified(FileModifiedEvent(f"{VAULT}/Daily/note.md"))
    deferred.cancel_pending()
    _time.sleep(0.6)
    check("a stopped watcher fires nothing", not _se._sync_trigger.is_set())
finally:
    (_watcher._QUIET_PERIOD_SECONDS, _watcher._MIN_INTERVAL_SECONDS,
     _watcher._RETRY_SECONDS) = _quiet, _floor, _retry
    _se._watchdog_suppress_until = 0.0
    _se._sync_running.clear()
    _se._sync_trigger.clear()

import inspect as _inspect
check("the watcher cancels its timer when it stops",
      "cancel_pending" in _inspect.getsource(_watcher.VaultWatcher.stop))

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
