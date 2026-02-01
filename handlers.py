import os
import sqlite3
import logging
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from engine import GroqEngine
from toolkit import Toolkit

logger = logging.getLogger(__name__)
engine = GroqEngine()
tools = Toolkit()

TWA_URL = "https://inikonoff.github.io/gramotey-twa/"

def db_op(sql, params=(), fetch=False):
    conn = sqlite3.connect('data/database.db')
    cur = conn.cursor()
    cur.execute(sql, params)
    res = cur.fetchall() if fetch else None
    conn.commit(); conn.close()
    return res

def get_main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🧹 Очистить контекст"), KeyboardButton(text="❓ Справка")]],
        resize_keyboard=True
    )

def create_options_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📝 Исправить", callback_data="mode_basic"),
                InlineKeyboardButton(text="✨ Стиль", callback_data="mode_premium"))
    builder.row(InlineKeyboardButton(text="📊 Саммари", callback_data="mode_summary"))
    builder.row(InlineKeyboardButton(text="📖 Читать полностью", web_app=WebAppInfo(url=TWA_URL)))
    return builder.as_markup()

def register_handlers(dp):
    @dp.message(Command("start"))
    async def start(m: types.Message):
        await m.answer("🤖 **Грамотей готов!**\nПришли файл или голос.", reply_markup=get_main_kb())

    @dp.message(F.text == "🧹 Очистить контекст")
    async def clear(m: types.Message):
        db_op("DELETE FROM users WHERE user_id=?", (m.from_user.id,))
        await m.answer("✨ Память очищена.")

    @dp.message(F.document | F.photo | F.voice | F.audio | F.video_note)
    async def handle_media(m: types.Message, bot):
        status = await m.answer("⏳ Читаю...")
        uid = m.from_user.id
        media = m.document or m.voice or m.audio or m.video_note or (m.photo[-1] if m.photo else None)
        file_info = await bot.get_file(media.file_id)
        path = f"data/{media.file_id}.{file_info.file_path.split('.')[-1]}"
        await bot.download_file(file_info.file_path, path)
        
        try:
            if m.voice or m.audio or m.video_note:
                text = await engine.transcribe(path)
                data = db_op("SELECT last_text FROM users WHERE user_id=?", (uid,), True)
                if data and len(text.split()) < 20:
                    await status.delete()
                    ans = await engine.get_response(f"Контекст: {data[0][0][:4000]}\nВопрос: {text}")
                    return await m.answer(f"🎤 {text}\n\n{ans}")
            else:
                text = await tools.parse_file(path)

            if text and len(text.strip()) > 5:
                db_op("INSERT OR REPLACE INTO users (user_id, last_text, last_result) VALUES (?, ?, NULL)", (uid, text))
                await status.edit_text(f"✅ Готово! Выбери режим:", reply_markup=create_options_keyboard())
            else:
                await status.edit_text("❌ Текст не найден.")
        finally:
            if os.path.exists(path): os.remove(path)

    @dp.callback_query(F.data.startswith("mode_"))
    async def set_mode(cb: types.CallbackQuery):
        mode = cb.data.split("_")[1]
        data = db_op("SELECT last_text FROM users WHERE user_id=?", (cb.from_user.id,), True)
        if not data: return await cb.answer("Файл не найден!")
        
        await cb.message.edit_text(f"⏳ Режим: {mode}...")
        prompts = {"basic": "Исправь ошибки.", "premium": "Улучши стиль.", "summary": "Сделай краткое саммари."}
        res = await engine.get_response(f"{prompts[mode]}\n\nТекст: {data[0][0][:4500]}")
        db_op("UPDATE users SET last_result=? WHERE user_id=?", (res, cb.from_user.id))
        await cb.message.edit_text(f"✨ **Результат:**\n\n{res}", reply_markup=create_options_keyboard())

    @dp.message(F.text)
    async def ask(m: types.Message):
        if m.text.startswith("/") or m.text in ["🧹 Очистить контекст", "❓ Справка"]: return
        data = db_op("SELECT last_text, last_result FROM users WHERE user_id=?", (m.from_user.id,), True)
        if data and data[0][0]:
            ctx = f"Документ: {data[0][0][:3000]}\nРезультат: {data[0][1]}"
            ans = await engine.get_response(f"Контекст: {ctx}\nВопрос: {m.text}")
            await m.answer(ans)
        else:
            await m.answer("Пришли файл!")