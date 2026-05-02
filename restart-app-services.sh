#!/bin/bash
# Скрипт для перезапуска всех сервисов приложения image-gen
# Не блокирует терминал (использует --no-block для systemctl)

set -e

echo "🔄 Перезапуск сервисов image-gen..."

# Перезагрузка конфигурации демона systemd
echo "  → Перезагрузка демона systemd..."
systemctl --user daemon-reload 2>/dev/null || \
sudo systemctl daemon-reload 2>/dev/null || \
systemctl daemon-reload 2>/dev/null || \
echo "    ⚠️  Не удалось перезагрузить демона systemd"

# Перезапуск image-gen.service (основной MCP сервер)
echo "  → Перезапуск image-gen.service..."
systemctl --user restart --no-block image-gen.service 2>/dev/null || \
sudo systemctl restart --no-block image-gen.service 2>/dev/null || \
systemctl restart --no-block image-gen.service 2>/dev/null || \
echo "    ⚠️  Не удалось перезапустить image-gen.service"

# Перезапуск image-cleanup.service (таймер очистки изображений)
echo "  → Перезапуск image-cleanup.service..."
systemctl --user restart --no-block image-cleanup.service 2>/dev/null || \
sudo systemctl restart --no-block image-cleanup.service 2>/dev/null || \
systemctl restart --no-block image-cleanup.service 2>/dev/null || \
echo "    ⚠️  Не удалось перезапустить image-cleanup.service"

echo "✅ Команды перезапуска отправлены (неблокирующий режим)"
echo "   Проверка статуса: systemctl --user status image-gen.service"