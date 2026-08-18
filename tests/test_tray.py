"""Tray state, notification de-duplication, and autostart."""
import os, sys, tempfile, shutil
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

S = tempfile.mkdtemp(prefix="obsisync-tray-")
import config, state_db
config.CONFIG_DIR = S; config.CONFIG_FILE = os.path.join(S, "config.json")
state_db.DB_PATH = os.path.join(S, "t.db"); state_db.init()

from PySide6.QtWidgets import QApplication
app = QApplication([])
from gui.tray import Tray, _make_icon
import sync_engine

print("\n== icon ==")
check("icon renders for every state",
      all(not _make_icon(s).isNull()
          for s in ("idle", "syncing", "paused", "attention", "offline")))
check("unknown state still yields an icon", not _make_icon("nonsense").isNull())

tray = Tray()

print("\n== state reflects what the engine is doing ==")
tray.on_status({"paused": False, "running": False, "pending_deletions": 0, "pending_ignored": 0})
check("idle when nothing happening", tray._state == "idle", str(tray._state))
tray.on_status({"paused": False, "running": True, "pending_deletions": 0, "pending_ignored": 0})
check("syncing while a cycle runs", tray._state == "syncing", str(tray._state))
tray.on_status({"paused": True, "running": False, "pending_deletions": 0, "pending_ignored": 0})
check("paused shown", tray._state == "paused", str(tray._state))
tray.on_status({"paused": True, "running": False, "pending_deletions": 12, "pending_ignored": 0})
check("attention outranks paused when a decision is waiting",
      tray._state == "attention", str(tray._state))

print("\n== pause checkbox follows engine-side pauses ==")
# The bulk-deletion guard pauses the engine itself; the menu must reflect that
# without bouncing back through pause()/resume().
sync_engine._sync_paused.clear()
tray.on_status({"paused": True, "running": False, "pending_deletions": 0, "pending_ignored": 0})
check("menu shows paused", tray.pause_action.isChecked())
check("reflecting it did not call resume/pause on the engine",
      not sync_engine.is_paused(), "engine state was mutated by a UI refresh")

print("\n== notifications are de-duplicated ==")
sent = []
tray.icon.showMessage = lambda *a, **k: sent.append(a[0])
tray.icon.isVisible = lambda: True

tray.on_pending_ignored(["a.pdf"])
tray.on_pending_ignored(["a.pdf"])
tray.on_pending_ignored(["a.pdf", "b.pdf"])
check("repeated pending state notifies only once", len(sent) == 1, f"{len(sent)} notifications")

tray.on_pending_ignored([])          # resolved
tray.on_pending_ignored(["c.pdf"])   # happens again
check("notifies again after the condition clears", len(sent) == 2, f"{len(sent)}")

tray.on_pending_deletions(["x.md"])
check("deletions notify independently of ignores", len(sent) == 3, f"{len(sent)}")

print("\n== autostart ==")
from gui import autostart
check("supported on this platform", autostart.supported())
check("command is quoted and runnable",
      autostart._executable_command().startswith('"'))
check("running from source launches the module, not a bare .py",
      "-m gui.app" in autostart._executable_command() or getattr(sys, "frozen", False))

if sys.platform == "win32":
    # Enabling here would write to the real HKCU Run key of the machine running
    # the tests, so only the platform-independent parts are exercised.
    print("  (skipping OS writes on Windows: they would touch the real registry)")
else:
    os.environ["XDG_CONFIG_HOME"] = S     # keep the real autostart dir untouched
    was = autostart.is_enabled()
    autostart.set_enabled(True)
    check("enabling writes a desktop entry", autostart.is_enabled())
    entry = open(os.path.join(S, "autostart", "obsisync.desktop")).read()
    check("entry is a valid desktop file", entry.startswith("[Desktop Entry]"))
    check("entry has an Exec line", "Exec=" in entry)
    check("entry does not try to exec a .py directly",
          ".py\n" not in entry.split("Exec=")[1].split("\n")[0] + "\n"
          or "-m gui.app" in entry)
    autostart.set_enabled(False)
    check("disabling removes it", not autostart.is_enabled())
    check("disabling twice is safe", (autostart.set_enabled(False), True)[1])

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
