# Image MCP Server

MCP-сервер для генерации изображений через Stable Diffusion WebUI.  
**Возвращает только URL — никакого base64 в контексте LLM.**

## Архитектура

```
┌──────────────────────┐
│  MCP Client          │
│  (Cherry Studio, etc)│
└──────────┬───────────┘
           │ Streamable HTTP
           │ http://192.168.88.16:8081/mcp
           v
┌──────────────────────────────┐
│  MCP Server (port 8081)      │
│  - generate_image            │
│  - upscale_images            │
│  - get_sd_models             │
│  - set_sd_model              │
│  - get_sd_upscalers          │
│  - get_gallery               │
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
                 │ /root/image-gen/     │
                 │   code/ (проект)      │
                 │   images/ (картинки)  │
                 └──────────┬───────────┘
                            │
┌──────────────────────┐    │
│  WEB Server (8080)   │◄───┘
│  - /                 │  HTML-галерея
│  - /health           │  Health check
│  - /images/{file}    │  Оригинал
│  - /thumbs/{file}    │  Превью
│  - /gallery          │  JSON-список
└──────────────────────┘
```

## Особенности

- **No base64 в контексте** — MCP возвращает только текст + HTTP URL
- **Streamable HTTP** — сервер всегда работает, клиенты подключаются по HTTP
- **Автоматические превью** — thumbnails создаются при генерации
- **Встроенная галерея** — просмотр всех картинок в браузере
- **Автоочистка** — старые файлы удаляются по таймеру
- **systemd сервис** — готово к продакшену с автозапуском

## Быстрый старт

```bash
# Установка зависимостей
pip install -r requirements.txt

# Настройка
cp .env.example .env
nano .env

# Запуск MCP + Web сервера
python -m app.server
```

## Доступные MCP инструменты

| Инструмент | Описание |
|---|---|
| `generate_image` | Генерация изображения через SD WebUI |
| `upscale_images` | Апскейл изображений через WebUI |
| `get_sd_models` | Список доступных моделей |
| `set_sd_model` | Установка активной модели |
| `get_sd_upscalers` | Список доступных апскейлеров |
| `get_gallery` | Список последних сгенерированных изображений |

## WEB-эндпоинты (порт 8080)

| Endpoint | Метод | Описание |
|---|---|---|
| `/` | GET | HTML-галерея |
| `/health` | GET | Health check |
| `/images/{filename}` | GET | Оригинал изображения |
| `/thumbs/{filename}` | GET | Превью |
| `/meta/{filename}` | GET | Метаданные файла |
| `/gallery` | GET | Список всех изображений (JSON) |
| `/cleanup` | POST | Очистка старых файлов |

## Подключение MCP-клиента

### Streamable HTTP (рекомендуется)

В `mcp-config.json`:

```json
{
  "mcpServers": {
    "image-gen-pro": {
      "url": "http://192.168.88.16:8081",
      "transport": "streamable-http"
    }
  }
}
```

## Полный цикл установки на Proxmox LXC

См. [deploy/INSTALL.md](deploy/INSTALL.md)

## Структура проекта

```
/root/image-gen/
├── code/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── settings.py     # Настройки из .env
│   │   ├── server.py       # Единый сервер (MCP + Web)
│   │   ├── tools.py        # MCP инструменты
│   │   ├── utils.py        # Утилиты (сохранение, превью, очистка)
│   │   └── sd_client.py    # Клиент SD WebUI
│   ├── tmp/
│   ├── logs/
│   ├── requirements.txt
│   ├── .env.example
│   └── deploy/
│       ├── image-gen.service
│       ├── image-cleanup.service
│       ├── image-cleanup.timer
│       └── INSTALL.md
├── images/
│   ├── *.png               # Оригиналы
│   └── thumbs/
│       └── *.jpg           # Превью
```

## Настройки (.env)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `SD_WEBUI_URL` | `http://127.0.0.1:7860` | URL SD WebUI |
| `IMAGE_DIR` | `/root/image-gen/images` | Директория изображений |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Внешний URL (для генерации ссылок) |
| `WEB_HOST` | `0.0.0.0` | Хост WEB-сервера |
| `WEB_PORT` | `8080` | Порт WEB-сервера |
| `REQUEST_TIMEOUT` | `300` | Таймаут запросов (сек) |
| `IMAGE_RETENTION_DAYS` | `3` | Срок хранения изображений (дней) |

## Порты

| Порт | Назначение |
|---|---|
| 8080 | Web-сервер (галерея, раздача изображений) |
| 8081 | MCP Streamable HTTP endpoint |

## Логи и диагностика

Просмотр логов сервиса:
```bash
journalctl -u image-gen -f
```

Проверка статуса:
```bash
systemctl status image-gen
```

## Документация

### Основная документация

- [GUIDE.md](GUIDE.md) — Подробное руководство по использованию (на русском)
- [deploy/INSTALL.md](deploy/INSTALL.md) — Инструкция по установке (на русском)

### Документация Cherry Studio

Документация по интеграции с Cherry Studio находится в папке `cherry-studio-docs/`:
- [EN Documentation](cherry-studio-docs/en/)
- [RU Documentation](cherry-studio-docs/zh/)

## Структура проекта

```
/root/image-gen/
├── code/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── settings.py     # Настройки из .env
│   │   ├── server.py       # Единый сервер (MCP + Web)
│   │   ├── tools.py        # MCP инструменты
│   │   ├── utils.py        # Утилиты (сохранение, превью, очистка)
│   │   └── sd_client.py    # Клиент SD WebUI
│   ├── tmp/
│   ├── logs/
│   ├── requirements.txt
│   ├── .env.example
│   ├── GUIDE.md            # Руководство по использованию
│   └── deploy/
│       ├── image-gen.service
│       ├── image-cleanup.service
│       ├── image-cleanup.timer
│       └── INSTALL.md
├── images/
│   ├── *.png               # Оригиналы
│   └── thumbs/
│       └── *.jpg           # Превью
```
