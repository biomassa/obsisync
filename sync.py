#!/usr/bin/env python3
import json
import os
import sys
import signal
import threading
import time

import click

from config import load, save, path_for, CONFIG_DIR, CONFIG_FILE
from paths import default_vault_path
from state_db import init as db_init, log as db_log
import keyring

from auth import authenticate, discover_vaults, find_vault_root, save_password, get_password, clear_password
from sync_engine import (
    daemon_loop, run_sync_cycle, shutdown as engine_shutdown,
    log as engine_log, get_logs, _load_stats, _save_stats,
)
from state_db import set_meta, clear_logs
from watcher import VaultWatcher

HERE = os.path.dirname(os.path.abspath(__file__))


@click.group()
def cli():
    pass


@cli.command()
@click.option("--config", "cfg_path", default=None, help="Config file path")
def run(cfg_path):
    """Start the sync daemon in the foreground (headless; no GUI)."""
    cfg = _ensure_config(cfg_path)
    _ensure_setup(cfg)

    api = authenticate(cfg["apple_id"], get_password(cfg["apple_id"]), interactive=False)
    vault_node = find_vault_root(api, cfg["vault_name"])
    if vault_node is None:
        click.echo(f"Error: vault '{cfg['vault_name']}' not found on iCloud Drive", err=True)
        sys.exit(1)

    db_init()

    # Start the sync daemon in a background thread
    daemon_thread = threading.Thread(
        target=daemon_loop,
        args=(api, vault_node, cfg),
        daemon=True,
    )
    daemon_thread.start()

    # Start the watchdog watcher in a background thread
    watcher = None
    local_path = cfg["local_path"]
    if os.path.isdir(local_path):
        watcher = VaultWatcher(local_path)
        watcher.start()

    click.echo("Sync daemon running. Press Ctrl-C to stop.")

    stopping = threading.Event()

    def sig_handler(sig, frame):
        if stopping.is_set():
            return
        stopping.set()
        engine_log("INFO", "Shutting down...")
        engine_shutdown()
        if watcher:
            watcher.stop()

    # SIGTERM is not delivered the same way on Windows; register what exists.
    signal.signal(signal.SIGINT, sig_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, sig_handler)

    try:
        while not stopping.is_set() and daemon_thread.is_alive():
            stopping.wait(0.5)
    except KeyboardInterrupt:
        sig_handler(None, None)

    daemon_thread.join(timeout=10)
    click.echo("Stopped.")


@cli.command()
@click.option("--config", "cfg_path", default=None, help="Config file path")
def once(cfg_path):
    """Run a single sync cycle, then exit."""
    cfg = _ensure_config(cfg_path)
    _ensure_setup(cfg)

    api = authenticate(cfg["apple_id"], get_password(cfg["apple_id"]), interactive=False)
    vault_node = find_vault_root(api, cfg["vault_name"])
    if vault_node is None:
        click.echo(f"Error: vault '{cfg['vault_name']}' not found on iCloud Drive", err=True)
        sys.exit(1)

    db_init()
    run_sync_cycle(api, vault_node, cfg)


@cli.command()
def setup():
    """Interactive first-run configuration."""
    click.echo("=== Obsidian iCloud Sync Setup ===\n")

    email = click.prompt("Apple ID email")
    password = click.prompt("iCloud password", hide_input=True)

    save_password(email, password)

    click.echo("\nAuthenticating with iCloud...")
    try:
        api = authenticate(email, password, interactive=True)
    except Exception as e:
        click.echo(f"Authentication failed: {e}", err=True)
        sys.exit(1)

    click.echo("\nDiscovering Obsidian vaults on iCloud Drive...")
    vaults = discover_vaults(api)
    if vaults:
        click.echo("Found vaults:")
        for i, v in enumerate(vaults):
            click.echo(f"  {i + 1}. {v}")
        vault_idx = click.prompt("Select vault number", type=int, default=1)
        vault_name = vaults[vault_idx - 1]
    else:
        click.echo("No Obsidian vaults auto-discovered.")
        vault_name = click.prompt("Enter vault path on iCloud Drive", default="Obsidian/Obsidian")

    local_path = click.prompt("Local vault path", default=default_vault_path())

    cfg = load()
    cfg["apple_id"] = email
    cfg["vault_name"] = vault_name
    cfg["local_path"] = local_path
    save(cfg)

    click.echo(f"\nSetup complete! Config saved to {CONFIG_FILE}")
    click.echo("Run the daemon: source .venv/bin/activate && python3 sync.py run")


