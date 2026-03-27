import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database import claim_payment_by_client, create_order, list_active_snacks
from handlers.barista import notify_barista_payment_pending
from keyboards import (
    DRINKS,
    PRICES,
    SIZES,
    kb_drink_categories,
    kb_drinks_in_category,
    kb_confirm_order,
    kb_main,
    kb_order_builder,
    kb_paid,
    kb_leave_preparation_comment,
    kb_ready_time,
    kb_sizes_for_drink,
    kb_snacks_empty_continue,
    kb_snacks_selection,
    kb_start_panel,
)
from states import OrderStates
from utils.qr import make_qr_bytes


router = Router(name="client")

# Текст-инструкция, который показывает бот перед началом оформления
WELCOME_TEXT = (
    "Как я работаю:\n"
    "1) Нажмите кнопку «Сделать заказ».\n"
    "2) Выберите время, когда вам удобно забрать напиток.\n"
    "3) В конструкторе соберите заказ: напиток, объём и еду с витрины.\n"
    "4) В конце получите QR для Kaspi, оплатите и нажмите «Я оплатил (Kaspi)».\n\n"
    "После подтверждения оплаты бариста получит ваш заказ и комментарий по приготовлению (если вы его оставите)."
)

TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

# Фотографии меню хранятся внутри проекта: bot/assets/menu_1.png и menu_2.png
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
PREP_PHOTO_1_PATH = str(ASSETS_DIR / "menu_1.png")
PREP_PHOTO_2_PATH = str(ASSETS_DIR / "menu_2.png")

async def bot_send_menu_photos(*, bot: Bot, chat_id: int) -> None:
    """Отправляет 2 фото меню клиенту."""
    await bot.send_photo(chat_id=chat_id, photo=FSInputFile(PREP_PHOTO_1_PATH))
    await bot.send_photo(chat_id=chat_id, photo=FSInputFile(PREP_PHOTO_2_PATH))


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


def _extras_total(selected: list[dict]) -> int:
    return sum(int(x.get("price", 0)) for x in selected)


async def _track_step_message(state: FSMContext, new_msg: Message) -> None:
    """
    Сохраняет id последнего «служебного» сообщения шага и удаляет предыдущее.
    Не вызывается для фото меню, QR и критичных статусов.
    """
    data = await state.get_data()
    old_id = data.get("step_message_id")
    if old_id:
        try:
            await new_msg.bot.delete_message(chat_id=new_msg.chat.id, message_id=int(old_id))
        except Exception:
            pass
    await state.update_data(step_message_id=int(new_msg.message_id))


