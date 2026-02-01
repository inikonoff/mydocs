import os
import sqlite3
import aiohttp
import logging
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from engine import GroqEngine
from toolkit import Toolkit

logger = logging.getLogger(__name__)
engine = GroqEngine()
tools = Toolkit()

def db_op(sql, params=(), fetch=False):
    conn = sqlite3.connect('data/database.db')
    cur = conn.cursor(); cur.execute(sql, params)
    res = cur.fetchall() if fetch else None
    conn.commit(); conn.close()
    return res

def register_handlers(dp):
    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        await m.answer("🤖 **Грамотей готов!**\nПрисылай тяжелые PDF, ссылки на YouTube или голос.\nЯ использую умное сжатие для больших файлов.")

    @dp.message(F.document | F.photo | F.video_note | F.voice | F.audio)
    async def handle_media(m: types.Message, bot):
        uid = m.from_user.id
        status = await m.answer("⏳ Читаю файл...")
        
        media = m.document or m.voice or m.video_note or m.audio or (m.photo[-1] if m.photo else None)
        file_info = await bot.get_file(media.file_id)
        ext = file_info.file_path.split('.')[-1]
        path = f"data/{media.file_id}.{ext}"
        await bot.download_file(file_info.file_path, path)
        
        try:
            if m.document or m.photo:
                text = await tools.parse_file(path)
            else:
                text = await engine.transcribe(path)
                # Логика короткого вопроса голосом
                old = db_op("SELECT last_text FROM users WHERE user_id=?", (uid,), True)
                if old and len(text.split()) < 30:
                    await status.delete()
                    # ХИТРОСТЬ 2: Chunking контекста для вопроса
                    context = old[0][0]
                    if len(context) > 15000:
                        context = context[:10000] + "\n[...]\n" + context[-5000:]
                    
                    ans = await engine.get_response(f"Текст: {context}\nВопрос: {text}")
                    await m.answer(f"🎤 **Вопрос:** {text}\n\n🤖 {ans}")
                    return

            await finish_up(m, status, text)
        except Exception as e:
            await status.edit_text(f"❌ Ошибка: {str(e)[:100]}")
        finally:
            if os.path.exists(path): os.remove(path)

    @dp.message(F.text)
    async def chat_qna(m: types.Message):
        if m.text.startswith("/") or m.text.startswith("http"): return
        
        data = db_op("SELECT last_text FROM users WHERE user_id=?", (m.from_user.id,), True)
        if data and data[0][0]:
            full_text = data[0][0]
            await m.bot.send_chat_action(m.chat.id, "typing")
            
            # ХИТРОСТЬ 3: Агрессивный чанкинг для Q&A
            # Если текст больше ~15к символов, берем только куски
            if len(full_text) > 18000:
                # Берем начало, середину и конец
                context = full_text[:8000] + "\n...[середина]...\n" + \
                          full_text[len(full_text)//2 - 2000 : len(full_text)//2 + 2000] + \
                          "\n...[конец]...\n" + full_text[-4000:]
                note = "\n\n⚠️ *Текст очень большой, анализ может быть неполным.*"
            else:
                context = full_text
                note = ""

            ans = await engine.get_response(
                f"Документ: {context}\n\nВопрос: {m.text}",
                system="Отвечай на основе документа. Если информации нет в кусках текста, так и скажи."
            )
            await m.answer(ans + note, parse_mode="Markdown")
        else:
            await m.answer("Сначала пришли файл.")

    async def finish_up(m, status, text):
        if not text: return await status.edit_text("❌ Текст не найден.")
        db_op("INSERT OR REPLACE INTO users (user_id, last_text) VALUES (?, ?)", (m.from_user.id, text))
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Читать полностью", web_app=WebAppInfo(url="https://your-git-pages.io"))],
            [InlineKeyboardButton(text="📄 DOCX", callback_data="ex_docx"), 
             InlineKeyboardButton(text="📝 TXT", callback_data="ex_txt")]
        ])
        
        # В превью тоже ограничиваем, чтобы само сообщение в ТГ не упало
        await status.edit_text(f"✅ Готово! ({len(text)} симв.)\n\n_{text[:500]}..._\n\nСпрашивай!", reply_markup=kb)

    @dp.callback_query(F.data.startswith("ex_"))
    async def export(cb: types.CallbackQuery):
        fmt = cb.data.split('_')[1]
        t = db_op("SELECT last_text FROM users WHERE user_id=?", (cb.from_user.id,), True)
        if t:
            p = tools.export_file(t[0][0], fmt, cb.from_user.id)
            await cb.message.answer_document(types.FSInputFile(p))
            os.remove(p)
        await cb.answer()

def register_handlers(dp):
    # Костыль для правильной регистрации (вызывать в bot.py)
    pass # В реальном коде тут вызовы хэндлеров