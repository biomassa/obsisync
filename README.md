# obsisync

Native desktop app to keep an Obsidian vault in sync between iCloud Drive and Linux or Windows.

> ## ⚠️ Early stage — not usable yet
>
> This repository currently contains **only the sync engine**, carried over from
> [iObsi](https://github.com/biomassa/iObsi). There is no GUI yet, and nothing has been
> verified on real Windows hardware.
>
> If you want something that works today, on Linux, use [iObsi](https://github.com/biomassa/iObsi).

## What this is

iObsi is a Linux-only daemon with a web dashboard — you run it in a terminal and point a browser at
`localhost:11111`. obsisync is the same sync engine rebuilt as a **native compiled desktop
application**: real Qt widgets, a system tray, an installer, and no browser involved anywhere.

macOS is deliberately out of scope, since iCloud Drive sync is native there.

## Status

| Area | State |
|---|---|
| Sync engine (scan, diff, upload/download, conflicts, deletion guards) | works, carried over from iObsi |
| Cross-platform paths, config locations, shutdown | done — engine is Windows-capable |
| Windows end-to-end verification | not yet run on real hardware |
| Native GUI (dashboard, logs, conflicts, settings) | working from source |
| Setup wizard and 2FA re-auth | working from source |
| Tray, autostart, notifications | working from source |
| Installers (Windows / AppImage / deb / Arch) | not started |

## Roadmap

1. ~~**Portability** — canonical path handling, `platformdirs` config locations, clean shutdown,
   explicit keyring backends.~~ Done.
2. ~~**GUI** — PySide6 main window: stats, logs, conflicts, settings.~~ Done.
3. ~~**Setup wizard and re-auth** — including the 2FA prompt.~~ Done.
4. ~~**Background behaviour** — tray icon, close-hides-to-tray, start on login, notifications.~~ Done.
5. **Packaging** — Nuitka-compiled binaries, built in GitHub Actions.

## Design

The sync engine started as a copy of iObsi's and has since diverged — cross-platform path handling,
platform config directories and keyring hardening all landed here. obsisync is developed on its own
terms from this point; iObsi remains a separate, working Linux tool and is not a constraint on this
one. The GUI lives in `gui/` and depends on the engine, never the reverse.

`sync.py` is a headless CLI over the same engine — useful for debugging and for running on a machine
with no desktop.

```
sync_engine.py   core loop: diff, upload/download, conflicts, deletion guards
scanner.py       local and remote inventory
conflict.py      resolution strategies
state_db.py      SQLite tracking state
auth.py          iCloud auth, keyring, vault discovery
filters.py       ignore patterns
watcher.py       filesystem watcher
sync.py          headless CLI
gui/
  bridge.py      engine threads -> Qt signals
  pages.py       dashboard, logs, conflicts, settings
  main_window.py navigation, controller, close-to-tray
  app.py         entry point
  session.py     iCloud connection, cross-thread 2FA prompt, daemon lifecycle
  wizard.py      first-run setup and re-auth dialogs
  tray.py        tray icon, menu, notifications
  autostart.py   start-on-login (registry / XDG)
```

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python tests/test_engine.py       # engine regression suite (62 checks)
python tests/test_portability.py  # cross-platform path handling (24 checks)
python tests/test_bridge.py       # engine -> Qt signal bridge (5 checks)
python tests/test_gui.py          # main window wiring (22 checks)
python tests/test_auth_flow.py    # wizard, 2FA bridging, session (24 checks)
python tests/test_tray.py         # tray state, notifications, autostart (18 checks)

python spike/demo.py              # UI preview with sample data, no iCloud

python -m gui.app                 # run the GUI
python sync.py --help             # headless CLI
```

## Credits

The sync engine originates in [iObsi](https://github.com/biomassa/iObsi) by the same author.

**Back up your vault.** This software moves and deletes files in it.

## License

MIT — see [LICENSE](LICENSE).
