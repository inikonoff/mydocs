import fitz  # PyMuPDF
import os
import re
import aiohttp
import yt_dlp
from docx import Document

class Toolkit:
    @staticmethod
    def format_ts(ms):
        td = int(ms / 1000)
        m, s = divmod(td, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    @staticmethod
    async def process_video(url):
        opts = {'skip_download': True, 'writesubtitles': True, 'writeautomaticsub': True, 'subtitleslangs': ['ru', 'en'], 'quiet': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subs = info.get('subtitles') or info.get('automatic_captions')
            if subs:
                lang = 'ru' if 'ru' in subs else 'en'
                try:
                    # Ищем json3 формат для извлечения таймкодов
                    url_json = next(s['url'] for s in subs[lang] if 'json3' in s['url'] or 'json' in s['url'])
                    async with aiohttp.ClientSession() as s:
                        async with s.get(url_json) as r:
                            data = await r.json()
                            res = []
                            last_t = -60000 # Метка раз в минуту
                            for ev in data.get('events', []):
                                start = ev.get('tStartMs', 0)
                                if start - last_t > 60000:
                                    res.append(f"\n📍 [{Toolkit.format_ts(start)}]")
                                    last_t = start
                                for seg in ev.get('segs', []):
                                    res.append(seg.get('utf8', '').strip())
                            return " ".join(res)
                except Exception as e:
                    return f"Ошибка парсинга субтитров: {e}"
            return "NEED_WHISPER"

    @staticmethod
    async def parse_file(path):
        ext = path.split('.')[-1].lower()
        if ext == 'pdf':
            # PyMuPDF намного лучше извлекает текст
            text = ""
            with fitz.open(path) as doc:
                for page in doc:
                    text += page.get_text()
            return text
        if ext in ['docx', 'doc']:
            return "\n".join([p.text for p in Document(path).paragraphs])
        if ext == 'txt':
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        return ""