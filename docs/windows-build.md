# Debugging the Windows build

The Windows build compiles, and CI runs the tests and a smoke test against the binary, but on a
real machine it exits with no window and no message. Linux is unaffected.

Start here.

A silent exit is the expected symptom of `--windows-console-mode=disable` in `build.py`. That flag
detaches the console, so an exception raised during start-up has nowhere to print. The steps below
are in order of cost.

1. Build without that flag and run the result from a terminal. The traceback should then appear.
2. Look for a missing dynamic import. `build.py` already forces `keyring.backends` in.
   `platformdirs`, `srp` and the `cryptography` bindings are the next candidates.
3. Check the unpack directory. The Linux tempdir override is deliberately not applied on Windows,
   so the payload goes to `%TEMP%`. A failure there is also silent.
4. Open the Windows Event Viewer, under Application. It records a faulting module for a crash that
   happens before Python starts.

CI still builds and smoke-tests the Windows binary, so a regression there fails the run. The
release does not carry it.
