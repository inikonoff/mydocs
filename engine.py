import os
import random
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

class GroqEngine:
    def __init__(self):
        keys = os.getenv("GROQ_API_KEYS", "").split(",")
        self.clients = [AsyncOpenAI(api_key=k.strip(), base_url="https://api.groq.com/openai/v1") for k in keys if k.strip()]

    async def transcribe(self, file_path):
        client = random.choice(self.clients)
        file_name = os.path.basename(file_path)
        # Гарантируем расширение для Whisper
        if not any(file_name.lower().endswith(ext) for ext in ['.ogg', '.mp3', '.mp4', '.mpeg', '.m4a', '.wav']):
            file_name += ".ogg"

        with open(file_path, "rb") as f:
            try:
                res = await client.audio.transcriptions.create(
                    file=(file_name, f.read()),
                    model="whisper-large-v3-turbo",
                    response_format="text"
                )
                return res
            except Exception as e:
                logger.error(f"Whisper error: {e}")
                return f"❌ Ошибка транскрибации: {e}"

    async def get_response(self, prompt, system="You are a helpful assistant", force_light=False):
        client = random.choice(self.clients)
        
        # ХИТРОСТЬ 1: Если текст большой или стоит флаг, используем 8b модель (у неё лимиты выше)
        # Лимит бесплатного Groq для 70b ~ 6-15к токенов. Для 8b ~ 30к+.
        selected_model = "llama-3.1-8b-instant" if (len(prompt) > 10000 or force_light) else "llama-3.3-70b-specdec"
        
        try:
            res = await client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ],
                model=selected_model
            )
            return res.choices[0].message.content
        except Exception as e:
            if "rate_limit" in str(e).lower() and selected_model != "llama-3.1-8b-instant":
                # Если 70b упала по лимитам, пробуем автоматически переключиться на 8b
                return await self.get_response(prompt, system, force_light=True)
            return f"AI Error: {e}"