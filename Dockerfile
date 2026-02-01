FROM python:3.10-slim

# Установка системных библиотек для аудио и PDF
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libmupdf-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Создаем папку для базы данных
RUN mkdir -p /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Запускаем только bot.py (база создастся внутри него)
CMD ["python", "bot.py"]