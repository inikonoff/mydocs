import os
import random
import re
from openai import AsyncOpenAI

class GroqEngine:
    def __init__(self):
        keys = os.getenv("GROQ_API_KEYS", "").split(",")
        self.clients = [AsyncOpenAI(api_key=k.strip(), base_url="https://api.groq.com/openai/v1") for k in keys]

    async def get_response(self, prompt, system="You are a helpful assistant", model="llama-3.3-70b-specdec"):
        client = random.choice(self.clients)
        try:
            res = await client.chat.completions.create(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                model=model
            )
            return res.choices[0].message.content
        except Exception as e:
            return f"AI Error: {e}"

    async def transcribe(self, file_path):
        client = random.choice(self.clients)
        with open(file_path, "rb") as f:
            res = await client.audio.transcriptions.create(
                file=(os.path.basename(file_path), f.read()),
                model="whisper-large-v3-turbo"
            )
            return res.text

    async def detect_and_translate(self, text):
        if not text or len(text) < 10: return None
        prompt = f"Determine the language. If not Russian, translate to Russian. If Russian, reply ONLY 'SKIP'. Text: {text[:500]}"
        res = await self.get_response(prompt, "You are a translator.")
        return None if "SKIP" in res.upper() else await self.get_response(f"Translate to Russian: {text[:3000]}")