import asyncio
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

# Веб-сервер для "обмана" Render (Health Check)
async def handle_health(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    # Render дает порт в переменной среды PORT, по умолчанию 10000
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

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
    
    # Запускаем Health Check сервер
    await start_web_server()
    
    logging.info("🚀 Бот запущен и порт открыт...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())