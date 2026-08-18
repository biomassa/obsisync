# Changelog

Notable changes to obsisync. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
Entries are dated; the project does not use version numbers yet.

## 2026-08-18 — portability

### Fixed

- **The engine now works on Windows.** `scan_local()` keyed files with the native separator while
  `scan_remote()` used forward slashes, so on Windows every nested file appeared as *both*
  local-only and remote-only: duplicate uploads, redundant downloads, and phantom deletions
  against `file_states`. Local keys are now normalised to canonical POSIX form via `paths.to_key()`,
  matching what the remote walk already produced. Ignore patterns containing `/`
  (`.obsidian/workspace*`, `.trash/`) match again as a result.

  The normalisation translates only the *platform's* separator, so a backslash in a POSIX
  filename is preserved rather than corrupted.

- Config, state and iCloud session cookies moved out of the hardcoded `~/.config/obsidian-icloud-sync`
  to platform-appropriate locations via `platformdirs` — `~/.config/obsisync` and
  `~/.local/share/obsisync` on Linux, `%APPDATA%`/`%LOCALAPPDATA%` on Windows.
- The default vault path is derived from the user's home directory instead of being hardcoded to
  the author's.
- `SIGTERM` is only registered where it exists, and the daemon shuts down cooperatively instead of
  calling `os._exit()`, which skipped cleanup and could leave the SQLite WAL dirty.
- The keyring backend is checked before a password is stored. keyring resolves backends
  dynamically, and in a compiled binary a plaintext fallback could win the priority contest and
  silently write an Apple ID password to disk in the clear; that now raises instead.

### Added

- `paths.py` — canonical path keys and platform directories.
- `tests/test_portability.py` — 24 checks. Separator logic is parameterised rather than read from
  the running platform, so the Windows branch is genuinely exercised when the suite runs on Linux.
- `sync.py import-from-iobsi` — copies config, session, sync state and the stored password from an
  existing iObsi install. A copy rather than a move, so iObsi keeps working.

## 2026-08-18 — initial

### Added

- Initial repository. The sync engine is carried over byte-identical from
  [iObsi](https://github.com/biomassa/iObsi): `sync_engine.py`, `scanner.py`, `conflict.py`,
  `state_db.py`, `auth.py`, `filters.py`, `config.py`, `watcher.py`. It arrives with iObsi's
  2026-08-18 fixes already applied, including the log-level suppression bug that hid the
  data-loss abort, the ignore-pattern asymmetry that could delete files from iCloud, and the
  truncated-scan guard that wedged the daemon when notes were added.
- `tests/test_engine.py` — 62-check engine regression suite, iCloud fully stubbed.
- MIT license, `pyproject.toml`, project scaffolding.

### Changed

- `sync.py` is now a headless CLI. The FastAPI web dashboard, its templates, and its JavaScript
  are not carried over — a native Qt front end replaces them.

### Known issues at this point

- The engine did not work on Windows (fixed the same day, above).
- 2FA is a blocking `input()` call, so an expired iCloud session kills the daemon rather than
  prompting for a new code. Still open; the GUI will own this.
