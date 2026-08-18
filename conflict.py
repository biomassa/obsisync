import os
import time
from state_db import upsert_state, upsert_conflict, remove_conflict, resolve_conflict

STRATEGIES = ("last-writer-wins", "keep-both", "prefer-local", "prefer-remote", "skip")


def resolve(strategy, rel_path, local_info, remote_info):
    if strategy == "prefer-local":
        _local_wins(rel_path, local_info, remote_info)
        return "upload", local_info

    if strategy == "prefer-remote":
        _remote_wins(rel_path, remote_info, local_info)
        return "download", remote_info

    if strategy == "last-writer-wins":
        if local_info["mtime"] >= remote_info["mtime"]:
            _local_wins(rel_path, local_info, remote_info)
            return "upload", local_info
        else:
            _remote_wins(rel_path, remote_info, local_info)
            return "download", remote_info

    if strategy == "keep-both":
        _record_conflict(rel_path, local_info, remote_info)
        _local_wins(rel_path, local_info, remote_info)
        return "upload", local_info

    if strategy == "skip":
        _record_conflict(rel_path, local_info, remote_info)
        return "skip", None

    raise ValueError(f"Unknown strategy: {strategy}")


def _local_wins(rel_path, local_info, remote_info):
    upsert_state(
        rel_path,
        local_mtime=local_info["mtime"],
        local_hash=local_info.get("hash", ""),
        remote_etag=remote_info.get("etag", ""),
        remote_mtime=remote_info["mtime"],
        remote_size=remote_info.get("size", 0),
        last_sync_hash=local_info.get("hash", ""),
        resolution="local_wins",
    )


def _remote_wins(rel_path, remote_info, local_info):
    upsert_state(
        rel_path,
        local_mtime=local_info.get("mtime", 0),
        local_hash=local_info.get("hash", ""),
        remote_etag=remote_info.get("etag", ""),
        remote_mtime=remote_info["mtime"],
        remote_size=remote_info.get("size", 0),
        last_sync_hash="",
        resolution="remote_wins",
    )


def _record_conflict(rel_path, local_info, remote_info):
    local_preview = ""
    remote_preview = ""

    local_path = _get_local_path(rel_path)
    if local_path and os.path.isfile(local_path):
        try:
            with open(local_path, "rb") as f:
                local_preview = f.read(500).decode("utf-8", errors="replace")
        except Exception:
            pass

    upsert_conflict(
        rel_path,
        local_mtime=local_info["mtime"],
        remote_mtime=remote_info["mtime"],
        local_hash=local_info.get("hash", ""),
        remote_hash=remote_info.get("hash", ""),
        local_preview=local_preview,
        remote_preview=remote_preview,
        resolved=0,
    )


def resolve_manually(rel_path, action, vault_path):
    if action == "local":
        resolve_conflict(rel_path)
        return "keep_local"

    if action == "remote":
        resolve_conflict(rel_path)
        return "download"

    if action == "keep-both":
        conflict_path = _make_conflict_path(rel_path, vault_path)
        _rename_local(rel_path, conflict_path, vault_path)
        resolve_conflict(rel_path)
        return "keep_both"

    return "skip"


def _make_conflict_path(rel_path, vault_path):
    base, ext = os.path.splitext(rel_path)
    return f"{base} (iCloud Conflict){ext}"


def _rename_local(old_rel, new_rel, vault_path):
    old_abs = os.path.join(vault_path, old_rel)
    new_abs = os.path.join(vault_path, new_rel)
    os.makedirs(os.path.dirname(new_abs), exist_ok=True)
    os.rename(old_abs, new_abs)


def _get_local_path(rel_path):
    from config import load
    cfg = load()
    return os.path.join(cfg["local_path"], rel_path)
