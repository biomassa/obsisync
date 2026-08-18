#!/usr/bin/env python3
"""Single entry point for both the GUI and the headless CLI.

A compiled build is one executable, so the mode is chosen by argument rather
than by which script was launched:

    obsisync                 -> GUI
    obsisync --headless ...  -> the CLI, with the remaining arguments

``--profile DIR`` puts config and state under DIR instead of the usual
locations, so a second account or a throwaway sign-in test does not disturb a
working installation. It is handled here rather than by Click, because it has to
take effect before any module that reads those locations is imported.
"""
import multiprocessing
import sys


def _take_profile(argv):
    """Consume --profile from the arguments and apply it.

    This runs before sync or gui.app is imported, because config, state_db and
    auth resolve their locations at import time into module-level constants. A
    profile applied afterwards would have no effect on them.
    """
    if "--profile" not in argv:
        return argv
    index = argv.index("--profile")
    if index + 1 >= len(argv):
        sys.exit("--profile needs a directory")
    import paths
    paths.set_profile(argv[index + 1])
    return argv[:index] + argv[index + 2:]


def main():
    # Nuitka onefile re-executes the bootstrap; without this, any use of
    # multiprocessing would spawn copies of the whole app.
    multiprocessing.freeze_support()

    argv = _take_profile(sys.argv[1:])

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
