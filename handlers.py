import os
import sqlite3
import aiohttp
import logging
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from engine import GroqEngine
from toolkit import Toolkit

# Логирование
logger = logging.getLogger(__name__)

engine = GroqEngine()
tools = Toolkit()

def db_op(sql, params=(), fetch=False):
    """Работа с локальной БД SQLite"""
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

def register_handlers(dp):
    
    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        await m.answer(
            "🤖 **Грамотей готов к работе!**\n\n"
            "Я оптимизирован для тяжелых файлов и длинных видео.\n"
            "• Присылай PDF (до 20Мб+)\n"
            "• Ссылки на YouTube (сделаю таймкоды)\n"
            "• Голосовые и кружочки\n\n"
            "После загрузки просто пиши вопросы по тексту!"
        )

    @dp.message(F.document | F.photo | F.video_note | F.voice | F.audio)
    async def handle_media(m: types.Message, bot):
        uid = m.from_user.id
        status = await m.answer("⏳ Анализирую медиа...")
        
        # Определяем тип файла
        media = m.document or m.voice or m.video_note or m.audio
        if m.photo: media = m.photo[-1]
            
        file_info = await bot.get_file(media.file_id)
        ext = file_info.file_path.split('.')[-1]
        path = f"data/{media.file_id}.{ext}"
        
        await bot.download_file(file_info.file_path, path)
        
        try:
            # 1. Если это аудио — транскрибируем
            if m.voice or m.video_note or m.audio:
                text = await engine.transcribe(path)
                
                # Проверка: не вопрос ли это к старому тексту?
                old = db_op("SELECT last_text FROM users WHERE user_id=?", (uid,), True)
                if old and old[0][0] and len(text.split()) < 35:
                    await status.delete()
                    await m.answer(f"🎤 **Вопрос:** _{text}_")
                    # Чанкинг контекста для Whisper-вопроса
                    ctx = old[0][0]
                    if len(ctx) > 15000:
                        ctx = ctx[:9000] + "\n[...]\n" + ctx[-6000:]
                    ans = await engine.get_response(f"Текст: {ctx}\nВопрос: {text}")
                    await m.answer(ans)
                    return
            
            # 2. Документы или фото
            else:
                text = await tools.parse_file(path)

            await finish_up(m, status, text)
            
        except Exception as e:
            logger.error(f"Error: {e}")
            await status.edit_text(f"❌ Ошибка обработки: {str(e)[:50]}")
        finally:
            if os.path.exists(path): os.remove(path)

    @dp.message(F.text.startswith("http"))
    async def handle_links(m: types.Message):
        status = await m.answer("🔗 Обрабатываю ссылку...")
        url = m.text
        try:
            if "youtu" in url:
                text = await tools.process_video(url)
            else:
                link = await tools.get_cloud_link(url)
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

    @dp.message(F.text)
    async def chat_qna(m: types.Message):
        """Интеллектуальный Q&A с чанкингом"""
        if m.text.startswith("/"): return
        
        data = db_op("SELECT last_text FROM users WHERE user_id=?", (m.from_user.id,), True)
        if data and data[0][0]:
            full_text = data[0][0]
            await m.bot.send_chat_action(m.chat.id, "typing")
            
            # Агрессивный чанкинг для очень больших текстов (как твой 17Мб PDF)
            if len(full_text) > 18000:
                # Собираем "скелет" документа: начало, середина и конец
                context = (
                    "НАЧАЛО ДОКУМЕНТА:\n" + full_text[:8000] + 
                    "\n\n...[СРЕДИНА]...\n" + full_text[len(full_text)//2 - 2000 : len(full_text)//2 + 2000] + 
                    "\n\nКОНЕЦ ДОКУМЕНТА:\n" + full_text[-5000:]
                )
                note = "\n\n⚠️ *Текст очень большой. Я проанализировал ключевые части.*"
            else:
                context = full_text
                note = ""

            ans = await engine.get_response(
                f"Документ: {context}\n\nВопрос: {m.text}",
                system="Ты аналитик. Отвечай только по тексту документа. Если информации нет, так и скажи."
            )
            await m.answer(ans + note, parse_mode="Markdown")
        else:
            await m.answer("Пришли файл, и я отвечу на любые вопросы по нему!")

    async def finish_up(m, status, text):
        if not text: return await status.edit_text("❌ Не удалось извлечь текст.")
        
        db_op("INSERT OR REPLACE INTO users (user_id, last_text) VALUES (?, ?)", (m.from_user.id, text))
        
        # Ссылка на твой GitHub Pages (TWA)
        twa_url = "https://your-username.github.io/gramotey-twa/"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Читать полностью (TWA)", web_app=WebAppInfo(url=twa_url))],
            [
                InlineKeyboardButton(text="📄 DOCX", callback_data="ex_docx"), 
                InlineKeyboardButton(text="📝 TXT", callback_data="ex_txt")
            ]
        ])
        
        await status.edit_text(
            f"✅ **Готово!** ({len(text)} симв.)\n\n"
            f"_{text[:400]}..._\n\n"
            f"💬 Спрашивай что угодно по тексту!",
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