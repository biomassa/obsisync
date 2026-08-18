#!/usr/bin/env python3
"""Compile obsisync with Nuitka.

CI calls this rather than duplicating the flag list in a workflow file, so a
local build and a release build cannot drift apart.

The flags are not arbitrary — each non-obvious one is here because a build
failed without it:

* ``--include-package=keyring.backends`` — keyring resolves backends dynamically,
  so Nuitka's static analysis misses them and the binary dies on first
  credential access.
* Qt module exclusions — PySide6 installs ~650 MB. Only QtCore/QtGui/QtWidgets
  are used; excluding the rest is most of the difference between a 52 MB binary
  and a far larger one.
* No ``--nofollow-import-to=unittest`` — it looks like free bloat removal, but
  something in the iCloud chain hard-imports ``unittest.mock`` at runtime and the
  binary aborts on stderr while still exiting 0.
* ``-O`` rather than ``-OO`` — the extra O strips docstrings, and Click derives
  its ``--help`` text from them, so the compiled CLI would document nothing.
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(ROOT, "obsisync.py")

# Qt ships far more than this app uses. Excluding them is pure size win.
_UNUSED_QT = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSerialPort",
    "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
]


def build(onefile=True, output_dir="dist", jobs=None):
    if not os.path.isfile(ENTRY):
        sys.exit(f"entry point missing: {ENTRY}")

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--noinclude-qt-translations",
        "--include-package=keyring.backends",
        "--include-package=icloudlite",
        "--nofollow-import-to=tkinter",
        # -O not -OO: -OO strips docstrings, and Click builds its --help
        # text from them, leaving the compiled CLI with no help at all.
        "--python-flag=-O",
        "--assume-yes-for-downloads",
        f"--output-dir={output_dir}",
        "--output-filename=obsisync",
        "--company-name=obsisync",
        "--product-name=obsisync",
        "--file-description=Obsidian iCloud sync",
        "--remove-output",
    ]
    if onefile:
        cmd.append("--onefile")
    for mod in _UNUSED_QT:
        cmd.append(f"--nofollow-import-to={mod}")

    if sys.platform == "win32":
        cmd += [
            "--windows-console-mode=disable",
            f"--windows-icon-from-ico={os.path.join(ROOT, 'assets', 'icon.ico')}",
        ]
    else:
        cmd.append(f"--linux-icon={os.path.join(ROOT, 'assets', 'icon.png')}")

    if jobs:
        cmd.append(f"--jobs={jobs}")

    cmd.append(ENTRY)
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)

    produced = os.path.join(
        ROOT, output_dir, "obsisync.exe" if sys.platform == "win32" else "obsisync")
    if not onefile:
        produced = os.path.join(ROOT, output_dir, "obsisync.dist")
    if not os.path.exists(produced):
        sys.exit(f"build reported success but {produced} is missing")
    if os.path.isfile(produced):
        print(f"built {produced} ({os.path.getsize(produced) / 1048576:.1f} MB)")
    return produced


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-onefile", action="store_true",
                    help="produce a directory rather than a single file")
    ap.add_argument("--output-dir", default="dist")
    ap.add_argument("--jobs", type=int, default=None)
    args = ap.parse_args()
    build(onefile=not args.no_onefile, output_dir=args.output_dir, jobs=args.jobs)


if __name__ == "__main__":
    main()
