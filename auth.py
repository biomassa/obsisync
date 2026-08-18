import os
import keyring
from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudFailedLoginException
from config import path_for

SERVICE_NAME = "obsidian-icloud-sync"
COOKIE_DIR = path_for("session")


def _ensure_cookie_dir():
    os.makedirs(COOKIE_DIR, exist_ok=True)


def save_password(email, password):
    keyring.set_password(SERVICE_NAME, email, password)


def get_password(email):
    return keyring.get_password(SERVICE_NAME, email)


def clear_password(email):
    try:
        keyring.delete_password(SERVICE_NAME, email)
    except keyring.errors.PasswordDeleteError:
        pass


def authenticate(email, password=None, interactive=False):
    if not password:
        password = get_password(email)

    _ensure_cookie_dir()
    api = PyiCloudService(email, password, cookie_directory=COOKIE_DIR)

    if api.requires_2fa:
        if not interactive:
            raise RuntimeError(
                "2FA required but running in non-interactive mode. "
                "Run 'sync.py setup' first."
            )
        print("Two-factor authentication required.")
        code = input("Enter the code sent to your devices: ")
        result = api.validate_2fa_code(code)
        if not result:
            raise RuntimeError("Invalid 2FA code")
        if not api.is_trusted_session:
            result = api.trust_session()
            if not result:
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
