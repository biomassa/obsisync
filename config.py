import json
import os

CONFIG_DIR = os.path.expanduser("~/.config/obsidian-icloud-sync")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "apple_id": "",
    "vault_name": "",
    "local_path": "/home/dingus/obsi",
    "web_port": 11111,
    "poll_interval": 120,
    "conflict_strategy": "last-writer-wins",
    "sync_deletes": True,
    "log_level": "INFO",
    "ignore_patterns": [
        "*.tmp", "*.swp", "*.part", "*.icloud", ".DS_Store",
        "._*", "~$*", ".trash/", ".sync-tmp-*",
        ".obsidian/workspace", ".obsidian/workspace-mobile",
    ],
}


def ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load():
    ensure_config_dir()
    if not os.path.exists(CONFIG_FILE):
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save(cfg):
    ensure_config_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, default=str)


def path_for(*parts):
    ensure_config_dir()
    return os.path.join(CONFIG_DIR, *parts)
