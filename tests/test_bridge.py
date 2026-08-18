"""Engine -> Qt bridge: signals must cross threads onto the GUI thread."""
import os, sys, tempfile, threading
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

import config, state_db
SCRATCH = tempfile.mkdtemp(prefix="obsisync-bridge-")
config.CONFIG_DIR = SCRATCH
config.CONFIG_FILE = os.path.join(SCRATCH, "config.json")
state_db.DB_PATH = os.path.join(SCRATCH, "b.db")
state_db.init()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QThread
import sync_engine
from gui.bridge import EngineBridge

app = QApplication([])
gui_thread = QThread.currentThread()
bridge = EngineBridge()

seen, seen_threads = [], []
bridge.logReceived.connect(lambda e: (seen.append(e), seen_threads.append(QThread.currentThread())))

status_seen = []
bridge.statusChanged.connect(lambda s: status_seen.append(s))

# emitted from a non-GUI thread, exactly as the engine does
threading.Thread(target=lambda: sync_engine.log("INFO", "from worker thread")).start()

QTimer.singleShot(2600, app.quit)
app.exec()

check("log entry reached the GUI", any(e["message"] == "from worker thread" for e in seen),
      str([e['message'] for e in seen]))
check("slot ran on the GUI thread, not the worker",
      all(t is gui_thread for t in seen_threads), str(seen_threads))
check("status was polled and emitted", len(status_seen) >= 1, str(len(status_seen)))
check("status carries the fields the GUI needs",
      status_seen and all(k in status_seen[0] for k in
          ("paused", "running", "pending_deletions", "pending_ignored", "uploaded")),
      str(sorted(status_seen[0])) if status_seen else "none")

bridge.shutdown()
check("queue unsubscribed on shutdown", bridge._queue not in sync_engine._web_log_listeners)

import shutil; shutil.rmtree(SCRATCH, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
