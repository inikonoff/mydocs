# bot.py
import os
import io
import logging
import asyncio
import sys
import json
import base64
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiohttp import web
from openai import AsyncOpenAI
import random
import mimetypes

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEYS = os.environ.get("GROQ_API_KEYS", "")

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found! Exiting.")
    exit(1)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Импортируем модули
from link_processor import LinkProcessor
from docx_exporter import smart_docx_export

# Инициализируем процессор ссылок
link_processor = LinkProcessor()

# --- ХРАНИЛИЩЕ ТЕКСТОВ С TTL ---
class TextStorage:
    def __init__(self, max_texts=1000, ttl_hours=1):
        self.storage = {}
        self.max_texts = max_texts
        self.ttl_seconds = ttl_hours * 3600
    
    def add(self, user_id: int, text: str, metadata: dict = None):
        """Добавляем текст с TTL"""
        # Автоочистка при превышении лимита
        if len(self.storage) >= self.max_texts:
            self.cleanup()
        
        self.storage[user_id] = {
            "text": text[:10000],  # Ограничиваем размер
            "timestamp": time.time(),
            "expires_at": time.time() + self.ttl_seconds,
            "metadata": metadata or {},
            "questions_count": 0  # Счетчик вопросов
        }
    
    def get(self, user_id: int):
        """Получаем текст, если он не истек"""
        if user_id not in self.storage:
            return None
        
        item = self.storage[user_id]
        if time.time() > item["expires_at"]:
            del self.storage[user_id]
            return None
        
        return item
    
    def increment_questions(self, user_id: int):
        """Увеличиваем счетчик вопросов"""
        if user_id in self.storage:
            self.storage[user_id]["questions_count"] += 1
    
    def cleanup(self):
        """Очистка устаревших текстов"""
        current_time = time.time()
        expired = []
        
        for user_id, item in self.storage.items():
            if current_time > item["expires_at"]:
                expired.append(user_id)
        
        for user_id in expired:
            del self.storage[user_id]
        
        logger.info(f"Cleaned up {len(expired)} expired texts")

text_storage = TextStorage()

# --- FSM СОСТОЯНИЯ ДЛЯ Q&A ---
class QAStates(StatesGroup):
    waiting_for_question = State()

# Хранилище контекста (старое, для совместимости)
user_context = {}

# --- ИНИЦИАЛИЗАЦИЯ GROQ КЛИЕНТОВ ---
groq_clients = []
current_client_index = 0

def init_groq_clients():
    """Инициализация клиентов Groq"""
    global groq_clients
    
    if not GROQ_API_KEYS:
        logger.warning("GROQ_API_KEYS не настроены!")
        return
    
    keys = [key.strip() for key in GROQ_API_KEYS.split(",") if key.strip()]
    
    for key in keys:
        try:
            client = AsyncOpenAI(
                api_key=key,
                base_url="https://api.groq.com/openai/v1",
                timeout=60.0,
            )
            groq_clients.append(client)
            logger.info(f"✅ Groq client: {key[:8]}...")
        except Exception as e:
            logger.error(f"❌ Error client {key[:8]}: {e}")
    
    logger.info(f"✅ Total clients: {len(groq_clients)}")

def get_client():
    """Получаем следующего клиента по кругу"""
    if not groq_clients:
        return None
    
    global current_client_index
    client = groq_clients[current_client_index]
    current_client_index = (current_client_index + 1) % len(groq_clients)
    return client

async def make_groq_request(func, *args, **kwargs):
    """Делаем запрос с перебором ключей"""
    if not groq_clients:
        raise Exception("No Groq clients available")
    
    errors = []
    
    for _ in range(len(groq_clients) * 2):
        client = get_client()
        if not client:
            break
        
        try:
            return await func(client, *args, **kwargs)
        except Exception as e:
            errors.append(str(e))
            logger.warning(f"Request error: {e}")
            await asyncio.sleep(1 + random.random())
    
    raise Exception(f"All clients failed: {'; '.join(errors[:3])}")

