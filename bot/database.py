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
    """Создаёт таблицы в SQLite (если их ещё нет) и применяет простые миграции."""
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

        # Миграции для заказов (старые БД без новых полей)
        await _ensure_column(db, "orders", "extras_json", "TEXT NOT NULL DEFAULT '[]'")
        await _ensure_column(db, "orders", "drink_subtotal", "INTEGER")
        await _ensure_column(db, "orders", "preparation_comment", "TEXT NOT NULL DEFAULT ''")
        await _ensure_column(db, "orders", "payment_claimed", "INTEGER NOT NULL DEFAULT 0")
        await _ensure_column(db, "orders", "drinks_json", "TEXT NOT NULL DEFAULT '[]'")
        await _ensure_column(db, "orders", "drinks_subtotal", "INTEGER NOT NULL DEFAULT 0")

        await db.commit()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def list_active_snacks() -> list[dict]:
    """Активные позиции витрины (закуски / выпечка)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, name, price FROM snacks WHERE active = 1 ORDER BY id ASC",
        )
        rows = await cur.fetchall()
        return [{"id": int(r["id"]), "name": str(r["name"]), "price": int(r["price"])} for r in rows]


async def add_snack(*, name: str, price: int) -> int:
    """Добавляет позицию на витрину, возвращает id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO snacks (name, price, active, created_at) VALUES (?, ?, 1, ?)",
            (name.strip(), price, _utc_now_iso()),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_all_snacks_barista() -> list[dict]:
    """Все позиции (для баристы)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, name, price, active FROM snacks ORDER BY id ASC",
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def deactivate_snack(snack_id: int) -> bool:
    """Скрывает позицию с витрины (active=0)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE snacks SET active = 0 WHERE id = ?",
            (snack_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def create_order(
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    drink_key: str,
    drink_name: str,
    size_key: str,
    size_ml: int,
    ready_time: str,
    drink_subtotal: int,
    preparation_comment: str,
    extras: list[dict],
    total_price: int,
) -> int:
    """Сохраняет заказ в БД и возвращает его id."""
    extras_json = json.dumps(extras, ensure_ascii=False)
    preparation_comment = (preparation_comment or "").strip()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders (
                telegram_user_id,
                telegram_username,
                drink_key,
                drink_name,
                size_key,
                size_ml,
                ready_time,
                drink_subtotal,
                preparation_comment,
                extras_json,
                price,
                status,
                payment_claimed,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment', 0, ?)
            """,
            (
                telegram_user_id,
                telegram_username,
                drink_key,
                drink_name,
                size_key,
                size_ml,
                ready_time,
                drink_subtotal,
                preparation_comment,
                extras_json,
                total_price,
                _utc_now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def create_order_multi_drinks(
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    drinks: list[dict],
    ready_time: str,
    preparation_comment: str,
    extras: list[dict],
    total_price: int,
) -> int:
    """Сохраняет заказ с несколькими напитками в БД и возвращает его id."""
    drinks_json = json.dumps(drinks, ensure_ascii=False)
    extras_json = json.dumps(extras, ensure_ascii=False)
    preparation_comment = (preparation_comment or "").strip()
    drinks_subtotal = sum(int(d.get("price", 0)) for d in drinks)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders (
                telegram_user_id,
                telegram_username,
                drink_key,
                drink_name,
                size_key,
                size_ml,
                ready_time,
                drink_subtotal,
                drinks_json,
                drinks_subtotal,
                preparation_comment,
                extras_json,
                price,
                status,
                payment_claimed,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment', 0, ?)
            """,
            (
                telegram_user_id,
                telegram_username,
                drinks[0].get("key", "") if drinks else "",
                drinks[0].get("name", "") if drinks else "",
                drinks[0].get("size_key", "") if drinks else "",
                drinks[0].get("size_ml", 0) if drinks else 0,
                ready_time,
                drinks[0].get("price", 0) if drinks else 0,
                drinks_json,
                drinks_subtotal,
                preparation_comment,
                extras_json,
                total_price,
                _utc_now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


def parse_extras_json(row: dict) -> list[dict]:
    raw = row.get("extras_json") or "[]"
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def parse_drinks_json(row: dict) -> list[dict]:
    """Парсит drinks_json из заказа. Возвращает список напитков."""
    raw = row.get("drinks_json") or "[]"
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


async def get_order(order_id: int) -> dict[str, object] | None:
    """Возвращает заказ по id (extras_json и drinks_json остаются строками; при необходимости парсите через parse_extras_json/parse_drinks_json)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,),
        )
        rec = await cur.fetchone()
        if not rec:
            return None
        return dict(rec)


async def claim_payment_by_client(order_id: int, telegram_user_id: int) -> str:
    """
    Клиент нажал «Я оплатил».
    Возвращает: 'ok' | 'already_claimed' | 'already_paid' | 'not_found' | 'wrong_user'
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = await cur.fetchone()
        if not row:
            return "not_found"
        o = dict(row)
        if int(o["telegram_user_id"]) != int(telegram_user_id):
            return "wrong_user"
        if str(o["status"]) == "paid":
            return "already_paid"
        if int(o.get("payment_claimed") or 0) == 1:
            return "already_claimed"

        await db.execute(
            "UPDATE orders SET payment_claimed = 1 WHERE id = ? AND telegram_user_id = ? AND status = 'pending_payment'",
            (order_id, telegram_user_id),
        )
        await db.commit()
        return "ok"


async def barista_confirm_payment(order_id: int) -> bool:
    """Бариста подтверждает оплату (ставит status=paid)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE orders
            SET status = 'paid'
            WHERE id = ?
              AND status = 'pending_payment'
              AND payment_claimed = 1
            """,
            (order_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def barista_reject_payment_claim(order_id: int) -> bool:
    """Бариста не видит оплату — снимаем заявку клиента (снова можно нажать «Я оплатил»)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE orders
            SET payment_claimed = 0
            WHERE id = ?
              AND status = 'pending_payment'
              AND payment_claimed = 1
            """,
            (order_id,),
        )
        await db.commit()
        return cur.rowcount > 0


def order_drink_subtotal(order_row: dict) -> int:
    """Сумма только напитка(ов) (для старых строк без drink_subtotal или новых с drinks_subtotal)."""
    # Сначала проверяем новое поле drinks_subtotal
    drinks_subtotal = order_row.get("drinks_subtotal")
    if drinks_subtotal is not None and drinks_subtotal != "" and int(drinks_subtotal) > 0:
        return int(drinks_subtotal)
    
    # Затем проверяем старое поле drink_subtotal
    ds = order_row.get("drink_subtotal")
    if ds is not None and ds != "":
        try:
            return int(ds)
        except (TypeError, ValueError):
            pass
    return int(order_row.get("price", 0))
