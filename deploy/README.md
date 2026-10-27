# Размещение бота на сервере

Бот работает через MTProto (Telethon) и не принимает входящие HTTP-запросы:
ему нужен постоянный исходящий интернет и **постоянный диск** для папки `data/`.
В `data/` лежат `chess.db` (партии и часы) и `chessbot.session` (сессия Telethon).

Для Oracle Cloud Always Free есть отдельная пошаговая инструкция:
[`oracle-cloud.md`](oracle-cloud.md).
Для старого Android-телефона (Termux, без входящих портов) —
[`termux.md`](termux.md).
Сравнение бесплатных вариантов для России (Render, Bot-Hosting, Bothost,
тестовые VPS) — [`free-hosting.md`](free-hosting.md).

## Вариант 1. Oracle Cloud Always Free (рекомендуется для 24/7)

Бесплатная VM без ограничения по времени; для регистрации нужна карта.

1. Создай аккаунт на [cloud.oracle.com](https://cloud.oracle.com) и выбери
   **Always Free**.
2. Создай VM: образ **Ubuntu 22.04/24.04**, минимальный Always Free shape.
3. Открой SSH и установи Docker:

   ```bash
   sudo apt update
   sudo apt install -y docker.io docker-compose-v2 git
   sudo usermod -aG docker "$USER"
   ```

   Перезайди по SSH, чтобы применилась группа `docker`.
4. Скопируй проект на сервер (git clone или `scp`):

   ```bash
   git clone <ссылка-на-репозиторий> chess-bot
   cd chess-bot
   ```

5. Создай `.env`:

   ```bash
   cp .env.example .env
   nano .env
   ```

   Обязательно заполни `BOT_TOKEN`. Если в `.env` остался локальный
   `MTPROXY=127.0.0.1:1443` от Windows-запуска, на сервере его нужно убрать
   или заменить на прокси, доступный с сервера. Обычно зарубежный сервер
   подключается к Telegram напрямую.

6. Запусти:

   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```

7. Проверь, что бот отвечает в Telegram. После перезагрузки VM контейнер
   поднимется сам (`restart: unless-stopped`).

Обновление:

```bash
git pull
docker compose up -d --build
```

## Вариант 2. Любой Docker-хостинг

Для бесплатного 24/7 без карты выбран **Render** — см.
[`free-hosting.md`](free-hosting.md) и готовый `render.yaml` в корне.

Далее общие требования к любому Docker-хостингу (Fly.io, Koyeb, Render,
Railway и т.п.). Важно:

- запускать **один** экземпляр — два процесса с одной сессией Telethon конфликтуют;
- подключить **persistent volume** к `/app/data`, иначе SQLite и сессия пропадут
  при перезапуске/засыпании;
- задать `BOT_TOKEN`, при необходимости `MTPROXY`/`PROXY_URL`,
  `FINISHED_GAME_TTL`;
- бесплатные тарифы часто засыпают или имеют лимит часов. Для постоянной игры
  лучше Always Free VM или дешёвый VPS.

Сборка и запуск вручную:

```bash
docker build -t chess-bot .
docker run -d --name chess-bot --restart unless-stopped \
  -e BOT_TOKEN=123456789:AA... \
  -e FINISHED_GAME_TTL=600 \
  -v chess_data:/app/data \
  chess-bot
```

## Вариант 3. Обычный VPS

```bash
sudo apt update
sudo apt install -y python3 python3-venv
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py
```

Для автозапуска используй systemd-юнит из основного `README.md`.

## Вариант 4. Старый Android-телефон (Termux)

Полная инструкция: [`termux.md`](termux.md). Коротко:

1. F-Droid → **Termux** и **Termux:Boot** (не из Google Play).
2. Скинуть архив проекта на телефон, распаковать в `~/chessbot`.
3. `bash deploy/termux-install.sh` — ставит зависимости и автозапуск.
4. Заполнить `.env` (`BOT_TOKEN`, без локального `MTPROXY`), запустить
   `termux-wake-lock && python main.py`.
5. В настройках Android разрешить Termux работу в фоне («Батарея → Без
   ограничений»), открыть Termux:Boot один раз.

Телефон не требует входящих портов, но должен быть на зарядке и в сети 24/7.

## Проверка

```bash
docker compose ps
docker compose logs --tail=100
```

В логах должно быть:

```
Бот запущен: @skldlgkjsjskfkkfheja_bot
```

Если видишь ошибки подключения — проверь `BOT_TOKEN`, доступ к Telegram
и настройки `MTPROXY`/`PROXY_URL`. Не публикуй `.env` и папку `data/` в git.
