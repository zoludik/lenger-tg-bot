def parse_drinks_json(raw: str | None) -> list[dict]:
    """Парсит JSON массив напитков."""
    if not raw:
        return []
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


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
    """Сохраняет заказ с НЕСКОЛЬКИМИ напитками."""
    drinks_json = json.dumps(drinks, ensure_ascii=False)
    extras_json = json.dumps(extras, ensure_ascii=False)
    preparation_comment = (preparation_comment or "").strip()
    drinks_subtotal = sum(int(d.get("price", 0)) for d in drinks)
    
    if drinks:
        first_drink = drinks[0]
        drink_key = first_drink.get("key", "")
        drink_name = first_drink.get("name", "")
        size_key = first_drink.get("size_key", "")
        size_ml = first_drink.get("size_ml", 0)
    else:
        drink_key = drink_name = size_key = ""
        size_ml = 0
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders (
                telegram_user_id, telegram_username, drink_key, drink_name,
                size_key, size_ml, ready_time, drink_subtotal, preparation_comment,
                extras_json, drinks_json, drinks_subtotal, price, status,
                payment_claimed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment', 0, ?)
            """,
            (
                telegram_user_id, telegram_username, drink_key, drink_name,
                size_key, size_ml, ready_time, drinks_subtotal, preparation_comment,
                extras_json, drinks_json, drinks_subtotal, total_price, _utc_now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)
