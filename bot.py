import os
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
import handlers

load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def quick_init_db():
    """Создает папку data и базу данных с нужной структурой"""
    if not os.path.exists('data'):
        os.makedirs('data')
    conn = sqlite3.connect('data/database.db')
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            last_text TEXT,
            last_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована.")

async def main():
    quick_init_db()
    
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN не найден!")
        return

    bot = Bot(token=token)
    dp = Dispatcher()

    # Регистрация всех хэндлеров
    handlers.register_handlers(dp)

    logger.info("Бот запущен...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())