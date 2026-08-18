"""Start-on-login, per platform.

Windows uses a registry value under the current user's Run key; Linux uses an XDG
autostart desktop entry. Both are per-user and need no elevation.
"""
import os
import sys

APP_ID = "obsisync"
_DISPLAY_NAME = "obsisync"


def _executable_command():
    """The command that should run at login.

    A compiled build is a single executable and can be invoked directly. Running
    from source needs the interpreter and the module, otherwise login would try
    to execute a .py file.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f'"{sys.executable}" -m gui.app'


# ── Linux (XDG) ─────────────────────────────────────


def _xdg_desktop_path():
    base = os.environ.get(
        "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "autostart", f"{APP_ID}.desktop")


def _xdg_enable():
    path = _xdg_desktop_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(path, "w") as f:
        f.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={_DISPLAY_NAME}\n"
            "Comment=Sync an Obsidian vault with iCloud Drive\n"
            f"Exec={_executable_command()}\n"
            f"Path={cwd}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )


def _xdg_disable():
    try:
        os.remove(_xdg_desktop_path())
    except FileNotFoundError:
        pass


def _xdg_enabled():
    return os.path.isfile(_xdg_desktop_path())


# ── Windows (registry) ──────────────────────────────

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _win_enable():
    import winreg
    # CreateKeyEx, not OpenKey: the Run key is usually present but not
    # guaranteed, and OpenKey raises FileNotFoundError when it is missing.
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, _executable_command())


def _win_disable():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_ID)
    except FileNotFoundError:
        pass          # neither the key nor the value exists; nothing to remove


def _win_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_ID)
            return True
    except (FileNotFoundError, OSError):
        return False


# ── public API ──────────────────────────────────────


def supported():
    return sys.platform in ("win32", "linux")


def is_enabled():
    if sys.platform == "win32":
        return _win_enabled()
    if sys.platform == "linux":
        return _xdg_enabled()
    return False


def set_enabled(enabled):
    """Returns True on success, False if the platform is unsupported."""
    if sys.platform == "win32":
        _win_enable() if enabled else _win_disable()
        return True
    if sys.platform == "linux":
        _xdg_enable() if enabled else _xdg_disable()
        return True
    return False
