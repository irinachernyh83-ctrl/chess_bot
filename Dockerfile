FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ca-certificates нужны для TLS-подключения к Telegram
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data
VOLUME ["/app/data"]

# Бот работает через MTProto, входящий порт не обязателен.
# Если хостинг требует слушать $PORT (Render и т.п.), бот сам поднимет health-сервер.
CMD ["python", "main.py"]
