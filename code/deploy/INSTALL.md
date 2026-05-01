# Установка и развертывание Image MCP Server

## Оглавление

1. [Введение](#введение)
2. [Требования](#требования)
3. [Установка на Proxmox LXC](#установка-на-proxmox-lxc)
4. [Настройка](#настройка)
5. [Запуск и управление](#запуск-и-управление)
6. [Диагностика](#диагностика)
7. [Обновление](#обновление)
8. [Резервное копирование](#резервное-копирование)

---

## Введение

Image MCP Server — это сервер для генерации изображений через Stable Diffusion WebUI. Сервер предоставляет:

- **MCP API** (порт 8081) — Streamable HTTP endpoint для интеграции с LLM-клиентами (Cherry Studio, Claude Desktop и др.)
- **Web API** (порт 8080) — HTML-галерея и REST API для управления изображениями
- **Автоматическую очистку** — удаление старых файлов по таймеру

### Архитектура

```
┌──────────────────────┐
│  MCP Client          │
│  (Cherry Studio)     │
└──────────┬───────────┘
           │ HTTP
           │ http://192.168.88.16:8081/mcp
           v
┌──────────────────────────────┐
│  Image MCP Server            │
│  - FastMCP (port 8081)       │
│  - FastAPI (port 8080)       │
│       │                      │
│       │ вызывает SD WebUI    │
│       └─────────► SD WebUI   │
│                 (192.168.88.52:7860)
│                              │
│       сохраняет файлы ──┐    │
└─────────────────────────┼────┘
                          v
                 ┌──────────────────────┐
                 │ Filesystem            │
                 │ /root/image-gen/      │
                 │   images/             │
                 │   thumbs/             │
                 └──────────┬───────────┘
```

---

## Требования

### Системные требования

- **Операционная система**: Ubuntu 22.04/24.04 или Debian 12
- **CPU**: 2 ядра (минимум), 4 ядра (рекомендуется)
- **RAM**: 2 ГБ (минимум), 4 ГБ (рекомендуется)
- **Диск**: 20 ГБ (минимум), 50 ГБ (с изображениями)
- **Сеть**: Доступ к SD WebUI (192.168.88.52:7860 по умолчанию)

### Зависимости

- Python 3.10+
- pip
- systemd
- curl

---

## Установка на Proxmox LXC

### Шаг 1: Создание контейнера

1. В Proxmox Web UI создайте новый контейнер:
   - **ID**: 101 (или любой свободный)
   - **Hostname**: image-mcp
   - **Шаблон**: Ubuntu 24.04
   - **Диск**: 20 ГБ
   - **CPU**: 2 ядра
   - **RAM**: 4096 МБ
   - **Сеть**: bridge=vmbr0, IP=192.168.88.16/24

2. Запустите контейнер и подключитесь по SSH:
   ```bash
   ssh root@192.168.88.16
   ```

### Шаг 2: Установка зависимостей

```bash
# Обновление системы
apt update && apt upgrade -y

# Установка необходимых пакетов
apt install -y \
    python3 python3-venv python3-pip \
    git curl wget \
    build-essential \
    libjpeg-dev zlib1g-dev
```

### Шаг 3: Клонирование проекта

```bash
# Создание рабочей директории
mkdir -p /root/image-gen
cd /root/image-gen

# Клонирование репозитория (если используется git)
git clone https://github.com/your-repo/image-gen.git code
cd code

# Или копирование файлов через scp/rsync
# scp -r user@host:/path/to/code/* ./
```

### Шаг 4: Создание виртуального окружения

```bash
# Создание venv
python3 -m venv venv
source venv/bin/activate

# Обновление pip
pip install --upgrade pip

# Установка зависимостей
pip install -r requirements.txt
```

### Шаг 5: Настройка .env

```bash
# Копирование примера конфигурации
cp .env.example .env

# Редактирование конфигурации
nano .env
```

**Основные параметры:**

| Параметр | Описание | Пример |
|----------|----------|--------|
| `SD_WEBUI_URL` | URL SD WebUI сервера | `http://192.168.88.52:7860` |
| `IMAGE_DIR` | Директория для изображений | `/root/image-gen/images` |
| `PUBLIC_BASE_URL` | Внешний URL сервера | `http://192.168.88.16:8080` |
| `WEB_HOST` | Хост для прослушивания | `0.0.0.0` |
| `WEB_PORT` | Порт веб-сервера | `8080` |
| `REQUEST_TIMEOUT` | Таймаут запросов (сек) | `300` |
| `IMAGE_RETENTION_DAYS` | Срок хранения (дней) | `3` |

### Шаг 6: Установка systemd сервисов

```bash
# Копирование файлов сервисов
cp deploy/image-gen.service /etc/systemd/system/
cp deploy/image-cleanup.timer /etc/systemd/system/

# Перезагрузка systemd
systemctl daemon-reload

# Включение автозапуска
systemctl enable image-gen image-cleanup.timer

# Запуск сервисов
systemctl start image-gen
systemctl start image-cleanup.timer
```

---

## Настройка

### Настройка SD WebUI

Убедитесь, что SD WebUI запущен и доступен по указанному URL:

```bash
curl http://192.168.88.52:7860
```

### Настройка аутентификации (опционально)

Если SD WebUI защищен паролем, добавьте в `.env`:

```bash
SD_AUTH_USER=your_username
SD_AUTH_PASS=your_password
```

### Настройка портов

По умолчанию:
- **MCP API**: 8081
- **Web API**: 8080

Для изменения портов отредактируйте `.env`:

```bash
WEB_PORT=8080  # Web API
# MCP API будет на WEB_PORT + 1 = 8081
```

---

## Запуск и управление

### Проверка статуса

```bash
# Статус основного сервиса
systemctl status image-gen

# Статус сервиса очистки
systemctl status image-cleanup.timer
systemctl status image-cleanup.service
```

### Управление сервисами

```bash
# Запуск
systemctl start image-gen

# Остановка
systemctl stop image-gen

# Перезапуск
systemctl restart image-gen

# Автозапуск при старте системы
systemctl enable image-gen

# Отключение автозапуска
systemctl disable image-gen
```

### Ручной запуск (для отладки)

```bash
cd /root/image-gen/code
source venv/bin/activate
python -m app.server
```

---

## Диагностика

### Просмотр логов

```bash
# Логи основного сервиса
journalctl -u image-gen -f

# Логи очистки
journalctl -u image-cleanup -f

# Логи приложения
tail -f /root/image-gen/code/logs/app.log
```

### Проверка работоспособности

```bash
# Health check
curl http://localhost:8080/health

# Проверка галереи
curl http://localhost:8080/gallery

# Проверка MCP API
curl http://localhost:8081/mcp
```

### Тестовая генерация

```bash
cd /root/image-gen/code
source venv/bin/activate

python -c "
from app.tools import generate_image
result = generate_image(
    prompt='a beautiful sunset over mountains',
    steps=4,
    width=512,
    height=512
)
print(result)
"
```

### Распространенные проблемы

| Проблема | Решение |
|----------|---------|
| Сервис не запускается | Проверьте логи: `journalctl -u image-gen -n 50` |
| Ошибка подключения к SD WebUI | Проверьте `SD_WEBUI_URL` в `.env` |
| Нет изображений в галерее | Проверьте права доступа к `/root/image-gen/images` |
| Ошибка импорта модулей | Убедитесь, что venv активирован: `source venv/bin/activate` |

---

## Обновление

### Обновление кода

```bash
cd /root/image-gen/code

# Остановка сервиса
systemctl stop image-gen

# Обновление кода (через git)
git pull origin main

# Или копирование новых файлов
# scp -r user@host:/path/to/code/* ./

# Перезапуск
systemctl start image-gen
```

### Обновление зависимостей

```bash
cd /root/image-gen/code
source venv/bin/activate
pip install --upgrade -r requirements.txt
systemctl restart image-gen
```

---

## Резервное копирование

### Копирование изображений

```bash
# Архивация изображений
tar -czf images_backup_$(date +%Y%m%d).tar.gz /root/image-gen/images

# Копирование на удаленный сервер
scp images_backup_*.tar.gz user@backup-server:/backup/
```

### Восстановление

```bash
# Остановка сервиса
systemctl stop image-gen

# Распаковка резервной копии
tar -xzf images_backup_*.tar.gz -C /

# Запуск сервиса
systemctl start image-gen
```

---

## Дополнительная информация

- [README.md](../README.md) — Основная документация проекта
- [GUIDE.md](../GUIDE.md) — Руководство по использованию
