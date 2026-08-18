import sqlite3
import os
import threading
from config import path_for

DB_PATH = path_for("sync_state.db")

_local = threading.local()


def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn


def init():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS file_states (
            path           TEXT PRIMARY KEY,
            local_mtime    REAL,
            local_hash     TEXT,
            remote_etag    TEXT,
            remote_mtime   REAL,
            remote_size    INTEGER,
            last_sync_hash TEXT,
            resolution     TEXT DEFAULT 'auto',
            created_at     TEXT DEFAULT (datetime('now')),
            updated_at     TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            level     TEXT,
            message   TEXT
        );

        CREATE TABLE IF NOT EXISTS conflicts (
            path         TEXT PRIMARY KEY,
            local_mtime  REAL,
            remote_mtime REAL,
            local_hash   TEXT,
            remote_hash  TEXT,
            local_preview TEXT,
            remote_preview TEXT,
            created_at   TEXT DEFAULT (datetime('now')),
            resolved     INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sync_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()


# ── file_states ──────────────────────────────────────


def get_state(path):
    cur = _get_conn().execute(
        "SELECT * FROM file_states WHERE path = ?", (path,)
    )
    row = cur.fetchone()
    return dict(row) if row else None


def upsert_state(path, **kw):
    fields = ", ".join(kw.keys())
    placeholders = ", ".join("?" for _ in kw)
    updates = ", ".join(f"{k}=excluded.{k}" for k in kw)
    _get_conn().execute(
        f"""
        INSERT INTO file_states (path, {fields}, updated_at)
        VALUES (?, {placeholders}, datetime('now'))
        ON CONFLICT(path) DO UPDATE SET
            {updates}, updated_at = datetime('now')
        """,
        (path, *kw.values()),
    )
    _get_conn().commit()


def delete_state(path):
    _get_conn().execute("DELETE FROM file_states WHERE path = ?", (path,))
    _get_conn().commit()


def all_states():
    cur = _get_conn().execute("SELECT * FROM file_states")
    return [dict(r) for r in cur.fetchall()]


# ── sync_log ─────────────────────────────────────────


def log(level, message):
    _get_conn().execute(
        "INSERT INTO sync_log (level, message) VALUES (?, ?)",
        (level, message),
    )
    _get_conn().commit()


def recent_logs(limit=100):
    cur = _get_conn().execute(
        "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = list(cur)
    rows.reverse()
    return [dict(r) for r in rows]


def clear_logs():
    _get_conn().execute("DELETE FROM sync_log")
    _get_conn().commit()


# ── conflicts ────────────────────────────────────────


def upsert_conflict(path, **kw):
    fields = ", ".join(kw.keys())
    placeholders = ", ".join("?" for _ in kw)
    updates = ", ".join(f"{k}=excluded.{k}" for k in kw)
    _get_conn().execute(
        f"""
        INSERT INTO conflicts (path, {fields})
        VALUES (?, {placeholders})
        ON CONFLICT(path) DO UPDATE SET
            {updates}
        """,
        (path, *kw.values()),
    )
    _get_conn().commit()


def resolve_conflict(path):
    _get_conn().execute(
        "UPDATE conflicts SET resolved = 1 WHERE path = ?", (path,)
    )
    _get_conn().commit()


def remove_conflict(path):
    _get_conn().execute("DELETE FROM conflicts WHERE path = ?", (path,))
    _get_conn().commit()


def unresolved_conflicts():
    cur = _get_conn().execute(
        "SELECT * FROM conflicts WHERE resolved = 0"
    )
    return [dict(r) for r in cur.fetchall()]


# ── sync_meta ────────────────────────────────────────


def get_meta(key, default=None):
    cur = _get_conn().execute(
        "SELECT value FROM sync_meta WHERE key = ?", (key,)
    )
    row = cur.fetchone()
    return row["value"] if row else default


def set_meta(key, value):
    _get_conn().execute(
        """
        INSERT INTO sync_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    _get_conn().commit()
