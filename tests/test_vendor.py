"""The vendored Drive-only iCloud client.

These check structure, not behaviour against Apple — nothing here talks to
iCloud. Their job is to catch a trim that removed something the engine calls.
"""
import ast, importlib, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

print("\n== the heavy dependencies are actually gone ==")
for mod in ("fido2", "pydantic", "google.protobuf"):
    sys.modules.pop(mod, None)
import icloudlite
loaded = lambda m: any(k == m or k.startswith(m + ".") for k in sys.modules)
check("fido2 not imported", not loaded("fido2"))
check("google.protobuf not imported", not loaded("google.protobuf"))
# pydantic is still pulled in by hsa2_bridge, which drives SMS/trusted-device 2FA.
check("pydantic still present (hsa2_bridge needs it)", loaded("pydantic"))

print("\n== nothing still reaches for upstream pyicloud ==")
# Prose and constants may still say "pyicloud" — provenance is documented on
# purpose. What must not survive is an actual import of the upstream package.
stale = []
for dirpath, _, files in os.walk(os.path.join(ROOT, "icloudlite")):
    for f in files:
        if not f.endswith(".py"):
            continue
        path = os.path.join(dirpath, f)
        for node in ast.walk(ast.parse(open(path).read())):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            if any(m == "pyicloud" or m.startswith("pyicloud.") for m in mods):
                stale.append(f)
check("no pyicloud imports remain inside icloudlite", not stale, str(stale))
check("engine imports the vendored client",
      "from icloudlite import" in open(os.path.join(ROOT, "auth.py")).read())

print("\n== upstream licence travelled with the code ==")
lic = os.path.join(ROOT, "icloudlite", "LICENSE.pyicloud")
check("LICENSE.pyicloud present", os.path.isfile(lic))
check("copyright notice retained",
      "The PyiCloud Authors" in open(lic).read() if os.path.isfile(lic) else False)
check("provenance documented in the package docstring",
      "pyicloud" in (icloudlite.__doc__ or "").lower())

print("\n== every import is declared as a dependency ==")
# Dropping pyicloud meant restating its runtime deps by hand, and three were
# missed — which only surfaced in CI, on a machine without the old package.
import tomllib
_declared = {d.split(">")[0].split("=")[0].split("[")[0].strip().lower().replace("_", "-")
             for d in tomllib.load(open(os.path.join(ROOT, "pyproject.toml"), "rb"))
             ["project"]["dependencies"]}
_stdlib = set(sys.stdlib_module_names)
_imported = set()
for dp, _, files in os.walk(os.path.join(ROOT, "icloudlite")):
    for f in files:
        if not f.endswith(".py"):
            continue
        for n in ast.walk(ast.parse(open(os.path.join(dp, f)).read())):
            if isinstance(n, ast.Import):
                _imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                _imported.add(n.module.split(".")[0])
_third = {m for m in _imported if m not in _stdlib and m != "icloudlite"}
_undeclared = sorted(m for m in _third
                     if m.lower().replace("_", "-") not in _declared)
check("every third-party import of icloudlite is declared",
      not _undeclared, f"undeclared: {_undeclared}")

print("\n== the surface the engine uses still exists ==")
from icloudlite import PyiCloudService
for attr in ("authenticate", "requires_2fa", "validate_2fa_code",
             "trust_session", "is_trusted_session", "drive"):
    check(f"PyiCloudService.{attr}", hasattr(PyiCloudService, attr))

from icloudlite.services.drive import DriveService
src = open(os.path.join(ROOT, "icloudlite", "services", "drive.py")).read()
tree = ast.parse(src)
defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
for name in ("get_children", "open", "upload", "delete", "mkdir"):
    check(f"drive node API: {name}", name in defined, str(sorted(defined))[:120])
check("node indexing supported (node['child'])", "__getitem__" in defined)

print("\n== nothing dangling after the trim ==")
base_src = open(os.path.join(ROOT, "icloudlite", "base.py")).read()
for gone in ("Fido2Client", "CtapHidDevice", "CloudKitExtraMode", "PhotosService",
             "NotesService", "RemindersService", "InvitesService", "CalendarService"):
    check(f"{gone} fully removed", gone not in base_src)
check("base.py parses", bool(ast.parse(base_src)))

# base.py has no `from __future__ import annotations`, and PEP 649 only defers
# annotation evaluation from 3.14. On 3.11-3.13 a leftover annotation naming a
# removed type raises NameError at construction, so none may remain.
check("no annotation names a removed type (crashes below 3.14)",
      not [n for n in ("CalendarService", "PhotosService", "RemindersService",
                       "NotesService", "InvitesService", "ContactsService",
                       "AccountService", "UbiquityService", "HideMyEmailService",
                       "FindMyiPhoneServiceManager")
           if n in base_src])

# Every module must import cleanly — a trim can leave a NameError that only
# surfaces at import time.
for mod in ("icloudlite.base", "icloudlite.session", "icloudlite.services.drive",
            "icloudlite.hsa2_bridge", "icloudlite.exceptions", "icloudlite.utils"):
    try:
        importlib.import_module(mod); ok = True; err = ""
    except Exception as exc:
        ok = False; err = str(exc)
    check(f"{mod} imports", ok, err)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
