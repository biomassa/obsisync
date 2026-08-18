"""Cross-platform path handling.

Two separate concerns live here:

1. **Vault-relative keys.** Every file is tracked by a key that is canonical POSIX
   form — forward slashes, no drive letter — regardless of the host platform. The
   remote scan already produces this form, so normalising the local scan to match
   is what lets the same vault sync from Linux and Windows against one database.

2. **Application directories.** Config, state and iCloud session cookies belong in
   the platform's conventional location, not a hardcoded ``~/.config``.
"""
import os

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "obsisync"

# When set, config and state live under this directory instead of the platform
# locations. It exists so a cold Apple ID sign-in can be attempted without
# touching a working installation, and it also allows a second vault or account.
#
# It must be set before config, state_db or auth are imported: those modules
# resolve their paths at import time into module-level constants.
_profile_root = os.environ.get("OBSISYNC_PROFILE") or None


def set_profile(root):
    """Redirect config and state under one directory. None restores the default."""
    global _profile_root
    _profile_root = os.path.abspath(os.path.expanduser(root)) if root else None
    return _profile_root


def active_profile():
    """The profile directory in use, or None when running normally."""
    return _profile_root


# ── vault-relative keys ─────────────────────────────


def to_key(rel_path, sep=None):
    """Normalise a vault-relative path to a canonical POSIX key.

    Only the *platform's* separators are translated. A backslash is a legal
    character in a POSIX filename, so on Linux ``weird\\name.md`` must survive
    intact; blindly replacing backslashes would corrupt it.

    ``sep`` is injectable so the Windows branch can be tested from Linux.
    """
    if sep is None:
        sep = os.sep
        altsep = os.altsep
    else:
        # When a separator is supplied explicitly, mirror the platform pairing:
        # Windows accepts '/' alongside '\\', POSIX has no alternate separator.
        altsep = "/" if sep == "\\" else None

    if sep != "/":
        rel_path = rel_path.replace(sep, "/")
    if altsep and altsep != "/":
        rel_path = rel_path.replace(altsep, "/")
    return rel_path


def to_native(base, key):
    """Join a canonical key onto a base directory using native separators."""
    return os.path.join(base, *[part for part in key.split("/") if part])


# ── application directories ─────────────────────────


def config_dir():
    """Config location: ~/.config/obsisync, %APPDATA%\\obsisync, or a profile."""
    if _profile_root:
        return os.path.join(_profile_root, "config")
    return user_config_dir(APP_NAME, appauthor=False)


def data_dir():
    """State location for the SQLite database and iCloud session cookies."""
    if _profile_root:
        return os.path.join(_profile_root, "data")
    return user_data_dir(APP_NAME, appauthor=False)


def default_vault_path():
    """A sensible default vault location for the current user."""
    return os.path.join(os.path.expanduser("~"), "Obsidian")


# The directory iObsi used. obsisync deliberately does NOT migrate it
# automatically: iObsi may still be installed and running against it, and two
# daemons syncing one vault through separate databases would fight each other.
# `sync.py import-from-iobsi` copies it explicitly when the user asks.
LEGACY_CONFIG_DIR = os.path.expanduser("~/.config/obsidian-icloud-sync")


def legacy_config_exists():
    return os.path.isfile(os.path.join(LEGACY_CONFIG_DIR, "config.json"))
