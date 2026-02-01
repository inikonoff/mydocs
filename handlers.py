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
    """Универсальная функция для работы с базой данных"""
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
    
    # --- КОМАНДЫ МЕНЮ ---

    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        await m.answer(
            "🤖 **Грамотей готов к работе!**\n\n"
            "Я помогу тебе переварить любой контент:\n"
            "• Присылай **PDF, DOCX, TXT** (даже тяжелые)\n"
            "• Кидай ссылки на **YouTube** (сделаю таймкоды)\n"
            "• Надиктовывай **голос** или присылай **кружочки**\n\n"
            "После загрузки я запомню текст, и ты сможешь задавать по нему вопросы прямо в чате.",
            reply_markup=get_main_kb()
        )

    @dp.message(Command("help") or F.text == "❓ Справка")
    async def cmd_help(m: types.Message):
        help_text = (
            "📖 **Инструкция:**\n\n"
            "1. **Загрузка:** Просто отправь файл или ссылку. Я отвечу 'Текст обработан'.\n"
            "2. **Вопросы:** Пиши любой вопрос (например: 'О чем третья глава?' или 'Выпиши главные цифры').\n"
            "3. **Голос:** Можно не писать, а просто надиктовать вопрос голосом.\n"
            "4. **Новый файл:** Чтобы сменить тему, нажми 'Очистить контекст'.\n\n"
            "⚠️ _Если файл очень большой, я проанализирую его по частям для экономии токенов._"
        )
        await m.answer(help_text, reply_markup=get_main_kb())

    @dp.message(Command("clear") or F.text == "🧹 Очистить контекст")
    async def cmd_clear(m: types.Message):
        db_op("DELETE FROM users WHERE user_id=?", (m.from_user.id,))
        await m.answer("✨ **Память очищена.** Я готов к новому документу!", reply_markup=get_main_kb())

    # --- ОБРАБОТКА МЕДИА ---

    @dp.message(F.document | F.photo | F.video_note | F.voice | F.audio)
    async def handle_media(m: types.Message, bot):
        uid = m.from_user.id
        status = await m.answer("⏳ Читаю...")
        
        media = m.document or m.voice or m.video_note or m.audio
        if m.photo: media = m.photo[-1]
            
        file_info = await bot.get_file(media.file_id)
        ext = file_info.file_path.split('.')[-1]
        path = f"data/{media.file_id}.{ext}"
        
        await bot.download_file(file_info.file_path, path)
        
        try:
            if m.voice or m.video_note or m.audio:
                text = await engine.transcribe(path)
                
                # Проверка на голосовой вопрос к старому тексту
                old = db_op("SELECT last_text FROM users WHERE user_id=?", (uid,), True)
                if old and old[0][0] and len(text.split()) < 35:
                    await status.delete()
                    await m.answer(f"🎤 **Вопрос:** _{text}_")
                    ctx = old[0][0]
                    # Умное обрезание для Groq
                    if len(ctx) > 15000:
                        ctx = ctx[:9000] + "\n[...]\n" + ctx[-6000:]
                    ans = await engine.get_response(f"Текст: {ctx}\nВопрос: {text}")
                    await m.answer(ans)
                    return
            else:
                text = await tools.parse_file(path)

            await finish_up(m, status, text)
            
        except Exception as e:
            logger.error(f"Error: {e}")
            await status.edit_text("❌ Ошибка при чтении файла. Возможно, там только картинки?")
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
                else: text = "Тип ссылки не поддерживается."
            await finish_up(m, status, text)
        except Exception as e:
            await status.edit_text(f"❌ Ошибка ссылки: {e}")

    # --- ВОПРОС-ОТВЕТ (Q&A) ---

    @dp.message(F.text)
    async def chat_qna(m: types.Message):
        # Игнорируем команды и системные кнопки
        if m.text.startswith("/") or m.text in ["🧹 Очистить контекст", "❓ Справка"]:
            return
        
        data = db_op("SELECT last_text FROM users WHERE user_id=?", (m.from_user.id,), True)
        if data and data[0][0]:
            full_text = data[0][0]
            await m.bot.send_chat_action(m.chat.id, "typing")
            
            # Агрессивный чанкинг для лимитов 413
            if len(full_text) > 18000:
                context = (
                    "НАЧАЛО:\n" + full_text[:8000] + 
                    "\n\n...[СРЕДИНА]...\n" + full_text[len(full_text)//2 - 2000 : len(full_text)//2 + 2000] + 
                    "\n\nКОНЕЦ:\n" + full_text[-5000:]
                )
                note = "\n\n⚠️ *Анализ частичный из-за размера файла.*"
            else:
                context = full_text
                note = ""

            ans = await engine.get_response(
                f"Документ: {context}\n\nВопрос: {m.text}",
                system="Отвечай кратко на основе предоставленного текста."
            )
            await m.answer(ans + note, parse_mode="Markdown")
        else:
            await m.answer("У меня нет данных для ответа. Пожалуйста, пришли файл или ссылку.")

    # --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

    async def finish_up(m, status, text):
        if not text: return await status.edit_text("❌ Текст не найден.")
        db_op("INSERT OR REPLACE INTO users (user_id, last_text) VALUES (?, ?)", (m.from_user.id, text))
        
        # Ссылка на твой GitHub Pages
        twa_url = "https://your-username.github.io/gramotey-twa/"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Читать полностью (Web App)", web_app=WebAppInfo(url=twa_url))],
            [
                InlineKeyboardButton(text="📄 DOCX", callback_data="ex_docx"), 
                InlineKeyboardButton(text="📝 TXT", callback_data="ex_txt")
            ]
        ])
        
        await status.edit_text(
            f"✅ **Текст обработан!** ({len(text)} симв.)\n\n"
            f"_{text[:400]}..._\n\n"
            f"💬 Задавай вопросы по содержанию!",
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