"""How often the remote tree is actually re-fetched.

A note added on another device only appears after a forced refresh, so the cache
must expire within one poll interval. It used to expire in "more than 120
seconds" while the daemon woke every 120, and a strict comparison then failed on
every other cycle.
"""
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

S = tempfile.mkdtemp(prefix="obsisync-cadence-")
import config, state_db
config.CONFIG_DIR = S; config.CONFIG_FILE = os.path.join(S, "c.json")
state_db.DB_PATH = os.path.join(S, "c.db"); state_db.init()

import scanner

class FakeNode:
    """Records every forced re-fetch."""
    def __init__(self): self._children = []; self.forced = 0
    def get_children(self, force=False):
        if force:
            self.forced += 1
        return self._children


def run_cycles(poll, cycles, jitter=0.0):
    """Drive scan_remote as the daemon does: one call per poll interval."""
    scanner.set_cache_age(poll)
    scanner.last_force_refresh = 0.0
    scanner._force_next = False
    node = FakeNode()
    clock = [0.0]
    real_time = scanner.time.time
    scanner.time.time = lambda: clock[0]
    refreshed = []
    try:
        for i in range(1, cycles + 1):
            clock[0] = i * poll - jitter
            before = node.forced
            scanner.scan_remote(node)          # force=False, as a timer cycle does
            refreshed.append(node.forced > before)
    finally:
        scanner.time.time = real_time
    return refreshed

print("\n== every timer cycle re-fetches the remote ==")
r = run_cycles(120, 8)
check("all 8 cycles refreshed", all(r), f"pattern: {['Y' if x else 'n' for x in r]}")
# Before the fix this alternated Y/n, so a remote change waited up to 240s.
check("no cycle uses a stale cache", r.count(False) == 0,
      f"{r.count(False)} cycles skipped")

print("\n== a wake that lands slightly early still counts ==")
r = run_cycles(120, 6, jitter=0.4)
check("scheduling jitter does not skip a refresh", all(r),
      f"pattern: {['Y' if x else 'n' for x in r]}")

print("\n== the cache age follows the configured interval ==")
for poll in (30, 60, 300):
    r = run_cycles(poll, 5)
    check(f"poll_interval={poll}s refreshes every cycle", all(r),
          f"pattern: {['Y' if x else 'n' for x in r]}")

print("\n== rapid cycles are still rate-limited ==")
# Watchdog bursts must not hammer iCloud: a cycle well inside the interval is
# served from cache.
scanner.set_cache_age(120)
scanner.last_force_refresh = 0.0
node = FakeNode()
clock = [0.0]
real_time = scanner.time.time
scanner.time.time = lambda: clock[0]
try:
    clock[0] = 120.0
    scanner.scan_remote(node)                  # due; refreshes
    first = node.forced
    clock[0] = 130.0                           # only 10s later
    scanner.scan_remote(node)
    second = node.forced
finally:
    scanner.time.time = real_time
check("a cycle 10s later is served from cache", second == first, f"{first} -> {second}")

print("\n== explicit requests are never held back ==")
scanner.last_force_refresh = 0.0
node = FakeNode()
clock = [0.0]
real_time = scanner.time.time
scanner.time.time = lambda: clock[0]
try:
    clock[0] = 5.0
    scanner.scan_remote(node, force=True)      # the Sync now button
    forced_now = node.forced
    clock[0] = 6.0
    scanner.invalidate_remote_cache()
    scanner.scan_remote(node)                  # after an upload
    invalidated = node.forced
finally:
    scanner.time.time = real_time
check("force=True refreshes immediately", forced_now == 1, str(forced_now))
check("invalidate_remote_cache refreshes immediately", invalidated == 2, str(invalidated))

print("\n== the engine ties the cache to poll_interval ==")
import inspect, sync_engine
check("daemon_loop sets the cache age",
      "set_cache_age" in inspect.getsource(sync_engine.daemon_loop))

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
