from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


# Категории и напитки (ключ = используемое значение в колбэках и БД)
DRINKS: dict[str, str] = {
    # 1) КОФЕ
    "cappuccino": "Капучино",
    "latte": "Латте",
    "americano": "Американо",
    "flat_white": "Флэт Уайт",
    "mocha": "Мокко",
    "raf": "РАФ",
    "doppio": "Доппио",
    # 2) ГОРЯЧИЕ НАПИТКИ
    "cocoa": "Какао",
    "chocolate": "Шоколад",
    "matcha_latte": "Матча Латте",
    # 3) ГОРЯЧИЕ ЧАИ
    "ginger_tea": "Имбирный чай",
    "tashkent_tea": "Ташкентский чай",
    "sea_buckthorn_tea": "Облепиховый чай",
    "berry_tea": "Ягодный чай",
    "maroccan_tea": "Марокканский чай",
    "honey_pear_tea": "Медовая груша",
    # 4) ЛИМОНАДЫ
    "mango_marakuiya": "Манго-маракуйя",
    "strawberry": "Клубника",
    "watermelon": "Арбуз",
    "kiwi": "Киви",
    "blue_curacao": "Блю кюрасао",
    # 5) ФИРМЕННЫЕ НАПИТКИ
    "nut_cocoa": "Ореховый какао",
    "raf_halva": "РАФ халва",
    "bumble": "Бамбл",
    "flower_latte": "Цветочный латте",
    "karak_tea": "Карак чай",
    "raspberry_frappuchino": "Малиновый фраппучино",
    "milkshake": "Молочный коктейль",
}

DRINK_CATEGORIES: dict[str, dict[str, object]] = {
    "coffee": {"label": "КОФЕ", "drinks": ["cappuccino", "latte", "americano", "flat_white", "mocha", "raf", "doppio"]},
    "hot_drinks": {
        "label": "ГОРЯЧИЕ НАПИТКИ",
        "drinks": ["cocoa", "chocolate", "matcha_latte"],
    },
    "hot_teas": {
        "label": "ГОРЯЧИЕ ЧАИ",
        "drinks": [
            "ginger_tea",
            "tashkent_tea",
            "sea_buckthorn_tea",
            "berry_tea",
            "maroccan_tea",
            "honey_pear_tea",
        ],
    },
    "lemonades": {
        "label": "ЛИМОНАДЫ",
        "drinks": ["mango_marakuiya", "strawberry", "watermelon", "kiwi", "blue_curacao"],
    },
    "signature": {
        "label": "ФИРМЕННЫЕ НАПИТКИ",
        "drinks": [
            "nut_cocoa",
            "raf_halva",
            "bumble",
            "flower_latte",
            "karak_tea",
            "raspberry_frappuchino",
            "milkshake",
        ],
    },
}


# Объёмы (ключ = используемое значение в колбэках и БД)
SIZES: dict[str, dict[str, object]] = {
    # старые ключи (оставлены для совместимости с уже созданными заказами)
    "S": {"label": "250 мл", "ml": 250},
    "M": {"label": "350 мл", "ml": 350},
    "L": {"label": "450 мл", "ml": 450},
    # новые объёмы
    "60": {"label": "60 мл", "ml": 60},
    "400": {"label": "400 мл", "ml": 400},
}


# Цены (легко менять)
# Важно: цена зависит от напитка и объема.
PRICES: dict[str, dict[str, int]] = {
    # 1) КОФЕ
    "cappuccino": {"S": 1050, "M": 1150, "L": 1200},
    "latte": {"M": 1150, "L": 1200},
    "americano": {"S": 700, "M": 900, "L": 1000},
    "flat_white": {"S": 1050},
    "mocha": {"M": 1300},
    "raf": {"M": 1200},
    "doppio": {"60": 900},
    # 2) ГОРЯЧИЕ НАПИТКИ
    "cocoa": {"M": 990},
    "chocolate": {"M": 1200},
    "matcha_latte": {"M": 1100},
    # 3) ГОРЯЧИЕ ЧАИ
    "ginger_tea": {"L": 1200},
    "tashkent_tea": {"L": 1200},
    "sea_buckthorn_tea": {"L": 1200},
    "berry_tea": {"L": 1200},
    "maroccan_tea": {"L": 1200},
    "honey_pear_tea": {"L": 1200},
    # 4) ЛИМОНАДЫ
    "mango_marakuiya": {"400": 1100},
    "strawberry": {"400": 1100},
    "watermelon": {"400": 1100},
    "kiwi": {"400": 1100},
    "blue_curacao": {"400": 1100},
    # 5) ФИРМЕННЫЕ НАПИТКИ
    "nut_cocoa": {"M": 1190},
    "raf_halva": {"M": 1300},
    "bumble": {"M": 1500},
    "flower_latte": {"M": 1350},
    "karak_tea": {"M": 1200},
    "raspberry_frappuchino": {"400": 1800},
    "milkshake": {"400": 1600},
}


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сделать заказ", callback_data="order_start")],
        ]
    )


