import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from database import claim_payment_by_client, create_order
from handlers.barista import notify_barista_payment_pending
from keyboards import (
    DRINK_CATEGORIES,
    DRINKS,
    PRICES,
    SIZES,
    SYRUP_PRICE,
    SYRUPS,
    kb_drink_categories,
    kb_drinks_in_category,
    kb_confirm_order,
    kb_main,
    kb_order_builder,
    kb_paid,
    kb_leave_preparation_comment,
    kb_ready_time,
    kb_sizes_for_drink,
    kb_start_panel,
    kb_cart_delete,
    kb_syrup_choice,
    kb_syrups,
)
from states import OrderStates


router = Router(name="client")

# Астана: фиксированный GMT+5 (не зависит от tzdata на сервере)
ASTANA_TZ = timezone(timedelta(hours=5))
DEFAULT_KASPI_URL = "https://pay.kaspi.kz/pay/h8xmix5d"

# Текст-инструкция, который показывает бот перед началом оформления
WELCOME_TEXT = (
    "Как оформить заказ? ☕\n\n"
    "Нажмите «Сделать заказ».\n"
    "Выберите удобное время получения (по времени Астаны, GMT+5).\n"
    "Соберите заказ, добавив один или несколько напитков.\n"
    "После оформления вы получите ссылку для оплаты через Kaspi. "
    "Оплатите заказ и нажмите «Я оплатил (Kaspi)».\n\n"
    "После подтверждения оплаты бариста сразу увидит ваш заказ и, при необходимости, "
    "учтет ваши пожелания к приготовлению напитка.\n\n"
    "Спасибо, что выбираете Lenger! ❤️"
)

TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

# Фотографии меню хранятся внутри проекта: bot/assets/menu_1.png и menu_2.png
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
PREP_PHOTO_1_PATH = str(ASSETS_DIR / "menu_1.png")
PREP_PHOTO_2_PATH = str(ASSETS_DIR / "menu_2.png")


def _now_astana() -> datetime:
    """Текущее время в Астане (GMT+5)."""
    return datetime.now(ASTANA_TZ)


async def bot_send_menu_photos(*, bot: Bot, chat_id: int, state: FSMContext) -> None:
    """Отправляет 2 фото меню и сохраняет их message_id, чтобы потом удалить."""
    p1 = await bot.send_photo(chat_id=chat_id, photo=FSInputFile(PREP_PHOTO_1_PATH))
    p2 = await bot.send_photo(chat_id=chat_id, photo=FSInputFile(PREP_PHOTO_2_PATH))
    data = await state.get_data()
    ids = list(data.get("chat_cleanup_ids") or [])
    ids.extend([int(p1.message_id), int(p2.message_id)])
    await state.update_data(
        menu_photo_ids=[int(p1.message_id), int(p2.message_id)],
        chat_cleanup_ids=ids,
    )


def parse_hhmm(text: str) -> str | None:
    """Парсит время в формате ЧЧ:ММ (например 09:30) и возвращает строку "HH:MM"."""
    text = text.strip()
    m = TIME_RE.match(text)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if hh < 0 or hh > 23:
        return None
    if mm < 0 or mm > 59:
        return None
    return f"{hh:02d}:{mm:02d}"


def _get_cart(data: dict) -> list[dict]:
    return list(data.get("cart_items") or [])


def _cart_total(items: list[dict]) -> int:
    return sum(int(x.get("price", 0)) for x in items)


def _grouped_cart_lines(items: list[dict]) -> list[str]:
    """Строки состава заказа с количеством."""
    groups: dict[str, dict] = {}
    order_keys: list[str] = []
    for it in items:
        syrup = it.get("syrup") or ""
        key = f"d:{it.get('drink_key')}:{it.get('size_key')}:{syrup}"
        label = str(
            it.get("label")
            or f"{it.get('drink_name')} ({SIZES.get(it.get('size_key'), {}).get('label', it.get('size_key'))})"
        )
        price = int(it.get("price", 0))
        if key not in groups:
            groups[key] = {"label": label, "qty": 0, "subtotal": 0, "unit": price}
            order_keys.append(key)
        groups[key]["qty"] += 1
        groups[key]["subtotal"] += price

    lines: list[str] = []
    for key in order_keys:
        g = groups[key]
        if g["qty"] > 1:
            lines.append(f"• {g['label']} × {g['qty']} — {g['subtotal']} ₸")
        else:
            lines.append(f"• {g['label']} — {g['unit']} ₸")
    return lines


