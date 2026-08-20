# Changelog

## Known issues

- **The Windows build does not start.** It compiles, and CI runs the tests and a smoke test against
  the binary, but on a real machine it exits with no window and no message. Linux is unaffected.
  See "Debugging the Windows build" below.

### Debugging the Windows build

Start here next time. A silent exit with no message is the expected symptom of
`--windows-console-mode=disable` in `build.py`: it detaches the console, so a startup exception has
nowhere to print. Steps in order of cost:

1. Build without that flag and run the result from a terminal. The traceback should then appear.
2. Check for a missing dynamic import. `keyring.backends` is already forced in; `platformdirs`,
   `srp` and the `cryptography` bindings are the next candidates.
3. Check the unpack directory. The Linux tempdir override is deliberately not applied on Windows,
   so the payload goes to `%TEMP%`; a failure there would also be silent.
4. Windows Event Viewer, under Application, records a faulting module for a crash before Python
   starts.

## 0.1.4 — 2026-08-20

obsisync no longer hangs on a network whose IPv6 does not work, and the dashboard shows what the
daemon is doing.

### Fixed

- **A broken IPv6 route made obsisync hang at "Connecting to iCloud…" forever.** A router can
  advertise IPv6 and route none of it: the host takes a global address and installs a default
  route, and every connection over that route then stalls. `requests` walks the addresses that
  `getaddrinfo` returns, in order, and that order puts IPv6 first. A browser survives this because
  it races both families (Happy Eyeballs, RFC 8305). `requests` does not.

  obsisync now connects over IPv4 only. iCloud is reachable over IPv4 everywhere, so this costs
  nothing on a healthy network. The **Connect to iCloud over IPv4 only** setting turns it off for
  an IPv6-only network.

- **The vendored iCloud client passed no request timeout, so a stalled connection blocked its
  thread forever.** This is what turned the condition above from a delay into a wedge: the only
  recovery was restarting the app. Every request now has a 10-second connect timeout and a
  60-second read timeout unless the caller sets its own.

- **A local edit could wait up to two minutes to sync.** The watcher accepted ignore patterns and
  never applied them, and no caller passed any. An ignored file still counted as "something
  changed", so Obsidian rewriting `.obsidian/workspace.json` spent the one allowed trigger per 30
  seconds on a cycle with nothing to do — and the real note edit that followed was dropped and left
  to the next poll. The watcher now filters events through the same patterns the scans use.

- **A local edit made during a sync was dropped.** The watcher's three guards — a cycle in
  progress, the post-cycle echo window, the minimum interval — each discarded the change instead of
  deferring it. A forced remote scan of an 800-file vault takes about 25 seconds, comfortably
  longer than the 30-second interval it was measured against, so editing a note while one ran meant
  waiting for the next poll. A blocked change is now rescheduled and fires as soon as the block
  lifts.

### Changed

- **The watcher waits 3 seconds after writing stops** instead of 0.3, so a note is not uploaded
  mid-save while you are still typing. The 30-second floor between watcher-driven cycles is
  unchanged, so a long editing session uploads about twice a minute rather than continuously.

### Added

- **A "recent activity" panel on the dashboard**, showing the last 10 log lines, as iObsi has.
  Warnings and errors are coloured, and long lines wrap. DEBUG lines stay on the Logs page. The panel fills from stored
  history, so a window that you reopen from the tray is not blank, and "Clear logs" empties it.

## 0.1.3 — 2026-08-18

Signing in with an Apple ID now works. Earlier versions could not complete a first-time sign-in, so
the only way in was to import a session from another tool.

### Fixed

- **Two-factor sign-in failed every time.** obsisync never called `request_2fa_code()`. For a modern
  HSA2 account that call performs Apple's trusted-device handshake and records the state that code
  validation then reads. Without it validation used the legacy verifier, which Apple rejects, so
  every code looked wrong however many times the prompt was approved.