# --- VISION ПРОЦЕССОР ---
class VisionProcessor:
    def __init__(self):
        pass
    
    async def check_content(self, image_bytes: bytes) -> tuple[bool, str]:
        """Проверка изображения на образовательный контент"""
        if len(image_bytes) > 10 * 1024 * 1024:
            return False, "Изображение слишком большое. Попробуйте сфотографировать ближе."
        
        if not groq_clients:
            return True, "OK"
        
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        async def analyze(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": """Analyze this image. Respond ONLY with JSON:
{
  "is_educational": true/false,
  "content_type": "homework/textbook/notes/diagram/inappropriate/unclear/other"
}"""
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.2,
                max_tokens=150
            )
            return response.choices[0].message.content
        
        try:
            result = await make_groq_request(analyze)
            analysis = json.loads(result)
            
            is_educational = analysis.get("is_educational", False)
            content_type = analysis.get("content_type", "unclear")
            
            if not is_educational:
                messages = {
                    "inappropriate": "Пожалуйста, отправляйте только текстовые материалы для обработки.",
                    "unclear": "Изображение нечёткое. Попробуйте сфотографировать ещё раз при хорошем освещении.",
                    "other": "Я вижу изображение, но не могу найти там текст. Отправьте фото с текстом или текстовый файл."
                }
                message = messages.get(content_type, "Отправьте, пожалуйста, фото с текстом.")
                return False, message
            
            return True, "OK"
            
        except Exception as e:
            logger.warning(f"Vision check error: {e}")
            return True, "OK"
    
    async def extract_text(self, image_bytes: bytes) -> str:
        """OCR через Groq Vision"""
        if not groq_clients:
            return "❌ Для распознавания изображений нужны ключи Groq API."
        
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        async def extract(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": """Распознай и перепиши ВЕСЬ текст с этого изображения максимально точно."""
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.1,
                max_tokens=4000,
            )
            return response.choices[0].message.content
        
        try:
            return await make_groq_request(extract)
        except Exception as e:
            logger.error(f"Vision OCR error: {e}")
            return f"❌ Ошибка распознавания текста: {str(e)[:100]}"

vision_processor = VisionProcessor()

# --- GROQ СЕРВИСЫ ---
async def transcribe_voice(audio_bytes: bytes) -> str:
    """Транскрибация голоса"""
    async def transcribe(client):
        return await client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=("audio.ogg", audio_bytes, "audio/ogg"),
            language="ru",
            response_format="text",
        )
    
    try:
        return await make_groq_request(transcribe)
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return f"❌ Ошибка распознавания: {str(e)[:100]}"

async def correct_text_basic(text: str) -> str:
    """Базовая коррекция"""
    if not text.strip():
        return "❌ Пустой текст"
    
    async def correct(client):
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Ты редактор русского языка. Только исправляешь ошибки."},
                {"role": "user", "content": f"Исправь орфографические и пунктуационные ошибки в тексте:\n\n{text}"}
            ],
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()
    
    try:
        return await make_groq_request(correct)
    except Exception as e:
        logger.error(f"Basic correction error: {e}")
        return f"❌ Ошибка коррекции: {str(e)[:100]}"

async def correct_text_premium(text: str) -> str:
    """Премиум коррекция"""
    if not text.strip():
        return "❌ Пустой текст"
    
    async def correct(client):
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Ты профессиональный редактор и стилист."},
                {"role": "user", "content": f"""Отредактируй текст профессионально:
1. Исправь все ошибки
2. Удали слова-паразиты
3. Замени матерные слова на литературные аналоги
4. Улучши стиль

Текст для редактирования:\n\n{text}"""}
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    
    try:
        return await make_groq_request(correct)
    except Exception as e:
        logger.error(f"Premium correction error: {e}")
        return f"❌ Ошибка коррекции: {str(e)[:100]}"

async def summarize_text(text: str) -> str:
    """Создание саммари"""
    if not text.strip():
        return "❌ Пустой текст"
    
    words = text.split()
    if len(words) < 50:
        return "📝 Текст слишком короткий для саммари."
    
    async def summarize(client):
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Ты создаешь краткие содержательные саммари."},
                {"role": "user", "content": f"Сделай краткое содержательное саммари текста:\n\n{text}"}
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    
    try:
        return await make_groq_request(summarize)
    except Exception as e:
        logger.error(f"Summarization error: {e}")
        return f"❌ Ошибка создания саммари: {str(e)[:100]}"

# --- Q&A ФУНКЦИИ ---
async def answer_question_about_text(text: str, question: str) -> str:
    """Ответ на вопрос о тексте"""
    if not text.strip():
        return "❌ Текст не найден."
    
    async def answer(client):
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system", 
                    "content": """Ты помощник, который отвечает на вопросы ТОЛЬКО на основе предоставленного текста.
                    Если в тексте нет ответа, скажи об этом.
                    Отвечай точно и по делу."""
                },
                {
                    "role": "user",
                    "content": f"""Текст для анализа:
{text[:3000]}

Вопрос: {question}

Ответь на вопрос, используя только информацию из текста выше."""
                }
            ],
            temperature=0.3,
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    
    try:
        return await make_groq_request(answer)
    except Exception as e:
        logger.error(f"Q&A error: {e}")
        return f"❌ Ошибка при ответе на вопрос: {str(e)[:100]}"

# --- ФУНКЦИИ ДЛЯ ФАЙЛОВ ---
async def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Извлечение текста из PDF"""
    try:
        from PyPDF2 import PdfReader
        pdf_buffer = io.BytesIO(pdf_bytes)
        reader = PdfReader(pdf_buffer)
        text = ""
        
        for page_num, page in enumerate(reader.pages, 1):
            page_text = page.extract_text()
            if page_text:
                text += f"\n--- Страница {page_num} ---\n"
                text += page_text + "\n"
        
        return text.strip() if text else "Не удалось извлечь текст из PDF"
    except ImportError:
        return "❌ Для работы с PDF требуется установить PyPDF2"
    except Exception as e:
        return f"❌ Ошибка обработки PDF: {str(e)}"

async def extract_text_from_docx(docx_bytes: bytes) -> str:
    """Извлечение текста из DOCX"""
    try:
        import docx
        doc_buffer = io.BytesIO(docx_bytes)
        doc = docx.Document(doc_buffer)
        text = ""
        
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text += paragraph.text + "\n"
        
        return text.strip() if text else "Документ пуст"
    except ImportError:
        return "❌ Для работы с DOCX требуется установить python-docx"
    except Exception as e:
        return f"❌ Ошибка обработки DOCX: {str(e)}"

async def extract_text_from_txt(txt_bytes: bytes) -> str:
    """Извлечение текста из TXT"""
    try:
        encodings = ['utf-8', 'cp1251', 'koi8-r', 'windows-1251', 'iso-8859-1']
        
        for encoding in encodings:
            try:
                return txt_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        
        return txt_bytes.decode('utf-8', errors='ignore')
    except Exception as e:
        return f"❌ Ошибка чтения текстового файла: {str(e)}"

async def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """Определяем тип файла и извлекаем текст"""
    mime_type, _ = mimetypes.guess_type(filename)
    
    if mime_type:
        if mime_type.startswith('image/'):
            is_educational, message = await vision_processor.check_content(file_bytes)
            if not is_educational:
                return f"❌ {message}"
            
            logger.info("🔍 Распознаю текст с изображения...")
            return await vision_processor.extract_text(file_bytes)
        
        elif mime_type == 'application/pdf':
            return await extract_text_from_pdf(file_bytes)
        
        elif mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
            return await extract_text_from_docx(file_bytes)
        
        elif mime_type == 'text/plain':
            return await extract_text_from_txt(file_bytes)
    
    file_ext = filename.lower().split('.')[-1] if '.' in filename else ''
    
    if file_ext in ['jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp']:
        is_educational, message = await vision_processor.check_content(file_bytes)
        if not is_educational:
            return f"❌ {message}"
        
        logger.info("🔍 Распознаю текст с изображения...")
        return await vision_processor.extract_text(file_bytes)
    
    elif file_ext == 'pdf':
        return await extract_text_from_pdf(file_bytes)
    
    elif file_ext == 'docx':
        return await extract_text_from_docx(file_bytes)
    
    elif file_ext == 'txt':
        return await extract_text_from_txt(file_bytes)
    
    elif file_ext == 'doc':
        return "❌ DOC файлы не поддерживаются. Сохраните файл как DOCX."
    
    else:
        return f"❌ Неподдерживаемый формат файла: .{file_ext}"

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_available_modes(text: str) -> list:
    """Определяем доступные режимы обработки"""
    words = text.split()
    if len(words) < 50 or len(text) < 300:
        return ["basic", "premium"]
    return ["basic", "premium", "summary"]

def create_options_keyboard(user_id: int, with_qa=False) -> types.InlineKeyboardMarkup:
    """Создаем клавиатуру с вариантами обработки"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        types.InlineKeyboardButton(text="📝 Как есть", callback_data=f"process_{user_id}_basic"),
        types.InlineKeyboardButton(text="✨ Красиво", callback_data=f"process_{user_id}_premium"),
    )
    
    builder.row(
        types.InlineKeyboardButton(text="📊 Саммари", callback_data=f"process_{user_id}_summary"),
    )
    
    if with_qa:
        builder.row(
            types.InlineKeyboardButton(text="💬 Задать вопрос о тексте", callback_data=f"start_qa_{user_id}"),
        )
    
    return builder.as_markup()

def create_switch_keyboard(user_id: int, with_qa=False) -> types.InlineKeyboardMarkup:
    """Создаем клавиатуру для переключения между режимами"""
    ctx = user_context.get(user_id)
    if not ctx:
        return None
    
    current = ctx.get("current_mode")
    available = ctx.get("available_modes", [])
    
    builder = InlineKeyboardBuilder()
    
    mode_buttons = []
    if "basic" in available and current != "basic":
        mode_buttons.append(types.InlineKeyboardButton(text="📝 Как есть", callback_data=f"switch_{user_id}_basic"))
    if "premium" in available and current != "premium":
        mode_buttons.append(types.InlineKeyboardButton(text="✨ Красиво", callback_data=f"switch_{user_id}_premium"))
    if "summary" in available and current != "summary":
        mode_buttons.append(types.InlineKeyboardButton(text="📊 Саммари", callback_data=f"switch_{user_id}_summary"))
    
    for i in range(0, len(mode_buttons), 2):
        builder.row(*mode_buttons[i:i+2])
    
    builder.row(
        types.InlineKeyboardButton(text="📄 TXT", callback_data=f"export_{user_id}_{current}_txt"),
        types.InlineKeyboardButton(text="📝 DOCX", callback_data=f"export_{user_id}_{current}_docx")
    )
    
    if with_qa:
        builder.row(
            types.InlineKeyboardButton(text="💬 Задать вопрос о тексте", callback_data=f"start_qa_{user_id}"),
        )
    
    return builder.as_markup()

async def save_to_file(user_id: int, text: str, format_type: str, mode: str = None) -> str:
    """Сохраняем текст в файл"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"text_{user_id}_{timestamp}"
    
    if format_type == "txt":
        filepath = f"/tmp/{filename}.txt"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text)
        return filepath
        
    elif format_type == "docx":
        try:
            import tempfile
            from docx import Document
            
            doc = smart_docx_export(text, mode)
            
            filepath = f"/tmp/{filename}.docx"
            doc.save(filepath)
            return filepath
            
        except ImportError:
            logger.warning("python-docx not installed, using txt fallback")
            filepath = f"/tmp/{filename}.txt"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(text)
            return filepath
        except Exception as e:
            logger.error(f"DOCX export error: {e}")
            return None
    
    return None

