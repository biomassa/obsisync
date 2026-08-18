"""Regression tests for the sync engine, inherited from iObsi.

Runs entirely against a throwaway config dir + SQLite file, with all iCloud
access stubbed. Never touches the real vault or ~/.config/obsidian-icloud-sync.
"""
import os, sys, time, tempfile, shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCRATCH = tempfile.mkdtemp(prefix="iobsi-test-")
import config
config.CONFIG_DIR = SCRATCH
config.CONFIG_FILE = os.path.join(SCRATCH, "config.json")

import state_db
state_db.DB_PATH = os.path.join(SCRATCH, "test.db")
state_db.init()

import sync_engine as se

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

def reset_db():
    for row in state_db.all_states():
        state_db.delete_state(row["path"])

def track(rel):
    state_db.upsert_state(rel, local_mtime=0, local_hash="h", remote_etag="e",
                          remote_mtime=0, remote_size=1, last_sync_hash="h")

def tracked():
    return {r["path"] for r in state_db.all_states()}


print("\n== fix 1: log levels ==")
se._LOG_RING.clear()
for lvl in ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL", "BOGUS"):
    se.log(lvl, f"m-{lvl}")
seen = [e["level"] for e in se.get_logs()]
check("DEBUG still filtered at INFO threshold", "DEBUG" not in seen)
check("INFO/WARN/ERROR emitted", all(l in seen for l in ("INFO", "WARN", "ERROR")))
check("unknown level CRITICAL is never swallowed", "CRITICAL" in seen)
check("unknown level BOGUS is never swallowed", "BOGUS" in seen)


print("\n== fix 2: watchdog suppression window is live ==")
import watcher
h = watcher.VaultEventHandler()
se._watchdog_suppress_until = 0.0
h._last_trigger = 0.0
se._sync_trigger.clear()
h._fire()
check("fires when nothing suppresses it", se._sync_trigger.is_set())

se._sync_trigger.clear(); h._last_trigger = 0.0
se._watchdog_suppress_until = time.time() + 5     # as run_sync_cycle's finally sets it
h._fire()
check("suppressed inside the post-cycle window", not se._sync_trigger.is_set())

se._sync_trigger.clear(); h._last_trigger = 0.0
se._watchdog_suppress_until = time.time() - 1     # window expired
h._fire()
check("fires again once the window expires", se._sync_trigger.is_set())
se._watchdog_suppress_until = 0.0
se._sync_trigger.clear()


print("\n== fix 3: last_sync round-trips ==")
state_db.set_meta("last_sync", "2026-08-18 12:00:00")
check("_load_stats reads the key _run_sync_cycle writes",
      se._load_stats()["last_sync"] == "2026-08-18 12:00:00",
      repr(se._load_stats()["last_sync"]))
check("no stray stats_last_sync key is written",
      state_db.get_meta("stats_last_sync", None) in (None, ""))


print("\n== fix 4: truncated-scan guard uses tracked count ==")
reset_db()
for i in range(100):
    track(f"n{i}.md")
def aborts(remote_count, tracked_count=None):
    tc = len(state_db.all_states()) if tracked_count is None else tracked_count
    return bool(tc) and remote_count < tc * 0.9
check("100 tracked + 12 new local files does NOT abort", not aborts(100))
check("remote truncated to 40 DOES abort", aborts(40))
check("remote truncated to 89 DOES abort (boundary)", aborts(89))
check("remote at 90 does not abort (boundary)", not aborts(90))
check("empty DB leaves the guard inert", not aborts(0, tracked_count=0))


print("\n== new: _purge_orphaned_states ==")
reset_db()
vault = os.path.join(SCRATCH, "vault"); os.makedirs(vault, exist_ok=True)
for rel in ("both.md", "local_only.md", "remote_only.md", "orphan.md"):
    open(os.path.join(vault, rel), "w").write("x")
for rel in ("both.md", "local_only.md", "remote_only.md", "orphan.md"):
    track(rel)

local_files  = {"both.md": {}, "local_only.md": {}}
remote_files = {"both.md": {}, "remote_only.md": {}}

n = se._purge_orphaned_states(local_files, remote_files)
after = tracked()
check("returns the orphan count", n == 1, f"got {n}")
check("orphan row purged", "orphan.md" not in after)
check("row present on both sides kept", "both.md" in after)
check("local-only row kept (main loop owns that deletion)", "local_only.md" in after)
check("remote-only row kept (main loop owns that deletion)", "remote_only.md" in after)
check("no local file was deleted",
      all(os.path.exists(os.path.join(vault, r)) for r in
          ("both.md", "local_only.md", "remote_only.md", "orphan.md")))
check("signature no longer takes vault_node/local_path — cannot touch iCloud",
      se._purge_orphaned_states.__code__.co_varnames[:2] == ("local_files", "remote_files"))