@cli.command()
def status():
    """Quick status check."""
    cfg = load()
    if not cfg.get("apple_id"):
        click.echo("Not configured. Run 'sync.py setup' first.")
        return

    db_init()
    stats = _load_stats()
    click.echo(f"Vault:     {cfg.get('vault_name', '?')}")
    click.echo(f"Local:     {cfg['local_path']}")
    click.echo(f"Files:     {stats.get('files', 0)}")
    click.echo(f"Uploaded:  {stats.get('uploaded', 0)}")
    click.echo(f"Downloaded: {stats.get('downloaded', 0)}")
    click.echo(f"Conflicts: {stats.get('conflicts', 0)}")
    click.echo(f"Last sync: {stats.get('last_sync', 'never')}")

    recent = get_logs(limit=5)
    if recent:
        click.echo("\nRecent logs:")
        for e in recent:
            click.echo(f"  [{e['level']}] {e['message']}")


@cli.command()
def clear_stats():
    """Reset sync statistics and logs."""
    db_init()
    for key in ("files", "uploaded", "downloaded", "conflicts", "errors", "deleted"):
        set_meta(f"stats_{key}", "0")
    set_meta("last_sync", "")
    clear_logs()
    click.echo("Statistics and logs cleared.")


@cli.command("import-from-iobsi")
@click.option("--force", is_flag=True,
              help="Overwrite existing obsisync config and state.")
def import_from_iobsi(force):
    """Copy config, session and sync state from an existing iObsi install.

    Deliberately a copy, not a move: iObsi may still be installed and running
    against its own directory, and nothing here should break it. Note that
    running both daemons against the same vault at once is a bad idea.
    """
    import shutil
    from paths import LEGACY_CONFIG_DIR, legacy_config_exists, config_dir, data_dir

    if not legacy_config_exists():
        click.echo(f"No iObsi config found at {LEGACY_CONFIG_DIR}", err=True)
        sys.exit(1)

    os.makedirs(config_dir(), exist_ok=True)
    os.makedirs(data_dir(), exist_ok=True)

    copied = []
    src_cfg = os.path.join(LEGACY_CONFIG_DIR, "config.json")
    dst_cfg = os.path.join(config_dir(), "config.json")
    if os.path.exists(dst_cfg) and not force:
        click.echo(f"Refusing to overwrite existing config at {dst_cfg}", err=True)
        click.echo("Re-run with --force to replace it.", err=True)
        sys.exit(1)
    cfg = json.load(open(src_cfg))
    cfg.pop("web_port", None)          # no web UI in obsisync
    with open(dst_cfg, "w") as f:
        json.dump(cfg, f, indent=2)
    copied.append("config.json")

    skipped = []
    for name in ("session", "sync_state.db"):
        src = os.path.join(LEGACY_CONFIG_DIR, name)
        dst = os.path.join(data_dir(), name)
        if not os.path.exists(src):
            continue
        if os.path.exists(dst):
            if not force:
                # Never skip silently. Merely launching the app creates an empty
                # sync_state.db, and quietly declining to replace it would leave
                # the first cycle treating every file as new: mass conflicts.
                skipped.append(name)
                continue
            (shutil.rmtree if os.path.isdir(dst) else os.remove)(dst)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
        copied.append(name)

    # The keyring service name differs, so the stored password must be re-keyed.
    email = cfg.get("apple_id")
    if email:
        old_pw = keyring.get_password("obsidian-icloud-sync", email)
        if old_pw:
            save_password(email, old_pw)
            copied.append("keyring password")

    click.echo(f"Imported from iObsi: {', '.join(copied)}")
    if skipped:
        click.echo("")
        click.echo(f"NOT imported, already present: {', '.join(skipped)}", err=True)
        if "sync_state.db" in skipped:
            click.echo(
                "The existing sync database was kept. If it is empty (merely "
                "starting the app creates one), the first sync will treat every "
                "file as new and raise a conflict for each. Re-run with --force "
                "to replace it.", err=True)
    click.echo(f"  config -> {config_dir()}")
    click.echo(f"  state  -> {data_dir()}")
    click.echo("\niObsi's own files were left untouched.")
    click.echo("Do not run both daemons against the same vault at the same time.")


@cli.command()
@click.confirmation_option(prompt="Clear all stored auth?")
def clear_auth():
    """Remove stored iCloud credentials."""
    cfg = load()
    if cfg.get("apple_id"):
        clear_password(cfg["apple_id"])
    click.echo("Credentials cleared.")


def _ensure_config(cfg_path=None):
    if cfg_path:
        return _load_cfg_path(cfg_path)
    return load()


def _load_cfg_path(cfg_path):
    import json
    with open(cfg_path) as f:
        return json.load(f)


def _ensure_setup(cfg):
    if not cfg.get("apple_id") or not cfg.get("vault_name"):
        click.echo("Not configured. Run 'sync.py setup' first.", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
