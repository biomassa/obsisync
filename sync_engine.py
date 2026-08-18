import os
import time
import threading
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from config import load
from state_db import (
    init as db_init,
    get_state,
    upsert_state,
    delete_state,
    all_states,
    log as db_log,
    recent_logs,
    get_meta,
    set_meta,
)
from scanner import scan_local, scan_remote, hash_file_head, invalidate_remote_cache
from conflict import resolve as resolve_conflict
from filters import should_ignore

_LOG_RING = []
_LOG_RING_MAX = 500
_LOG_LOCK = threading.Lock()

_sync_paused = threading.Event()
_sync_running = threading.Event()
_sync_trigger = threading.Event()
_sync_force_refresh = threading.Event()
_shutdown_event = threading.Event()
_watchdog_suppress_until = 0.0
_pending_remote_deletions = []
_DELETION_THRESHOLD = 10
_pending_deletions_lock = threading.Lock()

# Tracked files that a config ignore_patterns entry has started matching. Both scans
# filter identically, so these simply stop appearing on either side — we park them and
# let the user decide in the web UI rather than silently untracking or deleting.
_pending_ignored = []
_pending_ignored_lock = threading.Lock()

_web_log_listeners = []

_LOG_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_current_log_level = "INFO"


def set_log_level(level):
    global _current_log_level
    if level in _LOG_LEVEL_ORDER:
        _current_log_level = level
        log("INFO", f"Log level changed to {level}")


def log(level, message):
    # Unknown levels must never be silently dropped — treat them as most severe.
    if _LOG_LEVEL_ORDER.get(level, 99) < _LOG_LEVEL_ORDER.get(_current_log_level, 1):
        return
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "message": message,
    }
    with _LOG_LOCK:
        _LOG_RING.append(entry)
        if len(_LOG_RING) > _LOG_RING_MAX:
            _LOG_RING[:] = _LOG_RING[-_LOG_RING_MAX:]
    db_log(level, message)
    with _LOG_LOCK:
        for q in _web_log_listeners:
            try:
                q.put(entry)
            except Exception:
                pass


def get_logs(limit=100):
    with _LOG_LOCK:
        return list(_LOG_RING[-limit:])


def subscribe_logs(queue):
    with _LOG_LOCK:
        _web_log_listeners.append(queue)


def unsubscribe_logs(queue):
    with _LOG_LOCK:
        if queue in _web_log_listeners:
            _web_log_listeners.remove(queue)


def pause():
    _sync_paused.set()


def resume():
    _sync_paused.clear()
    _sync_trigger.set()


def is_paused():
    return _sync_paused.is_set()


def get_pending_deletions():
    with _pending_deletions_lock:
        return list(_pending_remote_deletions)


def confirm_pending_deletions(api, vault_node, cfg):
    log("INFO", "Re-scanning remote to verify pending deletions...")
    remote_files, _ = scan_remote(vault_node, force=True, extra_ignore=cfg.get("ignore_patterns", []))
    local_path = cfg["local_path"]
    with _pending_deletions_lock:
        pending = list(_pending_remote_deletions)
        _pending_remote_deletions.clear()
    for rel_path in pending:
        if rel_path in remote_files:
            try:
                node = _resolve_node(vault_node, rel_path)
                if node:
                    _download_file(node, os.path.join(local_path, rel_path))
                    h = hash_file_head(os.path.join(local_path, rel_path))
                    upsert_state(
                        rel_path,
                        local_mtime=remote_files[rel_path]["mtime"],
                        local_hash=h or "",
                        remote_etag=remote_files[rel_path].get("etag", ""),
                        remote_mtime=remote_files[rel_path]["mtime"],
                        remote_size=remote_files[rel_path].get("size", 0),
                        last_sync_hash=h or "",
                    )
                    log("INFO", f"Re-downloaded (file exists on remote): {rel_path}")
            except Exception as e:
                log("ERROR", f"Failed to re-download {rel_path}: {e}")
        else:
            try:
                abs_path = os.path.join(local_path, rel_path)
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
                elif os.path.islink(abs_path):
                    os.unlink(abs_path)
                delete_state(rel_path)
                log("INFO", f"Deleted locally (confirmed remote deletion): {rel_path}")
            except Exception as e:
                log("ERROR", f"Failed to delete locally {rel_path}: {e}")
    resume()


