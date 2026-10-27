# Oracle Cloud Always Free — пошагово

Это самый надёжный бесплатный вариант для бота 24/7. Нужны аккаунт Oracle
и банковская карта для верификации; в пределах Always Free списаний нет.

## 1. Аккаунт и регион

1. Открой [cloud.oracle.com/free](https://cloud.oracle.com/free) и зарегистрируйся.
2. В качестве **домашнего региона** выбери регион, где доступны Always Free
   ресурсы (например, Frankfurt, Amsterdam или другой ближайший). Регион потом
   поменять нельзя.
3. Дождись письма об активации аккаунта.

## 2. Создай виртуальную машину

В консоли Oracle: **Compute → Instances → Create instance**.

- **Name**: `chess-bot`
- **Image**: Ubuntu 24.04 (или 22.04)
- **Shape**:
  - `VM.Standard.E2.1.Micro` — самый простой Always Free вариант (1 ГБ RAM);
  - либо `VM.Standard.A1.Flex` — Ampere ARM, если доступен Always Free лимит.
    Для ARM-образа `python:3.12-slim` тоже подходит.
- **Networking**: оставь создание публичной подсети и назначь **public IPv4**.
- **SSH keys**: сгенерируй новую пару или загрузи свой публичный ключ.
  Приватный ключ сохрани — он нужен для входа.

После создания запомни **публичный IP** и имя пользователя (для Ubuntu — `ubuntu`).

Порт 22 должен быть открыт в Security List (обычно открыт по умолчанию).
Входящий HTTP-порт боту не нужен.

## 3. Подключись по SSH

Windows PowerShell:

```powershell
ssh -i "$env:USERPROFILE\.ssh\oracle-key" ubuntu@IP_АДРЕС
```

Если ключ в другом месте — укажи свой путь.

## 4. Установи Docker

На сервере:

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg git unzip
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Выйди из SSH и зайди снова, чтобы применилась группа `docker`.

Если на VM мало RAM (1 ГБ), добавь swap:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 5. Загрузи проект

Готовый архив уже собран: `dist/chessbot-deploy.zip` (без `.env`, базы и сессии).
На **своём компьютере** (PowerShell), из папки проекта:

```powershell
scp -i "$env:USERPROFILE\.ssh\oracle-key" dist\chessbot-deploy.zip ubuntu@IP_АДРЕС:/home/ubuntu/
```

На сервере:

```bash
cd /home/ubuntu
unzip -o chessbot-deploy.zip -d chess-bot
cd chess-bot
cp .env.example .env
nano .env
```

В `.env` впиши `BOT_TOKEN`. Если в локальном `.env` был
`MTPROXY=127.0.0.1:1443`, **не переноси его** на сервер: там этот адрес
указывает внутрь контейнера. Для зарубежной VM Telegram обычно доступен напрямую.

## 6. Запусти

```bash
docker compose up -d --build
docker compose logs -f
```

В логах должно появиться:

```
Бот запущен: @skldlgkjsjskfkkfheja_bot
```

Проверь бота в Telegram. Выход из логов — `Ctrl+C` (контейнер продолжит работу).

## 7. Полезные команды

```bash
cd /home/ubuntu/chess-bot

docker compose ps                 # статус
docker compose logs --tail=100    # последние логи
docker compose restart            # перезапуск
docker compose down               # остановить
docker compose up -d --build      # обновить и запустить
```

База и сессия лежат в Docker-томе `chess-bot_chess_data` и переживают
перезапуск контейнера и перезагрузку VM.

## Если что-то не работает

- `permission denied` при `docker` — перезайди по SSH после `usermod`.
- `Cannot connect to the Docker daemon` — `sudo systemctl start docker`.
- Бот не подключается к Telegram — проверь `BOT_TOKEN`; если регион/сеть
  блокирует Telegram, добавь рабочий `MTPROXY` или `PROXY_URL`.
- Не запускай второй контейнер с тем же токеном: сессия Telethon будет
  конфликтовать.
