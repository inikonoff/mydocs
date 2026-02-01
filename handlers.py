import os
import sqlite3
import aiohttp
import logging
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    WebAppInfo, 
    ReplyKeyboardMarkup, 
    KeyboardButton
)
from engine import GroqEngine
from toolkit import Toolkit

# Настройка логирования
logger = logging.getLogger(__name__)

engine = GroqEngine()
tools = Toolkit()

def db_op(sql, params=(), fetch=False):
    """Универсальная функция для работы с базой данных SQLite"""
    try:
        conn = sqlite3.connect('data/database.db')
        cur = conn.cursor()
        cur.execute(sql, params)
        res = cur.fetchall() if fetch else None
        conn.commit()
        conn.close()
        return res
    except Exception as e:
        logger.error(f"Database error: {e}")
        return None

def get_main_kb():
    """Создает постоянную клавиатуру внизу экрана"""
    kb = [
        [KeyboardButton(text="🧹 Очистить контекст"), KeyboardButton(text="❓ Справка")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=kb, 
        resize_keyboard=True, 
        input_field_placeholder="Пришли файл или задай вопрос..."
    )

def register_handlers(dp):
    
    # --- СИСТЕМНЫЕ КОМАНДЫ ---

    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        await m.answer(
            "🤖 **Грамотей готов!**\n\n"
            "Я оптимизирован под бесплатные лимиты. Присылай:\n"
            "• PDF, DOCX, TXT (читаю даже тяжелые файлы)\n"
            "• Ссылки на YouTube (делаю таймкоды)\n"
            "• Голосовые и кружочки\n\n"
            "После загрузки просто пиши вопросы!",
            reply_markup=get_main_kb()
        )

    @dp.message(Command("help") or F.text == "❓ Справка")
    async def cmd_help(m: types.Message):
        help_text = (
            "📖 **Инструкция:**\n"
            "1. Пришли файл или ссылку.\n"
            "2. Подожди подтверждения 'Текст обработан'.\n"
            "3. Задавай вопросы текстом или голосом.\n"
            "4. Для новой темы нажми 'Очистить контекст'."
        )
        await m.answer(help_text, reply_markup=get_main_kb())

    @dp.message(Command("clear") or F.text == "🧹 Очистить контекст")
    async def cmd_clear(m: types.Message):
        db_op("DELETE FROM users WHERE user_id=?", (m.from_user.id,))
        await m.answer("✨ **Память очищена.** Жду новый файл!", reply_markup=get_main_kb())

    # --- ОБРАБОТКА МЕДИА ---

    @dp.message(F.document | F.photo | F.video_note | F.voice | F.audio)
    async def handle_media(m: types.Message, bot):
        uid = m.from_user.id
        status = await m.answer("⏳ Читаю и распознаю...")
        
        # Обработка разных типов медиа
        media = m.document or m.voice or m.video_note or m.audio
        if m.photo: media = m.photo[-1]
            
        file_info = await bot.get_file(media.file_id)
        ext = file_info.file_path.split('.')[-1]
        path = f"data/{media.file_id}.{ext}"
        
        await bot.download_file(file_info.file_path, path)
        
        try:
            # Если это аудио/видео
            if m.voice or m.video_note or m.audio:
                text = await engine.transcribe(path)
                
                # Проверка на короткий голосовой вопрос
                old = db_op("SELECT last_text FROM users WHERE user_id=?", (uid,), True)
                if old and old[0][0] and len(text.split()) < 30:
                    await status.delete()
                    await m.answer(f"🎤 **Вопрос:** _{text}_")
                    # Ультра-сжатие контекста для вопроса
                    ctx = old[0][0][:4000] 
                    ans = await engine.get_response(f"Текст: {ctx}\nВопрос: {text}")
                    await m.answer(ans)
                    return
            else:
                # Если файл - используем Toolkit (важно иметь PyMuPDF в системе)
                text = await tools.parse_file(path)

            await finish_up(m, status, text)
            
        except Exception as e:
            logger.error(f"Media Error: {e}")
            await status.edit_text("❌ Не удалось прочитать файл. Возможно, он пуст или зашифрован.")
        finally:
            if os.path.exists(path): os.remove(path)

    @dp.message(F.text.startswith("http"))
    async def handle_links(m: types.Message):
        status = await m.answer("🔗 Обрабатываю ссылку...")
        try:
            if "youtu" in m.text:
                text = await tools.process_video(m.text)
            else:
                link = await tools.get_cloud_link(m.text)
                if link:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(link) as r:
                            with open("tmp_cloud", 'wb') as f: f.write(await r.read())
                    text = await tools.parse_file("tmp_cloud")
                    os.remove("tmp_cloud")
                else: text = "Ссылка не поддерживается."
            await finish_up(m, status, text)
        except Exception as e:
            await status.edit_text(f"❌ Ошибка ссылки: {e}")

    # --- ВОПРОС-ОТВЕТ (Q&A) ---

    @dp.message(F.text)
    async def chat_qna(m: types.Message):
        if m.text.startswith("/") or m.text in ["🧹 Очистить контекст", "❓ Справка"]:
            return
        
        data = db_op("SELECT last_text FROM users WHERE user_id=?", (m.from_user.id,), True)
        if data and data[0][0]:
            full_text = data[0][0]
            await m.bot.send_chat_action(m.chat.id, "typing")
            
            # УЛЬТРА-АГРЕССИВНЫЙ ЧАНКИНГ ДЛЯ ЛИМИТА 6000 TPM
            # Берем 3000 символов из начала и 1500 из конца
            if len(full_text) > 4500:
                context = (
                    "СУТЬ НАЧАЛА:\n" + full_text[:3000] + 
                    "\n\n[...]\n\n" + 
                    "СУТЬ КОНЦА:\n" + full_text[-1500:]
                )
                note = "\n\n⚠️ *Файл очень велик. Анализ ограничен лимитами API.*"
            else:
                context = full_text
                note = ""

            ans = await engine.get_response(
                f"Документ: {context}\n\nВопрос: {m.text}",
                system="Отвечай очень коротко по тексту. Если нет инфы - так и скажи."
            )
            await m.answer(ans + note, parse_mode="Markdown")
        else:
            await m.answer("Сначала пришли файл или ссылку!", reply_markup=get_main_kb())

    # --- ЗАВЕРШЕНИЕ ---

    async def finish_up(m, status, text):
        if not text or len(text.strip()) < 5: 
            return await status.edit_text("❌ Файл пуст или не содержит текста.")
            
        db_op("INSERT OR REPLACE INTO users (user_id, last_text) VALUES (?, ?)", (m.from_user.id, text))
        
        # Ссылка на твой GitHub Pages (TWA)
        twa_url = "https://inikonoff.github.io/gramotey-twa/"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Читать полностью (Web App)", web_app=WebAppInfo(url=twa_url))],
            [
                InlineKeyboardButton(text="📄 DOCX", callback_data="ex_docx"), 
                InlineKeyboardButton(text="📝 TXT", callback_data="ex_txt")
            ]
        ])
        
        await status.edit_text(
            f"✅ **Текст обработан!** ({len(text)} симв.)\n\n"
            f"_{text[:300]}..._\n\n"
            f"💬 Спрашивай!",
            reply_markup=kb
        )

    @dp.callback_query(F.data.startswith("ex_"))
    async def export_handler(cb: types.CallbackQuery):
        fmt = cb.data.split('_')[1]
        data = db_op("SELECT last_text FROM users WHERE user_id=?", (cb.from_user.id,), True)
        if data:
            path = tools.export_file(data[0][0], fmt, cb.from_user.id)
            await cb.message.answer_document(types.FSInputFile(path))
            os.remove(path)
        await cb.answer()