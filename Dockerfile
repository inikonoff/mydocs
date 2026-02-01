# Используем легкий образ Python
FROM python:3.10-slim

# Устанавливаем системные зависимости для работы с PDF и аудио
RUN apt-get update && apt-get install -y \
    build-essential \
    libmupdf-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Создаем папку для базы данных
RUN mkdir -p /app/data

# Копируем список зависимостей
COPY requirements.txt .

# Устанавливаем библиотеки
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код проекта
COPY . .

# Команда для автоматической инициализации БД и запуска бота
CMD python init_db.py && python bot.py