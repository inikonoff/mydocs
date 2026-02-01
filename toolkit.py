import fitz, os, re, aiohttp, yt_dlp
from docx import Document

class Toolkit:
    @staticmethod
    def clean_vtt(vtt_text):
        lines = vtt_text.splitlines()
        text = []
        for line in lines:
            if '-->' not in line and line.strip() and not line.strip().isdigit():
                clean = re.sub(r'<[^>]*>', '', line).strip()
                if clean and (not text or text[-1] != clean):
                    text.append(clean)
        return " ".join(text)

    @staticmethod
    async def parse_file(path):
        ext = path.split('.')[-1].lower()
        if ext == 'pdf':
            doc = fitz.open(path)
            return "".join([p.get_text() for p in doc])
        if ext in ['docx', 'doc']:
            return "\n".join([p.text for p in Document(path).paragraphs])
        if ext == 'txt':
            with open(path, 'r', encoding='utf-8') as f: return f.read()
        return ""

    @staticmethod
    async def get_cloud_link(url):
        if "yadi.sk" in url or "disk.yandex" in url:
            api = f"https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={url}"
            async with aiohttp.ClientSession() as s:
                async with s.get(api) as r:
                    if r.status == 200: return (await r.json()).get("href")
        if "drive.google.com" in url:
            m = re.search(r'd/([^/]+)', url)
            if m: return f"https://docs.google.com/uc?export=download&id={m.group(1)}"
        return None

    @staticmethod
    async def process_video(url):
        opts = {'skip_download': True, 'writesubtitles': True, 'writeautomaticsub': True, 'subtitleslangs': ['ru', 'en'], 'quiet': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subs = info.get('subtitles') or info.get('automatic_captions')
            if subs:
                for lang in ['ru', 'en']:
                    if lang in subs:
                        async with aiohttp.ClientSession() as s:
                            async with s.get(subs[lang][-1]['url']) as r:
                                return Toolkit.clean_vtt(await r.text())
            return "NEED_WHISPER"

    @staticmethod
    def export_file(text, fmt, user_id):
        path = f"data/result_{user_id}.{fmt}"
        if fmt == 'docx':
            doc = Document(); doc.add_paragraph(text); doc.save(path)
        elif fmt == 'txt':
            with open(path, 'w') as f: f.write(text)
        return path