async def _show_confirmation(*, message: Message | None, chat_id: int, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    drink_name = data.get("drink_name")
    size_key = data.get("size_key")
    ready_label = data.get("ready_label")
    ready_time = data.get("ready_time")
    drink_subtotal = data.get("drink_subtotal")
    selected_snacks: list[dict] = data.get("selected_snacks") or []

    if not (drink_name and size_key and ready_label and ready_time and drink_subtotal is not None):
        await bot.send_message(chat_id, "Не удалось сформировать summary. Начните заказ заново: /start")
        return

    size_label = SIZES[size_key]["label"]
    extras_lines = ""
    if selected_snacks:
        lines = [f"• {x.get('name')} — {x.get('price')} ₸" for x in selected_snacks]
        extras_lines = "\n" + "\n".join(lines) + "\n"

    total = int(drink_subtotal) + _extras_total(selected_snacks)

    text = (
        "Подтвердите заказ:\n\n"
        f"Напиток: {drink_name}\n"
        f"Объём: {size_label}\n"
        f"Напиток: {drink_subtotal} ₸{extras_lines}\n"
        f"Готово к: {ready_label} ({ready_time})\n"
        f"Итого к оплате (Kaspi): {total} ₸"
    )

    await state.update_data(order_total=total)

    if message:
        sent = await message.answer(text, reply_markup=kb_confirm_order())
    else:
        sent = await bot.send_message(chat_id, text, reply_markup=kb_confirm_order())
    await _track_step_message(state, sent)


async def _open_snacks_menu(anchor: Message, state: FSMContext, mode: str) -> None:
    """
    Шаг витрины: динамическое меню из БД (добавляет бариста).
    mode: 'add' — добавить еду; 'delete' — удалить выбранную еду.
    """
    data = await state.get_data()
    if "selected_snacks" not in data:
        await state.update_data(selected_snacks=[])
        data = await state.get_data()

    await state.update_data(snacks_mode=mode)
    await state.set_state(OrderStates.waiting_for_snacks)

    all_snacks = await list_active_snacks()
    chosen: list[dict] = data.get("selected_snacks") or []
    chosen_ids = {int(x["id"]) for x in chosen}

    if mode == "delete":
        # В режиме удаления показываем только те позиции, которые уже есть в заказе.
        snacks = [s for s in all_snacks if int(s["id"]) in chosen_ids]
        if not snacks:
            await anchor.answer("В заказе пока нет еды, нечего удалять.")
            await state.set_state(OrderStates.waiting_for_builder)
            await _show_builder_menu(anchor, state)
            return
        text = (
            "Удалите позиции еды из заказа.\n"
            "Нажмите на позицию, которую хотите убрать, — затем бот вернет вас в конструктор.\n\n"
            f"Сейчас выбрано позиций: {len(chosen)}"
        )
    else:
        # Режим добавления: показываем всю витрину.
        snacks = all_snacks
        if not snacks:
            await anchor.answer(
                "Сейчас нет закусок и выпечки на витрине.\nНажмите кнопку ниже, чтобы вернуться к заказу.",
                reply_markup=kb_snacks_empty_continue(),
            )
            return
        text = (
            "Добавьте закуски / выпечку: нажмите позицию, чтобы добавить в заказ.\n"
            "После выбора бот вернет вас в конструктор, чтобы продолжить оформление.\n\n"
            f"Уже в заказе: {len(chosen)} поз."
        )

    selected_ids = {int(x["id"]) for x in chosen}
    sent = await anchor.answer(
        text,
        reply_markup=kb_snacks_selection(snacks, selected_ids),
    )
    await _track_step_message(state, sent)


async def _show_builder_menu(anchor: Message, state: FSMContext) -> None:
    """Показывает меню конструктора заказа."""
    data = await state.get_data()
    drink_name = data.get("drink_name")
    size_key = data.get("size_key")
    selected_snacks: list[dict] = data.get("selected_snacks") or []
    ready_label = data.get("ready_label")
    ready_time = data.get("ready_time")

    coffee_status = "не выбрано"
    if drink_name and size_key:
        coffee_status = f"{drink_name} ({SIZES[size_key]['label']})"

    ready_status = "не выбрано"
    if ready_label and ready_time:
        ready_status = f"{ready_label} ({ready_time})"

    text = (
        "Соберите заказ:\n"
        f"Кофе: {coffee_status}\n"
        f"Еда: {len(selected_snacks)} поз.\n"
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
    await state.update_data(telegram_user_id=callback.from_user.id, telegram_username=callback.from_user.username)

    await callback.answer()
    sent = await callback.message.answer("Когда будет готово?", reply_markup=kb_ready_time())
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


@router.callback_query(F.data == "builder_add_food")
async def builder_add_food(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_builder.state:
        await callback.answer()
        return
    await callback.answer()
    await _open_snacks_menu(callback.message, state, mode="add")


@router.callback_query(F.data == "builder_finish")
async def builder_finish(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_builder.state:
        await callback.answer()
        return
    await callback.answer()
    data = await state.get_data()
    if not data.get("drink_key") or not data.get("size_key"):
        await callback.message.answer("Сначала добавьте кофе: напиток и объём.")
        return
    await state.set_state(OrderStates.waiting_for_preparation_comment_choice)
    sent = await callback.message.answer(
        "Хотите оставить комментарий баристе по приготовлению заказа?",
        reply_markup=kb_leave_preparation_comment(),
    )
    await _track_step_message(state, sent)


@router.callback_query(F.data == "builder_delete_food")
async def builder_delete_food(callback: CallbackQuery, state: FSMContext) -> None:
    """Удаление позиции: напитка или еды (по выбору клиента)."""
    if await state.get_state() != OrderStates.waiting_for_builder.state:
        await callback.answer()
        return
    await callback.answer()
    data = await state.get_data()
    has_drink = bool(data.get("drink_key") and data.get("size_key"))
    has_snacks = bool(data.get("selected_snacks"))

    if not has_drink and not has_snacks:
        await callback.message.answer("В заказе пока нет позиций, удалять нечего.")
        await _show_builder_menu(callback.message, state)
        return

    if has_drink and not has_snacks:
        # Удаляем только напиток.
        await state.update_data(
            drink_key=None,
            drink_name=None,
            size_key=None,
            size_ml=None,
            drink_subtotal=None,
        )
        await callback.message.answer("Напиток удалён из заказа.")
        await _show_builder_menu(callback.message, state)
        return

    if has_snacks and not has_drink:
        # Только еда — сразу переходим в режим удаления еды.
        await _open_snacks_menu(callback.message, state, mode="delete")
        return

    # И напиток, и еда — даём выбрать, что удалить.
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Удалить напиток", callback_data="del_pos:drink")],
            [InlineKeyboardButton(text="Удалить еду", callback_data="del_pos:snacks")],
        ]
    )
    await callback.message.answer("Что хотите удалить из заказа?", reply_markup=kb)


@router.callback_query(F.data.startswith("del_pos:"))
async def delete_position_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает выбор типа позиции для удаления (напиток или еда)."""
    if await state.get_state() != OrderStates.waiting_for_builder.state:
        await callback.answer()
        return
    await callback.answer()
    kind = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if kind == "drink":
        if not (data.get("drink_key") and data.get("size_key")):
            await callback.message.answer("Напиток в заказе не выбран.")
        else:
            await state.update_data(
                drink_key=None,
                drink_name=None,
                size_key=None,
                size_ml=None,
                drink_subtotal=None,
            )
            await callback.message.answer("Напиток удалён из заказа.")
        await _show_builder_menu(callback.message, state)
        return

    if kind == "snacks":
        if not data.get("selected_snacks"):
            await callback.message.answer("Еда в заказе ещё не выбрана, удалять нечего.")
            await _show_builder_menu(callback.message, state)
            return
        await _open_snacks_menu(callback.message, state, mode="delete")

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
    await state.set_state(OrderStates.waiting_for_drink)
    sent = await callback.message.answer(
        "Выберите напиток:",
        reply_markup=kb_drinks_in_category(category_key),
    )
    await _track_step_message(state, sent)


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
    await state.update_data(size_key=size_key, size_ml=int(SIZES[size_key]["ml"]), drink_subtotal=drink_subtotal)
    # Возвращаемся в конструктор. Время уже выбрано в начале.
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

    ready_dt = datetime.now() + timedelta(minutes=minutes)
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
    await bot_send_menu_photos(bot=bot, chat_id=callback.message.chat.id)
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
    await bot_send_menu_photos(bot=bot, chat_id=message.chat.id)
    await _show_builder_menu(message, state)


@router.callback_query(F.data.startswith("snack_toggle:"))
async def snack_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_snacks.state:
        await callback.answer()
        return

    try:
        sid = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer()
        return

    snacks = await list_active_snacks()
    item = next((s for s in snacks if s["id"] == sid), None)
    if not item:
        await callback.answer("Позиции уже нет на витрине.", show_alert=True)
        return

    data = await state.get_data()
    selected: list[dict] = list(data.get("selected_snacks") or [])
    mode = (data.get("snacks_mode") or "add").lower()

    if mode == "delete":
        # Удаляем все позиции с данным id из заказа.
        new_selected = [x for x in selected if int(x.get("id")) != sid]
        await state.update_data(selected_snacks=new_selected)
    else:
        # Режим добавления: при каждом нажатии добавляем ещё одну такую же позицию.
        selected.append({"id": item["id"], "name": item["name"], "price": item["price"]})
        await state.update_data(selected_snacks=selected)

    # После действия возвращаем клиента в окно конструктора.
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.set_state(OrderStates.waiting_for_builder)
    await _show_builder_menu(callback.message, state)


@router.callback_query(F.data == "snacks_done")
async def snacks_done(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != OrderStates.waiting_for_snacks.state:
        await callback.answer()
        return

    await callback.answer()
    await state.set_state(OrderStates.waiting_for_builder)
    await _show_builder_menu(callback.message, state)


@router.callback_query(F.data == "cancel")
async def cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Заказ отменён. Нажмите кнопку «Начать», чтобы оформить новый.")


@router.callback_query(F.data == "confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await state.get_state() != OrderStates.waiting_for_confirmation.state:
        await callback.answer()
        return

    await callback.answer()
    data = await state.get_data()

    drink_key = data.get("drink_key")
    drink_name = data.get("drink_name")
    size_key = data.get("size_key")
    size_ml = data.get("size_ml")
    ready_time = data.get("ready_time")
    drink_subtotal = data.get("drink_subtotal")
    selected_snacks: list[dict] = data.get("selected_snacks") or []
    preparation_comment = str(data.get("preparation_comment") or "")
    telegram_username = callback.from_user.username
    telegram_user_id = callback.from_user.id

    if not (drink_key and drink_name and size_key and size_ml and ready_time and drink_subtotal is not None):
        await state.clear()
        await callback.message.answer("Не удалось подтвердить заказ. Попробуйте оформить заново: /start")
        return

    total = int(drink_subtotal) + _extras_total(selected_snacks)

    order_id = await create_order(
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        drink_key=drink_key,
        drink_name=drink_name,
        size_key=size_key,
        size_ml=int(size_ml),
        ready_time=str(ready_time),
        drink_subtotal=int(drink_subtotal),
        preparation_comment=preparation_comment,
        extras=selected_snacks,
        total_price=total,
    )

    await state.clear()
    # Удаляем сообщение с подтверждением заказа (summary), чтобы не захламлять чат.
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Ссылка Kaspi (клиент вводит сумму вручную в приложении). QR кодирует только эту ссылку.
    kaspi_url = (os.getenv("KASPI_PAY_URL") or "https://pay.example.com/kaspi").strip()
    qr_png = make_qr_bytes(kaspi_url)
    qr_file = BufferedInputFile(qr_png, filename=f"order_{order_id}_kaspi.png")

    caption = (
        f"Заказ №{order_id}\n"
        f"Сумма к оплате в Kaspi: {total} ₸\n\n"
        "Отсканируйте QR и введите эту сумму вручную в приложении Kaspi.\n"
        "После оплаты нажмите «Я оплатил (Kaspi)» — бариста проверит платёж."
    )

    await callback.message.answer_photo(
        photo=qr_file,
        caption=caption,
        reply_markup=kb_paid(order_id),
    )


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
