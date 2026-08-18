"""Importing an existing iObsi installation.

Shared by the CLI and the setup wizard so both behave identically — the wizard is
the path most people will take, and it should not be a second implementation.

Everything is copied, never moved: iObsi may still be installed and running
against its own directory, and importing must not break it.
"""
import json
import os
import shutil

import keyring

from paths import LEGACY_CONFIG_DIR, config_dir, data_dir, legacy_config_exists

LEGACY_KEYRING_SERVICE = "obsidian-icloud-sync"
STATE_FILES = ("session", "sync_state.db")


def available():
    """True if there is an iObsi install worth importing."""
    return legacy_config_exists()


def describe():
    """A short summary of what would be imported, for showing to the user."""
    if not available():
        return None
    try:
        cfg = json.load(open(os.path.join(LEGACY_CONFIG_DIR, "config.json")))
    except Exception:
        return None
    tracked = None
    db = os.path.join(LEGACY_CONFIG_DIR, "sync_state.db")
    if os.path.isfile(db):
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            tracked = conn.execute("select count(*) from file_states").fetchone()[0]
            conn.close()
        except Exception:
            tracked = None
    return {
        "apple_id": cfg.get("apple_id", ""),
        "vault_name": cfg.get("vault_name", ""),
        "local_path": cfg.get("local_path", ""),
        "tracked": tracked,
        "source": LEGACY_CONFIG_DIR,
    }


def import_from_iobsi(force=False):
    """Copy config, session, sync state and the stored password across.

    Returns ``{"copied": [...], "skipped": [...]}``.

    Carrying the database over is the point of this: without it the first sync
    sees an empty database against a populated vault and has to ask how to
    reconcile every file. Merely launching the app creates an empty database, so
    a skipped copy is reported rather than passed over in silence.
    """
    if not available():
        raise FileNotFoundError(f"No iObsi config found at {LEGACY_CONFIG_DIR}")

    os.makedirs(config_dir(), exist_ok=True)
    os.makedirs(data_dir(), exist_ok=True)

    copied, skipped = [], []

    dst_cfg = os.path.join(config_dir(), "config.json")
    if os.path.exists(dst_cfg) and not force:
        raise FileExistsError(dst_cfg)
    cfg = json.load(open(os.path.join(LEGACY_CONFIG_DIR, "config.json")))
    cfg.pop("web_port", None)          # obsisync has no web UI
    with open(dst_cfg, "w") as f:
        json.dump(cfg, f, indent=2)
    copied.append("config.json")

    for name in STATE_FILES:
        src = os.path.join(LEGACY_CONFIG_DIR, name)
        dst = os.path.join(data_dir(), name)
        if not os.path.exists(src):
            continue
        if os.path.exists(dst):
            if not force:
                skipped.append(name)
                continue
            (shutil.rmtree if os.path.isdir(dst) else os.remove)(dst)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
        copied.append(name)

    # The keyring service name differs, so the password must be re-keyed.
    email = cfg.get("apple_id")
    if email:
        try:
            old = keyring.get_password(LEGACY_KEYRING_SERVICE, email)
        except Exception:
            old = None
        if old:
            from auth import save_password
            save_password(email, old)
            copied.append("keyring password")

    return {"copied": copied, "skipped": skipped}