- **The setup wizard signed in twice.** It authenticated once to discover that a code was needed,
  then authenticated again to submit it. The second sign-in created a new session, which made Apple
  send another code, and then checked the code against the wrong session. It could not succeed, and
  it spent a code on each attempt until Apple reported `tooManyCodesSent`.
- The code prompt now names the delivery route and the destination number. Apple often shows a
  prompt on a trusted device *and* sends an SMS, and only one of them is the code the session
  accepts.
- A failed sign-in no longer prints Apple's raw authentication payload, several hundred lines long
  and containing the account phone numbers, into the window.

### Added

- `--profile DIR` puts settings and sync state under `DIR`. Use it for a second vault or account, or
  to try a sign-in without disturbing a working installation.
- obsisync refuses to start a second instance against the same directory. Two daemons on one vault
  fight each other.

## 0.1.2 — 2026-08-18

### Fixed

- A note added on another device could take up to twice the poll interval to appear. The remote
  cache expired after a hardcoded 120 seconds while the daemon woke every 120 seconds, so a strict
  comparison failed and every second cycle used the cached tree. The cache lifetime now comes from
  `poll_interval`, so changing that setting also changes how often obsisync fetches the remote.
- A window opened from the tray showed empty tiles and "connecting...". The status poll emits only
  on a change, and the bridge outlives the window, so a new window received nothing until something
  altered the status.
- Closing the window destroys it, so the compositor releases its buffers. Hiding alone freed
  nothing.
- Stored and live log entries use the same clock. Stored entries were UTC and live entries local.

### Added

- **Clear stats** button on the dashboard. It zeroes the totals and leaves the tracked file count.
- Instructions for building on Linux, in the README.

### Changed

- The bare `obsisync` binary is no longer published. The AppImage covers the same use and the
  release listed the binary at four times its real size.

## 0.1.1 — 2026-08-18

First release that anyone can install and use.

### Added

- Native Qt desktop application: dashboard, logs, conflicts and settings.
- Setup wizard, with a two-factor prompt and re-authentication when a session expires.
- Import from an existing [iObsi](https://github.com/biomassa/iObsi) installation, offered by the
  wizard. It copies the account, the settings and the sync database, so there is no sign-in and
  nothing to reconcile.
- First-run reconciliation. If a folder already holds your vault and iCloud holds it too, obsisync
  asks how to proceed rather than resolving every file by timestamp.
- System tray icon, close to tray, start on login and desktop notifications.
- **Clear stats** and **Close window** buttons on the dashboard, and **Clear logs** on the logs page.
- Packages for Windows (NSIS installer), Linux (AppImage, `.deb`) and Arch.

### Fixed

- The engine works on Windows. Local file keys used the platform separator while remote keys used
  forward slashes, so every nested file looked both local-only and remote-only.
- Config and state moved to platform directories instead of a hardcoded `~/.config` path.
- Importing from iObsi no longer corrupts the database. iObsi runs SQLite in WAL mode, and copying
  only the main file produced an image SQLite rejects.
- An unreadable database no longer stops the program from starting. It is moved aside and recreated.
- The compiled build unpacks to the on-disk cache on Linux, not to `/tmp`, which is a memory
  filesystem on most systems and held about 179 MB of RAM.
- Closing the window destroys it, so the compositor releases its buffers. Hiding alone freed nothing.
- A window opened from the tray shows the current state at once, instead of empty tiles.
- Secondary labels are legible. They used Qt's disabled state, which renders at minimal contrast.
- Stored and live log entries use the same clock. Stored entries were UTC and live entries local.
- The log view survives a restart, because it reads the database and not the in-memory buffer.

### Known limitations

- Nobody has tested the Windows build on real hardware.
- The test suite replaces iCloud with a stub, so it proves the logic and not the network behavior.
- Hardware security keys do not work for two-factor authentication. SMS and trusted devices do.
