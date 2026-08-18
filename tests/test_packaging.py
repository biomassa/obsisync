"""Entry point and build configuration.

Catches the kind of packaging mistake that only shows up after a 12-minute
compile, or worse, after shipping.
"""
import ast, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

print("\n== entry point ==")
out = subprocess.run([sys.executable, "obsisync.py", "--version"],
                     cwd=ROOT, capture_output=True, text=True)
check("--version works", out.returncode == 0 and "obsisync" in out.stdout, out.stdout + out.stderr)

out = subprocess.run([sys.executable, "obsisync.py", "--headless", "--help"],
                     cwd=ROOT, capture_output=True, text=True)
check("--headless reaches the CLI", "import-from-iobsi" in out.stdout, out.stdout[:200])

entry = open(os.path.join(ROOT, "obsisync.py")).read()
check("freeze_support called (onefile re-executes the bootstrap)",
      "freeze_support" in entry)
check("GUI is imported lazily, so --headless does not load Qt",
      entry.index("--headless") < entry.index("from gui.app import"))

print("\n== build configuration ==")
# Read the FLAGS, not the file text. Comments and docstrings are absent from the
# AST, so prose explaining a deliberately-omitted flag cannot be mistaken for the
# flag being present — a mistake plain substring checks kept making.
_tree = ast.parse(open(os.path.join(ROOT, "build.py")).read())
FLAGS = {n.value for n in ast.walk(_tree)
         if isinstance(n, ast.Constant) and isinstance(n.value, str)
         and n.value.startswith("--")}
# f-string flags (Qt exclusions) appear as JoinedStr; collect their literal parts.
for n in ast.walk(_tree):
    if isinstance(n, ast.JoinedStr):
        lit = "".join(v.value for v in n.values if isinstance(v, ast.Constant))
        if lit.startswith("--"):
            FLAGS.add(lit)
QT_EXCLUDED = set()
for n in ast.walk(_tree):
    if isinstance(n, ast.Assign) and any(
            getattr(t, "id", "") == "_UNUSED_QT" for t in n.targets):
        QT_EXCLUDED = {e.value for e in n.value.elts}

check("keyring backends force-included (resolved dynamically at runtime)",
      "--include-package=keyring.backends" in FLAGS, str(sorted(FLAGS))[:150])
check("vendored client force-included", "--include-package=icloudlite" in FLAGS)
check("QtWebEngine excluded (the single largest Qt component)",
      "PySide6.QtWebEngineCore" in QT_EXCLUDED, str(sorted(QT_EXCLUDED))[:100])
check("QtQuick/QML excluded", "PySide6.QtQml" in QT_EXCLUDED)
check("unittest NOT excluded — the icloud chain hard-imports unittest.mock",
      "--nofollow-import-to=unittest" not in FLAGS)
# -OO strips docstrings and Click builds --help from them, so the compiled CLI
# would document nothing.
check("-O used, not -OO", "--python-flag=-O" in FLAGS)
check("-OO not used anywhere", "--python-flag=-OO" not in FLAGS)
check("windows console disabled for a GUI app",
      "--windows-console-mode=disable" in FLAGS)
check("onefile requested", "--onefile" in FLAGS)
# The default unpacks into tmpfs on most Linux systems, holding the whole
# payload in RAM for the life of the process.
_tempdir = [f for f in FLAGS if f.startswith("--onefile-tempdir-spec")]
check("onefile unpacks to disk, not tmpfs", bool(_tempdir), str(_tempdir))
# Windows %TEMP% is already on disk, and a stable path there breaks a second
# launch: Windows locks loaded .pyd files, so the extraction cannot be rewritten
# while another instance holds them open.
_guarded = False
for _node in ast.walk(_tree):
    if isinstance(_node, ast.If) and "linux" in ast.dump(_node.test):
        if "--onefile-tempdir-spec" in ast.dump(_node):
            _guarded = True
check("the tempdir override sits inside a Linux-only branch", _guarded)
check("the unpack path is stable across runs, so it unpacks once",
      bool(_tempdir) and "{PID}" not in _tempdir[0] and "{TIME}" not in _tempdir[0],
      str(_tempdir))
check("the unpack path is versioned so upgrades do not collide",
      bool(_tempdir) and "{VERSION}" in _tempdir[0], str(_tempdir))

print("\n== icons ==")
for name, minimum in (("icon.svg", 200), ("icon.png", 1000), ("icon.ico", 2000)):
    p = os.path.join(ROOT, "assets", name)
    check(f"assets/{name} present and non-trivial",
          os.path.isfile(p) and os.path.getsize(p) > minimum)

print("\n== workflow ==")
try:
    import yaml
    wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "build.yml")))
    jobs = wf["jobs"]
    check("tests run before any binary is built",
          "test" in jobs["build-linux"]["needs"] and "test" in jobs["build-windows"]["needs"])
    check("tests run on windows too, not just linux",
          "windows-latest" in str(jobs["test"]["strategy"]["matrix"]["os"]))
    check("python pinned below 3.14 (nuitka calls 3.14 experimental)",
          wf["env"]["PYTHON_VERSION"] < "3.14", str(wf["env"]["PYTHON_VERSION"]))
    check("release only fires on a tag",
          "refs/tags/v" in jobs["release"]["if"])
    linux_steps = str(jobs["build-linux"]["steps"])
    check("patchelf installed (standalone linux builds need it)", "patchelf" in linux_steps)
    check("compiled binary is smoke-tested before packaging",
          "--version" in linux_steps)
    # GitHub Actions has a fixed, small set of expression functions. Using one
    # that does not exist (substring(), say) fails only at evaluation time, deep
    # into a run — worth catching here instead.
    import re as _re
    _wf_text = open(os.path.join(ROOT, ".github", "workflows", "build.yml")).read()
    _known = {"contains", "startsWith", "endsWith", "format", "join", "toJSON",
              "fromJSON", "hashFiles", "success", "always", "cancelled", "failure"}
    _used = set()
    for expr in _re.findall(r"\$\{\{(.*?)\}\}", _wf_text, _re.S):
        _used |= set(_re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", expr))
    check("workflow uses only real Actions expression functions",
          not (_used - _known), f"unknown: {sorted(_used - _known)}")
    check("reserved GITHUB_* names are not overridden via env:",
          not _re.search(r"^\s+GITHUB_[A-Z_]+:", _wf_text, _re.M))

    check("built on old glibc for wide compatibility",
          jobs["build-linux"]["runs-on"] == "ubuntu-22.04", jobs["build-linux"]["runs-on"])
except ImportError:
    check("pyyaml available to validate the workflow", False, "pip install pyyaml")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
