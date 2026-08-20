# obsisync

> ## ⚠️ WINDOWS VERSION NON-FUNCTIONAL YET, LINUX ONLY FOR NOW
>
> The Windows build does not start. It compiles, and it passes the tests and the smoke test in CI,
> but on a real machine it exits without a window and without a message. Use the Linux build.

A native desktop application that keeps an Obsidian vault in sync between iCloud Drive and Linux
or Windows.

obsisync uses real Qt widgets, a system tray icon and an installer. It does not use a browser and
it does not embed one. macOS is out of scope, because iCloud Drive sync is native there.

## LLM Usage Disclaimer

A large language model (LLM) helped to develop this software. The author directed the work,
reviewed the changes and tested the result.

Back up your vault before you use it.

ALWAYS BACK UP YOUR VAULT!
ALWAYS BACK UP YOUR VAULT!
ALWAYS BACK UP YOUR VAULT!

## Status

The program runs and syncs a real vault on Linux. Some parts are still new.

| Area | State |
|---|---|
| Sync engine: scan, diff, upload, download, conflicts, deletion guards | works |
| Cross-platform paths, config locations, shutdown | works |
| Native GUI: dashboard, logs, conflicts, settings | works |
| Setup wizard, 2FA prompt, re-authentication | works |
| Tray icon, start on login, desktop notifications | works |
| Import from iObsi | works |
| Linux packages: AppImage, `.deb`, Arch | built by CI |
| Windows binary and installer | **does not start** — compiles and passes CI, but exits silently on a real machine |

The test suite runs 421 checks on Linux and Windows. The checks replace iCloud with a stub, so they
prove the logic and not the network behavior.

## Install

