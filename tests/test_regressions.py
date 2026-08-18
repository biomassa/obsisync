"""Regressions from the first real-world run."""
import os, sqlite3, sys, tempfile, shutil
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
check("settings are reloaded once the wizard finishes",
      "self.window.settings.load()" in src_app)
check("a watchdog reports a stalled connection",
      "_connect_slow" in src_app and "45000" in src_app)

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