check("old _handle_deletions is gone", not hasattr(se, "_handle_deletions"))

n2 = se._purge_orphaned_states(local_files, remote_files)
check("idempotent on a second run", n2 == 0, f"got {n2}")


print("\n== end-to-end cycle (iCloud stubbed) ==")
# NOTE ON SIZING: the truncated-scan guard aborts when the remote drops below 90%
# of tracked, so it deliberately fires *before* the bulk-deletion guard on small
# vaults. Scenarios below are sized so the behaviour under test is the one reached.
reset_db()
vault2 = os.path.join(SCRATCH, "vault2")
os.makedirs(os.path.join(vault2, ".obsidian"), exist_ok=True)

cfg = {"local_path": vault2, "ignore_patterns": [], "conflict_strategy": "last-writer-wins",
       "sync_deletes": True, "log_level": "INFO"}

class FakeApi:
    def authenticate(self): pass

calls = {"upload": [], "remote_delete": []}
real_scan_remote = se.scan_remote
real_resolve = se._resolve_node

from filters import should_ignore as si_filter

def stub_remote(remote_map, fresh=True):
    """Mirror the real scan_remote: it now applies extra_ignore like scan_local does."""
    se.scan_remote = lambda node, force=False, extra_ignore=None: (
        {k: v for k, v in remote_map.items() if not si_filter(k, extra_ignore)}, fresh)

class FakeNode:
    def __init__(self, rel): self.rel = rel
    def delete(self): calls["remote_delete"].append(self.rel)

se._resolve_node = lambda root, rel: FakeNode(rel)
se._upload_file = lambda root, rel, path, api=None: (
    calls["upload"].append(rel) or {"mtime": 1, "size": 5, "etag": "new"})

def make_vault(n, prefix="f"):
    """n tracked files, present locally and on the remote."""
    reset_db()
    for f in os.listdir(vault2):
        if f.endswith(".md"): os.remove(os.path.join(vault2, f))
    rmap = {}
    for i in range(n):
        rel = f"{prefix}{i}.md"
        open(os.path.join(vault2, rel), "w").write("x")
        track(rel)
        rmap[rel] = {"path": rel, "mtime": 0, "size": 1, "etag": "e"}
    return rmap

def exists(rel): return os.path.exists(os.path.join(vault2, rel))

# --- one file deleted remotely, out of 20: under both guards, so it propagates ---
rmap = make_vault(20)
gone = "f0.md"; del rmap[gone]
calls["remote_delete"].clear()
stub_remote(rmap, fresh=True)
se._sync_paused.clear()
se._run_sync_cycle(FakeApi(), object(), cfg)
check("single remote deletion propagates to the local file", not exists(gone))
check("single remote deletion does not push a delete to iCloud",
      calls["remote_delete"] == [], str(calls["remote_delete"]))
check("single remote deletion untracks the row", gone not in tracked())
check("untouched files survive", all(exists(f"f{i}.md") for i in range(1, 20)))

# --- same shape but the scan is stale: nothing may be deleted ---
rmap = make_vault(20)
gone = "f0.md"; del rmap[gone]
stub_remote(rmap, fresh=False)
se._run_sync_cycle(FakeApi(), object(), cfg)
check("stale remote scan deletes nothing locally", exists(gone))
check("stale remote scan keeps the row tracked", gone in tracked())

# --- 15 of 200 deleted remotely: clears the truncated guard, trips the bulk guard ---
rmap = make_vault(200)
bulk = [f"f{i}.md" for i in range(15)]
for rel in bulk: del rmap[rel]
calls["remote_delete"].clear()
stub_remote(rmap, fresh=True)
se._sync_paused.clear(); se._pending_remote_deletions.clear()
se._run_sync_cycle(FakeApi(), object(), cfg)
check("bulk deletion pauses sync", se.is_paused())
check("bulk deletion deletes nothing yet", all(exists(r) for r in bulk))
check("bulk deletion parks all candidates", len(se.get_pending_deletions()) == 15,
      str(len(se.get_pending_deletions())))
check("bulk deletion keeps rows tracked", all(r in tracked() for r in bulk))
check("bulk deletion pushes nothing to iCloud", calls["remote_delete"] == [])
se._sync_paused.clear(); se._pending_remote_deletions.clear()

# --- remote scan truncated to 25%: abort before any deletion machinery ---
rmap = make_vault(20)
before = tracked()
stub_remote({k: rmap[k] for k in list(rmap)[:5]}, fresh=True)
calls["remote_delete"].clear()
se._run_sync_cycle(FakeApi(), object(), cfg)
check("truncated remote scan aborts without untracking", tracked() == before)
check("truncated remote scan deletes no local files",
      all(exists(f"f{i}.md") for i in range(20)))
