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
_raw = open(os.path.join(ROOT, "build.py")).read()
# Strip the module docstring: it *explains* which flags are deliberately absent,
# so a plain substring search would find those flags in prose and mis-report.
_doc = ast.get_docstring(ast.parse(_raw)) or ""
build_src = _raw.replace(_doc, "")
check("keyring backends force-included (resolved dynamically at runtime)",
      "keyring.backends" in build_src)
check("vendored client force-included", "icloudlite" in build_src)
check("QtWebEngine excluded (the single largest Qt component)",
      "QtWebEngineCore" in build_src)
check("unittest NOT excluded — the icloud chain hard-imports unittest.mock",
      "nofollow-import-to=unittest" not in build_src)
check("windows console disabled for a GUI app",
      "windows-console-mode=disable" in build_src)

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
    check("built on old glibc for wide compatibility",
          jobs["build-linux"]["runs-on"] == "ubuntu-22.04", jobs["build-linux"]["runs-on"])
except ImportError:
    check("pyyaml available to validate the workflow", False, "pip install pyyaml")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
