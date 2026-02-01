from aiogram.types import WebAppInfo
import os

# ... (импорты и db_op как в предыдущем ответе)

async def handle_media(m: types.Message, bot):
    uid = m.from_user.id
    status = await m.answer("⏳ Скачиваю и анализирую...")
    
    # Определяем тип и расширение
    media = m.document or m.voice or m.video_note or m.audio or (m.photo[-1] if m.photo else None)
    file_info = await bot.get_file(media.file_id)
    
    # ФИКС: Обязательно сохраняем с расширением из Telegram
    ext = file_info.file_path.split('.')[-1]
    path = f"data/{media.file_id}.{ext}" 
    await bot.download_file(file_info.file_path, path)
    
    try:
        if m.document or m.photo:
            text = await tools.parse_file(path)
        else:
            # Для аудио/видео меток передаем путь с расширением
            text = await engine.transcribe(path)
            
            # Если это короткий вопрос к старому тексту
            old_ctx = db_op("SELECT last_text FROM users WHERE user_id=?", (uid,), True)
            if old_ctx and len(text.split()) < 30:
                ans = await engine.get_response(f"Текст: {old_ctx[0][0]}\nВопрос: {text}")
                await status.edit_text(f"🎤 **Вопрос:** {text}\n\n🤖 {ans}")
                return

        await finish_up(m, status, text)
    finally:
        if os.path.exists(path): os.remove(path)

async def finish_up(m, status, text):
    if not text or len(text.strip()) < 5:
        return await status.edit_text("❌ Не удалось извлечь текст (файл пуст или защищен).")
    
    db_op("INSERT OR REPLACE INTO users (user_id, last_text) VALUES (?, ?)", (m.from_user.id, text))
    
    # Ссылка на твой GitHub Pages
    twa_url = f"https://inikonoff.github.io/gramotey-twa/"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Читать в Web App", web_app=WebAppInfo(url=twa_url))],
        [InlineKeyboardButton(text="📄 DOCX", callback_data="ex_docx"), 
         InlineKeyboardButton(text="📝 TXT", callback_data="ex_txt")]
    ])
    
    await status.edit_text(
        f"✅ **Успешно!** ({len(text)} симв.)\n\n{text[:300]}...\n\n"
        f"Теперь ты можешь задавать вопросы по тексту!", 
        reply_markup=kb
    )