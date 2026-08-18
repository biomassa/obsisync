"""Setup wizard, 2FA prompt bridging, and session wiring — all offline."""
import os, sys, tempfile, shutil, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

S = tempfile.mkdtemp(prefix="obsisync-auth-")
import config, state_db
config.CONFIG_DIR = S; config.CONFIG_FILE = os.path.join(S, "config.json")
state_db.DB_PATH = os.path.join(S, "a.db"); state_db.init()

import auth

print("\n== authenticate() no longer blocks on stdin ==")
import inspect
check("twofa_callback is part of the signature",
      "twofa_callback" in inspect.signature(auth.authenticate).parameters)
src = inspect.getsource(auth.authenticate)
check("input() only reachable in interactive mode",
      src.index("interactive") < src.index("input("))
check("TwoFactorRequired is distinguishable from other errors",
      issubclass(auth.TwoFactorRequired, RuntimeError))

# A stand-in for pyicloud that demands 2FA, so no network is involved.
class FakeApi:
    def __init__(self, *a, **kw):
        self.requires_2fa = True
        self.is_trusted_session = False
        self.validated = None
        # obsisync asks Apple to deliver a code before validating one; without
        # that handshake Apple rejects every code.
        self.two_factor_delivery_method = "trusted_device"
        self.two_factor_delivery_notice = None
        self.requested = False

    def request_2fa_code(self):
        self.requested = True
        return True
    def validate_2fa_code(self, code):
        self.validated = code
        self.requires_2fa = False
        return code == "123456"
    def trust_session(self): return True

auth.PyiCloudService = lambda *a, **kw: FakeApi()

api = auth.authenticate("a@b.c", "pw", twofa_callback=lambda _api: "123456")
check("callback supplies the code and auth succeeds", api.validated == "123456")

try:
    auth.authenticate("a@b.c", "pw", twofa_callback=lambda _api: None)
    ok = False
except auth.TwoFactorRequired:
    ok = True
check("cancelling the prompt raises TwoFactorRequired", ok)

try:
    auth.authenticate("a@b.c", "pw")
    ok = False
except auth.TwoFactorRequired:
    ok = True
check("no callback and non-interactive raises instead of hanging", ok)

print("\n== 2FA prompt crosses threads without deadlocking ==")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QThread
from gui.session import TwoFactorPrompt

app = QApplication.instance() or QApplication([])
prompt = TwoFactorPrompt()
gui_thread = QThread.currentThread()
seen_on = []
prompt.codeRequested.connect(lambda: seen_on.append(QThread.currentThread()))
prompt.codeRequested.connect(lambda: prompt.provide("654321"))

result = {}
def worker():
    result["code"] = prompt.request()
t = threading.Thread(target=worker); t.start()
deadline = time.time() + 5
while t.is_alive() and time.time() < deadline:
    app.processEvents(); time.sleep(0.01)
t.join(timeout=1)

check("worker received the code from the GUI", result.get("code") == "654321", str(result))
check("prompt slot ran on the GUI thread", seen_on and all(x is gui_thread for x in seen_on))
check("worker thread finished (no deadlock)", not t.is_alive())

print("\n== session manager ==")
from gui.session import SessionManager
sm = SessionManager()
check("starts with no api", sm.api is None)
check("shutdown is safe before any daemon started",
      (sm.shutdown(), True)[1])

print("\n== wizard ==")
import sync_engine
from gui.wizard import (SetupDialog, TwoFactorDialog, PAGE_CREDENTIALS,
                        PAGE_TWOFA, PAGE_VAULT, PAGE_FOLDER, PAGE_FIRST_SYNC)
d = SetupDialog()
check("opens on the credentials page", d.stack.currentIndex() == PAGE_CREDENTIALS)
d._advance()
check("empty credentials are refused", "Apple ID" in d.message.text(), d.message.text())
d._on_twofa_needed(None)
check("2FA page shown when Apple asks for a code", d.stack.currentIndex() == PAGE_TWOFA)
d._advance()
check("empty code is refused", "code" in d.message.text().lower(), d.message.text())
d._on_vaults(["Obsidian", "Obsidian/Work"])
check("vault page lists what was discovered", d.vaults.count() == 2)
d._on_vaults([])
check("empty discovery falls back to manual entry",
      "type the path" in d.message.text(), d.message.text())
d.manual_vault.setText("Obsidian/Obsidian")
d._advance()
check("manual vault path accepted", d.stack.currentIndex() == PAGE_FOLDER)
folder = os.path.join(S, "vault")
d.folder.setText(folder)
d._advance()
saved = config.load()
check("config written on finish",
      saved["vault_name"] == "Obsidian/Obsidian" and saved["local_path"] == folder, str(saved))
check("vault folder created", os.path.isdir(folder))
check("password is not written into the config file",
      "password" not in open(config.CONFIG_FILE).read().lower())

print("\n== first-sync page appears only for a non-empty folder ==")
empty = os.path.join(S, "empty_vault"); os.makedirs(empty, exist_ok=True)
check("empty folder needs no decision", not SetupDialog.folder_has_vault(empty))
full = os.path.join(S, "full_vault"); os.makedirs(full, exist_ok=True)
open(os.path.join(full, "note.md"), "w").write("x")
check("folder with notes needs a decision", SetupDialog.folder_has_vault(full))
# .obsidian/app.json is a real file the engine will try to reconcile, so its
# guard would fire and show the dashboard banner. The wizard must agree, or the
# two disagree about whether a decision is needed.
dot = os.path.join(S, "dot_only"); os.makedirs(os.path.join(dot, ".obsidian"), exist_ok=True)
open(os.path.join(dot, ".obsidian", "app.json"), "w").write("{}")
check("a vault with only .obsidian config still needs a decision",
      SetupDialog.folder_has_vault(dot))
hidden = os.path.join(S, "hidden_only"); os.makedirs(hidden, exist_ok=True)
open(os.path.join(hidden, ".DS_Store"), "w").write("x")
check("a folder of only hidden files counts as empty",
      not SetupDialog.folder_has_vault(hidden))

d2 = SetupDialog()
d2._on_vaults(["V"]); d2.manual_vault.setText("V"); d2._advance()
d2.folder.setText(full); d2._advance()
check("wizard branches to the first-sync page", d2.stack.currentIndex() == PAGE_FIRST_SYNC)
check("adopt is the preselected default", d2.first_sync_mode() == "adopt")
d2.mode_remote.setChecked(True)
check("choosing iCloud is reported", d2.first_sync_mode() == "prefer-remote")
d2.mode_adopt.setChecked(True)
d2._advance()
saved2 = config.load()
check("chosen mode written to config", saved2.get("first_run_mode") == "adopt", str(saved2.get("first_run_mode")))

t2 = TwoFactorDialog()
t2.code.setText("111222")
check("re-auth dialog returns the entered code", t2.value() == "111222")

print("\n== app wiring ==")
from gui.app import is_configured
check("unconfigured detected", not is_configured({"apple_id": "", "vault_name": ""}))
check("configured detected", is_configured({"apple_id": "a@b.c", "vault_name": "V"}))

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
