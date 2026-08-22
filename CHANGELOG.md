# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **A failed folder listing was read as a deletion — a data-loss bug.** Every handler in the remote
  scan caught its exception and returned an empty result, so a folder that could not be listed was
  indistinguishable from a folder that had been emptied. The scan still reported itself fresh, and
  the missing subtrees went to the deletion logic as real deletions. On 2026-08-22 a laptop woke
  with no network, two subtrees failed to list, and 13 existing files were queued for deletion. The
  bulk-deletion guard caught it and paused, so nothing was lost, but the guard was the last line of
  defence rather than the first.

  A listing failure now poisons the scan instead of shrinking it: `scan_remote` names the folders
  that failed and raises `RemoteScanIncomplete`, and all four callers abort rather than act on a
  short tree. This matters most in `confirm_pending_deletions`, whose whole job is to re-verify
  deletions immediately before executing them — an incomplete scan there would have confirmed the
  very phantom deletions it exists to disprove.

  A request timeout was added in 0.1.4 so no network call could hang forever. That turned a hang
  into a raised exception, which these handlers then swallowed, so the failure mode became far more
  reachable than before.

- **An unanswered deletion prompt did not survive a restart.** The pending list and the pause were
  in-memory only, so quitting obsisync — or a laptop sleeping — discarded a question the user had
  not answered. On restart the guard re-derived the prompt only if the condition still held *and*
  the count was still above the threshold of ten, so a set that had since shrunk to ten or fewer
  was applied silently, without ever being asked about.

  The pending set is now written to the database before sync pauses, and restored on start. It is
  cleared in exactly two ways: the user answers, or a later **complete and fresh** scan shows the
  files present on iCloud after all — a proven false alarm, which resumes sync by itself. An
  incomplete or stale scan leaves the question open. The daemon re-verifies before honouring the
  pause, because the pause stops sync cycles and the scan that proves the all-clear would otherwise
  never run. A pause the user set by hand is never lifted automatically.

### Changed

- CI builds on a tag only. A branch push and its tag are two separate push events, and both matched
  the workflow trigger, so every release compiled twice — 8 to 15 minutes per platform, on Linux and
  Windows. The tests still run on every push and pull request.

### Removed

- Stale build artifacts, spike scripts and generated packaging metadata that were left in the
  working tree.

## [0.1.4] - 2026-08-20

obsisync no longer hangs on a network whose IPv6 does not work, and the dashboard shows what the
daemon is doing.

### Added

- A **recent activity** panel on the dashboard, showing the last 10 log lines. Warnings and errors
  are coloured, and long lines wrap. DEBUG lines stay on the Logs page. The panel fills from stored
  history, so a window that you reopen from the tray is not blank, and **Clear logs** empties it.
- A **Connect to iCloud over IPv4 only** setting, on by default. Turn it off on an IPv6-only
  network.

### Changed

- The watcher waits 3 seconds after writing stops, instead of 0.3, so a note is not uploaded
  mid-save while you are still typing. The 30-second floor between watcher-driven cycles is
  unchanged, so a long editing session uploads about twice a minute rather than continuously.

### Fixed

- A broken IPv6 route made obsisync hang at "Connecting to iCloud…" for ever. A router can advertise
  IPv6 and route none of it: the host takes a global address and installs a default route, and every
  connection over that route then stalls. `requests` walks the addresses that `getaddrinfo` returns,
  in order, and that order puts IPv6 first. A browser survives this because it races both families
  (Happy Eyeballs, RFC 8305). `requests` does not. obsisync now connects over IPv4 only, and iCloud
  is reachable over IPv4 everywhere.
- The vendored iCloud client passed no request timeout, so a stalled connection blocked its thread
  for ever. This turned the fault above from a delay into a wedge: the only recovery was restarting
  the app. Every request now has a 10-second connect timeout and a 60-second read timeout, unless
  the caller sets its own.
- A local edit could wait up to two minutes to sync. The watcher accepted ignore patterns and never
  applied them, and no caller passed any. An ignored file still counted as a change, so Obsidian
  rewriting `.obsidian/workspace.json` spent the one allowed trigger per 30 seconds on a cycle with
  nothing to do, and the note edit that followed was dropped. The watcher now filters events through
  the same patterns the scans use.
- A local edit made during a sync was dropped. The watcher's three guards — a cycle in progress, the
  post-cycle echo window, the minimum interval — each discarded the change instead of deferring it.
  A forced remote scan of an 800-file vault takes about 25 seconds, longer than the 30-second
  interval it was measured against, so editing a note while one ran meant waiting for the next poll.
  A blocked change is now rescheduled, and it fires as soon as the block lifts.

## [0.1.3] - 2026-08-18

Signing in with an Apple ID now works. Earlier versions could not complete a first-time sign-in, so
the only way in was to import a session from another tool.

### Added

- `--profile DIR` puts settings and sync state under `DIR`. Use it for a second vault or account, or
  to try a sign-in without disturbing a working installation.
- obsisync refuses to start a second instance against the same directory. Two daemons on one vault
  fight each other.

### Fixed

- Two-factor sign-in failed every time. obsisync never called `request_2fa_code()`. For a modern
  HSA2 account that call performs Apple's trusted-device handshake and records the state that code
  validation then reads. Without it validation used the legacy verifier, which Apple rejects, so
  every code looked wrong however many times the prompt was approved.
- The setup wizard signed in twice. It authenticated once to discover that a code was needed, then
  authenticated again to submit it. The second sign-in created a new session, which made Apple send
  another code, and then checked the code against the wrong session. It could not succeed, and it
  spent a code on each attempt until Apple reported `tooManyCodesSent`.
- The code prompt now names the delivery route and the destination number. Apple often shows a
  prompt on a trusted device *and* sends an SMS, and only one of them is the code the session
  accepts.
- A failed sign-in no longer prints Apple's raw authentication payload, several hundred lines long
  and containing the account phone numbers, into the window.

## [0.1.2] - 2026-08-18

### Added

- **Clear stats** button on the dashboard. It zeroes the totals and leaves the tracked file count.
- Instructions for building on Linux, in the README.

### Changed

- The bare `obsisync` binary is no longer published. The AppImage covers the same use, and the
  release listed the binary at four times its real size.

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

## [0.1.1] - 2026-08-18

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
- **Clear stats** and **Close window** buttons on the dashboard, and **Clear logs** on the logs
  page.
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
- Secondary labels are legible. They used Qt's disabled state, which renders at minimal contrast.
- The log view survives a restart, because it reads the database and not the in-memory buffer.

[Unreleased]: https://github.com/biomassa/obsisync/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/biomassa/obsisync/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/biomassa/obsisync/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/biomassa/obsisync/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/biomassa/obsisync/releases/tag/v0.1.1
