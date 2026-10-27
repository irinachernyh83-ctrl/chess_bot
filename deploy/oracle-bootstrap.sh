#!/usr/bin/env bash
# Устанавливает Docker и swap на свежую Ubuntu VM (Oracle Cloud Always Free).
# Запуск: sudo bash oracle-bootstrap.sh
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
  echo "Запусти от root: sudo bash $0"
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl gnupg git unzip
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

TARGET_USER="${SUDO_USER:-ubuntu}"
if id "$TARGET_USER" >/dev/null 2>&1; then
  usermod -aG docker "$TARGET_USER"
fi

# 2 ГБ swap: сборка образа на VM с 1 ГБ RAM может упасть без него.
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "Swap 2 ГБ добавлен."
fi

echo
echo "Готово. Перезайди по SSH (чтобы применилась группа docker) и запусти:"
echo "  cd ~/chess-bot && docker compose up -d --build"
