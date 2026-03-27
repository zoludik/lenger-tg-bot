import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from bot.database import init_db
from bot.handlers.client import router as client_router
from bot.handlers.barista import router as barista_router

async def main() -> None:
    # Загружаем переменные окружения из `bot/.env`
    dotenv_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=dotenv_path)

    bot_token = os.getenv("BOT_TOKEN")
    barista_chat_id = os.getenv("BARISTA_CHAT_ID")
    if not bot_token:
        raise RuntimeError("Не задана переменная BOT_TOKEN в файле bot/.env")
    if not barista_chat_id:
        raise RuntimeError("Не задана переменная BARISTA_CHAT_ID в файле bot/.env")

    bot = Bot(token=bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    # Сначала бариста (команды в BARISTA_CHAT_ID), затем клиент
    dp.include_routers(barista_router, client_router)

    # Инициализируем базу данных
    await init_db()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