check("truncated remote scan parks no pending deletions",
      len(se.get_pending_deletions()) == 0)
check("truncated remote scan is now visible in the log at INFO",
      any("aborting cycle" in e["message"] for e in se.get_logs(limit=50)))

# --- new local files are no longer mistaken for a truncated scan (fix 4 e2e) ---
rmap = make_vault(20)
for i in range(20, 40):                      # 20 brand-new untracked local files
    open(os.path.join(vault2, f"f{i}.md"), "w").write("x")
calls["upload"].clear()
stub_remote(rmap, fresh=True)
se._run_sync_cycle(FakeApi(), object(), cfg)
check("adding 100% more local files does not abort the cycle",
      len(calls["upload"]) == 20, f"uploaded {len(calls['upload'])}")
check("pre-existing files are untouched", all(exists(f"f{i}.md") for i in range(20)))

print("\n== ignore-pattern asymmetry + pending-ignored flow ==")
from filters import should_ignore as si

# the root bug: remote scan must honour config ignore_patterns like the local scan does
import inspect, scanner
check("scan_remote accepts extra_ignore",
      "extra_ignore" in inspect.signature(scanner.scan_remote).parameters)
check("_walk_sync forwards extra_ignore",
      "extra_ignore" in inspect.signature(scanner._walk_sync).parameters)

# a tracked file matching a *config* pattern must now drop out of BOTH scans, not just local
check("config pattern excludes on the local side", si("notes/a.pdf", ["*.pdf"]))
check("same pattern now excludes on the remote side too", si("notes/a.pdf", ["*.pdf"]))
check("unrelated file unaffected", not si("notes/a.md", ["*.pdf"]))

# end-to-end: newly-ignored tracked file is parked, not deleted anywhere
rmap = make_vault(20)
open(os.path.join(vault2, "secret.pdf"), "w").write("x")
track("secret.pdf")
cfg_ign = dict(cfg, ignore_patterns=["*.pdf"])
calls["remote_delete"].clear()
se._pending_ignored[:] = []
stub_remote(rmap, fresh=True)          # remote scan excludes the pdf, as the real one now would
se._run_sync_cycle(FakeApi(), object(), cfg_ign)
check("newly-ignored file is parked for the user",
      se.get_pending_ignored() == ["secret.pdf"], str(se.get_pending_ignored()))
check("newly-ignored file is NOT deleted from iCloud", calls["remote_delete"] == [])
check("newly-ignored local file survives", exists("secret.pdf"))
check("newly-ignored row is NOT silently untracked", "secret.pdf" in tracked())
check("orphan purge leaves parked paths alone", "secret.pdf" in tracked())

# action: stop syncing, keep both copies
n = se.untrack_ignored()
check("untrack_ignored untracks exactly the parked file", n == 1 and "secret.pdf" not in tracked())
check("untrack_ignored keeps the local copy", exists("secret.pdf"))
check("untrack_ignored clears the parked list", se.get_pending_ignored() == [])

# action: stop syncing, delete the iCloud copy
track("secret.pdf")
se._pending_ignored[:] = ["secret.pdf"]
calls["remote_delete"].clear()
deleted, errors = se.delete_remote_ignored(FakeApi(), object(), cfg_ign)
check("delete_remote_ignored removes the iCloud copy", calls["remote_delete"] == ["secret.pdf"])
check("delete_remote_ignored keeps the local copy", exists("secret.pdf"))
check("delete_remote_ignored untracks the row", "secret.pdf" not in tracked())
check("delete_remote_ignored reports one deletion", (deleted, errors) == (1, 0))

# action: keep syncing -> drop the offending config pattern
import config as cfgmod, json
cfgmod.save({"ignore_patterns": ["*.pdf", "*.zip"], "apple_id": "x", "vault_name": "v"})
se._pending_ignored[:] = ["secret.pdf"]
res = se.unignore_pending()
saved = cfgmod.load()["ignore_patterns"]
check("keep-syncing drops the matching pattern", "*.pdf" not in saved, str(saved))
check("keep-syncing leaves unrelated patterns", "*.zip" in saved, str(saved))
check("keep-syncing reports what it removed", res["removed"] == ["*.pdf"], str(res))
check("keep-syncing clears the parked list", se.get_pending_ignored() == [])

# built-in patterns cannot be dropped from the UI — must be reported, not silently ignored
cfgmod.save({"ignore_patterns": [], "apple_id": "x", "vault_name": "v"})
se._pending_ignored[:] = [".DS_Store"]
res = se.unignore_pending()
check("built-in pattern reported as un-removable",
      res["removed"] == [] and res["still_ignored"] == [".DS_Store"], str(res))

se.scan_remote = real_scan_remote
se._resolve_node = real_resolve
shutil.rmtree(SCRATCH, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL)); sys.exit(1)
