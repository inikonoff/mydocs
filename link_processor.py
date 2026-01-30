# link_processor.py
import re
import logging
from urllib.parse import urlparse
import yt_dlp
import requests
import io
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

class LinkProcessor:
    def __init__(self):
        self.youtube_regex = re.compile(
            r'(https?://)?(www\.)?'
            r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
            r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
        )
        
        self.yandex_disk_regex = re.compile(
            r'https?://(disk\.yandex\.ru|yadi\.sk)/'
            r'(d/|client/disk\?public_key=|i/)([a-zA-Z0-9_-]+)'
        )
        
        self.file_regex = re.compile(
            r'\.(pdf|docx?|txt|rtf|odt|jpg|jpeg|png|bmp|gif)$',
            re.IGNORECASE
        )
    
    def is_link(self, text: str) -> bool:
        """Проверяем, является ли текст ссылкой"""
        patterns = [
            self.youtube_regex,
            self.yandex_disk_regex,
            self.file_regex,
            r'^https?://',  # Любая HTTP/HTTPS ссылка
        ]
        
        for pattern in patterns:
            if isinstance(pattern, re.Pattern):
                if pattern.search(text):
                    return True
            elif re.search(pattern, text):
                return True
        
        return False
    
    async def process_url(self, url: str) -> str:
        """Обрабатываем ссылку и извлекаем текст"""
        try:
            # YouTube
            if self.youtube_regex.search(url):
                return await self.extract_youtube_subtitles(url)
            
            # Яндекс.Диск
            elif self.yandex_disk_regex.search(url):
                return await self.download_yandex_disk(url)
            
            # Прямая ссылка на файл
            elif self.file_regex.search(url):
                return await self.download_file(url)
            
            else:
                return "❌ Неподдерживаемый тип ссылки. Отправьте:\n• YouTube видео\n• Яндекс.Диск файл\n• Прямую ссылку на файл (PDF, DOCX, TXT, изображения)"
                
        except Exception as e:
            logger.error(f"Link processing error: {e}")
            return f"❌ Ошибка обработки ссылки: {str(e)[:100]}"
    
    async def extract_youtube_subtitles(self, url: str) -> str:
        """Извлекаем субтитры из YouTube видео"""
        try:
            ydl_opts = {
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['ru', 'en'],
                'skip_download': True,
                'quiet': True,
                'no_warnings': True,
            }
            
            def download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    video_id = info['id']
                    
                    # Пробуем получить субтитры
                    subtitles = info.get('subtitles') or info.get('automatic_captions')
                    
                    if subtitles:
                        # Ищем русские субтитры
                        for lang in ['ru', 'en']:
                            if lang in subtitles:
                                sub_url = subtitles[lang][0]['url']
                                response = requests.get(sub_url)
                                if response.status_code == 200:
                                    # Парсим субтитры
                                    import xml.etree.ElementTree as ET
                                    root = ET.fromstring(response.text)
                                    text_elements = []
                                    
                                    for elem in root.iter():
                                        if elem.text and elem.text.strip():
                                            text_elements.append(elem.text.strip())
                                    
                                    if text_elements:
                                        return " ".join(text_elements)
                    
                    # Если субтитров нет, возвращаем описание
                    description = info.get('description', '')
                    title = info.get('title', '')
                    
                    result = f"Название: {title}\n\nОписание:\n{description}"
                    return result if result.strip() else "❌ Не удалось извлечь текст из видео"
            
            # Запускаем в отдельном потоке
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, download)
            return result
            
        except Exception as e:
            logger.error(f"YouTube processing error: {e}")
            return f"❌ Ошибка обработки YouTube видео: {str(e)[:100]}"
    
    async def download_yandex_disk(self, url: str) -> str:
        """Скачиваем файл с Яндекс.Диска"""
        try:
            # Получаем публичную ссылку на скачивание
            if 'public_key' in url:
                # Извлекаем ключ
                match = re.search(r'public_key=([^&]+)', url)
                if match:
                    public_key = match.group(1)
                    download_url = f"https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={public_key}"
                else:
                    return "❌ Не удалось извлечь ключ из ссылки"
            else:
                # Прямая ссылка
                file_id = url.split('/')[-1]
                download_url = f"https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key=https://yadi.sk/i/{file_id}"
            
            # Получаем ссылку для скачивания
            response = requests.get(download_url)
            if response.status_code != 200:
                return "❌ Не удалось получить доступ к файлу"
            
            data = response.json()
            direct_url = data.get('href')
            
            if not direct_url:
                return "❌ Файл не найден или недоступен"
            
            # Скачиваем файл
            file_response = requests.get(direct_url, stream=True)
            if file_response.status_code != 200:
                return "❌ Ошибка при скачивании файла"
            
            # Определяем тип файла
            content_type = file_response.headers.get('content-type', '')
            filename = data.get('name', 'file')
            
            # Извлекаем текст в зависимости от типа файла
            from bot import extract_text_from_file
            
            file_bytes = file_response.content
            
            if 'application/pdf' in content_type:
                from bot import extract_text_from_pdf
                return await extract_text_from_pdf(file_bytes)
            elif 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' in content_type:
                from bot import extract_text_from_docx
                return await extract_text_from_docx(file_bytes)
            elif 'text/plain' in content_type:
                from bot import extract_text_from_txt
                return await extract_text_from_txt(file_bytes)
            elif any(img_type in content_type for img_type in ['image/jpeg', 'image/png', 'image/gif']):
                from bot import vision_processor
                # Проверяем контент
                is_educational, message = await vision_processor.check_content(file_bytes)
                if not is_educational:
                    return f"❌ {message}"
                # Распознаем текст
                return await vision_processor.extract_text(file_bytes)
            else:
                return f"❌ Неподдерживаемый формат файла: {content_type}"
                
        except Exception as e:
            logger.error(f"Yandex Disk error: {e}")
            return f"❌ Ошибка загрузки с Яндекс.Диска: {str(e)[:100]}"
    
    async def download_file(self, url: str) -> str:
        """Скачиваем файл по прямой ссылке"""
        try:
            response = requests.get(url, stream=True)
            if response.status_code != 200:
                return "❌ Не удалось загрузить файл"
            
            content_type = response.headers.get('content-type', '')
            file_bytes = response.content
            
            # Определяем расширение из URL
            parsed = urlparse(url)
            filename = parsed.path.split('/')[-1] or 'file'
            
            # Извлекаем текст
            from bot import extract_text_from_file
            return await extract_text_from_file(file_bytes, filename)
            
        except Exception as e:
            logger.error(f"File download error: {e}")
            return f"❌ Ошибка загрузки файла: {str(e)[:100]}"