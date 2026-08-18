"""Onboarding against a vault that already exists on both sides."""
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

S = tempfile.mkdtemp(prefix="obsisync-firstrun-")
import config, state_db
config.CONFIG_DIR = S; config.CONFIG_FILE = os.path.join(S, "config.json")
state_db.DB_PATH = os.path.join(S, "f.db"); state_db.init()
import sync_engine as se
from filters import should_ignore as si

vault = os.path.join(S, "vault")
os.makedirs(os.path.join(vault, ".obsidian"), exist_ok=True)
cfg = {"local_path": vault, "ignore_patterns": [], "conflict_strategy": "last-writer-wins",
       "sync_deletes": True, "log_level": "INFO"}

class FakeApi:
    def authenticate(self): pass
calls = {"down": [], "up": []}
class FakeNode:
    def __init__(self, rel): self.rel = rel
    def delete(self): pass
se._resolve_node = lambda root, rel: FakeNode(rel)
se._download_file = lambda node, dest: (calls["down"].append(node.rel),
                                        open(dest, "w").write("REMOTE"))
se._upload_file = lambda root, rel, path, api=None: (
    calls["up"].append(rel) or {"mtime": 1, "size": 6, "etag": "new"})

def seed(n=20, differing=()):
    for f in os.listdir(vault):
        if f.endswith(".md"): os.remove(os.path.join(vault, f))
    for r in state_db.all_states(): state_db.delete_state(r["path"])
    rmap = {}
    for i in range(n):
        rel = f"n{i}.md"
        body = "LOCALLOCAL" if rel in differing else "same"
        open(os.path.join(vault, rel), "w").write(body)
        rmap[rel] = {"path": rel, "mtime": 100.0,
                     "size": 4 if rel not in differing else 99, "etag": f"e{i}"}
    se.scan_remote = lambda node, force=False, extra_ignore=None: (
        {k: v for k, v in rmap.items() if not si(k, extra_ignore)}, True)
    return rmap

print("\n== the cycle refuses to guess ==")
seed(20)
se._sync_paused.clear(); se._pending_first_run.clear()
se._run_sync_cycle(FakeApi(), object(), cfg)
check("sync paused instead of resolving by mtime", se.is_paused())
check("nothing was transferred", not calls["down"] and not calls["up"],
      f"down={calls['down']} up={calls['up']}")
check("nothing was tracked yet", len(state_db.all_states()) == 0)
pend = se.get_pending_first_run()
check("the situation is reported to the UI", pend.get("both") == 20, str(pend))

print("\n== adopt: no transfer, files untouched ==")
seed(20); se._sync_paused.set(); calls["down"].clear(); calls["up"].clear()
r = se.reconcile_first_run(FakeApi(), object(), cfg, "adopt")
check("all matching files adopted", r["adopted"] == 20, str(r))
check("adopt transfers nothing", not calls["down"] and not calls["up"])
check("database now populated", len(state_db.all_states()) == 20)
check("sync resumed", not se.is_paused())
check("prompt cleared", se.get_pending_first_run() == {})
check("local content untouched",
      open(os.path.join(vault, "n0.md")).read() == "same")

print("\n== adopt: genuine differences become conflicts, not overwrites ==")
seed(20, differing=("n3.md", "n7.md")); calls["down"].clear(); calls["up"].clear()
r = se.reconcile_first_run(FakeApi(), object(), cfg, "adopt")
check("differing files left as conflicts", r["differing"] == 2, str(r))
check("still nothing transferred", not calls["down"] and not calls["up"])
unresolved = {c["path"] for c in state_db.unresolved_conflicts()}
check("conflicts recorded for the user", {"n3.md", "n7.md"} <= unresolved, str(unresolved))
check("differing local file NOT overwritten",
      open(os.path.join(vault, "n3.md")).read() == "LOCALLOCAL")

print("\n== prefer-remote / prefer-local only touch what differs ==")
seed(20, differing=("n5.md",)); calls["down"].clear(); calls["up"].clear()
se.reconcile_first_run(FakeApi(), object(), cfg, "prefer-remote")
check("prefer-remote downloads only the differing file", calls["down"] == ["n5.md"],
      str(calls["down"]))
check("prefer-remote overwrote it", open(os.path.join(vault, "n5.md")).read() == "REMOTE")

seed(20, differing=("n5.md",)); calls["down"].clear(); calls["up"].clear()
se.reconcile_first_run(FakeApi(), object(), cfg, "prefer-local")
check("prefer-local uploads only the differing file", calls["up"] == ["n5.md"],
      str(calls["up"]))

print("\n== after reconciling, a normal cycle is quiet ==")
seed(20); se.reconcile_first_run(FakeApi(), object(), cfg, "adopt")
calls["down"].clear(); calls["up"].clear()
se._sync_paused.clear()
se._run_sync_cycle(FakeApi(), object(), cfg)
check("second cycle transfers nothing", not calls["down"] and not calls["up"],
      f"down={calls['down']} up={calls['up']}")
check("second cycle does not re-pause", not se.is_paused())

try:
    se.reconcile_first_run(FakeApi(), object(), cfg, "nonsense"); ok = False
except ValueError: ok = True
check("unknown mode rejected", ok)

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
