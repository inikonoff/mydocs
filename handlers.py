import os, sqlite3, aiohttp
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import WebAppInfo
from engine import GroqEngine
from toolkit import Toolkit

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
        await m.answer("👋 Я Грамотей! Присылай видео, файлы или ссылки.\nПосле обработки я отвечу на твои вопросы по тексту!")

    @dp.message(F.document | F.video_note | F.voice | F.audio)
    async def handle_media(m: types.Message, bot):
        status = await m.answer("⏳ Обрабатываю...")
        uid = m.from_user.id
        media = m.document or m.video_note or m.voice or m.audio
        file = await bot.get_file(media.file_id)
        path = f"data/{media.file_id}_{uid}"
        await bot.download_file(file.file_path, path)
        
        # Если это голос — проверяем, не вопрос ли это
        if (m.voice or m.video_note):
            text_voice = await engine.transcribe(path)
            old = db_op("SELECT last_text FROM users WHERE user_id=?", (uid,), True)
            if old and len(text_voice.split()) < 30:
                ans = await engine.get_response(f"Context: {old[0][0]}\nQ: {text_voice}", "Отвечай по документу.")
                await status.edit_text(f"🎤 **Вопрос:** {text_voice}\n\n🤖 {ans}")
                return

        # Иначе — обычный парсинг
        text = await tools.parse_file(path) if m.document else await engine.transcribe(path)
        await finish_up(m, status, text)
        if os.path.exists(path): os.remove(path)

    @dp.message(F.text.startswith("http"))
    async def handle_links(m: types.Message):
        status = await m.answer("🔗 Ссылка принята...")
        url = m.text
        if "youtu" in url:
            text = await tools.process_video(url)
        else:
            link = await tools.get_cloud_link(url)
            if link:
                async with aiohttp.ClientSession() as s:
                    async with s.get(link) as r:
                        with open("tmp_cloud", 'wb') as f: f.write(await r.read())
                text = await tools.parse_file("tmp_cloud")
            else: text = "Не удалось скачать."
        await finish_up(m, status, text)

    async def finish_up(m, status, text):
        if not text: return await status.edit_text("❌ Пусто.")
        db_op("INSERT OR REPLACE INTO users (user_id, last_text) VALUES (?, ?)", (m.from_user.id, text))
        
        # TWA кнопка (замени URL на свой)
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📖 Открыть в Web App", web_app=WebAppInfo(url="https://inikonoff.github.io/gramotey-twa/"))],
            [types.InlineKeyboardButton(text="DOCX", callback_data="ex_docx"), types.InlineKeyboardButton(text="TXT", callback_data="ex_txt")]
        ])
        await status.edit_text(f"✅ Прочитано ({len(text)} симв.)\n\n{text[:300]}...\n\nСпрашивай!", reply_markup=kb)

    @dp.message(F.text)
    async def chat(m: types.Message):
        old = db_op("SELECT last_text FROM users WHERE user_id=?", (m.from_user.id,), True)
        if old:
            await m.bot.send_chat_action(m.chat.id, "typing")
            ans = await engine.get_response(f"Context: {old[0][0]}\nQ: {m.text}", "Отвечай по тексту.")
            await m.answer(ans)

    @dp.callback_query(F.data.startswith("ex_"))
    async def export(cb: types.CallbackQuery):
        fmt = cb.data.split('_')[1]
        t = db_op("SELECT last_text FROM users WHERE user_id=?", (cb.from_user.id,), True)
        if t:
            p = tools.export_file(t[0][0], fmt, cb.from_user.id)
            await cb.message.answer_document(types.FSInputFile(p))
            os.remove(p)
        await cb.answer()