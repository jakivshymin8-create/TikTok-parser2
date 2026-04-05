"""
SQLite база данных для TikTok парсера.

Таблицы:
  checked_usernames — все проверенные аккаунты (блеклист между запусками)
  found_accounts    — найденные трафер-аккаунты с полной информацией

Использование:
  from src.database import init_db, is_checked, mark_checked, save_account, print_stats
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "user_data" / "accounts.db"


def init_db() -> None:
    """Создаёт БД и таблицы если не существуют. Вызывать при старте."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS checked_usernames (
            username    TEXT PRIMARY KEY,
            checked_at  TEXT NOT NULL,
            result      TEXT NOT NULL DEFAULT 'SKIPPED'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS found_accounts (
            username    TEXT PRIMARY KEY,
            avg_views   INTEGER,
            score       INTEGER,
            bio         TEXT,
            ai_result   TEXT,
            found_at    TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def is_checked(username: str) -> bool:
    """True если аккаунт уже проверялся (любой результат)."""
    if not username:
        return False
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("SELECT 1 FROM checked_usernames WHERE username = ?", (username,))
        found = c.fetchone() is not None
        conn.close()
        return found
    except Exception:
        return False


def mark_checked(username: str, result: str = "SKIPPED") -> None:
    """Помечает аккаунт как проверенный. result: SKIPPED / NOT_TRAFFER / TRAFFER."""
    if not username:
        return
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO checked_usernames (username, checked_at, result) VALUES (?, ?, ?)",
            (username, datetime.now().isoformat(), result),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[db] mark_checked error: {e}")


def save_account(username: str, avg_views: int, score: int, bio: str, ai_result: str) -> None:
    """Сохраняет найденный трафер-аккаунт."""
    if not username:
        return
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            """INSERT OR REPLACE INTO found_accounts
               (username, avg_views, score, bio, ai_result, found_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (username, avg_views, score, bio[:300], ai_result, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[db] save_account error: {e}")


def print_stats() -> None:
    """Выводит статистику базы данных."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM checked_usernames")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM checked_usernames WHERE result = 'TRAFFER'")
        found = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM checked_usernames WHERE result = 'NOT_TRAFFER'")
        not_traffer = c.fetchone()[0]
        conn.close()
        print(f"[db] Всего проверено: {total} | Трафер: {found} | Не трафер: {not_traffer} | Скипнуто: {total - found - not_traffer}")
    except Exception as e:
        print(f"[db] stats error: {e}")
