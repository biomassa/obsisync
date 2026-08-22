import hashlib
import os
import time
import datetime
import concurrent.futures

from filters import should_ignore
from paths import to_key


def _log(level, message):
    from sync_engine import log as se_log
    se_log(level, message)


def hash_file_head(filepath, bytes_to_read=4096):
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            h.update(f.read(bytes_to_read))
        return h.hexdigest()
    except Exception:
        return None


def scan_local(vault_path, extra_ignore=None):
    result = {}
    vault_path = os.path.abspath(vault_path)
    for dirpath, dirnames, filenames in os.walk(vault_path):
        for d in list(dirnames):
            rel = to_key(os.path.relpath(os.path.join(dirpath, d), vault_path))
            if should_ignore(rel, extra_ignore):
                dirnames.remove(d)

        for fname in filenames:
            abspath = os.path.join(dirpath, fname)
            # Canonical POSIX key: the remote scan produces this form, and both
            # sides must agree or every nested file looks local-only AND
            # remote-only at once.
            rel = to_key(os.path.relpath(abspath, vault_path))
            if should_ignore(rel, extra_ignore):
                continue
            try:
                st = os.stat(abspath)
                result[rel] = {
                    "path": rel,
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "hash": hash_file_head(abspath),
                }
            except OSError:
                continue
    return result


class RemoteScanIncomplete(RuntimeError):
    """A folder listing failed, so the remote tree is missing whole subtrees.

    This must never be confused with "those files are gone". A listing fails for
    ordinary reasons — a dropped connection, a request timeout, a laptop waking
    with no network yet — and the result looks exactly like a deletion: entire
    folders absent from the scan. Acting on it deletes real files.
    """


def _walk_sync(node, prefix="", extra_ignore=None, failures=None):
    entries = {}
    children = getattr(node, "_children", None)
    if children is None:
        try:
            node.get_children()
            children = node._children or []
        except Exception as exc:
            # Returning an empty dict here is what made a failed listing
            # indistinguishable from an emptied folder.
            _record(failures, prefix or getattr(node, "name", "?"), exc)
            return entries
    for child in children or []:
        name = child.name
        rel = f"{prefix}/{name}" if prefix else name
        if should_ignore(rel, extra_ignore):
            continue
        try:
            if child.type in ("folder", "app_library"):
                entries.update(_walk_sync(child, rel, extra_ignore, failures))
            else:
                entries[rel] = {
                    "path": rel,
                    "mtime": (
                        child.date_modified.timestamp()
                        if isinstance(child.date_modified, datetime.datetime)
                        else child.date_modified or 0
                    ),
                    "size": child.size or 0,
                    "etag": getattr(child, "etag", None) or "",
                }
        except Exception as exc:
            _record(failures, rel, exc)
            continue
    return entries


def _record(failures, where, exc):
    """Note a subtree we could not read. None means the caller wants no record."""
    if failures is not None:
        failures.append(f"{where} ({type(exc).__name__})")


last_force_refresh = 0.0
_force_next = False
_FORCE_TIMEOUT = 60

# How long the cached remote tree stays usable. The daemon sets this from
# poll_interval; the default matches the default interval.
#
# It must never equal the poll interval exactly. The daemon wakes every
# poll_interval seconds, so the elapsed time at each wake is exactly that value,
# and a strict "greater than" comparison then fails on every other cycle. The
# effect was a remote refresh every second cycle, so a note added on another
# device took up to twice the poll interval to appear.
_max_cache_age = 120.0
_MIN_REFRESH_GAP_FRACTION = 0.5


def set_cache_age(poll_interval):
    """Tie the cache lifetime to the daemon's poll interval."""
    global _max_cache_age
    try:
        _max_cache_age = max(5.0, float(poll_interval))
    except (TypeError, ValueError):
        pass

_EXPLORE_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _explore_one(node, name, extra_ignore=None, failures=None):
    try:
        if getattr(node, "_children", None) is None:
            node.get_children()
        return _walk_sync(node, name, extra_ignore, failures)
    except Exception as exc:
        _record(failures, name, exc)
        return {}


def invalidate_remote_cache():
    global _force_next
    _force_next = True


def scan_remote(vault_node, force=False, extra_ignore=None):
    global last_force_refresh, _force_next
    now = time.time()
    forced = _force_next
    _force_next = False
    age = now - last_force_refresh
    # A small tolerance so a wake that lands fractionally early still counts;
    # otherwise scheduling jitter alone can skip a whole cycle.
    due = age >= (_max_cache_age - 1.0)
    do_force = force or forced or due
    # Guard against a burst of timer-driven cycles hammering iCloud. Explicit
    # and invalidated refreshes are never held back.
    if do_force and not force and not forced and age < _max_cache_age * _MIN_REFRESH_GAP_FRACTION:
        do_force = False
    fresh = False
    if do_force:
        reason = "force" if force else ("invalidated" if forced else "timer")
        _log("DEBUG", f"Force-refreshing remote scan ({reason})")
        saved = getattr(vault_node, "_children", None)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(vault_node.get_children, force=True)
            fut.result(timeout=_FORCE_TIMEOUT)
            last_force_refresh = now
            fresh = True
            _log("DEBUG", "Remote scan force-refresh succeeded")
        except concurrent.futures.TimeoutError:
            vault_node._children = saved
            _log("INFO", f"Remote scan force-refresh timed out after {_FORCE_TIMEOUT}s — using cached data")
            last_force_refresh = now
        except Exception:
            vault_node._children = saved
            _log("INFO", "Remote scan force-refresh failed — using cached data")
            last_force_refresh = now
        finally:
            pool.shutdown(wait=False)

    entries = {}
    futs = []
    failures = []
    for child in vault_node._children or []:
        name = child.name
        if should_ignore(name, extra_ignore):
            continue
        try:
            if child.type in ("folder", "app_library"):
                futs.append(
                    _EXPLORE_POOL.submit(_explore_one, child, name, extra_ignore, failures))
            else:
                entries[name] = {
                    "path": name,
                    "mtime": (
                        child.date_modified.timestamp()
                        if isinstance(child.date_modified, datetime.datetime)
                        else child.date_modified or 0
                    ),
                    "size": child.size or 0,
                    "etag": getattr(child, "etag", None) or "",
                }
        except Exception as exc:
            _record(failures, name, exc)
            continue

    try:
        for fut in concurrent.futures.as_completed(futs, timeout=180):
            try:
                entries.update(fut.result())
            except Exception as exc:
                _record(failures, "?", exc)
    except concurrent.futures.TimeoutError as exc:
        _record(failures, "(folders still listing after 180s)", exc)

    if failures:
        # Refuse to return a tree we know is short. Every caller compares this
        # against the tracked state, and a missing subtree is indistinguishable
        # from a deleted one — which is how a dropped connection turned into 13
        # files queued for deletion on 2026-08-22.
        shown = ", ".join(sorted(failures)[:5])
        if len(failures) > 5:
            shown += f", and {len(failures) - 5} more"
        _log("ERROR",
             f"Remote scan incomplete — {len(failures)} listing(s) failed: {shown}")
        raise RemoteScanIncomplete(f"{len(failures)} folder listing(s) failed")

    return entries, fresh
