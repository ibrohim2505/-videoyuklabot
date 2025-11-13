from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

DB_PATH = Path("data") / "bot.db"

DEFAULT_SETTINGS: Dict[str, str] = {
    "start_text": (
        "Assalomu alaykum! Bu bot orqali Instagram va TikTok videolarini tez va sifatli yuklab olishingiz mumkin."
    ),
    "subscribe_text": (
        "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling va '✅ Tekshirish' tugmasini bosing."
    ),
    "no_channels_text": "Hozircha majburiy obuna kanallari yo'q.",
    "share_button_enabled": "1",
    "share_button_text": "♻️ Do'stlarga ulashish",
    "share_button_url": "https://t.me/share/url",
}


def ensure_database() -> None:
    """Create required database and tables if they do not exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.cursor()
        cursor.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                join_date TEXT,
                last_active TEXT,
                downloads_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            );

            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                title TEXT,
                link TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        _populate_default_settings(connection)


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Provide a context-managed connection to the SQLite database."""
    ensure_database()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _populate_default_settings(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    for key, value in DEFAULT_SETTINGS.items():
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    connection.commit()


# --------------------------- User management --------------------------- #


def add_or_update_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO users (user_id, username, first_name, join_date, last_active)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_active=excluded.last_active
            """,
            (user_id, username, first_name, _now_iso(), _now_iso()),
        )
        connection.commit()


def update_last_active(user_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE users SET last_active = ? WHERE user_id = ?",
            (_now_iso(), user_id),
        )
        connection.commit()


def increment_downloads(user_id: int, count: int = 1) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE users SET downloads_count = downloads_count + ? WHERE user_id = ?",
            (count, user_id),
        )
        connection.commit()


def iter_user_ids() -> Iterator[int]:
    with get_connection() as connection:
        cursor = connection.execute("SELECT user_id FROM users")
        for row in cursor.fetchall():
            yield row["user_id"]


def get_user_counts() -> Dict[str, int]:
    with get_connection() as connection:
        cursor = connection.execute("SELECT COUNT(*) AS total FROM users")
        total_users = cursor.fetchone()["total"]

        today = datetime.now(timezone.utc).date()
        metrics = {}
        for label, delta in {"today": 1, "week": 7, "month": 30}.items():
            threshold = (today - timedelta(days=delta - 1)).isoformat()
            result = connection.execute(
                "SELECT COUNT(*) AS c FROM users WHERE DATE(last_active) >= ?",
                (threshold,),
            ).fetchone()
            metrics[label] = result["c"]

        downloads = connection.execute(
            "SELECT COALESCE(SUM(downloads_count), 0) AS total FROM users"
        ).fetchone()["total"]

    return {
        "total_users": total_users,
        "active_today": metrics["today"],
        "active_week": metrics["week"],
        "active_month": metrics["month"],
        "total_downloads": downloads,
    }


def get_users_join_dates(limit_days: int = 30) -> List[Dict[str, Any]]:
    threshold = (datetime.now(timezone.utc) - timedelta(days=limit_days)).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            "SELECT DATE(join_date) AS join_day FROM users WHERE join_date >= ?",
            (threshold,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_monthly_active_users(limit: int = 50) -> List[Dict[str, Any]]:
    threshold = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            SELECT user_id, username, first_name, last_active, downloads_count
            FROM users
            WHERE last_active >= ?
            ORDER BY datetime(last_active) DESC
            LIMIT ?
            """,
            (threshold, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_detailed_statistics() -> Dict[str, Any]:
    """Return comprehensive statistics for admin panel."""
    with get_connection() as connection:
        today = datetime.now(timezone.utc)
        
        # Basic counts
        basic_stats = connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        total_users = basic_stats["total"]
        
        # Download statistics
        download_stats = connection.execute(
            "SELECT COALESCE(SUM(downloads_count), 0) AS total, "
            "COALESCE(AVG(downloads_count), 0) AS avg "
            "FROM users WHERE downloads_count > 0"
        ).fetchone()
        
        # Daily activity for the last 30 days
        daily_activity = {}
        for days_back in range(30):
            date = (today - timedelta(days=days_back)).date().isoformat()
            count = connection.execute(
                "SELECT COUNT(*) AS c FROM users WHERE DATE(last_active) = ?",
                (date,)
            ).fetchone()["c"]
            daily_activity[date] = count
        
        # Hourly activity for today
        hourly_activity = {}
        today_str = today.date().isoformat()
        for hour in range(24):
            hour_start = f"{today_str} {hour:02d}:00:00"
            hour_end = f"{today_str} {hour:02d}:59:59"
            count = connection.execute(
                "SELECT COUNT(*) AS c FROM users WHERE last_active BETWEEN ? AND ?",
                (hour_start, hour_end)
            ).fetchone()["c"]
            hourly_activity[hour] = count
        
        # New users by day (last 7 days)
        new_users_weekly = {}
        for days_back in range(7):
            date = (today - timedelta(days=days_back)).date().isoformat()
            count = connection.execute(
                "SELECT COUNT(*) AS c FROM users WHERE DATE(join_date) = ?",
                (date,)
            ).fetchone()["c"]
            new_users_weekly[date] = count
        
        # Top downloaders
        top_downloaders = connection.execute(
            """
            SELECT username, first_name, downloads_count, user_id
            FROM users 
            WHERE downloads_count > 0 
            ORDER BY downloads_count DESC 
            LIMIT 5
            """
        ).fetchall()
        
        # Activity periods
        periods = {
            "today": 1, "yesterday": 2, "week": 7, 
            "month": 30, "3months": 90
        }
        activity_stats = {}
        
        for label, days in periods.items():
            if label == "yesterday":
                start_date = (today - timedelta(days=2)).date().isoformat()
                end_date = (today - timedelta(days=1)).date().isoformat()
                count = connection.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE DATE(last_active) BETWEEN ? AND ?",
                    (start_date, end_date)
                ).fetchone()["c"]
            else:
                threshold = (today - timedelta(days=days-1)).date().isoformat()
                count = connection.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE DATE(last_active) >= ?",
                    (threshold,)
                ).fetchone()["c"]
            activity_stats[label] = count
        
        return {
            "total_users": total_users,
            "total_downloads": int(download_stats["total"]),
            "avg_downloads": round(float(download_stats["avg"]), 1),
            "activity_stats": activity_stats,
            "daily_activity": daily_activity,
            "hourly_activity": hourly_activity,
            "new_users_weekly": new_users_weekly,
            "top_downloaders": [dict(row) for row in top_downloaders],
            "generated_at": today.isoformat()
        }


