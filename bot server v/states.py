from aiogram.fsm.state import State, StatesGroup


class OrderStates(StatesGroup):
    """FSM шаги оформления заказа клиента."""

    waiting_for_builder = State()  # меню конструктора заказа
    waiting_for_drink = State()  # выбор напитка
    waiting_for_size = State()  # выбор объема
    waiting_for_ready_time = State()  # выбор времени готовности
    waiting_for_manual_time = State()  # ввод времени вручную (ЧЧ:ММ)
    waiting_for_drink_category = State()  # выбор категории напитка
    waiting_for_snacks = State()  # закуски / выпечка (динамическое меню)
    waiting_for_preparation_comment_choice = State()  # хотим ли комментарий баристе
    waiting_for_preparation_comment_input = State()  # ввод комментария (текст)
    waiting_for_confirmation = State()  # подтверждение заказа (summary)