def cancel_pending_deletions():
    with _pending_deletions_lock:
        _pending_remote_deletions.clear()
    log("INFO", "Pending deletions cancelled — files left intact")
    resume()


def upload_pending_deletions(api, vault_node, cfg):
    log("INFO", "Uploading pending files back to iCloud...")
    local_path = cfg["local_path"]
    with _pending_deletions_lock:
        pending = list(_pending_remote_deletions)
        _pending_remote_deletions.clear()
    uploaded = 0
    errors = 0
    for rel_path in pending:
        abs_path = os.path.join(local_path, rel_path)
        if not os.path.exists(abs_path):
            log("ERROR", f"Cannot upload — local file missing: {rel_path}")
            errors += 1
            continue
        try:
            info = _upload_file(vault_node, rel_path, abs_path, api)
            if info:
                h = hash_file_head(abs_path)
                upsert_state(
                    rel_path,
                    local_mtime=info.get("mtime", os.path.getmtime(abs_path)),
                    local_hash=h or "",
                    remote_etag=info.get("etag", ""),
                    remote_mtime=info.get("mtime", os.path.getmtime(abs_path)),
                    remote_size=info.get("size", os.path.getsize(abs_path)),
                    last_sync_hash=h or "",
                )
                uploaded += 1
                log("INFO", f"Uploaded back to iCloud: {rel_path}")
        except Exception as e:
            errors += 1
            log("ERROR", f"Failed to upload {rel_path}: {e}")
    invalidate_remote_cache()
    resume()
    log("INFO", f"Uploaded {uploaded} file(s) back to iCloud ({errors} errors)")


def get_pending_ignored():
    with _pending_ignored_lock:
        return list(_pending_ignored)


def _take_pending_ignored():
    with _pending_ignored_lock:
        pending = list(_pending_ignored)
        _pending_ignored.clear()
    return pending


def untrack_ignored():
    """Stop syncing the parked files. Both copies are left exactly where they are."""
    pending = _take_pending_ignored()
    for rel_path in pending:
        delete_state(rel_path)
        log("INFO", f"Stopped syncing (matches ignore pattern): {rel_path}")
    log("INFO", f"Stopped syncing {len(pending)} ignored file(s) — local and iCloud copies kept")
    return len(pending)


def delete_remote_ignored(api, vault_node, cfg):
    """Stop syncing the parked files and remove the iCloud copy. Local copy is kept."""
    pending = _take_pending_ignored()
    deleted = errors = 0
    for rel_path in pending:
        try:
            node = _resolve_node(vault_node, rel_path)
            if node:
                node.delete()
                invalidate_remote_cache()
            delete_state(rel_path)
            deleted += 1
            log("INFO", f"Deleted from iCloud (now ignored, local copy kept): {rel_path}")
        except Exception as e:
            errors += 1
            log("ERROR", f"Failed to delete from iCloud {rel_path}: {e}")
    log("INFO", f"Removed {deleted} ignored file(s) from iCloud ({errors} errors)")
    return deleted, errors


def unignore_pending(cfg=None):
    """Treat the ignore pattern as the mistake: drop the config patterns that match the
    parked files so they resume syncing. Patterns baked into filters.DEFAULT_IGNORE
    cannot be removed this way and are reported back to the caller."""
    from config import load as load_cfg, save as save_cfg
    from filters import DEFAULT_IGNORE

    pending = _take_pending_ignored()
    cfg = load_cfg()
    patterns = list(cfg.get("ignore_patterns", []))

    culprits = [p for p in patterns if any(should_ignore(rel, [p]) for rel in pending)]
    remaining = [p for p in patterns if p not in culprits]

    still_ignored = [rel for rel in pending if should_ignore(rel, remaining)]

    if culprits:
        cfg["ignore_patterns"] = remaining
        save_cfg(cfg)
        log("INFO", f"Removed ignore pattern(s) {', '.join(culprits)} — resuming sync")
    if still_ignored:
        log(
            "ERROR",
            f"{len(still_ignored)} file(s) still match a built-in pattern in "
            f"filters.DEFAULT_IGNORE and cannot be un-ignored from the UI: "
            f"{', '.join(still_ignored[:5])}",
        )
    invalidate_remote_cache()
    return {"removed": culprits, "still_ignored": still_ignored}