async def start_qa_session(user_id: int, state: FSMContext):
    """Запускаем сессию вопросов"""
    ctx = user_context.get(user_id)
    if not ctx:
        return "❌ Текст не найден. Обработайте текст заново."
    
    # Сохраняем текст в хранилище с TTL
    text_storage.add(
        user_id=user_id,
        text=ctx["original"],
        metadata={
            "type": ctx["type"],
            "mode": ctx.get("current_mode", "original"),
            "source": "processed_text"
        }
    )
    
    await state.set_state(QAStates.waiting_for_question)
    await state.update_data(user_id=user_id)
    
    return (
        "💬 **Режим вопросов включен** (1 час | 5 вопросов)\n"
        "Задавайте вопросы по этому тексту.\n"
        "/cancel - выход из режима"
    )

async def process_question(user_id: int, question: str):
    """Обрабатываем вопрос пользователя"""
    item = text_storage.get(user_id)
    if not item:
        return "❌ Время для вопросов истекло. Обработайте текст заново."
    
    if item["questions_count"] >= 5:
        return "⚠️ Лимит вопросов исчерпан. Для новых вопросов обработайте текст заново."
    
    # Увеличиваем счетчик вопросов
    text_storage.increment_questions(user_id)
    
    # Отвечаем на вопрос
    answer = await answer_question_about_text(item["text"], question)
    
    # Добавляем информацию о лимите
    remaining = 5 - item["questions_count"] - 1
    if remaining > 0:
        answer += f"\n\n⏳ Осталось вопросов: {remaining}"
    else:
        answer += "\n\n⚠️ Это был последний вопрос. Режим завершен."
    
    return answer

