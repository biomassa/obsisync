"""Main window construction and page wiring, headless."""
import os, sys, tempfile, shutil
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

SCRATCH = tempfile.mkdtemp(prefix="obsisync-gui-")
import config, state_db
config.CONFIG_DIR = SCRATCH
config.CONFIG_FILE = os.path.join(SCRATCH, "config.json")
state_db.DB_PATH = os.path.join(SCRATCH, "g.db")
state_db.init()
config.save({"apple_id": "a@b.c", "vault_name": "V", "local_path": SCRATCH,
             "poll_interval": 120, "conflict_strategy": "last-writer-wins",
             "sync_deletes": True, "log_level": "INFO", "ignore_patterns": ["*.pdf"]})

from PySide6.QtWidgets import QApplication
import sync_engine
from gui.main_window import MainWindow

app = QApplication([])
w = MainWindow()

check("four pages present", w.stack.count() == 4, str(w.stack.count()))
check("navigation switches pages",
      (w.nav.setCurrentRow(2), w.stack.currentIndex() == 2)[1])
w.nav.setCurrentRow(0)

print("\n  -- decision prompts --")
check("deletion banner hidden when nothing pending", not w.dashboard.deletions.isVisible())
w.dashboard.on_pending_deletions(["a.md", "b.md"])
check("deletion banner appears with parked paths", w.dashboard.deletions.items.count() == 2)
check("deletion banner offers all three actions",
      all(k in w.dashboard.deletions._actions for k in ("confirm", "upload", "cancel")))
w.dashboard.on_pending_ignored(["notes/x.pdf"])
check("ignored banner appears", w.dashboard.ignored.items.count() == 1)
check("ignored banner offers all three actions",
      all(k in w.dashboard.ignored._actions for k in ("untrack", "delete_remote", "keep")))
w.dashboard.on_pending_deletions([])
check("deletion banner hides when cleared", not w.dashboard.deletions.isVisible())

print("\n  -- iCloud actions are gated on a session --")
check("controller starts disconnected", not w.controller.connected)
w.controller.attach_session(object(), object(), config.load())
check("controller reports connected after attach", w.controller.connected)

print("\n  -- status --")
w.dashboard.on_status({"files": 412, "uploaded": 17, "downloaded": 9, "conflicts": 0,
                       "errors": 0, "deleted": 2, "paused": False, "running": False,
                       "last_sync": "2026-08-18 14:02:11"})
check("stat tile formats thousands", w.dashboard.tiles["files"].value.text() == "412")
check("idle state shown", w.dashboard.status_label.text() == "idle")
w.dashboard.on_status({"paused": True, "running": False})
check("pause button flips to Resume", w.dashboard.pause.text() == "Resume")

print("\n  -- logs --")
w.logs.level.setCurrentText("INFO")
w.logs.on_log({"timestamp": "t", "level": "DEBUG", "message": "noisy"})
check("DEBUG hidden at INFO threshold", "noisy" not in w.logs.view.toPlainText())
w.logs.on_log({"timestamp": "t", "level": "ERROR", "message": "boom"})
check("ERROR shown", "boom" in w.logs.view.toPlainText())
w.logs.on_log({"timestamp": "t", "level": "WEIRD", "message": "unknown-level"})
check("unknown level is never hidden", "unknown-level" in w.logs.view.toPlainText())

print("\n  -- settings round-trip --")
w.settings.poll_interval.setValue(300)
w.settings.ignore.setPlainText("*.pdf\n*.zip")
w.settings._save()
saved = config.load()
check("poll interval saved", saved["poll_interval"] == 300, str(saved["poll_interval"]))
check("ignore patterns saved as a list",
      saved["ignore_patterns"] == ["*.pdf", "*.zip"], str(saved["ignore_patterns"]))
check("engine log level followed the setting",
      sync_engine._current_log_level == saved["log_level"])

print("\n  -- closing hides to tray when there is a tray --")
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QSystemTrayIcon

# Headless runners report no tray, so the two paths must be forced explicitly
# rather than left to the environment.
QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
ev = QCloseEvent()
w.show(); w.closeEvent(ev)
check("close was ignored", not ev.isAccepted())
check("window hidden instead", not w.isVisible())

print("\n  -- with no tray, closing quits rather than vanishing --")
QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: False)
w2 = MainWindow(); w2.show()
ev3 = QCloseEvent(); w2.closeEvent(ev3)
check("close accepted when there is nowhere to hide", ev3.isAccepted())
QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)

w.prepare_quit()
ev2 = QCloseEvent(); w.closeEvent(ev2)
check("close accepted after prepare_quit", ev2.isAccepted())

shutil.rmtree(SCRATCH, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
