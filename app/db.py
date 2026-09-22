"""SQLite persistence: links, per-user state, audit trail and settings.

Plain stdlib sqlite3, one schema version counter in ``PRAGMA user_version``.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    telegram_id   TEXT PRIMARY KEY,
    jellyfin_user TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_user ON links(jellyfin_user);

CREATE TABLE IF NOT EXISTS user_state (
    jellyfin_user  TEXT PRIMARY KEY,
    absent_since   INTEGER,
    disabled_by_us INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    action        TEXT NOT NULL,
    jellyfin_user TEXT,
    detail        TEXT,
    dry_run       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class UserState:
    """What this service remembers about one Jellyfin account."""

    absent_since: int | None = None
    disabled_by_us: bool = False


def connect(path: str) -> sqlite3.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _legacy_rows(conn: sqlite3.Connection, legacy_path: str | None) -> list[tuple]:
    """Rows of the pre-1.0 ``users(ID, JellyfinUser, Enabled)`` table, if any.

    The old table may live in this same file or in the old ``jellyfin_users.db``.
    """
    if _table_exists(conn, "users"):
        cursor = conn.execute("SELECT ID, JellyfinUser, Enabled FROM users")
        return [tuple(row) for row in cursor.fetchall()]
    if legacy_path and os.path.exists(legacy_path):
        legacy = sqlite3.connect(legacy_path)
        try:
            if not _table_exists(legacy, "users"):
                return []
            cursor = legacy.execute("SELECT ID, JellyfinUser, Enabled FROM users")
            return [tuple(row) for row in cursor.fetchall()]
        finally:
            legacy.close()
    return []


def migrate(conn: sqlite3.Connection, legacy_path: str | None = None, now: int | None = None) -> int:
    """Create the schema and, on a fresh database, import the old table.

    Returns the number of links imported from the legacy table.
    """
    now = int(time.time()) if now is None else now
    if _version(conn) >= SCHEMA_VERSION:
        return 0

    conn.executescript(_SCHEMA)
    imported = 0
    for raw_ids, jellyfin_user, enabled in _legacy_rows(conn, legacy_path):
        if not jellyfin_user:
            continue
        for telegram_id in str(raw_ids or "").split():
            conn.execute(
                "INSERT OR REPLACE INTO links (telegram_id, jellyfin_user, created_at)"
                " VALUES (?, ?, ?)",
                (telegram_id, jellyfin_user, now),
            )
            imported += 1
        if not enabled:
            # It was disabled by the old version of this service, so this
            # service owns re-enabling it.
            conn.execute(
                "INSERT OR REPLACE INTO user_state (jellyfin_user, absent_since, disabled_by_us)"
                " VALUES (?, ?, 1)",
                (jellyfin_user, now),
            )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return imported


# --- links ---------------------------------------------------------------

def add_link(conn: sqlite3.Connection, telegram_id: str, jellyfin_user: str, now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    conn.execute(
        "INSERT OR REPLACE INTO links (telegram_id, jellyfin_user, created_at) VALUES (?, ?, ?)",
        (str(telegram_id), jellyfin_user, now),
    )
    conn.commit()


def remove_link(conn: sqlite3.Connection, telegram_id: str) -> str | None:
    """Drop a link; returns the Jellyfin user it pointed at, or None."""
    row = conn.execute(
        "SELECT jellyfin_user FROM links WHERE telegram_id = ?", (str(telegram_id),)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM links WHERE telegram_id = ?", (str(telegram_id),))
    conn.commit()
    return row["jellyfin_user"]


def get_links(conn: sqlite3.Connection) -> dict[str, str]:
    """telegram_id -> jellyfin_user."""
    return {
        row["telegram_id"]: row["jellyfin_user"]
        for row in conn.execute("SELECT telegram_id, jellyfin_user FROM links")
    }


def links_by_user(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """jellyfin_user -> {telegram_id, ...}."""
    grouped: dict[str, set[str]] = {}
    for telegram_id, jellyfin_user in get_links(conn).items():
        grouped.setdefault(jellyfin_user, set()).add(telegram_id)
    return grouped


# --- per-user state ------------------------------------------------------

def get_states(conn: sqlite3.Connection) -> dict[str, UserState]:
    return {
        row["jellyfin_user"]: UserState(
            absent_since=row["absent_since"],
            disabled_by_us=bool(row["disabled_by_us"]),
        )
        for row in conn.execute(
            "SELECT jellyfin_user, absent_since, disabled_by_us FROM user_state"
        )
    }


def _upsert_state(conn: sqlite3.Connection, jellyfin_user: str, **fields) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_state (jellyfin_user, absent_since, disabled_by_us)"
        " VALUES (?, NULL, 0)",
        (jellyfin_user,),
    )
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(
        f"UPDATE user_state SET {assignments} WHERE jellyfin_user = ?",
        (*fields.values(), jellyfin_user),
    )
    conn.commit()


def set_absent_since(conn: sqlite3.Connection, jellyfin_user: str, when: int | None) -> None:
    _upsert_state(conn, jellyfin_user, absent_since=when)


def set_disabled_by_us(conn: sqlite3.Connection, jellyfin_user: str, owned: bool) -> None:
    _upsert_state(conn, jellyfin_user, disabled_by_us=int(owned))


# --- audit ---------------------------------------------------------------

def record_audit(
    conn: sqlite3.Connection,
    action: str,
    jellyfin_user: str | None = None,
    detail: str | None = None,
    dry_run: bool = False,
    now: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, action, jellyfin_user, detail, dry_run) VALUES (?, ?, ?, ?, ?)",
        (int(time.time()) if now is None else now, action, jellyfin_user, detail, int(dry_run)),
    )
    conn.commit()


def recent_audit(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    )


def count_audit(conn: sqlite3.Connection, action: str | None = None) -> int:
    if action is None:
        return int(conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0])
    return int(
        conn.execute("SELECT COUNT(*) FROM audit WHERE action = ?", (action,)).fetchone()[0]
    )


# --- settings ------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return default if row is None else row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value))
    )
    conn.commit()


def get_dry_run(conn: sqlite3.Connection, default: bool) -> bool:
    stored = get_setting(conn, "dry_run")
    if stored is None:
        return default
    return stored == "1"


def set_dry_run(conn: sqlite3.Connection, value: bool) -> None:
    set_setting(conn, "dry_run", "1" if value else "0")


def get_last_sync(conn: sqlite3.Connection) -> int | None:
    stored = get_setting(conn, "last_sync")
    return None if stored is None else int(stored)


def set_last_sync(conn: sqlite3.Connection, when: int) -> None:
    set_setting(conn, "last_sync", str(int(when)))
