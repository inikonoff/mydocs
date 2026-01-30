FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости системы (включая ffmpeg для yt-dlp)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Создаем директорию для временных файлов
RUN mkdir -p /tmp

# Запускаем бота
CMD ["python", "bot.py"]