# --- ВЕБ-СЕРВЕР ---
async def health_check(request):
    return web.Response(text="Bot is alive!", status=200)

async def start_web_server():
    try:
        app = web.Application()
        app.router.add_get('/', health_check)
        app.router.add_get('/health', health_check)
        app.router.add_get('/ping', health_check)
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"✅ WEB SERVER STARTED ON PORT {port}")
    except Exception as e:
        logger.error(f"❌ Error starting web server: {e}")

# --- ХЭНДЛЕРЫ БОТА ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "👋 <b>Текст-редактор бот Грамотей</b>\n\n"
        "📁 <b>Что я умею:</b>\n"
        "• Распознавать текст с изображений\n"
        "• Читать текст из файлов (PDF, DOCX, TXT)\n"
        "• Обрабатывать ссылки (YouTube, Яндекс.Диск)\n"
        "• Транскрибировать голосовые сообщения\n"
        "• Отвечать на вопросы по тексту\n\n"
        "📌 <b>Просто отправьте:</b>\n"
        "• Текст сообщением\n"
        "• Фото/файл с текстом\n"
        "• Ссылку на видео/файл\n"
        "• Голосовое сообщение",
        parse_mode="HTML"
    )

@dp.message(Command("help"))
async def help_handler(message: types.Message):
    await message.answer(
        "📋 <b>Как использовать:</b>\n\n"
        "1. Отправьте текст любым способом\n"
        "2. Выберите вариант обработки\n"
        "3. Переключайтесь между вариантами\n"
        "4. Экспортируйте в файлы\n"
        "5. Задавайте вопросы по тексту\n\n"
        "🔗 <b>Поддерживаемые ссылки:</b>\n"
        "• YouTube видео\n"
        "• Яндекс.Диск файлы\n"
        "• Прямые ссылки на файлы",
        parse_mode="HTML"
    )

