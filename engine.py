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
        # Добавляем расширение для корректной работы Whisper
        if not any(file_name.lower().endswith(ext) for ext in ['.ogg', '.mp3', '.mp4', '.m4a', '.wav']):
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
                return f"❌ Ошибка распознавания: {e}"

    async def get_response(self, prompt, system="You are a helpful assistant", force_light=False):
        client = random.choice(self.clients)
        
        # Жесткая обрезка промпта для влезания в бесплатный лимит 6000 TPM
        safe_prompt = prompt[:4500] 
        
        # Автоматический выбор модели: если текст длинный — только легкая модель
        selected_model = "llama-3.1-8b-instant" if (len(safe_prompt) > 3000 or force_light) else "llama-3.3-70b-specdec"
        
        try:
            res = await client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": safe_prompt}
                ],
                model=selected_model
            )
            return res.choices[0].message.content
        except Exception as e:
            if "rate_limit" in str(e).lower() and not force_light:
                return await self.get_response(safe_prompt, system, force_light=True)
            return f"AI Error: {e}"