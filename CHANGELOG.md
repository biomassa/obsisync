# Changelog

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
