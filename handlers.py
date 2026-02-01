import os
import sqlite3
import aiohttp
import logging
from aiogram import F, types
from aiogram.filters import Command
from engine import GroqEngine
from toolkit import Toolkit

# Инициализация логгера
logger = logging.getLogger(__name__)

engine = GroqEngine()
tools = Toolkit()

def db_query(sql, params=(), fetch=False):
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
            "👋 Привет! Я — **Грамотей**, твой AI-ассистент.\n\n"
            "📂 **Что я умею:**\n"
            "• Читать PDF, DOCX, TXT файлы.\n"
            "• Распознавать текст с фото и картинок.\n"
            "• Транскрибировать голосовые, кружочки и аудио.\n"
            "• Работать с ссылками на YouTube и Облако.\n"
            "• Отвечать на вопросы по присланным документам.\n\n"
            "Просто пришли мне файл или ссылку, чтобы начать!"
        )

    @dp.message(F.document | F.photo | F.video_note | F.voice | F.audio)
    async def handle_media(m: types.Message, bot):
        user_id = m.from_user.id
        
        # Проверяем, не является ли голосовое/кружочек вопросом к предыдущему тексту
        existing_data = db_query("SELECT last_text FROM users WHERE user_id=?", (user_id,), True)
        
        # Если это голос/видео-заметка и у нас уже есть контекст — это может быть вопрос
        is_audio = m.voice or m.video_note or m.audio
        
        status = await m.answer("⏳ Обрабатываю медиа...")
        
        # Определяем ID файла
        media = m.document or m.photo[-1] if m.photo else m.video_note or m.voice or m.audio
        file_id = media.file_id
        
        # Генерируем временный путь
        file_info = await bot.get_file(file_id)
        ext = file_info.file_path.split('.')[-1]
        path = f"data/{file_id}_{user_id}.{ext}"
        
        await bot.download_file(file_info.file_path, path)
        
        try:
            # 1. Если это аудио и есть старый текст — пробуем понять, вопрос ли это
            if is_audio and existing_data and existing_data[0][0]:
                recognized_text = await engine.transcribe(path)
                # Если надиктовка короткая (до 40 слов) — считаем это вопросом
                if len(recognized_text.split()) < 40:
                    await status.delete()
                    await m.answer(f"🎤 **Ваш вопрос:** _{recognized_text}_")
                    await bot.send_chat_action(m.chat.id, "typing")
                    
                    ans = await engine.get_response(
                        f"Контекст: {existing_data[0][0]}\n\nВопрос пользователя: {recognized_text}", 
                        system="Ты ассистент, который отвечает на вопросы строго на основе предоставленного текста документа."
                    )
                    await m.answer(ans)
                    return
                else:
                    text = recognized_text # Иначе это новый длинный текст для анализа
            
            # 2. Обычная обработка документов и фото
            elif m.document or m.photo:
                text = await tools.parse_file(path)
            else:
                text = await engine.transcribe(path)

            await finish_processing(m, status, text)
            
        finally:
            if os.path.exists(path):
                os.remove(path)

    @dp.message(F.text.startswith("http"))
    async def handle_links(m: types.Message):
        status = await m.answer("🔗 Изучаю ссылку...")
        url = m.text
        text = ""
        
        try:
            if any(x in url for x in ["youtu", "vimeo"]):
                res = await tools.process_video(url)
                if res == "NEED_WHISPER":
                    await status.edit_text("🔊 Субтитров нет. Скачиваю аудио для Whisper...")
                    # Здесь в toolkit можно добавить загрузку через yt-dlp -> engine.transcribe
                    text = "Извините, скачивание тяжелого аудио временно ограничено. Используйте видео с субтитрами."
                else:
                    text = res
            else:
                direct = await tools.get_cloud_link(url)
                if direct:
                    path = f"data/cloud_{m.from_user.id}"
                    async with aiohttp.ClientSession() as s:
                        async with s.get(direct) as r:
                            if r.status == 200:
                                with open(path, 'wb') as f:
                                    f.write(await r.read())
                                text = await tools.parse_file(path)
                                os.remove(path)
                            else:
                                text = "Ошибка доступа к облачному файлу."
                else:
                    text = "Неизвестный тип ссылки или доступ закрыт."
            
            await finish_processing(m, status, text)
        except Exception as e:
            await status.edit_text(f"❌ Ошибка при обработке ссылки: {e}")

    async def finish_processing(m, status, text):
        if not text or len(text.strip()) < 5:
            await status.edit_text("❌ Не удалось извлечь текст или файл пуст.")
            return

        # Сохраняем в БД для Q&A
        db_query("INSERT OR REPLACE INTO users (user_id, last_text, state) VALUES (?, ?, ?)", 
                 (m.from_user.id, text, 'idle'))
        
        # Предлагаем перевод, если язык не русский
        trans_suggestion = await engine.detect_and_translate(text)
        
        kb_builder = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="📄 в DOCX", callback_data="exp_docx"), 
                types.InlineKeyboardButton(text="📝 в TXT", callback_data="exp_txt")
            ]
        ])
        
        header = "✅ **Текст успешно обработан!**\n\n"
        preview = f"_{text[:600]}..._"
        footer = "\n\n💡 Теперь ты можешь **задать вопрос** по этому тексту (текстом или голосом) или экспортировать его."
        
        if trans_suggestion:
            # Добавляем кнопку перевода, если текст иностранный
            kb_builder.inline_keyboard.append([types.InlineKeyboardButton(text="🌍 Перевести на русский", callback_data="do_translate")])
            await status.edit_text(f"{header}{preview}{footer}", reply_markup=kb_builder)
        else:
            await status.edit_text(f"{header}{preview}{footer}", reply_markup=kb_builder)

    @dp.callback_query(F.data.startswith("exp_"))
    async def export_handler(cb: types.CallbackQuery):
        fmt = cb.data.split('_')[1]
        data = db_query("SELECT last_text FROM users WHERE user_id=?", (cb.from_user.id,), True)
        if data and data[0][0]:
            path = tools.export_file(data[0][0], fmt, cb.from_user.id)
            await cb.message.answer_document(types.FSInputFile(path, filename=f"result.{fmt}"))
            if os.path.exists(path):
                os.remove(path)
        await cb.answer()

    @dp.callback_query(F.data == "do_translate")
    async def translate_callback(cb: types.CallbackQuery):
        await cb.message.edit_text("🌐 Перевожу, пожалуйста подождите...")
        data = db_query("SELECT last_text FROM users WHERE user_id=?", (cb.from_user.id,), True)
        if data:
            translated = await engine.get_response(f"Переведи этот текст на русский язык максимально качественно: {data[0][0]}")
            db_query("UPDATE users SET last_text = ? WHERE user_id = ?", (translated, cb.from_user.id))
            await finish_processing(cb.message, cb.message, translated)
        await cb.answer()

    @dp.message(F.text)
    async def qna_handler(m: types.Message):
        """Обработка текстовых вопросов к последнему документу"""
        data = db_query("SELECT last_text FROM users WHERE user_id=?", (m.from_user.id,), True)
        if data and data[0][0]:
            await m.bot.send_chat_action(m.chat.id, "typing")
            ans = await engine.get_response(
                f"Текст документа: {data[0][0]}\n\nВопрос пользователя: {m.text}", 
                system="Ты — эксперт-аналитик. Отвечай на вопросы пользователя четко и кратко, основываясь только на предоставленном контексте."
            )
            await m.answer(ans)
        else:
            await m.answer("Сначала пришли мне файл или ссылку, чтобы я мог отвечать на вопросы.")