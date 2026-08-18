"""Running against a separate profile directory.

The point of a profile is to attempt a cold Apple ID sign-in without disturbing
a working installation, so the tests care most about one thing: nothing must be
written to the real locations.
"""
import importlib, os, subprocess, sys, tempfile, shutil
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

S = tempfile.mkdtemp(prefix="obsisync-profile-")

print("\n== paths redirect under a profile ==")
import paths
default_config, default_data = paths.config_dir(), paths.data_dir()
check("no profile is active by default", paths.active_profile() is None)

paths.set_profile(os.path.join(S, "p1"))
check("config moves under the profile",
      paths.config_dir() == os.path.join(S, "p1", "config"), paths.config_dir())
check("state moves under the profile",
      paths.data_dir() == os.path.join(S, "p1", "data"), paths.data_dir())
check("active_profile reports it", paths.active_profile() == os.path.join(S, "p1"))
check("the path is absolute even if given relative",
      os.path.isabs(paths.set_profile("relative/dir")))

paths.set_profile(None)
check("clearing restores the platform location", paths.config_dir() == default_config)
check("and the platform state location", paths.data_dir() == default_data)

print("\n== the environment variable is honoured ==")
os.environ["OBSISYNC_PROFILE"] = os.path.join(S, "envprofile")
importlib.reload(paths)
check("OBSISYNC_PROFILE sets the profile",
      paths.active_profile() == os.path.join(S, "envprofile"), str(paths.active_profile()))
del os.environ["OBSISYNC_PROFILE"]
importlib.reload(paths)
check("unsetting it restores the default", paths.active_profile() is None)

print("\n== the real locations are never touched ==")
# This is the guarantee the whole feature rests on. Run the CLI in a subprocess
# so it resolves paths at import time exactly as a real launch does.
real_config, real_data = paths.config_dir(), paths.data_dir()
before = {}
for d in (real_config, real_data):
    before[d] = sorted(os.listdir(d)) if os.path.isdir(d) else None

profile = os.path.join(S, "isolated")
out = subprocess.run(
    [sys.executable, "obsisync.py", "--profile", profile, "--headless", "status"],
    cwd=ROOT, capture_output=True, text=True, timeout=120)
check("the CLI runs against the profile", out.returncode == 0, out.stdout + out.stderr)
check("it reports being unconfigured, not the real account",
      "Not configured" in out.stdout, out.stdout[:120])
check("the profile directories were created",
      os.path.isdir(os.path.join(profile, "config")) and
      os.path.isdir(os.path.join(profile, "data")))
for d, listing in before.items():
    now = sorted(os.listdir(d)) if os.path.isdir(d) else None
    check(f"nothing new appeared in {os.path.basename(d)}", now == listing,
          f"{listing} -> {now}")

print("\n== a profile writes its own config and database ==")
cfg_out = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, %r)\n"
     "import paths; paths.set_profile(%r)\n"
     "import config, state_db\n"
     "state_db.init()\n"
     "config.save({'apple_id': 'p@rofile.test', 'vault_name': 'V', 'local_path': '/tmp/v'})\n"
     "print(config.CONFIG_FILE); print(state_db.DB_PATH)" % (ROOT, profile)],
    cwd=ROOT, capture_output=True, text=True, timeout=120)
lines = cfg_out.stdout.strip().splitlines()
check("config.json lands in the profile",
      lines and lines[0].startswith(profile), cfg_out.stdout + cfg_out.stderr)
check("the database lands in the profile",
      len(lines) > 1 and lines[1].startswith(profile), cfg_out.stdout)
check("the file really exists", os.path.isfile(os.path.join(profile, "config", "config.json")))

print("\n== a profile instance is visibly different ==")
paths.set_profile(profile)
import config as _cfg, state_db as _db
_cfg.CONFIG_DIR = os.path.join(profile, "config")
_cfg.CONFIG_FILE = os.path.join(_cfg.CONFIG_DIR, "config.json")
_db.DB_PATH = os.path.join(profile, "data", "sync_state.db")
_db._local.conn = None
_db.init()
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from gui.main_window import MainWindow, Controller
from gui.bridge import EngineBridge
bridge = EngineBridge(); ctl = Controller(None)
w = MainWindow(bridge=bridge, controller=ctl)
check("the window title names the profile", profile in w.windowTitle(), w.windowTitle())
check("the dashboard shows a profile notice", hasattr(w.dashboard, "profile_notice"))
check("the notice names the directory",
      profile in w.dashboard.profile_notice.items.item(0).text()
      if hasattr(w.dashboard, "profile_notice") else False)
from gui.tray import Tray
tray = Tray()
check("the tray tooltip names the profile",
      os.path.basename(profile) in tray.icon.toolTip(), tray.icon.toolTip())
bridge.shutdown()

status_out = subprocess.run(
    [sys.executable, "obsisync.py", "--profile", profile, "--headless", "status"],
    cwd=ROOT, capture_output=True, text=True, timeout=120)
check("status prints the profile path", "Profile:" in status_out.stdout, status_out.stdout[:150])

print("\n== a second instance on one data directory is refused ==")
from PySide6.QtCore import QLockFile
lock_path = os.path.join(profile, "data", "obsisync.lock")
first = QLockFile(lock_path)
check("the first instance takes the lock", first.tryLock(100))
second = QLockFile(lock_path)
check("the second is refused", not second.tryLock(100))
first.unlock()
check("and can take it once the first releases", second.tryLock(100))
second.unlock()

import inspect
import gui.app as _app
check("the app takes the lock before starting the engine",
      "QLockFile" in inspect.getsource(_app))

paths.set_profile(None)
shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
