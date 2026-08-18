import os
import keyring
from icloudlite import PyiCloudService
from icloudlite.exceptions import PyiCloudFailedLoginException
from config import path_for
from paths import data_dir

SERVICE_NAME = "obsisync"
COOKIE_DIR = os.path.join(data_dir(), "session")


def _assert_secure_keyring():
    """Refuse to store an Apple ID password in a plaintext backend.

    keyring resolves its backend dynamically at runtime. In a compiled binary the
    real backend can fail to load and keyrings.alt can win the priority contest,
    which would silently write the password to a plaintext file. Fail loudly
    instead.
    """
    backend = keyring.get_keyring()
    name = f"{type(backend).__module__}.{type(backend).__name__}"
    if "keyrings.alt" in name or "fail" in name.lower():
        raise RuntimeError(
            f"No secure credential store available (keyring selected {name}). "
            "On Linux install gnome-keyring or kwallet and ensure a session is "
            "running; on Windows the Credential Manager should be used."
        )
    return name


def _ensure_cookie_dir():
    os.makedirs(COOKIE_DIR, exist_ok=True)


def save_password(email, password):
    _assert_secure_keyring()
    keyring.set_password(SERVICE_NAME, email, password)


def get_password(email):
    return keyring.get_password(SERVICE_NAME, email)


def clear_password(email):
    try:
        keyring.delete_password(SERVICE_NAME, email)
    except keyring.errors.PasswordDeleteError:
        pass


class TwoFactorRequired(RuntimeError):
    """Raised when a 2FA code is needed but no way to ask for one was supplied."""


def authenticate(email, password=None, interactive=False, twofa_callback=None):
    """Authenticate against iCloud.

    iCloud trust cookies expire every few weeks, so 2FA is not only a first-run
    concern — a long-running daemon will hit it again. ``twofa_callback`` is how
    a GUI supplies a code without this function blocking on stdin: it is called
    with the api object and must return a code string, or None to cancel.
    """
    if not password:
        password = get_password(email)

    _ensure_cookie_dir()
    api = PyiCloudService(email, password, cookie_directory=COOKIE_DIR)

    if api.requires_2fa:
        if twofa_callback is not None:
            code = twofa_callback(api)
        elif interactive:
            print("Two-factor authentication required.")
            code = input("Enter the code sent to your devices: ")
        else:
            raise TwoFactorRequired(
                "iCloud requires a two-factor code and there is no way to ask for "
                "one here. Open the app, or run 'sync.py setup'."
            )

        if not code:
            raise TwoFactorRequired("Two-factor authentication was cancelled")

        if not api.validate_2fa_code(code):
            raise RuntimeError("Invalid 2FA code")
        if not api.is_trusted_session and not api.trust_session():
            # Not fatal: the session works, it just will not be remembered.
            print("Warning: failed to trust session")

    return api


def discover_vaults(api):
    vaults = []
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _has_obsidian(name):
        try:
            entry = api.drive[name]
            if entry.type == "file":
                return None
            children = entry.dir()
            if ".obsidian" in children:
                return name
        except Exception:
            pass
        return None

    root_items = api.drive.dir()

    # Parallel pass: check all root entries simultaneously
    with ThreadPoolExecutor(max_workers=10) as pool:
        fut_map = {pool.submit(_has_obsidian, item): item for item in root_items}
        for fut in as_completed(fut_map):
            result = fut.result()
            if result:
                vaults.append(result)

    # If not found at root, check inside Obsidian app container
    if not vaults and "Obsidian" in root_items:
        try:
            container = api.drive["Obsidian"]
            if container.type == "app_library":
                for child_name in container.dir():
                    child = container[child_name]
                    if child.type != "folder":
                        continue
                    try:
                        if ".obsidian" in child.dir():
                            vaults.append(f"Obsidian/{child_name}")
                    except Exception:
                        pass
        except Exception:
            pass

    return vaults


def find_vault_root(api, vault_name):
    parts = vault_name.strip("/").split("/")
    try:
        node = api.drive[parts[0]]
        for part in parts[1:]:
            node = node[part]
        if node.type in ("folder", "app_library"):
            return node
    except Exception:
        pass
    return None
