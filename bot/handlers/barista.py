from bot.database import (
    parse_extras_json,
    add_snack,
    barista_confirm_payment,
    barista_reject_payment_claim,
    deactivate_snack,
    get_order,
    list_all_snacks_barista,
    order_drink_subtotal,
)

from bot.keyboards import kb_barista_payment_review
router = Router(name="barista")

ADD_SNACK_RE = re.compile(r"^\s*(\d+)\s+(.+)$")


def _barista_chat_id() -> int | None:
    raw = os.getenv("BARISTA_CHAT_ID")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_barista_chat(chat_id: int) -> bool:
    bid = _barista_chat_id()
    return bid is not None and int(chat_id) == bid


def format_order_full_text(order_row: dict, *, include_comment: bool = False) -> str:
    """Текст заказа для баристы (напиток, закуски, сумма, время, клиент, при необходимости комментарий)."""
    oid = int(order_row["id"])
    drink = str(order_row["drink_name"])
    size_key = str(order_row["size_key"])
    from keyboards import SIZES  # локальный импорт, чтобы избежать циклов

    size_label = str(SIZES.get(size_key, {}).get("label", size_key))
    ready = str(order_row["ready_time"])
    handle = order_row.get("telegram_username")
    uid = int(order_row["telegram_user_id"])
    user_ref = f"@{handle}" if handle else f"id:{uid}"

    drink_part = order_drink_subtotal(order_row)
    extras = parse_extras_json(order_row)
    extras_lines = ""
    if extras:
        lines = [f"  • {x.get('name', '?')} — {x.get('price', 0)} ₸" for x in extras]
        extras_lines = "\nЗакуски / выпечка:\n" + "\n".join(lines)
    total = int(order_row["price"])

    comment_lines = ""
    if include_comment:
        c = str(order_row.get("preparation_comment") or "").strip()
        if c:
            comment_lines = f"\nКомментарий клиента: {c}"

    return (
        f"☕ Заказ #{oid}\n"
        f"Напиток: {drink} ({size_label})\n"
        f"Сумма напитка: {drink_part} ₸{extras_lines}\n"
        f"Итого к оплате (Kaspi): {total} ₸\n"
        f"Готово к: {ready}\n"
        f"Клиент: {user_ref}"
        f"{comment_lines}"
    )


async def notify_barista_payment_pending(*, bot: Bot, barista_chat_id: int, order_id: int) -> None:
    """Клиент заявил об оплате — бариста проверяет и подтверждает."""
    row = await get_order(order_id)
    if not row:
        return
    text = (
        "💳 Проверьте оплату в Kaspi.\n"
        "Клиент нажал «Я оплатил».\n\n"
        f"{format_order_full_text(row, include_comment=False)}"
    )
    await bot.send_message(
        chat_id=barista_chat_id,
        text=text,
        reply_markup=kb_barista_payment_review(order_id),
    )


async def notify_barista_order_finalized(*, bot: Bot, barista_chat_id: int, order_id: int) -> None:
    """Оплата подтверждена — итоговый заказ на приготовление."""
    row = await get_order(order_id)
    if not row:
        return
    text = "✅ Оплата подтверждена. Готовить:\n\n" + format_order_full_text(row, include_comment=True)
    await bot.send_message(chat_id=barista_chat_id, text=text)


@router.message(Command("help_barista"))
async def help_barista(message: Message) -> None:
    if not _is_barista_chat(message.chat.id):
        return
    await message.answer(
        "Команды баристы:\n"
        "/add_snack <цена> <название> — добавить на витрину\n"
        "/snacks — список всех позиций\n"
        "/del_snack <id> — убрать с витрины (скрыть)\n"
    )


@router.message(Command("add_snack"))
async def cmd_add_snack(message: Message) -> None:
    if not _is_barista_chat(message.chat.id):
        return
    text = (message.text or "").replace("/add_snack", "", 1).strip()
    m = ADD_SNACK_RE.match(text)
    if not m:
        await message.answer("Формат: /add_snack 450 Круассан")
        return
    price = int(m.group(1))
    name = m.group(2).strip()
    if price <= 0 or not name:
        await message.answer("Цена должна быть > 0, название не пустое.")
        return
    sid = await add_snack(name=name, price=price)
    await message.answer(f"Добавлено на витрину: #{sid} — {name} ({price} ₸)")


@router.message(Command("snacks"))
async def cmd_list_snacks(message: Message) -> None:
    if not _is_barista_chat(message.chat.id):
        return
    rows = await list_all_snacks_barista()
    if not rows:
        await message.answer("Витрина пустая. Добавьте: /add_snack 450 Круассан")
        return
    lines = []
    for r in rows:
        st = "в продаже" if int(r["active"]) == 1 else "скрыто"
        lines.append(f"#{r['id']} — {r['name']} ({r['price']} ₸) [{st}]")
    await message.answer("Позиции:\n" + "\n".join(lines))


@router.message(Command("del_snack"))
async def cmd_del_snack(message: Message) -> None:
    if not _is_barista_chat(message.chat.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Формат: /del_snack 3")
        return
    sid = int(parts[1].strip())
    ok = await deactivate_snack(sid)
    if ok:
        await message.answer(f"Позиция #{sid} скрыта с витрины.")
    else:
        await message.answer("Не найдено или уже скрыто.")


@router.callback_query(F.data.startswith("b_pay_ok:"))
async def cb_pay_ok(callback: CallbackQuery, bot: Bot) -> None:
    if not _is_barista_chat(callback.message.chat.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    try:
        oid = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка id.", show_alert=True)
        return

    ok = await barista_confirm_payment(oid)
    await callback.answer()
    if not ok:
        await callback.message.answer("Не удалось подтвердить (уже оплачен или нет заявки клиента).")
        return

    bid = _barista_chat_id()
    if bid is None:
        return

    row = await get_order(oid)

    # Клиенту — подтверждение
    if row:
        uid = int(row["telegram_user_id"])
        try:
            await bot.send_message(
                uid,
                f"✅ Оплата подтверждена баристой. Заказ №{oid} принят в работу. Спасибо!",
            )
        except Exception:
            pass

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Оплата по заказу #{oid} отмечена.")

    await notify_barista_order_finalized(bot=bot, barista_chat_id=bid, order_id=oid)


@router.callback_query(F.data.startswith("b_pay_bad:"))
async def cb_pay_bad(callback: CallbackQuery, bot: Bot) -> None:
    if not _is_barista_chat(callback.message.chat.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    try:
        oid = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка id.", show_alert=True)
        return

    ok = await barista_reject_payment_claim(oid)
    await callback.answer()
    if not ok:
        await callback.message.answer("Не удалось снять заявку (возможно, уже подтверждено).")
        return

    row = await get_order(oid)
    if row:
        uid = int(row["telegram_user_id"])
        try:
            await bot.send_message(
                uid,
                "⚠️ Бариста не увидел оплату. Если вы точно оплатили в Kaspi — "
                "обратитесь на кассу или нажмите «Я оплатил» ещё раз после уточнения.",
            )
        except Exception:
            pass

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Заявка по заказу #{oid} снята. Клиент уведомлён.")
