#!/data/data/com.termux/files/usr/bin/bash
# Установка зависимостей бота в Termux (Android) и настройка автозапуска.
# Запуск из папки проекта:  bash deploy/termux-install.sh
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/chessbot}"
BOOT_DIR="$HOME/.termux/boot"

if [ ! -f "$PROJECT_DIR/main.py" ]; then
  echo "Не найден $PROJECT_DIR/main.py."
  echo "Распакуй проект в ~/chessbot или задай PROJECT_DIR=/путь/к/проекту."
  exit 1
fi

echo "→ Пакеты Termux"
pkg update -y
# python-pillow ставится готовым пакетом: pip-сборка Pillow под Android не работает
pkg install -y python python-pillow unzip

echo "→ Python-зависимости"
python -m pip install --upgrade pip
python -m pip install \
  "python-chess==1.999" \
  "telethon==1.42.0" \
  "python-dotenv==1.1.1" \
  "python-socks[asyncio]==2.7.2"

mkdir -p "$PROJECT_DIR/data"

if [ ! -f "$PROJECT_DIR/.env" ]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "→ Создан $PROJECT_DIR/.env — впиши BOT_TOKEN: nano $PROJECT_DIR/.env"
fi

echo "→ Скрипт автозапуска $BOOT_DIR/start-chessbot.sh"
mkdir -p "$BOOT_DIR"
cat > "$BOOT_DIR/start-chessbot.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
# Автозапуск шахматного бота после перезагрузки телефона (Termux:Boot).
termux-wake-lock
cd "$PROJECT_DIR" || exit 1
while true; do
  python main.py >> data/bot.log 2>&1
  sleep 10
done &
EOF
chmod +x "$BOOT_DIR/start-chessbot.sh"

cat <<EOF

Готово.

1. Заполни токен:      nano $PROJECT_DIR/.env
2. Первый запуск:      cd $PROJECT_DIR && termux-wake-lock && python main.py
3. Разреши фон:        Настройки Android → Приложения → Termux → Батарея → «Без ограничений»
                       и то же для Termux:Boot, приложение Termux:Boot открыть один раз.
4. Логи:               tail -f $PROJECT_DIR/data/bot.log

Перед запуском на телефоне останови бота на компьютере — два экземпляра
с одним токеном конфликтуют.
EOF
