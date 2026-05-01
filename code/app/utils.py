"""
Утилиты для работы с изображениями и файлами.

Этот модуль содержит вспомогательные функции для:
- Генерации уникальных имен файлов
- Безопасной обработки имен файлов
- Сохранения изображений из base64
- Создания превью (thumbnails)
- Очистки старых файлов
- Получения метаданных файлов
"""

import uuid
import time
import logging
from pathlib import Path
from PIL import Image
from io import BytesIO
from app.settings import IMAGE_DIR, THUMB_DIR, WEBP_DIR, IMAGE_RETENTION_DAYS

logger = logging.getLogger(__name__)


def generate_filename(prefix: str = "sd", extension: str = "png") -> str:
    """
    Создать уникальное имя файла с UUID.

    Генерирует имя файла в формате: {prefix}_{uuid}.{extension}
    Это гарантирует уникальность имен файлов даже при параллельной генерации.

    Args:
        prefix: Префикс имени файла (по умолчанию "sd")
        extension: Расширение файла (по умолчанию "png")

    Returns:
        str: Уникальное имя файла

    Пример:
        >>> generate_filename("test", "jpg")
        'test_a1b2c3d4e5f6.jpg'
    """
    return f"{prefix}_{uuid.uuid4().hex}.{extension}"


def safe_filename(filename: str) -> str:
    """
    Проверить и очистить имя файла от опасных символов.

    Предотвращает path traversal атаки и другие попытки доступа
    к файлам вне разрешенных директорий.

    Разрешенные символы:
        - Буквы латинского алфавита (a-z, A-Z)
        - Цифры (0-9)
        - Подчеркивание (_)
        - Дефис (-)
        - Точка (.)

    Args:
        filename: Имя файла для проверки

    Returns:
        str: Очищенное имя файла или пустая строка если недопустимо

    Пример:
        >>> safe_filename("../../../etc/passwd")
        ''
        >>> safe_filename("image_001.png")
        'image_001.png'
    """
    # Убираем любые path traversal попытки
    safe = Path(filename).name
    # Разрешаем только буквенно-цифровые символы, точки и подчёркивания
    allowed_chars = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        "_-."
    )
    return "".join(c for c in safe if c in allowed_chars)


def save_image(data: bytes, filename: str | None = None) -> str:
    """
    Сохранить изображение в папку IMAGE_DIR.

    Args:
        data: Бинарные данные изображения
        filename: Имя файла (если None, генерируется автоматически)

    Returns:
        str: Имя сохраненного файла

    Пример:
        >>> save_image(b"...", "my_image.png")
        'my_image.png'
    """
    if not filename:
        filename = generate_filename()
    path = IMAGE_DIR / filename
    with open(path, "wb") as f:
        f.write(data)
    logger.info("Saved image: %s (%d bytes)", filename, len(data))
    return filename


def save_image_from_base64(b64_data: str, filename: str | None = None) -> str:
    """
    Декодировать base64 и сохранить изображение.

    Поддерживает два формата:
        - Чистый base64: "iVBORw0KGgoAAAANSUhEUg..."
        - Data URL: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."

    Args:
        b64_data: Base64-encoded строка с изображением
        filename: Имя файла (если None, генерируется автоматически)

    Returns:
        str: Имя сохраненного файла

    Пример:
        >>> save_image_from_base64("iVBORw0KGgoAAAANSUhEUg...")
        'sd_a1b2c3d4e5f6.png'
    """
    import base64
    if "," in b64_data:
        _, b64 = b64_data.split(",", 1)
    else:
        b64 = b64_data
    img_bytes = base64.b64decode(b64)
    return save_image(img_bytes, filename)