def kb_start_panel() -> ReplyKeyboardMarkup:
    """Нижняя панель с кнопкой 'Начать' вместо ввода /start."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Начать")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def kb_order_builder() -> InlineKeyboardMarkup:
    """Меню конструктора: клиент поэтапно собирает заказ."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить напиток", callback_data="builder_add_coffee")],
            [InlineKeyboardButton(text="Добавить еду", callback_data="builder_add_food")],
            [InlineKeyboardButton(text="Удалить позицию", callback_data="builder_delete_food")],
            [InlineKeyboardButton(text="Завершить заказ", callback_data="builder_finish")],
        ]
    )

def kb_drink_categories() -> InlineKeyboardMarkup:
    """5 кнопок-категорий напитков."""
    order: list[str] = ["coffee", "hot_drinks", "hot_teas", "lemonades", "signature"]
    buttons = [InlineKeyboardButton(text=DRINK_CATEGORIES[k]["label"], callback_data=f"cat:{k}") for k in order]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [buttons[0], buttons[1]],
            [buttons[2], buttons[3]],
            [buttons[4]],
        ]
    )


def kb_drinks_in_category(category_key: str) -> InlineKeyboardMarkup:
    """Список напитков для выбранной категории."""
    cat = DRINK_CATEGORIES.get(category_key)
    if not cat:
        # fallback: пусто, чтобы не падать
        return InlineKeyboardMarkup(inline_keyboard=[])
    drink_keys: list[str] = list(cat["drinks"])  # type: ignore[assignment]
    rows: list[list[InlineKeyboardButton]] = []
    for dk in drink_keys:
        name = str(DRINKS.get(dk, dk))
        rows.append([InlineKeyboardButton(text=name, callback_data=f"drink:{dk}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_sizes_for_drink(drink_key: str) -> InlineKeyboardMarkup:
    """Кнопки доступных объемов только для конкретного напитка."""
    prices = PRICES.get(drink_key) or {}
    # порядок по ml (чтобы 60 мл был внизу/сверху как вам удобно)
    size_keys = sorted(prices.keys(), key=lambda sk: int(SIZES.get(sk, {}).get("ml", 0)))
    rows: list[list[InlineKeyboardButton]] = []
    for sk in size_keys:
        ml = int(SIZES[sk]["ml"])
        price = int(prices[sk])
        label = f"{ml} мл — {price} тг"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"size:{sk}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Старые функции оставлены на случай, если где-то остался вызов.
def kb_drinks() -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text=name, callback_data=f"drink:{key}") for key, name in DRINKS.items()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_sizes() -> InlineKeyboardMarkup:
    # Универсальный fallback (новая логика использует `kb_sizes_for_drink()`).
    buttons: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text=f"{SIZES[key]['ml']} мл", callback_data=f"size:{key}") for key in SIZES
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[b] for b in buttons])


def kb_ready_time() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Через 15 мин", callback_data="ready:15"),
                InlineKeyboardButton(text="Через 30 мин", callback_data="ready:30"),
            ],
            [
                InlineKeyboardButton(text="Через 1 час", callback_data="ready:60"),
            ],
            [
                InlineKeyboardButton(text="Указать время вручную", callback_data="ready_manual"),
            ],
        ]
    )


def kb_confirm_order() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="confirm"),
                InlineKeyboardButton(text="Отменить", callback_data="cancel"),
            ]
        ]
    )


def kb_paid(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил (Kaspi)", callback_data=f"paid:{order_id}")],
        ]
    )


def kb_snacks_selection(snacks: list[dict], selected_ids: set[int]) -> InlineKeyboardMarkup:
    """
    Inline-клавиатура закусок (данные из БД).
    snacks: элементы вида {"id": int, "name": str, "price": int}
    """
    rows: list[list[InlineKeyboardButton]] = []
    for s in snacks:
        sid = int(s["id"])
        mark = "✓ " if sid in selected_ids else ""
        label = f"{mark}{s['name']} — {s['price']} ₸"
        rows.append([InlineKeyboardButton(text=label[:64], callback_data=f"snack_toggle:{sid}")])
    rows.append(
        [
            InlineKeyboardButton(text="Далее → подтверждение", callback_data="snacks_done"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_snacks_empty_continue() -> InlineKeyboardMarkup:
    """Когда в витрине нет позиций — сразу к подтверждению."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Продолжить без закусок", callback_data="snacks_done")],
        ]
    )

def kb_leave_preparation_comment() -> InlineKeyboardMarkup:
    """Вопрос клиенту: оставить ли комментарий баристе."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, оставить комментарий", callback_data="prep_comment_yes"),
                InlineKeyboardButton(text="Нет", callback_data="prep_comment_no"),
            ]
        ]
    )


def kb_barista_payment_review(order_id: int) -> InlineKeyboardMarkup:
    """Проверка оплаты: бариста подтверждает или отклоняет заявку клиента."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Оплата получена",
                    callback_data=f"b_pay_ok:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Оплаты нет",
                    callback_data=f"b_pay_bad:{order_id}",
                )
            ],
        ]
    )

