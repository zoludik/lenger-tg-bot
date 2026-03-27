from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


DB_PATH = Path(__file__).resolve().parent / "orders.db"


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, declaration: str) -> None:
    """Добавляет колонку, если её ещё нет (простая миграция для SQLite)."""
    cur = await db.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    existing = {r[1] for r in rows}
    if column not in existing:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


async def init_db() -> None:
    """Создаёт таблицы в SQLite и применяет миграции."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                telegram_username TEXT,
                drink_key TEXT NOT NULL,
                drink_name TEXT NOT NULL,
                size_key TEXT NOT NULL,
                size_ml INTEGER NOT NULL,
                ready_time TEXT NOT NULL,
                preparation_comment TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending_payment','paid')),
                created_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS snacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(telegram_user_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_snacks_active ON snacks(active);")

        # Миграции
        await _ensure_column(db, "orders", "extras_json", "TEXT NOT NULL DEFAULT '[]'")
        await _ensure_column(db, "orders", "drink_subtotal", "INTEGER")
        await _ensure_column(db, "orders", "preparation_comment", "TEXT NOT NULL DEFAULT ''")
        await _ensure_column(db, "orders", "payment_claimed", "INTEGER NOT NULL DEFAULT 0")
        # НОВЫЕ КОЛОНКИ ДЛЯ МНОЖЕСТВЕННЫХ НАПИТКОВ
        await _ensure_column(db, "orders", "drinks_json", "TEXT DEFAULT '[]'")
        await _ensure_column(db, "orders", "drinks_subtotal", "INTEGER DEFAULT 0")

        await db.commit()