async def _alloc_uid(state: FSMContext) -> str:
    data = await state.get_data()
    n = int(data.get("cart_uid_seq") or 0) + 1
    await state.update_data(cart_uid_seq=n)
    return str(n)


async def _remember_cleanup_id(state: FSMContext, message_id: int) -> None:
    data = await state.get_data()
    ids = list(data.get("chat_cleanup_ids") or [])
    mid = int(message_id)
    if mid not in ids:
        ids.append(mid)
    await state.update_data(chat_cleanup_ids=ids)


async def _track_step_message(state: FSMContext, new_msg: Message) -> None:
    """
    Сохраняет id последнего «служебного» сообщения шага и удаляет предыдущее.
    Все id копятся для полной очистки чата в конце заказа.
    """
    data = await state.get_data()
    old_id = data.get("step_message_id")
    if old_id:
        try:
            await new_msg.bot.delete_message(chat_id=new_msg.chat.id, message_id=int(old_id))
        except Exception:
            pass
    await state.update_data(step_message_id=int(new_msg.message_id))
    await _remember_cleanup_id(state, int(new_msg.message_id))


async def _cleanup_order_chat(*, bot: Bot, chat_id: int, message_ids: list[int]) -> None:
    """Удаляет сообщения бота, накопленные за оформление заказа."""
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(mid))
        except Exception:
            pass


async def _show_confirmation(*, message: Message | None, chat_id: int, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    ready_label = data.get("ready_label")
    ready_time = data.get("ready_time")
    cart = _get_cart(data)

    if not (ready_label and ready_time and cart):
        await bot.send_message(chat_id, "Не удалось сформировать summary. Начните заказ заново: /start")
        return

    lines = _grouped_cart_lines(cart)
    total = _cart_total(cart)

    text = (
        "Подтвердите заказ:\n\n"
        + "\n".join(lines)
        + f"\n\nГотово к: {ready_label} ({ready_time})\n"
        f"Итого к оплате (Kaspi): {total} ₸"
    )

    await state.update_data(order_total=total)

    if message:
        sent = await message.answer(text, reply_markup=kb_confirm_order())
    else:
        sent = await bot.send_message(chat_id, text, reply_markup=kb_confirm_order())
    await _track_step_message(state, sent)


async def _show_builder_menu(anchor: Message, state: FSMContext) -> None:
    """Показывает меню конструктора заказа."""
    data = await state.get_data()
    cart = _get_cart(data)
    ready_label = data.get("ready_label")
    ready_time = data.get("ready_time")

    ready_status = "не выбрано"
    if ready_label and ready_time:
        ready_status = f"{ready_label} ({ready_time})"

    if cart:
        lines = _grouped_cart_lines(cart)
        items_block = "\n".join(lines)
        total = _cart_total(cart)
        items_section = f"Состав заказа:\n{items_block}\n\nИтого: {total} ₸"
    else:
        items_section = "Состав заказа: пока пусто"

    text = (
        "Соберите заказ:\n"
        f"{items_section}\n"
        f"Время готовности: {ready_status}"
    )
    sent = await anchor.answer(text, reply_markup=kb_order_builder())
    await _track_step_message(state, sent)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    """Старт по /start: показываем панель 'Начать' и лёгкое описание."""
    await state.clear()
    await message.answer(
        "Нажмите кнопку «Начать» внизу, чтобы увидеть инструкцию и начать оформление заказа.",
        reply_markup=kb_start_panel(),
    )


@router.message(F.text == "Начать")
async def start_via_button(message: Message, state: FSMContext) -> None:
    """Старт через кнопку 'Начать' на панели."""
    await state.clear()
    await state.set_state(OrderStates.waiting_for_builder)
    await state.update_data(telegram_user_id=message.from_user.id, telegram_username=message.from_user.username)

    sent = await message.answer(WELCOME_TEXT, reply_markup=kb_main())
    await _track_step_message(state, sent)


@router.callback_query(F.data == "order_start")
async def order_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OrderStates.waiting_for_ready_time)
    await state.update_data(
        telegram_user_id=callback.from_user.id,
        telegram_username=callback.from_user.username,
        cart_items=[],
        cart_uid_seq=0,
        chat_cleanup_ids=[],
    )

    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    sent = await callback.message.answer(
        "🕒 Выберите время, к которому ваш заказ должен быть готов.",
        reply_markup=kb_ready_time(),
    )
    await _track_step_message(state, sent)