def make_thumbnail(
    filename: str,
    max_size: tuple[int, int] = (512, 512),
    quality: int = 85,
) -> str | None:
    """
    Создать JPEG-превью для изображения.

    Создает уменьшенную копию изображения с сохранением пропорций.
    Превью всегда сохраняется в формате JPEG с заданным качеством.

    Args:
        filename: Имя оригинального файла
        max_size: Максимальный размер превью (по умолчанию 512x512)
        quality: Качество JPEG (1-100, по умолчанию 85)

    Returns:
        str | None: Имя превью или None при ошибке

    Пример:
        >>> make_thumbnail("image.png")
        'image.jpg'
    """
    src = IMAGE_DIR / filename
    if not src.exists():
        logger.error("Cannot create thumbnail: %s not found", src)
        return None

    # Превью всегда JPEG
    thumb_name = Path(filename).stem + ".jpg"
    dst = THUMB_DIR / thumb_name

    try:
        with Image.open(src) as img:
            # Уменьшаем размер с сохранением пропорций
            img.thumbnail(max_size)
            # Конвертируем RGBA в RGB для JPEG
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            # Сохраняем как JPEG
            img.save(dst, "JPEG", quality=quality)

        logger.info("Created thumbnail: %s", thumb_name)
        return thumb_name
    except Exception as e:
        logger.error("Failed to create thumbnail for %s: %s", filename, e)
        return None


def ensure_webp(filename: str, quality: int = 80) -> str | None:
    """
    Обеспечить наличие WebP-копии изображения.

    Проверяет наличие WebP-копии в кэше. Если нет, загружает оригинальное
    изображение, конвертирует в WebP и сохраняет.

    Args:
        filename: Имя оригинального файла (png, jpg, jpeg)
        quality: Качество WebP 1-100 (по умолчанию 80 — хороший баланс размер/качество)

    Returns:
        str | None: Имя WebP-файла (без пути) или None при ошибке

    Пример:
        >>> ensure_webp("image.png")
        'image.webp'
    """
    # Определяем имя WebP-файла
    webp_name = Path(filename).stem + ".webp"
    webp_path = WEBP_DIR / webp_name
    src_path = IMAGE_DIR / filename

    # Проверяем, есть ли уже WebP-копия
    if webp_path.exists():
        # Если оригинал новее WebP-копии — перегенерируем
        if src_path.exists() and src_path.stat().st_mtime > webp_path.stat().st_mtime:
            logger.info("WebP cache stale for %s, regenerating", filename)
        else:
            return webp_name

    # Проверяем наличие оригинала
    if not src_path.exists():
        logger.error("Cannot create WebP: %s not found", src_path)
        return None

    try:
        with Image.open(src_path) as img:
            # Конвертируем RGBA в RGB если необходимо (WebP поддерживает RGBA, но для совместимости)
            if img.mode in ("P", "LA"):
                img = img.convert("RGBA")
            # Сохраняем как WebP
            img.save(webp_path, "WEBP", quality=quality)

        logger.info("Created WebP: %s", webp_name)
        return webp_name
    except Exception as e:
        logger.error("Failed to create WebP for %s: %s", filename, e)
        return None


def cleanup_old_files() -> int:
    """
    Удалить файлы старше IMAGE_RETENTION_DAYS дней.

    Удаляет файлы из IMAGE_DIR и THUMB_DIR, которые старше
    заданного срока хранения (IMAGE_RETENTION_DAYS).

    Returns:
        int: Количество удаленных файлов

    Пример:
        >>> cleanup_old_files()
        5
    """
    now = time.time()
    cutoff = now - (IMAGE_RETENTION_DAYS * 86400)  # 86400 секунд в сутках
    removed = 0

    for directory in (IMAGE_DIR, THUMB_DIR, WEBP_DIR):
        for filepath in directory.iterdir():
            if filepath.is_file() and filepath.stat().st_mtime < cutoff:
                try:
                    filepath.unlink()
                    removed += 1
                    logger.info("Deleted old file: %s", filepath)
                except OSError as e:
                    logger.error("Failed to delete %s: %s", filepath, e)

    logger.info("Cleanup complete: %d files removed", removed)
    return removed