Download a release from the [releases page](https://github.com/biomassa/obsisync/releases).

On Linux, use the AppImage. It runs on any distribution, needs no installation, and does not keep
its payload in memory.

```bash
chmod +x obsisync-x86_64.AppImage
./obsisync-x86_64.AppImage
```

You can also install the `.deb` or the Arch package. To build the binary yourself, read
[Build from source](#build-from-source).

On Windows, run `obsisync-setup-*.exe`. The installer is per-user, so it does not need
administrator rights. It adds a Start Menu entry and an entry in Add/Remove Programs.

`obsisync.exe` is the same program without an installer. Use it to run obsisync from a USB stick,
or to try it without installing anything. Only the installer removes the start-on-login registry
entry again, so prefer the installer if you plan to use that setting.

Windows shows a SmartScreen warning on the first start, because the program has no code signature.
Select **More info**, then **Run anyway**.

## First start

The setup wizard asks for your Apple ID and password, then for a two-factor code if Apple wants
one. It then finds your vault on iCloud Drive and asks where to keep it on this computer.

obsisync signs in to Apple directly. It needs no external authenticator. Apple then sends a
verification code, either to your trusted devices or by SMS, and the dialog names which one to use.

obsisync stores the password in the system credential store. It never writes the password to a
file.

### If you already use iObsi

The wizard finds an existing [iObsi](https://github.com/biomassa/iObsi) installation and offers to
import it. The import copies the account, the vault settings and the sync database. As a result you
do not sign in again, and the first sync has nothing to reconcile.

The import copies the files. It does not move them, so iObsi continues to work. Do not run both
programs against the same vault at the same time.

The command-line equivalent is `obsisync --headless import-from-iobsi`.

### If the folder already holds your vault

This is the dangerous moment. obsisync tracks nothing yet, so every file looks changed on both
sides. A
program that resolves this by timestamp can overwrite the newer copy, because files that you copied
onto a machine recently carry new timestamps.

obsisync does not guess. It asks, and it offers three answers:

- **They already match — just start tracking.** This transfers nothing. obsisync records files of
  equal size as in sync, and lists the rest as conflicts for you to resolve. This is the only
  answer that cannot lose data.
- **Trust iCloud.** obsisync downloads the different files over the local copies.
- **Trust this computer.** obsisync uploads the different files over the iCloud copies.

## How it works

The daemon watches your vault with `watchdog`, so it sees a local edit immediately. Two limits sit
on that. A change syncs 3 seconds after writing stops, so a note is never uploaded mid-save, and a
watcher-driven sync runs at most once every 30 seconds, so a long editing session uploads about
twice a minute instead of continuously. Ignored files do not count as changes at all.

It also scans iCloud Drive every 120 seconds, because iCloud Drive has no webhooks and polling is
the only option. A note that you add on another device therefore appears within one poll interval. Change
`poll_interval` in the settings to scan more often.

Three guards protect your files:

1. **Truncated scan.** If a remote scan returns fewer than 90% of the tracked files, the cycle
   stops. A partial scan looks the same as a mass deletion.
2. **Bulk deletion.** If more than 10 tracked files disappear from iCloud in one cycle, obsisync
   pauses and asks you first.
3. **Stale data.** If the remote data comes from a cache, obsisync considers no deletion at all.

### If it hangs at "Connecting to iCloud…"

obsisync connects over IPv4 only, because a router can advertise IPv6 and route none of it. On such
a network every IPv6 connection stalls, and `requests` tries IPv6 first. A browser survives this
because it races both families. Turn **Connect to iCloud over IPv4 only** off in the settings if
you are on an IPv6-only network.

## Running a second profile

`--profile DIR` puts the settings and the sync state under `DIR` instead of the usual locations.
Use it to sync a second vault or a second Apple ID, or to try a sign-in without disturbing a
working installation.

```bash
obsisync --profile ~/obsisync-test
obsisync --profile ~/obsisync-test --headless status
```

A profile window says so in its title, and the dashboard names the directory, so you cannot confuse
it with your main instance. obsisync also refuses to start a second instance against the same
directory, because two daemons on one vault fight each other.

## Command line

Every command also works without the GUI:

```bash
obsisync --headless status
obsisync --headless once                # one sync cycle, then exit
obsisync --headless run                 # daemon, no GUI
obsisync --headless import-from-iobsi
```

## Build from source

These steps make a single self-contained binary of about 50 MB on Linux. The binary needs no
Python and no Qt on the computer that runs it.

### 1. Install the build tools

You need Python 3.11 or later, a C compiler and `patchelf`. Nuitka compiles the program to C, and
`patchelf` corrects the library paths afterwards.

```bash
# Arch
sudo pacman -S --needed base-devel python

# Debian or Ubuntu
sudo apt install build-essential python3-dev python3-venv
```

Do not install `patchelf` from your package manager. `pip` supplies it in the next step, which
keeps the version the same on every machine.

### 2. Get the source and make an environment

```bash
git clone https://github.com/biomassa/obsisync.git
cd obsisync
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,build]"
pip install patchelf
```

### 3. Run the tests

Run the tests before you build. They take about a minute, and they need no iCloud account.

```bash
QT_QPA_PLATFORM=offscreen python tests/test_engine.py
QT_QPA_PLATFORM=offscreen python tests/test_portability.py
```

The `QT_QPA_PLATFORM=offscreen` variable lets the Qt tests run without a display.

### 4. Build

```bash
python build.py
```

The build takes 8 to 15 minutes, because Nuitka compiles the program and Qt to C. Most of that time
is the C compiler. Install `ccache` first if you plan to build more than once.

The result is `dist/obsisync`. Test it:

```bash
./dist/obsisync --version
./dist/obsisync --headless --help
```

### 5. Install it

```bash
install -Dm755 dist/obsisync ~/.local/bin/obsisync
install -Dm644 assets/icon.png \
  ~/.local/share/icons/hicolor/256x256/apps/obsisync.png
```

To get a desktop entry, write this file to
`~/.local/share/applications/obsisync.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=obsisync
Comment=Sync an Obsidian vault with iCloud Drive
Exec=obsisync
Icon=obsisync
Categories=Utility;
Terminal=false
```

### Run from source instead

You do not have to compile the program to use it:

```bash
python -m gui.app                       # the GUI
python sync.py --help                   # the headless CLI
python spike/demo.py                    # UI preview, sample data, no iCloud
```

This needs no C compiler, and it uses about 20 MB less memory than the compiled binary, because a
compiled module occupies more memory than bytecode. It does need the virtual environment, so it
suits development more than daily use.

### Notes on the build

`build.py` holds the Nuitka options, so a local build and a CI release cannot differ. CI runs the
tests on Linux and Windows, then builds the binary, an AppImage, a `.deb`, an Arch package and a
Windows NSIS installer. A `v*` tag publishes a GitHub Release.

The build pins Python to 3.13, because Nuitka 4.1 supports 3.14 only as an experiment. Later
versions work, but Nuitka calls them experimental.

On Linux the binary unpacks itself into `~/.cache/obsisync/` on the first start. Delete that
directory after you replace the binary with a different version.

### Tests

```bash
python tests/test_engine.py         # sync engine (62 checks)
python tests/test_portability.py    # cross-platform paths (24 checks)
python tests/test_vendor.py         # vendored iCloud client (37 checks)
python tests/test_bridge.py         # engine to Qt signals (5 checks)
python tests/test_gui.py            # main window (23 checks)
python tests/test_auth_flow.py      # wizard, 2FA, session (32 checks)
python tests/test_tray.py           # tray, notifications, autostart (20 checks)
python tests/test_packaging.py      # entry point, build flags, CI (28 checks)
python tests/test_first_run.py      # first-run reconciliation (20 checks)
python tests/test_migrate.py        # import from iObsi (18 checks)
python tests/test_regressions.py    # bugs found in real use (81 checks)
```

## Design

```
icloudlite/      Drive-only fork of pyicloud (MIT)
sync_engine.py   main loop: diff, upload, download, conflicts, deletion guards
scanner.py       local and remote inventory
conflict.py      resolution strategies
state_db.py      SQLite tracking state
auth.py          iCloud authentication, keyring, vault discovery
filters.py       ignore patterns
watcher.py       filesystem watcher
paths.py         path keys and platform directories
migrate.py       import from iObsi
sync.py          headless CLI
gui/
  bridge.py      engine threads to Qt signals
  pages.py       dashboard, logs, conflicts, settings
  main_window.py navigation, controller, close to tray
  app.py         entry point
  session.py     iCloud connection, 2FA prompt across threads, daemon lifecycle
  wizard.py      first-run setup and re-authentication
  tray.py        tray icon, menu, notifications
  autostart.py   start on login
```

The GUI depends on the engine. The engine never depends on the GUI. `gui/bridge.py` is the only
place where the two meet.

The engine started as a copy of the iObsi engine. The two are now different. iObsi remains a
separate Linux tool and does not constrain this one.

## Memory and CPU

obsisync runs all the time, so its footprint matters. These numbers come from Linux, with the
GUI open and a vault of 817 files:

| | resident memory |
|---|---|
| from source, upstream pyicloud | 135 MB |
| from source, vendored Drive-only client | 113 MB |
| compiled | 136 MB |

CPU stays at about 0.5% of one core. The watcher waits on filesystem events, and the poll runs
every 120 seconds.

Vault size changes little. The scan structures cost 0.57 KB per file, so 20000 files add 11 MB.

`icloudlite/` exists for this reason. Upstream pyicloud imports fido2, CloudKit and every service
— photos, calendar, contacts, reminders, notes — before your code touches Drive. Those imports are
unconditional, so a build cannot exclude them.

The compiled build unpacks itself into `~/.cache/obsisync/`. The default location is `/tmp`, which
is a memory filesystem on most Linux systems, and that held about 179 MB of RAM.

## Credits

The sync engine comes from [iObsi](https://github.com/biomassa/iObsi) by the same author.

`icloudlite/` is a trimmed fork of [pyicloud](https://github.com/picklepete/pyicloud) 2.6.5 under
the MIT license. The original notice stays in `icloudlite/LICENSE.pyicloud`.

## License

MIT — see [LICENSE](LICENSE).
