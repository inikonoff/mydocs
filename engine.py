import os
import random
from openai import AsyncOpenAI

class GroqEngine:
    def __init__(self):
        keys = os.getenv("GROQ_API_KEYS", "").split(",")
        self.clients = [AsyncOpenAI(api_key=k.strip(), base_url="https://api.groq.com/openai/v1") for k in keys if k.strip()]

    async def transcribe(self, file_path):
        """Исправленная транскрибация с корректным форматом файла"""
        client = random.choice(self.clients)
        # Важно: Groq требует расширение в имени файла для определения типа
        file_name = os.path.basename(file_path)
        if not any(file_name.endswith(ext) for ext in ['ogg', 'mp3', 'mp4', 'mpeg', 'm4a', 'wav']):
            # Если расширения нет (как в твоем логе), принудительно добавляем ogg (для голоса ТГ)
            file_name += ".ogg"

        with open(file_path, "rb") as f:
            try:
                res = await client.audio.transcriptions.create(
                    file=(file_name, f.read()), # Передаем кортеж (имя, контент)
                    model="whisper-large-v3-turbo",
                    response_format="text"
                )
                return res
            except Exception as e:
                return f"❌ Ошибка Whisper: {e}"

    async def get_response(self, prompt, system="You are a helpful assistant"):
        client = random.choice(self.clients)
        res = await client.chat.completions.create(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile"
        )
        return res.choices[0].message.content

    # --- SUPABASE INTEGRATION (Draft) ---
    async def save_to_vector_db(self, user_id, text):
        # Здесь будет логика для Supabase:
        # 1. Генерация эмбеддинга (нужен ключ OpenAI или HuggingFace)
        # 2. Insert в таблицу 'documents' через supabase-py
        pass