def extract_image_metadata(img_path: Path) -> dict | None:
    """
    Извлечь метаданные из изображения (prompt, negative prompt, parameters, description).

    Универсальный парсер: поддерживает стандартные PNG parameters от SD WebUI,
    а также кастомное поле Description (может содержать prompt/negative/params).

    Args:
        img_path: Путь к файлу изображения

    Returns:
        dict | None: Словарь с метаданными или None при ошибке
    """
    try:
        with Image.open(img_path) as img:
            parameters_raw = img.info.get("parameters", "").strip()
            description_raw = img.info.get("Description", "").strip()

            # Если есть стандартные parameters — парсим их
            if parameters_raw:
                lines = parameters_raw.splitlines()
                prompt_lines = []
                negative_prompt = ""
                other_lines = []
                in_negative = False

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    if line.lower().startswith("negative prompt:"):
                        in_negative = True
                        negative_prompt = line.split(":", 1)[1].strip()
                    elif in_negative and ":" not in line:
                        negative_prompt += ", " + line
                    elif in_negative and ":" in line:
                        in_negative = False
                        other_lines.append(line)
                    elif not in_negative and ":" not in line:
                        prompt_lines.append(line)
                    else:
                        other_lines.append(line)

                processed_other_lines = "\n".join(other_lines)
                if "Steps:" in processed_other_lines:
                    pre, post = processed_other_lines.split("Steps:", 1)
                    if pre:
                        prompt_lines.append(pre.strip())
                    processed_other_lines = "Steps:" + post.strip()

                return {
                    "prompt": "\n".join(prompt_lines),
                    "negative": negative_prompt,
                    "params": processed_other_lines,
                    "description": description_raw,
                }

            # Если нет parameters, но есть Description — пробуем распарсить Description
            if description_raw:
                lines = description_raw.splitlines()
                prompt_lines = []
                negative_prompt = ""
                other_lines = []
                in_negative = False

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    if line.lower().startswith("negative prompt:"):
                        in_negative = True
                        negative_prompt = line.split(":", 1)[1].strip()
                    elif in_negative and ":" not in line:
                        negative_prompt += ", " + line
                    elif in_negative and ":" in line:
                        in_negative = False
                        other_lines.append(line)
                    elif not in_negative and ":" not in line:
                        prompt_lines.append(line)
                    else:
                        other_lines.append(line)

                processed_other_lines = "\n".join(other_lines)
                if "Steps:" in processed_other_lines:
                    pre, post = processed_other_lines.split("Steps:", 1)
                    if pre:
                        prompt_lines.append(pre.strip())
                    processed_other_lines = "Steps:" + post.strip()

                return {
                    "prompt": "\n".join(prompt_lines),
                    "negative": negative_prompt,
                    "params": processed_other_lines,
                    "description": "",
                }

            # Нет ни parameters, ни Description
            return {
                "prompt": "",
                "negative": "",
                "params": "",
                "description": "",
            }

    except Exception as e:
        logger.error("Ошибка при извлечении метаданных из %s: %s", img_path, e)
        return None


def get_file_info(filename: str) -> dict | None:
    """
    Получить информацию о файле изображения включая метаданные PNG.

    Args:
        filename: Имя файла

    Returns:
        dict | None: Словарь с метаданными или None если файл не найден

    Пример:
        >>> get_file_info("image.png")
        {
            'filename': 'image.png',
            'size_bytes': 123456,
            'created': 1714567890.123,
            'modified': 1714567890.123,
            'prompt': 'a beautiful cat',
            'negative': 'blurry, low quality',
            'params': 'Steps: 20, Sampler: Euler a, ...',
            'description': 'My generated image'
        }
    """
    path = IMAGE_DIR / filename
    if not path.exists():
        return None
    stat = path.stat()
    base_info = {
        "filename": filename,
        "size_bytes": stat.st_size,
        "created": stat.st_ctime,
        "modified": stat.st_mtime,
    }

    # Извлекаем метаданные изображения (prompt, negative, params, description)
    meta = extract_image_metadata(path)
    if meta:
        base_info.update(meta)

    return base_info