@router.callback_query(F.data == "builder_add_coffee")
async def builder_add_coffee(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_builder.state:
        await callback.answer()
        return
    await callback.answer()
    await state.set_state(OrderStates.waiting_for_drink_category)
    sent = await callback.message.answer(
        "Выберите категорию напитков:",
        reply_markup=kb_drink_categories(),
    )
    await _track_step_message(state, sent)


@router.callback_query(F.data == "builder_finish")
async def builder_finish(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_builder.state:
        await callback.answer()
        return
    await callback.answer()
    data = await state.get_data()
    if not _get_cart(data):
        await callback.message.answer("Добавьте хотя бы один напиток в заказ.")
        return
    await state.set_state(OrderStates.waiting_for_preparation_comment_choice)
    sent = await callback.message.answer(
        "Хотите оставить комментарий баристе по приготовлению заказа?",
        reply_markup=kb_leave_preparation_comment(),
    )
    await _track_step_message(state, sent)


@router.callback_query(F.data == "builder_delete_item")
async def builder_delete_position(callback: CallbackQuery, state: FSMContext) -> None:
    """Удаление одной позиции из корзины."""
    if await state.get_state() != OrderStates.waiting_for_builder.state:
        await callback.answer()
        return
    await callback.answer()
    data = await state.get_data()
    cart = _get_cart(data)
    if not cart:
        await callback.message.answer("В заказе пока нет позиций, удалять нечего.")
        await _show_builder_menu(callback.message, state)
        return

    sent = await callback.message.answer(
        "Выберите позицию для удаления:",
        reply_markup=kb_cart_delete(cart),
    )
    await _track_step_message(state, sent)


@router.callback_query(F.data.startswith("cart_del:"))
async def cart_delete_item(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    uid = callback.data.split(":", 1)[1]

    data = await state.get_data()
    cart = _get_cart(data)
    new_cart = [x for x in cart if str(x.get("uid")) != uid]
    if len(new_cart) == len(cart):
        await callback.message.answer("Позиция уже удалена.")
    else:
        await state.update_data(cart_items=new_cart)
        try:
            await callback.message.delete()
        except Exception:
            pass

    await state.set_state(OrderStates.waiting_for_builder)
    await _show_builder_menu(callback.message, state)


@router.callback_query(F.data == "cart_del_cancel")
async def cart_delete_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.set_state(OrderStates.waiting_for_builder)
    await _show_builder_menu(callback.message, state)

@router.callback_query(F.data == "prep_comment_yes")
async def prep_comment_yes(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_preparation_comment_choice.state:
        await callback.answer()
        return
    await callback.answer()
    await state.set_state(OrderStates.waiting_for_preparation_comment_input)
    sent = await callback.message.answer("Напишите комментарий (например: без сахара, сделать посильнее и т.п.).")
    await _track_step_message(state, sent)


@router.callback_query(F.data == "prep_comment_no")
async def prep_comment_no(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_preparation_comment_choice.state:
        await callback.answer()
        return
    await callback.answer()
    await state.update_data(preparation_comment="")
    await state.set_state(OrderStates.waiting_for_confirmation)
    await _show_confirmation(
        message=callback.message,
        chat_id=callback.message.chat.id,
        state=state,
        bot=bot,
    )


@router.message(StateFilter(OrderStates.waiting_for_preparation_comment_input))
async def prep_comment_input(message: Message, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_preparation_comment_input.state:
        return
    await state.update_data(preparation_comment=(message.text or "").strip())
    await state.set_state(OrderStates.waiting_for_confirmation)
    await _show_confirmation(
        message=message,
        chat_id=message.chat.id,
        state=state,
        bot=bot,
    )


@router.callback_query(F.data.startswith("drink:"))
async def choose_drink(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_drink.state:
        await callback.answer()
        return

    await callback.answer()
    drink_key = callback.data.split(":", 1)[1]
    if drink_key not in DRINKS:
        await callback.message.answer("Неизвестный напиток. Попробуйте снова.")
        return

    await state.update_data(drink_key=drink_key, drink_name=DRINKS[drink_key])
    await state.set_state(OrderStates.waiting_for_size)
    sent = await callback.message.answer("Выберите объём:", reply_markup=kb_sizes_for_drink(drink_key))
    await _track_step_message(state, sent)


@router.callback_query(F.data.startswith("cat:"))
async def choose_drink_category(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_drink_category.state:
        await callback.answer()
        return

    await callback.answer()
    category_key = callback.data.split(":", 1)[1]
    await state.update_data(drink_category=category_key)
    await state.set_state(OrderStates.waiting_for_drink)
    sent = await callback.message.answer(
        "Выберите напиток:",
        reply_markup=kb_drinks_in_category(category_key),
    )
    await _track_step_message(state, sent)


async def _add_pending_drink_to_cart(state: FSMContext, *, syrup: str | None = None) -> None:
    """Добавляет в корзину напиток из pending_drink (с опциональным сиропом)."""
    data = await state.get_data()
    pending = data.get("pending_drink") or {}
    if not pending:
        return

    price = int(pending["price"])
    label = str(pending["label"])
    if syrup:
        price += int(SYRUP_PRICE)
        label = f"{label} + сироп {syrup}"

    uid = await _alloc_uid(state)
    cart = _get_cart(data)
    cart.append(
        {
            "uid": uid,
            "kind": "drink",
            "drink_key": pending["drink_key"],
            "drink_name": pending["drink_name"],
            "size_key": pending["size_key"],
            "size_ml": pending["size_ml"],
            "price": price,
            "label": label,
            "syrup": syrup,
        }
    )
    await state.update_data(cart_items=cart, pending_drink=None)


@router.callback_query(F.data.startswith("size:"))
async def choose_size(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_size.state:
        await callback.answer()
        return

    await callback.answer()
    size_key = callback.data.split(":", 1)[1]
    if size_key not in SIZES:
        await callback.message.answer("Неизвестный объём. Попробуйте снова.")
        return

    data = await state.get_data()
    drink_key = data.get("drink_key")
    if not drink_key or drink_key not in DRINKS:
        await callback.message.answer("Сначала выберите напиток. Нажмите /start и оформите заказ заново.")
        await state.clear()
        return

    drink_subtotal = PRICES[drink_key][size_key]
    size_label = str(SIZES[size_key]["label"])
    drink_name = str(DRINKS[drink_key])
    pending = {
        "drink_key": drink_key,
        "drink_name": drink_name,
        "size_key": size_key,
        "size_ml": int(SIZES[size_key]["ml"]),
        "price": int(drink_subtotal),
        "label": f"{drink_name} ({size_label})",
    }
    await state.update_data(pending_drink=pending)

    coffee_keys = set(DRINK_CATEGORIES.get("coffee", {}).get("drinks") or [])
    is_coffee = drink_key in coffee_keys or data.get("drink_category") == "coffee"

    if is_coffee:
        await state.set_state(OrderStates.waiting_for_syrup_choice)
        sent = await callback.message.answer(
            f"Добавить сироп к «{drink_name}»? (+{SYRUP_PRICE} тг)",
            reply_markup=kb_syrup_choice(),
        )
        await _track_step_message(state, sent)
        return

    await _add_pending_drink_to_cart(state, syrup=None)
    await state.set_state(OrderStates.waiting_for_builder)
    await _show_builder_menu(callback.message, state)


@router.callback_query(F.data == "syrup_yes")
async def syrup_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_syrup_choice.state:
        await callback.answer()
        return
    await callback.answer()
    await state.set_state(OrderStates.waiting_for_syrup)
    sent = await callback.message.answer(
        f"Выберите сироп (+{SYRUP_PRICE} тг):",
        reply_markup=kb_syrups(),
    )
    await _track_step_message(state, sent)


@router.callback_query(F.data == "syrup_no")
async def syrup_no(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_syrup_choice.state:
        await callback.answer()
        return
    await callback.answer()
    await _add_pending_drink_to_cart(state, syrup=None)
    await state.set_state(OrderStates.waiting_for_builder)
    await _show_builder_menu(callback.message, state)


@router.callback_query(F.data.startswith("syrup:"))
async def choose_syrup(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_syrup.state:
        await callback.answer()
        return
    await callback.answer()
    try:
        idx = int(callback.data.split(":", 1)[1])
        syrup_name = SYRUPS[idx]
    except (IndexError, ValueError):
        await callback.message.answer("Неизвестный сироп. Попробуйте снова.")
        return

    await _add_pending_drink_to_cart(state, syrup=syrup_name)
    await state.set_state(OrderStates.waiting_for_builder)
    await _show_builder_menu(callback.message, state)


@router.callback_query(F.data.startswith("ready:"))
async def choose_ready_time(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_ready_time.state:
        await callback.answer()
        return

    await callback.answer()
    minutes = int(callback.data.split(":", 1)[1])
    if minutes not in (15, 30, 60):
        await callback.message.answer("Некорректный выбор времени. Попробуйте снова.")
        return

    ready_dt = _now_astana() + timedelta(minutes=minutes)
    ready_time = ready_dt.strftime("%H:%M")

    # Проверка: время приготовления только в диапазоне 08:00–22:00.
    hour = int(ready_time.split(":", 1)[0])
    if hour < 8 or hour >= 22:
        await callback.message.answer(
            "Время приготовления доступно только с 08:00 до 22:00.\n"
            "Пожалуйста, выберите другой вариант времени."
        )
        return

    if minutes == 60:
        ready_label = "Через 1 час"
    else:
        ready_label = f"Через {minutes} мин"

    await state.update_data(ready_time=ready_time, ready_label=ready_label)
    await state.set_state(OrderStates.waiting_for_builder)
    # После настройки времени: 2 фото меню + стандартное окно конструктора.
    await bot_send_menu_photos(bot=bot, chat_id=callback.message.chat.id, state=state)
    await _show_builder_menu(callback.message, state)


@router.callback_query(F.data == "ready_manual")
async def choose_manual_ready_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_ready_time.state:
        await callback.answer()
        return

    await callback.answer()
    await state.set_state(OrderStates.waiting_for_manual_time)
    await callback.message.answer("Укажите время готовности в формате ЧЧ:ММ (например 14:30).")


@router.message(StateFilter(OrderStates.waiting_for_manual_time))
async def manual_ready_time_input(message: Message, state: FSMContext, bot: Bot) -> None:
    ready_time = parse_hhmm(message.text or "")
    if not ready_time:
        await message.answer("Неверный формат. Введите время как ЧЧ:ММ (пример: 09:30).")
        return

    # Проверка диапазона 08:00–22:00 для ручного ввода.
    hour = int(ready_time.split(":", 1)[0])
    if hour < 8 or hour >= 22:
        await message.answer("Время приготовления доступно только с 08:00 до 22:00. Введите другое время.")
        return

    await state.update_data(ready_time=ready_time, ready_label="Указано вручную")
    await state.set_state(OrderStates.waiting_for_builder)
    await bot_send_menu_photos(bot=bot, chat_id=message.chat.id, state=state)
    await _show_builder_menu(message, state)


@router.callback_query(F.data == "cancel")
async def cancel_order(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    data = await state.get_data()
    cleanup_ids = list(data.get("chat_cleanup_ids") or [])
    if callback.message:
        cleanup_ids.append(int(callback.message.message_id))
    await state.clear()
    await _cleanup_order_chat(
        bot=bot,
        chat_id=callback.message.chat.id,
        message_ids=cleanup_ids,
    )
    await callback.message.answer(
        "Заказ отменён. Нажмите кнопку «Начать», чтобы оформить новый.",
        reply_markup=kb_start_panel(),
    )


@router.callback_query(F.data == "confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_confirmation.state:
        await callback.answer()
        return

    await callback.answer()
    data = await state.get_data()

    ready_time = data.get("ready_time")
    ready_label = data.get("ready_label") or ""
    cart = _get_cart(data)
    preparation_comment = str(data.get("preparation_comment") or "")
    telegram_username = callback.from_user.username
    telegram_user_id = callback.from_user.id
    cleanup_ids = list(data.get("chat_cleanup_ids") or [])
    if callback.message:
        cleanup_ids.append(int(callback.message.message_id))

    if not (ready_time and cart):
        await state.clear()
        await callback.message.answer(
            "Не удалось подтвердить заказ. Нажмите «Начать» и оформите заново.",
            reply_markup=kb_start_panel(),
        )
        return

    total = _cart_total(cart)
    lines = _grouped_cart_lines(cart)

    order_id = await create_order(
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        ready_time=str(ready_time),
        preparation_comment=preparation_comment,
        items=cart,
        total_price=total,
    )

    await state.clear()
    await _cleanup_order_chat(
        bot=bot,
        chat_id=callback.message.chat.id,
        message_ids=cleanup_ids,
    )

    kaspi_url = (os.getenv("KASPI_PAY_URL") or DEFAULT_KASPI_URL).strip()
    text = (
        f"✅ Заказ №{order_id} подтверждён\n\n"
        f"Состав:\n"
        + "\n".join(lines)
        + f"\n\nГотово к: {ready_label} ({ready_time})\n"
        f"Итого к оплате: {total} ₸\n\n"
        f"Ссылка для оплаты:\n{kaspi_url}\n\n"
        "Откройте ссылку, введите сумму вручную в Kaspi.\n"
        "После оплаты нажмите «Я оплатил (Kaspi)»."
    )
    await callback.message.answer(text, reply_markup=kb_paid(order_id))
    # Панель «Начать» для следующего заказа (клавиатура останется и после удаления служебного сообщения).
    kb_msg = await bot.send_message(
        chat_id=callback.message.chat.id,
        text="Для нового заказа нажмите «Начать».",
        reply_markup=kb_start_panel(),
    )
    try:
        await bot.delete_message(chat_id=callback.message.chat.id, message_id=kb_msg.message_id)
    except Exception:
        pass


@router.callback_query(F.data.startswith("paid:"))
async def paid(callback: CallbackQuery, bot: Bot) -> None:
    """Клиент сообщил об оплате — дальше проверка баристой."""
    await callback.answer()
    try:
        order_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.message.answer("Некорректный идентификатор заказа.")
        return

    telegram_user_id = callback.from_user.id
    res = await claim_payment_by_client(order_id=order_id, telegram_user_id=telegram_user_id)

    if res == "ok":
        await callback.message.answer(
            "Заявка на оплату отправлена баристе. Ожидайте подтверждения (обычно это быстро)."
        )
        barista_raw = os.getenv("BARISTA_CHAT_ID")
        if barista_raw:
            await notify_barista_payment_pending(
                bot=bot,
                barista_chat_id=int(barista_raw),
                order_id=order_id,
            )
        return

    if res == "already_claimed":
        await callback.message.answer("Вы уже отправили заявку на оплату. Ждём подтверждения баристы.")
        return
    if res == "already_paid":
        await callback.message.answer("Этот заказ уже оплачен и подтверждён.")
        return
    if res == "wrong_user":
        await callback.message.answer("Это не ваш заказ.")
        return

    await callback.message.answer("Заказ не найден.")
