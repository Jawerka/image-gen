"""
Настройки приложения, загружаемые из переменных окружения (.env).

Этот модуль определяет все настройки приложения, которые могут быть
настроены через переменные окружения. При импорте автоматически
загружает переменные из файла .env (если он существует).

Структура настроек:
    1. Пути и директории
    2. Настройки Stable Diffusion WebUI
    3. Настройки генерации изображений
    4. Настройки апскейлинга
    5. Настройки веб-сервера
    6. Настройки очистки файлов
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_logger = logging.getLogger("settings")

# Загрузка переменных окружения из файла .env
load_dotenv()


# ---------------------------------------------------------------------------
# Утилиты валидации
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int, *, min_val: int | None = None, max_val: int | None = None) -> int:
    """Безопасно прочитать int из env. При ошибке — вернуть default и залогировать предупреждение."""
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (ValueError, TypeError):
        _logger.warning("Invalid %s=%r — falling back to default %d", name, raw, default)
        value = default
    if min_val is not None and value < min_val:
        _logger.warning("%s=%d is below minimum %d — clamping", name, value, min_val)
        value = min_val
    if max_val is not None and value > max_val:
        _logger.warning("%s=%d is above maximum %d — clamping", name, value, max_val)
        value = max_val
    return value


def _env_float(name: str, default: float, *, min_val: float | None = None, max_val: float | None = None) -> float:
    """Безопасно прочитать float из env. При ошибке — вернуть default и залогировать предупреждение."""
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except (ValueError, TypeError):
        _logger.warning("Invalid %s=%r — falling back to default %.1f", name, raw, default)
        value = default
    if min_val is not None and value < min_val:
        _logger.warning("%s=%.2f is below minimum %.2f — clamping", name, value, min_val)
        value = min_val
    if max_val is not None and value > max_val:
        _logger.warning("%s=%.2f is above maximum %.2f — clamping", name, value, max_val)
        value = max_val
    return value

# ---------------------------------------------------------------------------
# Пути и директории
# ---------------------------------------------------------------------------
# Базовая директория проекта
# По умолчанию: /root/image-gen/code
BASE_DIR = Path(os.getenv("BASE_DIR", "/root/image-gen/code"))

# Директория для изображений - может быть вне проекта
# По умолчанию: /root/image-gen/images (родительская директория BASE_DIR)
IMAGE_DIR = Path(os.getenv("IMAGE_DIR", str(BASE_DIR.parent / "images")))

# Директория для превью изображений
# Располагается внутри IMAGE_DIR: /root/image-gen/images/thumbs
THUMB_DIR = IMAGE_DIR / "thumbs"

# Директория для WebP-копий изображений (оптимизированные для веба)
# Располагается внутри IMAGE_DIR: /root/image-gen/images/webp
WEBP_DIR = IMAGE_DIR / "webp"

# Создаём директории при импорте модуля
# Это гарантирует, что все необходимые директории существуют
for _dir in (IMAGE_DIR, THUMB_DIR, WEBP_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Основные настройки WebUI
# ---------------------------------------------------------------------------
# URL сервера Stable Diffusion WebUI
# По умолчанию: http://127.0.0.1:7860
SD_WEBUI_URL = os.getenv("SD_WEBUI_URL", "http://127.0.0.1:7860")

# Имя пользователя для аутентификации (опционально)
AUTH_USER = os.getenv("SD_AUTH_USER")

# Пароль для аутентификации (опционально)
AUTH_PASS = os.getenv("SD_AUTH_PASS")

# Таймаут запросов к WebUI в секундах
# По умолчанию: 600 секунд (10 минут)
REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 600, min_val=10, max_val=3600)  # seconds

# Таймаут MCP сервера в секундах (для Streamable HTTP)
# По умолчанию: 900 секунд (15 минут) - должен быть больше REQUEST_TIMEOUT
MCP_TIMEOUT = _env_int("MCP_TIMEOUT", 900, min_val=10, max_val=7200)  # seconds

# ---------------------------------------------------------------------------
# Настройки генерации изображений (по умолчанию)
# ---------------------------------------------------------------------------
# Негативный промпт (то, что не должно быть на изображении)
SD_NEGATIVE_PROMPT = os.getenv("SD_NEGATIVE_PROMPT", "")

# Количество шагов диффузии (1-150)
# Больше шагов = лучше качество, но дольше генерация
SD_STEPS = _env_int("SD_STEPS", 22, min_val=1, max_val=150)

# Имя сэмплера для генерации
# Доступные: Euler a, Euler, LMS, DPM++ 2M Karras и др.
SD_SAMPLER = os.getenv("SD_SAMPLER", "Euler a")

# Тип планировщика
# Доступные: Karras, Exponential, Polyexponential, Sigmoid и др.
SD_SCHEDULE_TYPE = os.getenv("SD_SCHEDULE_TYPE", "Karras")

# Масштаб следования промпту (1-30)
# Больше значение = строже следование промпту
SD_CFG_SCALE = _env_float("SD_CFG_SCALE", 5.0, min_val=1.0, max_val=30.0)

# Сид для воспроизводимости (-1 для случайного)
SD_SEED = _env_int("SD_SEED", -1, min_val=-1)

# Ширина изображения в пикселях (512-2048)
SD_WIDTH = _env_int("SD_WIDTH", 1040, min_val=512, max_val=2048)

# Высота изображения в пикселях (512-2048)
SD_HEIGHT = _env_int("SD_HEIGHT", 1160, min_val=512, max_val=2048)

# ---------------------------------------------------------------------------
# WEB сервер
# ---------------------------------------------------------------------------
# Хост для прослушивания (0.0.0.0 = все интерфейсы)
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")

# Порт для раздачи изображений
WEB_PORT = _env_int("WEB_PORT", 8080, min_val=1024, max_val=65535)

# Внешний URL (используется для генерации ссылок в ответах)
# Формируется автоматически на основе WEB_PORT, если не задан
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://localhost:{WEB_PORT}")

# ---------------------------------------------------------------------------
# MCP сессии (ограничение и очистка)
# ---------------------------------------------------------------------------
# Максимальное количество одновременно отслеживаемых MCP-сессий
MAX_SESSIONS = _env_int("MAX_SESSIONS", 500, min_val=10, max_val=10000)

# Время жизни сессии без активности (секунды)
SESSION_MAX_AGE_SECONDS = _env_int("SESSION_MAX_AGE_SECONDS", 3600, min_val=60, max_val=86400)

# ---------------------------------------------------------------------------
# Очистка файлов (дни)
# ---------------------------------------------------------------------------
# Сколько дней хранить изображения перед автоматической очисткой
IMAGE_RETENTION_DAYS = _env_int("IMAGE_RETENTION_DAYS", 3, min_val=1, max_val=365)


# ---------------------------------------------------------------------------
# Публичная функция для проверки согласованности настроек
# ---------------------------------------------------------------------------
def validate_settings() -> None:
    """Проверить критичные зависимости между настройками. Вызывается при старте сервера."""
    if MCP_TIMEOUT <= REQUEST_TIMEOUT:
        _logger.warning(
            "MCP_TIMEOUT (%ds) should be greater than REQUEST_TIMEOUT (%ds)",
            MCP_TIMEOUT, REQUEST_TIMEOUT,
        )
