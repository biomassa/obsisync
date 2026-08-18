# Changelog

Notable changes to obsisync. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
Entries are dated; the project does not use version numbers yet.

## 2026-08-18

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

### Known issues

- **The engine does not work on Windows yet.** `scan_local()` keys files with the native separator
  while `scan_remote()` uses forward slashes, so every nested file would appear as both local-only
  and remote-only: duplicate uploads, redundant downloads, and phantom deletions. Ignore patterns
  containing `/` also stop matching. Fixing this is the first task before any GUI work.
- Config and state paths are still hardcoded to `~/.config/obsidian-icloud-sync`.
- 2FA is a blocking `input()` call, so an expired iCloud session kills the daemon rather than
  prompting for a new code.