@dp.message(Command("status"))
async def status_handler(message: types.Message):
    text_storage.cleanup()
    status_text = (
        f"🤖 <b>Статус бота:</b>\n"
        f"• Groq клиентов: {len(groq_clients)}\n"
        f"• Текстов в памяти: {len(text_storage.storage)}\n"
        f"• Активные Q&A: {len([v for v in text_storage.storage.values() if v['questions_count'] > 0])}"
    )
    await message.answer(status_text, parse_mode="HTML")

@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.clear()
    await message.answer("❌ Режим вопросов отключен.")

# --- ОБРАБОТКА ССЫЛОК ---
@dp.message(F.text & ~F.text.startswith('/'))
async def link_or_text_handler(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Проверяем, является ли это ссылкой
    if link_processor.is_link(text):
        await process_link_message(message, text)
    else:
        # Обрабатываем как обычный текст
        await text_handler(message)

async def process_link_message(message: types.Message, url: str):
    user_id = message.from_user.id
    msg = await message.answer("🔗 Обрабатываю ссылку...")
    
    try:
        # Пробуем извлечь текст из ссылки
        original_text = await link_processor.process_url(url)
        
        if original_text.startswith("❌"):
            await msg.edit_text(original_text)
            return
        
        # Определяем доступные режимы
        available_modes = get_available_modes(original_text)
        
        # Сохраняем контекст
        user_context[user_id] = {
            "type": "link",
            "original": original_text,
            "cached_results": {"basic": None, "premium": None, "summary": None},
            "current_mode": None,
            "available_modes": available_modes,
            "message_id": msg.message_id,
            "chat_id": message.chat.id,
            "url": url
        }
        
        # Предлагаем варианты
        preview = original_text[:200] + "..." if len(original_text) > 200 else original_text
        
        modes_text = "📝 Как есть, ✨ Красиво"
        if "summary" in available_modes:
            modes_text += ", 📊 Саммари"
        
        await msg.edit_text(
            f"✅ <b>Извлеченный текст из ссылки:</b>\n\n"
            f"<i>{preview}</i>\n\n"
            f"<b>Доступные режимы:</b> {modes_text}\n"
            f"<b>Выберите вариант обработки:</b>",
            parse_mode="HTML",
            reply_markup=create_options_keyboard(user_id)
        )
        
    except Exception as e:
        logger.error(f"Link processing error: {e}")
        await msg.edit_text(f"❌ Ошибка обработки ссылки: {str(e)[:100]}")

# --- ОСТАЛЬНЫЕ ХЭНДЛЕРЫ (voice_handler, text_handler, file_handler) ---
# Они остаются такими же, но добавлю обработку Q&A

@dp.message(F.voice | F.audio)
async def voice_handler(message: types.Message):
    user_id = message.from_user.id
    msg = await message.answer("🎧 Распознаю голосовое сообщение...")
    
    try:
        if message.voice:
            file_info = await bot.get_file(message.voice.file_id)
        else:
            file_info = await bot.get_file(message.audio.file_id)
        
        voice_buffer = io.BytesIO()
        await bot.download_file(file_info.file_path, voice_buffer)
        
        original_text = await transcribe_voice(voice_buffer.getvalue())
        
        if original_text.startswith("❌"):
            await msg.edit_text(original_text)
            return
        
        available_modes = get_available_modes(original_text)
        
        user_context[user_id] = {
            "type": "voice",
            "original": original_text,
            "cached_results": {"basic": None, "premium": None, "summary": None},
            "current_mode": None,
            "available_modes": available_modes,
            "message_id": msg.message_id,
            "chat_id": message.chat.id
        }
        
        preview = original_text[:200] + "..." if len(original_text) > 200 else original_text
        
        modes_text = "📝 Как есть, ✨ Красиво"
        if "summary" in available_modes:
            modes_text += ", 📊 Саммари"
        
        await msg.edit_text(
            f"✅ <b>Распознанный текст:</b>\n\n"
            f"<i>{preview}</i>\n\n"
            f"<b>Доступные режимы:</b> {modes_text}\n"
            f"<b>Выберите вариант обработки:</b>",
            parse_mode="HTML",
            reply_markup=create_options_keyboard(user_id, with_qa=True)
        )
        
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await msg.edit_text("❌ Ошибка обработки голосового сообщения")

@dp.message(F.text & ~F.text.startswith('/'))
async def text_handler(message: types.Message):
    user_id = message.from_user.id
    original_text = message.text.strip()
    
    if original_text.startswith("/"):
        return
    
    msg = await message.answer("📝 Анализирую текст...")
    
    try:
        available_modes = get_available_modes(original_text)
        
        user_context[user_id] = {
            "type": "text",
            "original": original_text,
            "cached_results": {"basic": None, "premium": None, "summary": None},
            "current_mode": None,
            "available_modes": available_modes,
            "message_id": msg.message_id,
            "chat_id": message.chat.id
        }
        
        preview = original_text[:200] + "..." if len(original_text) > 200 else original_text
        
        modes_text = "📝 Как есть, ✨ Красиво"
        if "summary" in available_modes:
            modes_text += ", 📊 Саммари"
        
        await msg.edit_text(
            f"📝 <b>Полученный текст:</b>\n\n"
            f"<i>{preview}</i>\n\n"
            f"<b>Доступные режимы:</b> {modes_text}\n"
            f"<b>Выберите вариант обработки:</b>",
            parse_mode="HTML",
            reply_markup=create_options_keyboard(user_id, with_qa=True)
        )
        
    except Exception as e:
        logger.error(f"Text error: {e}")
        await msg.edit_text("❌ Ошибка обработки текста")

@dp.message(F.photo | F.document)
async def file_handler(message: types.Message):
    user_id = message.from_user.id
    msg = await message.answer("📁 Обрабатываю файл...")
    
    try:
        file_info = None
        file_bytes = None
        filename = ""
        
        if message.photo:
            file_info = await bot.get_file(message.photo[-1].file_id)
            filename = f"photo_{file_info.file_unique_id}.jpg"
        elif message.document:
            file_info = await bot.get_file(message.document.file_id)
            filename = message.document.file_name or f"file_{file_info.file_unique_id}"
        
        file_buffer = io.BytesIO()
        await bot.download_file(file_info.file_path, file_buffer)
        file_bytes = file_buffer.getvalue()
        
        if len(file_bytes) > 10 * 1024 * 1024:
            await msg.edit_text("❌ Файл слишком большой (максимум 10 MB)")
            return
        
        status_msg = await msg.edit_text("🔍 Извлекаю текст...")
        original_text = await extract_text_from_file(file_bytes, filename)
        
        if original_text.startswith("❌"):
            await status_msg.edit_text(original_text)
            return
        
        if not original_text.strip() or len(original_text.strip()) < 10:
            await status_msg.edit_text(
                "❌ Не удалось найти текст в файле."
            )
            return
        
        available_modes = get_available_modes(original_text)
        
        user_context[user_id] = {
            "type": "file",
            "original": original_text,
            "cached_results": {"basic": None, "premium": None, "summary": None},
            "current_mode": None,
            "available_modes": available_modes,
            "message_id": msg.message_id,
            "chat_id": message.chat.id,
            "filename": filename
        }
        
        preview = original_text[:200] + "..." if len(original_text) > 200 else original_text
        
        modes_text = "📝 Как есть, ✨ Красиво"
        if "summary" in available_modes:
            modes_text += ", 📊 Саммари"
        
        await status_msg.edit_text(
            f"✅ <b>Извлеченный текст:</b>\n\n"
            f"<i>{preview}</i>\n\n"
            f"<b>Доступные режимы:</b> {modes_text}\n"
            f"<b>Выберите вариант обработки:</b>",
            parse_mode="HTML",
            reply_markup=create_options_keyboard(user_id, with_qa=True)
        )
        
    except Exception as e:
        logger.error(f"File error: {e}")
        await msg.edit_text(f"❌ Ошибка обработки файла: {str(e)[:100]}")

# --- Q&A CALLBACK HANDLERS ---
@dp.callback_query(F.data.startswith("start_qa_"))
async def start_qa_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            return
        
        target_user_id = int(parts[2])
        
        if callback.from_user.id != target_user_id:
            await callback.message.answer("⚠️ Это не ваш запрос!")
            return
        
        response = await start_qa_session(target_user_id, state)
        await callback.message.answer(response)
        
    except Exception as e:
        logger.error(f"Start Q&A error: {e}")
        await callback.message.answer("❌ Ошибка запуска режима вопросов")

@dp.message(StateFilter(QAStates.waiting_for_question))
async def handle_question(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверяем, есть ли текст для вопросов
    item = text_storage.get(user_id)
    if not item:
        await message.answer("❌ Время для вопросов истекло. Обработайте текст заново.")
        await state.clear()
        return
    
    if item["questions_count"] >= 5:
        await message.answer("⚠️ Лимит вопросов исчерпан. Для новых вопросов обработайте текст заново.")
        await state.clear()
        return
    
    # Обрабатываем вопрос
    msg = await message.answer("🤔 Ищу ответ...")
    question = message.text
    
    answer = await process_question(user_id, question)
    
    await msg.edit_text(answer)
    
    # Проверяем лимит после ответа
    item = text_storage.get(user_id)
    if item and item["questions_count"] >= 5:
        await message.answer("✅ Режим вопросов завершен.")
        await state.clear()

# --- PROCESS, SWITCH, EXPORT HANDLERS (остаются похожими, но добавляем кнопку Q&A) ---
@dp.callback_query(F.data.startswith("process_"))
async def process_callback(callback: types.CallbackQuery):
    await callback.answer()
    
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            return
        
        target_user_id = int(parts[1])
        process_type = parts[2]
        
        if callback.from_user.id != target_user_id:
            await callback.message.answer("⚠️ Это не ваш запрос!")
            return
        
        if target_user_id not in user_context:
            await callback.message.edit_text("❌ Время обработки истекло.")
            return
        
        ctx = user_context[target_user_id]
        available_modes = ctx.get("available_modes", [])
        
        if process_type not in available_modes:
            await callback.answer("⚠️ Этот режим недоступен", show_alert=True)
            return
        
        original_text = ctx["original"]
        
        processing_msg = await callback.message.edit_text(f"⏳ Обрабатываю ({process_type})...")
        
        if process_type == "basic":
            result = await correct_text_basic(original_text)
        elif process_type == "premium":
            result = await correct_text_premium(original_text)
        elif process_type == "summary":
            result = await summarize_text(original_text)
        else:
            result = "Неизвестный тип обработки"
        
        user_context[target_user_id]["cached_results"][process_type] = result
        user_context[target_user_id]["current_mode"] = process_type
        
        if len(result) > 4000:
            await processing_msg.delete()
            
            for i in range(0, len(result), 4000):
                await callback.message.answer(result[i:i+4000])
            
            await callback.message.answer(
                "💾 <b>Действия с текстом:</b>",
                parse_mode="HTML",
                reply_markup=create_switch_keyboard(target_user_id, with_qa=True)
            )
        else:
            await processing_msg.edit_text(
                result,
                reply_markup=create_switch_keyboard(target_user_id, with_qa=True)
            )
            
    except Exception as e:
        logger.error(f"Process error: {e}")
        await callback.message.edit_text("❌ Ошибка обработки")

@dp.callback_query(F.data.startswith("switch_"))
async def switch_callback(callback: types.CallbackQuery):
    await callback.answer()
    
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            return
        
        target_user_id = int(parts[1])
        target_mode = parts[2]
        
        if callback.from_user.id != target_user_id:
            return
        
        if target_user_id not in user_context:
            await callback.message.answer("❌ Текст не найден.")
            return
        
        ctx = user_context[target_user_id]
        available_modes = ctx.get("available_modes", [])
        
        if target_mode not in available_modes:
            await callback.answer("⚠️ Этот режим недоступен", show_alert=True)
            return
        
        cached = ctx["cached_results"].get(target_mode)
        
        if cached:
            result = cached
        else:
            processing_msg = await callback.message.edit_text(f"⏳ Обрабатываю ({target_mode})...")
            
            original_text = ctx["original"]
            
            if target_mode == "basic":
                result = await correct_text_basic(original_text)
            elif target_mode == "premium":
                result = await correct_text_premium(original_text)
            elif target_mode == "summary":
                result = await summarize_text(original_text)
            else:
                result = "Неизвестный режим"
            
            user_context[target_user_id]["cached_results"][target_mode] = result
        
        user_context[target_user_id]["current_mode"] = target_mode
        
        if len(result) > 4000:
            await callback.message.delete()
            
            for i in range(0, len(result), 4000):
                await callback.message.answer(result[i:i+4000])
            
            await callback.message.answer(
                "💾 <b>Действия с текстом:</b>",
                parse_mode="HTML",
                reply_markup=create_switch_keyboard(target_user_id, with_qa=True)
            )
        else:
            await callback.message.edit_text(
                result,
                reply_markup=create_switch_keyboard(target_user_id, with_qa=True)
            )
            
    except Exception as e:
        logger.error(f"Switch error: {e}")
        await callback.message.edit_text("❌ Ошибка переключения режима")

@dp.callback_query(F.data.startswith("export_"))
async def export_callback(callback: types.CallbackQuery):
    await callback.answer()
    
    try:
        parts = callback.data.split("_")
        if len(parts) < 4:
            return
        
        target_user_id = int(parts[1])
        mode = parts[2]
        export_format = parts[3]
        
        if callback.from_user.id != target_user_id:
            return
        
        if target_user_id not in user_context:
            await callback.message.answer("❌ Текст не найден.")
            return
        
        ctx = user_context[target_user_id]
        text = ctx["cached_results"].get(mode)
        
        if not text:
            await callback.answer("⚠️ Текст не найден в кэше", show_alert=True)
            return
        
        status_msg = await callback.message.answer("📁 Создаю файл...")
        filepath = await save_to_file(target_user_id, text, export_format, mode)
        
        if not filepath:
            await status_msg.edit_text("❌ Ошибка создания файла")
            return
        
        filename = os.path.basename(filepath)
        
        if export_format == "docx":
            caption = "📝 DOCX файл с обработанным текстом"
        else:
            caption = "📄 Текстовый файл с обработанным текстом"
        
        document = types.FSInputFile(filepath, filename=filename)
        await callback.message.answer_document(document=document, caption=caption)
        
        await status_msg.delete()
        
        try:
            os.remove(filepath)
        except:
            pass
        
    except Exception as e:
        logger.error(f"Export error: {e}")
        await callback.message.answer("❌ Ошибка создания файла")

# --- ФОНОВЫЕ ЗАДАЧИ ---
async def background_cleanup():
    """Фоновая очистка устаревших текстов"""
    while True:
        try:
            text_storage.cleanup()
            await asyncio.sleep(300)  # Каждые 5 минут
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            await asyncio.sleep(60)

# --- ЗАПУСК ---
async def main():
    logger.info("Bot starting process...")
    
    init_groq_clients()
    
    # Запускаем фоновые задачи
    asyncio.create_task(start_web_server())
    asyncio.create_task(background_cleanup())
    
    logger.info("🚀 Starting polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")