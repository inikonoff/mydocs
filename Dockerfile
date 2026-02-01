FROM python:3.10-slim
RUN apt-get update && apt-get install -y ffmpeg fonts-dejavu-core && apt-get clean && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p data
ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]