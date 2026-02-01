import os
import sqlite3
import aiohttp
import logging
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from engine import GroqEngine
from toolkit import Toolkit

# Инициализация логгера
logger = logging.getLogger(__name__)

engine = GroqEngine()
tools = Toolkit()

def db_op(sql, params=(), fetch=False):
    """Универсальная функция для работы с локальной БД SQLite"""
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
            "👋 Привет! Я — **Твои документы**, AI-ассистент.\n\n"
            "📂 **Что я умею:**\n"
            "• Читать PDF, DOCX, TXT файлы.\n"
            "• Распознавать текст с фото и картинок.\n"
            "• Транскрибировать голосовые, кружочки и аудио.\n"
            "• YouTube видео с таймкодами.\n"
            "• Отвечать на вопросы по вашим документам.\n\n"
            "Просто пришли мне файл или ссылку!"
        )

    @dp.message(F.document | F.photo | F.video_note | F.voice | F.audio)
    async def handle_media(m: types.Message, bot):
        uid = m.from_user.id
        status = await m.answer("⏳ Обрабатываю медиа...")
        
        # Определяем объект медиа
        media = m.document or m.voice or m.video_note or m.audio
        if m.photo:
            media = m.photo[-1]
            
        file_info = await bot.get_file(media.file_id)
        
        # ВАЖНО: сохраняем с расширением, чтобы Whisper не выдавал ошибку 400
        ext = file_info.file_path.split('.')[-1]
        path = f"data/{media.file_id}.{ext}"
        
        await bot.download_file(file_info.file_path, path)
        
        try:
            # 1. Если это аудио/кружочек — транскрибируем
            if m.voice or m.video_note or m.audio:
                text = await engine.transcribe(path)
                
                # Проверяем, не является ли это коротким вопросом к предыдущему тексту
                old_ctx = db_op("SELECT last_text FROM users WHERE user_id=?", (uid,), True)
                if old_ctx and old_ctx[0][0] and len(text.split()) < 30:
                    await status.delete()
                    await m.answer(f"🎤 **Ваш вопрос:** _{text}_")
                    ans = await engine.get_response(
                        f"Контекст: {old_ctx[0][0]}\n\nВопрос: {text}",
                        system="Отвечай на вопросы только на основе предоставленного текста."
                    )
                    await m.answer(ans)
                    return
            
            # 2. Если документ или фото — извлекаем текст
            else:
                text = await tools.parse_file(path)

            await finish_up(m, status, text)
            
        except Exception as e:
            logger.error(f"Error handling media: {e}")
            await status.edit_text(f"❌ Произошла ошибка: {str(e)[:100]}")
        finally:
            if os.path.exists(path):
                os.remove(path)

    @dp.message(F.text.startswith("http"))
    async def handle_links(m: types.Message):
        status = await m.answer("🔗 Обрабатываю ссылку...")
        url = m.text
        
        try:
            if any(x in url for x in ["youtu", "vimeo"]):
                text = await tools.process_video(url)
                if text == "NEED_WHISPER":
                    await status.edit_text("🔊 В видео нет субтитров. Попробуйте отправить аудиофайл.")
                    return
            else:
                # Логика для облачных ссылок (Яндекс.Диск и т.д.)
                link = await tools.get_cloud_link(url)
                if link:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(link) as resp:
                            if resp.status == 200:
                                tmp_path = f"data/cloud_{m.from_user.id}"
                                with open(tmp_path, 'wb') as f:
                                    f.write(await resp.read())
                                text = await tools.parse_file(tmp_path)
                                os.remove(tmp_path)
                            else:
                                text = "Ошибка скачивания файла из облака."
                else:
                    text = "Тип ссылки не поддерживается."
            
            await finish_up(m, status, text)
        except Exception as e:
            await status.edit_text(f"❌ Ошибка ссылки: {e}")

    async def finish_up(m, status, text):
        if not text or len(text.strip()) < 5:
            await status.edit_text("❌ Текст не найден или файл пуст.")
            return

        # Сохраняем в базу данных
        db_op("INSERT OR REPLACE INTO users (user_id, last_text) VALUES (?, ?)", (m.from_user.id, text))
        
        # ЗАМЕНИ ЭТУ ССЫЛКУ НА СВОЮ ИЗ GITHUB PAGES
        twa_url = "https://your-username.github.io/gramotey-twa/"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Читать в Web App", web_app=WebAppInfo(url=twa_url))],
            [
                InlineKeyboardButton(text="📄 в DOCX", callback_data="ex_docx"), 
                InlineKeyboardButton(text="📝 в TXT", callback_data="ex_txt")
            ]
        ])
        
        await status.edit_text(
            f"✅ **Текст обработан!** ({len(text)} симв.)\n\n"
            f"_{text[:350]}..._\n\n"
            f"💡 Вы можете задавать вопросы по этому тексту!",
            reply_markup=kb
        )

    @dp.message(F.text)
    async def chat_qna(m: types.Message):
        """Обработка текстовых вопросов"""
        if m.text.startswith("/"): return
        
        data = db_op("SELECT last_text FROM users WHERE user_id=?", (m.from_user.id,), True)
        if data and data[0][0]:
            await m.bot.send_chat_action(m.chat.id, "typing")
            ans = await engine.get_response(
                f"Текст документа: {data[0][0]}\n\nВопрос пользователя: {m.text}",
                system="Ты ассистент-аналитик. Отвечай кратко и точно по тексту документа."
            )
            await m.answer(ans)
        else:
            await m.answer("Сначала пришлите документ или ссылку для анализа.")

    @dp.callback_query(F.data.startswith("ex_"))
    async def export_handler(cb: types.CallbackQuery):
        fmt = cb.data.split('_')[1]
        data = db_op("SELECT last_text FROM users WHERE user_id=?", (cb.from_user.id,), True)
        if data and data[0][0]:
            path = tools.export_file(data[0][0], fmt, cb.from_user.id)
            await cb.message.answer_document(types.FSInputFile(path))
            if os.path.exists(path): os.remove(path)
        await cb.answer()