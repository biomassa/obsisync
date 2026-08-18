"""Importing an existing iObsi install (shared by the CLI and the wizard)."""
import json, os, sqlite3, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

S = tempfile.mkdtemp(prefix="obsisync-migrate-")
LEGACY = os.path.join(S, "legacy")
os.makedirs(os.path.join(LEGACY, "session"), exist_ok=True)
json.dump({"apple_id": "a@b.c", "vault_name": "V", "local_path": "/tmp/v",
           "web_port": 11111}, open(os.path.join(LEGACY, "config.json"), "w"))
open(os.path.join(LEGACY, "session", "cookie"), "w").write("c")
db = os.path.join(LEGACY, "sync_state.db")
c = sqlite3.connect(db)
c.execute("create table file_states (path text primary key)")
c.executemany("insert into file_states values (?)", [(f"n{i}.md",) for i in range(42)])
c.commit(); c.close()

import paths
paths.LEGACY_CONFIG_DIR = LEGACY
paths.legacy_config_exists = lambda: os.path.isfile(os.path.join(LEGACY, "config.json"))
DST_CFG = os.path.join(S, "cfg"); DST_DATA = os.path.join(S, "data")
paths.config_dir = lambda: DST_CFG
paths.data_dir = lambda: DST_DATA

import migrate
migrate.LEGACY_CONFIG_DIR = LEGACY
migrate.config_dir = lambda: DST_CFG
migrate.data_dir = lambda: DST_DATA
migrate.legacy_config_exists = paths.legacy_config_exists
import keyring
keyring.get_password = lambda s, u: "pw" if s == migrate.LEGACY_KEYRING_SERVICE else None
_saved = {}
import auth; auth.save_password = lambda e, p: _saved.update({e: p})

print("\n== describe ==")
check("detects an iObsi install", migrate.available())
info = migrate.describe()
check("reports the apple id", info["apple_id"] == "a@b.c", str(info))
check("reports the tracked file count", info["tracked"] == 42, str(info))

print("\n== import ==")
r = migrate.import_from_iobsi()
check("config copied", "config.json" in r["copied"])
check("sync database copied", "sync_state.db" in r["copied"])
check("session copied", "session" in r["copied"])
check("password re-keyed under the new service", _saved.get("a@b.c") == "pw", str(_saved))
check("nothing skipped on a clean destination", not r["skipped"], str(r["skipped"]))

cfg = json.load(open(os.path.join(DST_CFG, "config.json")))
check("web_port dropped (no web UI here)", "web_port" not in cfg, str(cfg))
check("vault settings carried over", cfg["vault_name"] == "V")
def _count(path):
    # Close the handle: Windows refuses to delete or replace an open file, so a
    # leaked connection makes the next import fail with PermissionError.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("select count(*) from file_states").fetchone()[0]
    finally:
        conn.close()

n = _count(os.path.join(DST_DATA, "sync_state.db"))
check("database arrived populated, not empty", n == 42, str(n))

print("\n== the source install is untouched ==")
check("iObsi config still there", os.path.isfile(os.path.join(LEGACY, "config.json")))
check("iObsi database still there", os.path.isfile(db))
check("iObsi database still populated", _count(db) == 42)

print("\n== re-import is refused, and skips are reported ==")
try:
    migrate.import_from_iobsi(); refused = False
except FileExistsError:
    refused = True
check("refuses to overwrite an existing config", refused)

os.remove(os.path.join(DST_CFG, "config.json"))
r2 = migrate.import_from_iobsi()
check("existing state reported as skipped, not silently ignored",
      "sync_state.db" in r2["skipped"], str(r2))

print("\n== force replaces an empty database ==")
os.remove(os.path.join(DST_CFG, "config.json"))
open(os.path.join(DST_DATA, "sync_state.db"), "w").write("")   # the stray empty one
r3 = migrate.import_from_iobsi(force=True)
check("force copies the database", "sync_state.db" in r3["copied"], str(r3))
n3 = _count(os.path.join(DST_DATA, "sync_state.db"))
check("forced copy is the populated one", n3 == 42, str(n3))

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
