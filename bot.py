import asyncio
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

load_dotenv()

def init_db():
    os.makedirs('data', exist_ok=True)
    conn = sqlite3.connect('data/database.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users 
        (user_id INTEGER PRIMARY KEY, last_text TEXT, state TEXT)''')
    conn.commit()
    conn.close()

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    
    from handlers import register_handlers
    
    bot = Bot(token=os.getenv("BOT_TOKEN"))
    dp = Dispatcher()
    
    register_handlers(dp)
    
    logging.info("🚀 Бот Грамотей запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())