def is_running():
    return _sync_running.is_set()


def trigger_sync():
    _sync_trigger.set()
    _sync_force_refresh.set()


def shutdown():
    _shutdown_event.set()
    _sync_trigger.set()


# ── sync stats ──────────────────────────────────────


def _load_stats():
    return {
        "files": int(get_meta("stats_files", 0)),
        "uploaded": int(get_meta("stats_uploaded", 0)),
        "downloaded": int(get_meta("stats_downloaded", 0)),
        "conflicts": int(get_meta("stats_conflicts", 0)),
        "errors": int(get_meta("stats_errors", 0)),
        "deleted": int(get_meta("stats_deleted", 0)),
        "last_sync": get_meta("last_sync", ""),
    }


def _save_stats(**kw):
    for k, v in kw.items():
        set_meta(f"stats_{k}", str(v))


# ── bootstrap: full download ────────────────────────


def bootstrap_vault(api, vault_node, local_path, extra_ignore=None):
    log("INFO", "Bootstrapping vault — downloading all files from iCloud Drive...")
    remote_files = scan_remote(vault_node, extra_ignore=extra_ignore)
    if isinstance(remote_files, tuple):
        remote_files = remote_files[0]
    total = len(remote_files)
    log("INFO", f"Found {total} files in iCloud vault")

    for i, (rel_path, info) in enumerate(sorted(remote_files.items())):
        if _shutdown_event.is_set():
            return
        abs_path = os.path.join(local_path, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        try:
            node = _resolve_node(vault_node, rel_path)
            if node is None:
                continue

            _download_file(node, abs_path)
            h = hash_file_head(abs_path)
            upsert_state(
                rel_path,
                local_mtime=info["mtime"],
                local_hash=h or "",
                remote_etag=info.get("etag", ""),
                remote_mtime=info["mtime"],
                remote_size=info.get("size", 0),
                last_sync_hash=h or "",
            )
            if (i + 1) % 50 == 0:
                log("INFO", f"Bootstrap: {i + 1}/{total} files downloaded")
        except Exception as e:
            log("ERROR", f"Failed to download {rel_path}: {e}")

    _save_stats(files=total, uploaded=0, downloaded=total, conflicts=0, errors=0)
    set_meta("last_sync", time.strftime("%Y-%m-%d %H:%M:%S"))
    log("INFO", f"Bootstrap complete — {total} files synced")


# ── helper: resolve node path ───────────────────────


def _resolve_node(root_node, rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    node = root_node
    for part in parts:
        if not part:
            continue
        try:
            node = node[part]
        except Exception:
            return None
    return node


# ── helper: download file ───────────────────────────


def _download_file(node, dest_path):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix=".sync-tmp-", dir=os.path.dirname(dest_path))
    try:
        tmp_path = os.path.join(tmp_dir, os.path.basename(dest_path))
        with node.open(stream=True) as resp:
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        shutil.move(tmp_path, dest_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── helper: upload file ─────────────────────────────


def _upload_file(root_node, rel_path, local_abs_path, api=None):
    parent = root_node
    parts = rel_path.replace("\\", "/").split("/")

    for i, part in enumerate(parts[:-1]):
        if not part:
            continue
        try:
            parent = parent[part]
        except Exception:
            parent = parent.mkdir(part)

    filename = parts[-1]

    for _attempt in range(2):
        try:
            try:
                existing = _resolve_node(root_node, rel_path)
                if existing:
                    existing.delete()
            except Exception:
                pass

            with open(local_abs_path, "rb") as f:
                parent.upload(f)
            break
        except Exception as e:
            if _attempt == 0 and api and (any(x in str(e) for x in ("Authentication required", "Unauthorized"))):
                try:
                    api.authenticate()
                    continue
                except Exception:
                    pass
            raise

    try:
        parent.get_children(force=True)
    except Exception:
        pass

    new_node = _resolve_node(root_node, rel_path)
    if new_node:
        mtime_val = (
            new_node.date_modified.timestamp()
            if isinstance(new_node.date_modified, datetime)
            else new_node.date_modified or 0
        )
        return {
            "mtime": mtime_val,
            "size": getattr(new_node, "size", None) or 0,
            "etag": getattr(new_node, "etag", None) or "",
        }
    return None


# ── main sync cycle ─────────────────────────────────


def run_sync_cycle(api, vault_node, cfg, force=False):
    if _sync_running.is_set():
        log("INFO", "Sync already in progress, skipping")
        return
    _sync_running.set()
    try:
        _run_sync_cycle(api, vault_node, cfg, force=force)
    except Exception as e:
        log("ERROR", f"Sync cycle failed: {e}")
    finally:
        _sync_running.clear()
        global _watchdog_suppress_until
        _watchdog_suppress_until = time.time() + 5


def _run_sync_cycle(api, vault_node, cfg, force=False):
    local_path = cfg["local_path"]
    extra_ignore = cfg.get("ignore_patterns", [])
    strategy = cfg.get("conflict_strategy", "last-writer-wins")
    sync_deletes = cfg.get("sync_deletes", True)

    if not os.path.isdir(os.path.join(local_path, ".obsidian")):
        log("INFO", "No local vault found — starting bootstrap")
        os.makedirs(local_path, exist_ok=True)
        bootstrap_vault(api, vault_node, local_path, extra_ignore)
        set_meta("last_sync", time.strftime("%Y-%m-%d %H:%M:%S"))
        log("DEBUG", "Sync cycle complete")
        return

    log("DEBUG", "Starting sync cycle")

    try:
        api.authenticate()
    except Exception as e:
        log("INFO", f"Re-auth attempt failed: {e}")

    local_files = scan_local(local_path, extra_ignore)
    remote_files, remote_fresh = scan_remote(vault_node, force=force, extra_ignore=extra_ignore)

    # A tracked file that now matches an ignore pattern drops out of BOTH scans. Park it
    # for the user instead of guessing: untracking, deleting the remote copy and keeping
    # it in sync are all reasonable, and only they know which they meant.
    newly_ignored = sorted(
        row["path"] for row in all_states()
        if should_ignore(row["path"], extra_ignore)
    )
    with _pending_ignored_lock:
        if newly_ignored != _pending_ignored:
            for rel in newly_ignored:
                if rel not in _pending_ignored:
                    log("INFO", f"Tracked file now matches an ignore pattern: {rel}")
            if newly_ignored:
                log("INFO", f"{len(newly_ignored)} tracked file(s) match ignore patterns — awaiting a decision in the web UI")
        _pending_ignored[:] = newly_ignored
    ignored_tracked = set(newly_ignored)

    # Safety: a remote scan that lost most of the files we have already synced is
    # almost certainly truncated (partial walk, auth hiccup), and acting on it would
    # look like mass remote deletion. Compare against the tracked count rather than
    # the local count — new local files are not yet expected on the remote and must
    # not trip this guard.
    remote_count = len(remote_files)
    tracked_count = len(all_states())
    if tracked_count and remote_count < tracked_count * 0.9:
        log("ERROR", f"Remote scan returned {remote_count} files vs {tracked_count} tracked — aborting cycle to prevent data loss")
        return

    all_paths = set(local_files.keys()) | set(remote_files.keys())

    stats_uploaded = 0
    stats_downloaded = 0
    stats_conflicts = 0
    stats_errors = 0
    stats_deleted = 0
    _deletion_candidates = []

    sorted_paths = sorted(all_paths)
    for idx, rel_path in enumerate(sorted_paths):
        if _shutdown_event.is_set():
            log("INFO", "Shutdown requested, aborting sync")
            return

        local_info = local_files.get(rel_path)
        remote_info = remote_files.get(rel_path)
        db_state = get_state(rel_path)

        # ── Both exist ──
        if local_info and remote_info:
            local_changed = (
                db_state is None
                or local_info["hash"] != db_state.get("last_sync_hash")
            )
            remote_changed = (
                db_state is None
                or remote_info.get("etag", "") != db_state.get("remote_etag", "")
                or abs(remote_info["mtime"] - (db_state.get("remote_mtime") or 0)) > 1
            )

            if not local_changed and not remote_changed:
                continue

            if local_changed and remote_changed:
                if db_state and remote_info.get("size") == local_info["size"]:
                    h = hash_file_head(os.path.join(local_path, rel_path))
                    upsert_state(
                        rel_path,
                        local_mtime=local_info["mtime"],
                        local_hash=h or "",
                        remote_etag=remote_info.get("etag", ""),
                        remote_mtime=remote_info["mtime"],
                        remote_size=remote_info.get("size", 0),
                        last_sync_hash=h or "",
                    )
                    local_files[rel_path]["hash"] = h or ""
                    continue
                stats_conflicts += 1
                log("INFO", f"Conflict detected: {rel_path}")
                action, _ = resolve_conflict(
                    strategy, rel_path, local_info, remote_info
                )
                if action == "upload":
                    try:
                        new_remote = _upload_file(vault_node, rel_path, os.path.join(local_path, rel_path), api) or {}
                        h = hash_file_head(os.path.join(local_path, rel_path))
                        upsert_state(
                            rel_path,
                            local_mtime=local_info["mtime"],
                            local_hash=h or "",
                            remote_etag=new_remote.get("etag", remote_info.get("etag", "")),
                            remote_mtime=new_remote.get("mtime", remote_info["mtime"]),
                            remote_size=new_remote.get("size", remote_info.get("size", 0)),
                            last_sync_hash=h or "",
                        )
                        remote_files[rel_path] = {
                            "path": rel_path,
                            "mtime": new_remote.get("mtime", remote_info["mtime"]),
                            "size": new_remote.get("size", remote_info.get("size", 0)),
                            "etag": new_remote.get("etag", ""),
                        }
                        stats_uploaded += 1
                        log("INFO", f"Uploaded (conflict resolved): {rel_path}")
                    except Exception as e:
                        stats_errors += 1
                        log("ERROR", f"Upload failed {rel_path}: {e}")
                elif action == "download":
                    if remote_info.get("size") == local_info["size"]:
                        h = hash_file_head(os.path.join(local_path, rel_path))
                        upsert_state(
                            rel_path,
                            local_mtime=local_info["mtime"],
                            local_hash=h or "",
                            remote_etag=remote_info.get("etag", ""),
                            remote_mtime=remote_info["mtime"],
                            remote_size=remote_info.get("size", 0),
                            last_sync_hash=h or "",
                        )
                        local_files[rel_path]["hash"] = h or ""
                        continue
                    try:
                        node = _resolve_node(vault_node, rel_path)
                        if node:
                            _download_file(node, os.path.join(local_path, rel_path))
                            h = hash_file_head(os.path.join(local_path, rel_path))
                            upsert_state(
                                rel_path,
                                local_mtime=remote_info["mtime"],
                                local_hash=h or "",
                                remote_etag=remote_info.get("etag", ""),
                                remote_mtime=remote_info["mtime"],
                                remote_size=remote_info.get("size", 0),
                                last_sync_hash=h or "",
                            )
                            local_files[rel_path] = {
                                "path": rel_path,
                                "mtime": remote_info["mtime"],
                                "size": remote_info.get("size", 0),
                                "hash": h or "",
                            }
                            stats_downloaded += 1
                            log("INFO", f"Downloaded (conflict resolved): {rel_path}")
                    except Exception as e:
                        stats_errors += 1
                        log("ERROR", f"Download failed {rel_path}: {e}")
                else:
                    log("INFO", f"Conflict skipped: {rel_path}")
                continue

            if local_changed:
                if db_state and remote_info.get("size") == local_info["size"]:
                    h = hash_file_head(os.path.join(local_path, rel_path))
                    upsert_state(
                        rel_path,
                        local_mtime=local_info["mtime"],
                        local_hash=h or "",
                        remote_etag=remote_info.get("etag", ""),
                        remote_mtime=remote_info["mtime"],
                        remote_size=remote_info.get("size", 0),
                        last_sync_hash=h or "",
                    )
                    local_files[rel_path]["hash"] = h or ""
                    continue
                try:
                    new_remote = _upload_file(vault_node, rel_path, os.path.join(local_path, rel_path), api) or {}
                    h = hash_file_head(os.path.join(local_path, rel_path))
                    upsert_state(
                        rel_path,
                        local_mtime=local_info["mtime"],
                        local_hash=h or "",
                        remote_etag=new_remote.get("etag", remote_info.get("etag", "")),
                        remote_mtime=new_remote.get("mtime", remote_info["mtime"]),
                        remote_size=new_remote.get("size", remote_info.get("size", 0)),
                        last_sync_hash=h or "",
                    )
                    remote_files[rel_path] = {
                        "path": rel_path,
                        "mtime": new_remote.get("mtime", remote_info["mtime"]),
                        "size": new_remote.get("size", remote_info.get("size", 0)),
                        "etag": new_remote.get("etag", ""),
                    }
                    stats_uploaded += 1
                    log("INFO", f"Uploaded: {rel_path}")
                except Exception as e:
                    stats_errors += 1
                    log("ERROR", f"Upload failed {rel_path}: {e}")

            if remote_changed:
                if db_state and local_info and remote_info.get("size") == local_info["size"]:
                    h = hash_file_head(os.path.join(local_path, rel_path))
                    upsert_state(
                        rel_path,
                        local_mtime=local_info["mtime"],
                        local_hash=h or "",
                        remote_etag=remote_info.get("etag", ""),
                        remote_mtime=remote_info["mtime"],
                        remote_size=remote_info.get("size", 0),
                        last_sync_hash=h or "",
                    )
                    local_files[rel_path]["hash"] = h or ""
                    continue
                try:
                    node = _resolve_node(vault_node, rel_path)
                    if node:
                        _download_file(node, os.path.join(local_path, rel_path))
                        h = hash_file_head(os.path.join(local_path, rel_path))
                        upsert_state(
                            rel_path,
                            local_mtime=remote_info["mtime"],
                            local_hash=h or "",
                            remote_etag=remote_info.get("etag", ""),
                            remote_mtime=remote_info["mtime"],
                            remote_size=remote_info.get("size", 0),
                            last_sync_hash=h or "",
                        )
                        local_files[rel_path] = {
                            "path": rel_path,
                            "mtime": remote_info["mtime"],
                            "size": remote_info.get("size", 0),
                            "hash": h or "",
                        }
                        stats_downloaded += 1
                        log("INFO", f"Downloaded: {rel_path}")
                except Exception as e:
                    stats_errors += 1
                    log("ERROR", f"Download failed {rel_path}: {e}")

        # ── Only local ──
        elif local_info and not remote_info:
            if db_state:
                if remote_fresh and sync_deletes:
                    _deletion_candidates.append(rel_path)
            else:
                try:
                    new_remote = _upload_file(vault_node, rel_path, os.path.join(local_path, rel_path), api) or {}
                    h = hash_file_head(os.path.join(local_path, rel_path))
                    upsert_state(
                        rel_path,
                        local_mtime=local_info["mtime"],
                        local_hash=h or "",
                        remote_etag=new_remote.get("etag", ""),
                        remote_mtime=new_remote.get("mtime", local_info["mtime"]),
                        remote_size=new_remote.get("size", local_info.get("size", 0)),
                        last_sync_hash=h or "",
                    )
                    remote_files[rel_path] = {
                        "path": rel_path,
                        "mtime": new_remote.get("mtime", local_info["mtime"]),
                        "size": new_remote.get("size", local_info.get("size", 0)),
                        "etag": new_remote.get("etag", ""),
                    }
                    stats_uploaded += 1
                    log("INFO", f"Uploaded (new): {rel_path}")
                except Exception as e:
                    stats_errors += 1
                    log("ERROR", f"Upload failed {rel_path}: {e}")

        # ── Only remote ──
        elif not local_info and remote_info:
            if db_state:
                if remote_fresh and sync_deletes:
                    try:
                        node = _resolve_node(vault_node, rel_path)
                        if node:
                            node.delete()
                        delete_state(rel_path)
                        stats_deleted += 1
                        invalidate_remote_cache()
                        log("INFO", f"Deleted from iCloud (local deletion propagated): {rel_path}")
                    except Exception as e:
                        stats_errors += 1
                        log("ERROR", f"Failed to delete from iCloud {rel_path}: {e}")
            else:
                try:
                    node = _resolve_node(vault_node, rel_path)
                    if node:
                        _download_file(node, os.path.join(local_path, rel_path))
                        h = hash_file_head(os.path.join(local_path, rel_path))
                        upsert_state(
                            rel_path,
                            local_mtime=remote_info["mtime"],
                            local_hash=h or "",
                            remote_etag=remote_info.get("etag", ""),
                            remote_mtime=remote_info["mtime"],
                            remote_size=remote_info.get("size", 0),
                            last_sync_hash=h or "",
                        )
                        local_files[rel_path] = {
                            "path": rel_path,
                            "mtime": remote_info["mtime"],
                            "size": remote_info.get("size", 0),
                            "hash": h or "",
                        }
                        stats_downloaded += 1
                        log("INFO", f"Downloaded (new): {rel_path}")
                except Exception as e:
                    stats_errors += 1
                    log("ERROR", f"Download failed {rel_path}: {e}")

    # ── Bulk remote deletion guard ──
    if _deletion_candidates:
        if len(_deletion_candidates) > _DELETION_THRESHOLD:
            with _pending_deletions_lock:
                _pending_remote_deletions[:] = _deletion_candidates
            for rel_path in _deletion_candidates:
                log("ERROR", f"Remote deletion pending confirmation: {rel_path}")
            log("ERROR", f"Bulk remote deletion detected ({len(_deletion_candidates)} files) — sync paused until confirmed")
            _sync_paused.set()
            return

        for rel_path in _deletion_candidates:
            try:
                abs_path = os.path.join(local_path, rel_path)
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
                elif os.path.islink(abs_path):
                    os.unlink(abs_path)
                delete_state(rel_path)
                stats_deleted += 1
                log("INFO", f"Deleted locally (remote deletion propagated): {rel_path}")
            except Exception as e:
                stats_errors += 1
                log("ERROR", f"Failed to delete locally {rel_path}: {e}")

    if not remote_fresh:
        log("DEBUG", "Remote scan data is stale — skipping deletions")

    if sync_deletes and remote_fresh:
        stats_deleted += _purge_orphaned_states(local_files, remote_files, protected=ignored_tracked)

    # ── Update stats ──
    prev = _load_stats()
    _save_stats(
        files=len(local_files),
        uploaded=prev["uploaded"] + stats_uploaded,
        downloaded=prev["downloaded"] + stats_downloaded,
        conflicts=prev["conflicts"] + stats_conflicts,
        errors=prev["errors"] + stats_errors,
        deleted=prev["deleted"] + stats_deleted,
    )
    set_meta("last_sync", time.strftime("%Y-%m-%d %H:%M:%S"))

    log(
        "DEBUG",
        f"Sync complete: ↑{stats_uploaded}  ↓{stats_downloaded}  "
        f"⚠{stats_conflicts}  ✗{stats_errors}  🗑{stats_deleted}",
    )


def _purge_orphaned_states(local_files, remote_files, protected=()):
    """Clear tracking rows for paths that are absent from both sides.

    The main loop iterates local ∪ remote, so it never visits these rows at all.
    This must not delete files: propagating a deletion to either side is the main
    loop's job, where it goes through the bulk-deletion guard first.

    `protected` holds paths awaiting a user decision (currently the newly-ignored
    list) — they are absent from both scans for a reason that is not deletion.
    """
    deleted = 0
    for row in all_states():
        rel = row["path"]
        if rel in protected:
            continue
        if rel not in local_files and rel not in remote_files:
            delete_state(rel)
            deleted += 1
            log("INFO", f"Removed from tracking (orphaned): {rel}")
    return deleted


# ── daemon loop ─────────────────────────────────────


def daemon_loop(api, vault_node, cfg):
    db_init()
    global _current_log_level
    _current_log_level = cfg.get("log_level", "INFO")
    poll_interval = cfg.get("poll_interval", 120)

    local_path = cfg["local_path"]
    is_bootstrapped = os.path.isdir(os.path.join(local_path, ".obsidian"))

    if not is_bootstrapped:
        log("INFO", "No local vault found — starting bootstrap")
        os.makedirs(local_path, exist_ok=True)
        bootstrap_vault(api, vault_node, local_path, cfg.get("ignore_patterns", []))

    log("INFO", "Daemon started — watching for changes")

    while not _shutdown_event.is_set():
        _sync_trigger.wait(poll_interval)
        _sync_trigger.clear()

        if _shutdown_event.is_set():
            break

        if _sync_paused.is_set():
            continue

        if _sync_running.is_set():
            continue

        force = _sync_force_refresh.is_set()
        if force:
            _sync_force_refresh.clear()
        run_sync_cycle(api, vault_node, cfg, force=force)

    log("INFO", "Daemon stopped")
