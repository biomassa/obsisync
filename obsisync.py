#!/usr/bin/env python3
"""Single entry point for both the GUI and the headless CLI.

A compiled build is one executable, so the mode is chosen by argument rather
than by which script was launched:

    obsisync                 -> GUI
    obsisync --headless ...  -> the CLI, with the remaining arguments
"""
import multiprocessing
import sys


def main():
    # Nuitka onefile re-executes the bootstrap; without this, any use of
    # multiprocessing would spawn copies of the whole app.
    multiprocessing.freeze_support()

    argv = sys.argv[1:]
    if argv and argv[0] in ("--headless", "--cli"):
        sys.argv = [sys.argv[0]] + argv[1:]
        from sync import cli
        return cli(standalone_mode=True)

    if argv and argv[0] in ("--version", "-V"):
        from _version import __version__
        print(f"obsisync {__version__}")
        return 0

    from gui.app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main() or 0)