# --------------------------- Admin management -------------------------- #


def add_admin(user_id: int, username: Optional[str]) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO admins (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        connection.commit()


def remove_admin(user_id: int) -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        connection.commit()


def get_admins() -> List[Dict[str, Any]]:
    with get_connection() as connection:
        cursor = connection.execute("SELECT user_id, username FROM admins ORDER BY user_id")
        return [dict(row) for row in cursor.fetchall()]


def is_admin(user_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            "SELECT 1 FROM admins WHERE user_id = ?",
            (user_id,),
        )
        return cursor.fetchone() is not None


# -------------------------- Channel management ------------------------ #


def add_channel(channel_id: str, title: str, link: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO channels (channel_id, title, link) VALUES (?, ?, ?)",
            (channel_id, title, link),
        )
        connection.commit()


def remove_channel(channel_id: str) -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
        connection.commit()


def get_channels() -> List[Dict[str, Any]]:
    with get_connection() as connection:
        cursor = connection.execute(
            "SELECT channel_id, title, link FROM channels ORDER BY title COLLATE NOCASE"
        )
        return [dict(row) for row in cursor.fetchall()]


# --------------------------- Settings helpers ------------------------- #


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_connection() as connection:
        cursor = connection.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
    if row is None:
        return default
    return row["value"]


def set_setting(key: str, value: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        connection.commit()


# ----------------------------- Logging -------------------------------- #


def add_log(user_id: Optional[int], action: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO logs (user_id, action, created_at) VALUES (?, ?, ?)",
            (user_id, action, _now_iso()),
        )
        connection.commit()


def get_recent_logs(limit: int = 20) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        cursor = connection.execute(
            "SELECT user_id, action, created_at FROM logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


# --------------------------- Admin Permissions ------------------------- #


def get_admin_permissions(user_id: int) -> Dict[str, bool]:
    """Get all permissions for a specific admin."""
    with get_connection() as connection:
        cursor = connection.execute(
            """SELECT can_manage_users, can_manage_channels, can_broadcast, 
               can_view_stats, can_manage_admins FROM admins WHERE user_id = ?""",
            (user_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return {}
        return dict(row)


def update_admin_permission(user_id: int, permission: str, value: bool) -> None:
    """Update a specific permission for an admin."""
    allowed_permissions = [
        'can_manage_users', 'can_manage_channels', 'can_broadcast',
        'can_view_stats', 'can_manage_admins'
    ]
    if permission not in allowed_permissions:
        raise ValueError(f"Invalid permission: {permission}")
    
    with get_connection() as connection:
        connection.execute(
            f"UPDATE admins SET {permission} = ? WHERE user_id = ?",
            (1 if value else 0, user_id)
        )
        connection.commit()


def has_permission(user_id: int, permission: str) -> bool:
    """Check if an admin has a specific permission."""
    perms = get_admin_permissions(user_id)
    return perms.get(permission, False) == 1
