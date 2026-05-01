#!/bin/bash
# Скрипт деплоя на Proxmox LXC контейнер
# Использование: ./deploy/deploy.sh root@192.168.88.16

set -e

REMOTE="${1:-root@192.168.88.16}"
REMOTE_DIR="/srv/image-mcp"

echo "🚀 Деплой на $REMOTE..."

# Создаём директорию на удалённом сервере
ssh "$REMOTE" "mkdir -p $REMOTE_DIR"

# Копируем файлы
echo "📦 Копирование файлов..."
rsync -avz --exclude '__pycache__' --exclude 'venv' --exclude '.git' \
  --exclude '*.pyc' --exclude 'node_modules' \
  ./ "$REMOTE:$REMOTE_DIR/"

# Установка зависимостей на сервере
echo "🔧 Установка зависимостей..."
ssh "$REMOTE" "cd $REMOTE_DIR && 
  pip3 install -q -r requirements.txt &&
  cp -n .env.example .env &&
  mkdir -p data/images data/thumbs data/tmp logs"

echo "✅ Деплой завершён!"
echo "📝 Не забудьте настроить .env файл:"
echo "   ssh $REMOTE 'nano $REMOTE_DIR/.env'"
echo ""
echo "🚀 Для запуска сервисов:"
echo "   ssh $REMOTE 'systemctl daemon-reload && systemctl enable image-mcp image-web && systemctl start image-mcp image-web'"