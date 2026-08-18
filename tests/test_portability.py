"""Cross-platform path handling.

These tests must pass identically on Linux and Windows. The separator logic is
parameterised rather than read from the running platform, so the Windows branch
is genuinely exercised when the suite runs on Linux.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")


print("\n== path keys are canonical POSIX form ==")
import paths

check("windows separators become forward slashes",
      paths.to_key(r"notes\sub\a.md", sep="\\") == "notes/sub/a.md",
      paths.to_key(r"notes\sub\a.md", sep="\\"))
check("posix separators pass through unchanged",
      paths.to_key("notes/sub/a.md", sep="/") == "notes/sub/a.md")
check("single-segment path unchanged",
      paths.to_key("a.md", sep="\\") == "a.md")
check("windows altsep also normalised",
      paths.to_key("notes/sub\\a.md", sep="\\") == "notes/sub/a.md",
      paths.to_key("notes/sub\\a.md", sep="\\"))

# A backslash is a legal character in a POSIX filename. Normalising blindly would
# corrupt such names on Linux, so the separator must come from the platform.
check("backslash in a POSIX filename is preserved",
      paths.to_key("weird\\name.md", sep="/") == "weird\\name.md",
      paths.to_key("weird\\name.md", sep="/"))

check("to_native rebuilds a platform path",
      paths.to_native("/vault", "notes/sub/a.md") ==
      os.path.join("/vault", "notes", "sub", "a.md"))


print("\n== scan_local emits canonical keys ==")
from scanner import scan_local

vault = tempfile.mkdtemp(prefix="obsisync-paths-")
os.makedirs(os.path.join(vault, "notes", "sub"), exist_ok=True)
os.makedirs(os.path.join(vault, "attachments"), exist_ok=True)
for rel in [("top.md",), ("notes", "a.md"), ("notes", "sub", "deep.md"),
            ("attachments", "img.png")]:
    open(os.path.join(vault, *rel), "w").write("x")

local = scan_local(vault)
check("all four files found", len(local) == 4, str(sorted(local)))
check("no key contains a native separator other than '/'",
      all(os.sep == "/" or os.sep not in k for k in local), str(sorted(local)))
check("nested key uses forward slashes",
      "notes/sub/deep.md" in local, str(sorted(local)))
check("every key round-trips through to_native",
      all(os.path.isfile(paths.to_native(vault, k)) for k in local))


print("\n== ignore patterns match canonical keys ==")
from filters import should_ignore

check("built-in .obsidian/workspace* matches a canonical key",
      should_ignore(".obsidian/workspace"))
check("built-in pattern matches the mobile variant",
      should_ignore(".obsidian/workspace-mobile"))
check("config pattern with a slash matches a canonical key",
      should_ignore("notes/secret.pdf", ["notes/*.pdf"]))
check("unrelated nested file is not ignored",
      not should_ignore("notes/a.md"))


print("\n== windows-style scan cannot desync against a posix remote ==")
# Before the fix, scan_local on Windows produced 'notes\\sub\\deep.md' while
# scan_remote produced 'notes/sub/deep.md', so every nested file appeared as both
# local-only and remote-only: duplicate uploads and phantom deletions.
win_local = {paths.to_key(r"notes\sub\deep.md", sep="\\"): {}}
posix_remote = {"notes/sub/deep.md": {}}
check("a windows local key equals the remote key for the same file",
      set(win_local) == set(posix_remote),
      f"{set(win_local)} vs {set(posix_remote)}")
check("no file appears as local-only", not (set(win_local) - set(posix_remote)))
check("no file appears as remote-only", not (set(posix_remote) - set(win_local)))


print("\n== config and state live in platform-appropriate locations ==")
check("config_dir is absolute", os.path.isabs(paths.config_dir()))
check("config_dir is not hardcoded to the author's home",
      "dingus" not in paths.config_dir() or os.path.expanduser("~").endswith("dingus"))
check("data_dir is absolute", os.path.isabs(paths.data_dir()))
# The value legitimately contains this user's home; what matters is that it is
# *derived* from expanduser("~") rather than baked into the source.
check("default vault path is derived from the user's home",
      paths.default_vault_path().startswith(os.path.expanduser("~")),
      paths.default_vault_path())
check("no source file hardcodes an author-specific home path",
      not [f for f in ("paths.py", "config.py", "scanner.py", "sync.py", "sync_engine.py")
           if "/home/dingus" in open(os.path.join(
               os.path.dirname(os.path.dirname(os.path.abspath(__file__))), f)).read()],
      str([f for f in ("paths.py", "config.py", "scanner.py", "sync.py", "sync_engine.py")
           if "/home/dingus" in open(os.path.join(
               os.path.dirname(os.path.dirname(os.path.abspath(__file__))), f)).read()]))

import config
check("config module uses paths.config_dir()",
      os.path.abspath(config.CONFIG_DIR) == os.path.abspath(paths.config_dir()),
      f"{config.CONFIG_DIR} vs {paths.config_dir()}")
check("DEFAULT_CONFIG derives local_path from the user's home",
      config.DEFAULT_CONFIG["local_path"].startswith(os.path.expanduser("~")),
      config.DEFAULT_CONFIG["local_path"])

shutil.rmtree(vault, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
