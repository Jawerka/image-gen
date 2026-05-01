#!/usr/bin/env python3
"""
Единый сервер: MCP (Streamable HTTP) для MCP + Web (FastAPI) для раздачи.

Архитектура:
  - MCP Endpoint: /mcp  (Streamable HTTP transport)
  - Web Endpoints: /images, /thumbs, /gallery, /

Описание:
    Этот модуль объединяет два сервера в одном процессе:
    1. MCP Server (FastMCP) - обрабатывает запросы от LLM-клиентов
       через Streamable HTTP протокол на порту 8081.
    2. Web Server (FastAPI) - предоставляет веб-интерфейс для
       просмотра галереи и REST API для управления изображениями
       на порту 8080.

Оба сервера работают в отдельных потоках для одновременной обработки
запросов.
"""

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.settings import (
    IMAGE_DIR, THUMB_DIR, WEBP_DIR, PUBLIC_BASE_URL, WEB_HOST, WEB_PORT, MCP_TIMEOUT
)
from app.utils import safe_filename, get_file_info, cleanup_old_files, extract_image_metadata, ensure_webp
from app.web_server import generate_gallery_html, _build_image_data_list
from fastmcp import FastMCP
from app.tools import register_image_tools

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
# Настройка базового конфигурационного логирования
# Формат: время [уровень] имя_модуля: сообщение
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("image-server")

# ---------------------------------------------------------------------------
# MCP сервер
# ---------------------------------------------------------------------------
# Импорт необходим для настройки порта через переменную окружения
# FastMCP v3.x больше не принимает port в конструкторе
import os
os.environ["FASTMCP_PORT"] = str(WEB_PORT + 1)

# Создание MCP сервера с именем "image-gen-pro"
# Этот сервер будет доступен по адресу http://host:8081/mcp
mcp = FastMCP("image-gen-pro")
# Регистрация всех инструментов для работы с изображениями
register_image_tools(mcp)

# Получаем FastAPI-приложение для MCP (Streamable HTTP)
# FastMCP встроенный HTTP сервер работает на отдельном порту
# Мы монитируем его через FastAPI

# ---------------------------------------------------------------------------
# Middleware для логирования MCP подключений
# ---------------------------------------------------------------------------
class MCPConnectionLogger(BaseHTTPMiddleware):
    """Middleware для логирования подключений и запросов к MCP endpoint."""
    
    def __init__(self, app, mcp_logger):
        super().__init__(app)
        self.logger = mcp_logger
        # Отслеживаем активные сессии
        self.active_sessions = {}
    
    async def dispatch(self, request: Request, call_next):
        # Логируем только запросы к MCP endpoint
        if request.url.path.startswith("/mcp"):
            client_host = request.client.host if request.client else "unknown"
            client_port = request.client.port if request.client else 0
            method = request.method
            path = request.url.path
            
            # Получаем session ID из заголовков (если есть)
            session_id = request.headers.get("mcp-session-id", "no-session")
            
            # Логируем новые подключения (POST без session ID = инициализация)
            if method == "POST" and session_id == "no-session":
                self.logger.info(
                    "🔌 NEW MCP CONNECTION from %s:%d",
                    client_host, client_port
                )
            elif session_id != "no-session":
                # Отслеживаем активные сессии
                if session_id not in self.active_sessions:
                    self.active_sessions[session_id] = {
                        "client": f"{client_host}:{client_port}",
                        "connected_at": time.time(),
                        "request_count": 0
                    }
                    self.logger.info(
                        "🔑 NEW MCP SESSION: %s from %s:%d",
                        session_id[:16], client_host, client_port
                    )
                else:
                    self.active_sessions[session_id]["request_count"] += 1
                    self.active_sessions[session_id]["last_request"] = time.time()
                
                # Логируем запросы к инструментам
                if session_id in self.active_sessions:
                    sess = self.active_sessions[session_id]
                    self.logger.debug(
                        "📨 MCP REQUEST session=%s... requests=%d from %s",
                        session_id[:16], sess["request_count"], client_host
                    )
            
            # Замеряем время выполнения
            start_time = time.time()
            response = await call_next(request)
            duration = time.time() - start_time
            
            # Логируем ответ
            self.logger.info(
                "📤 MCP RESPONSE: %s %s -> %d (%.2fs) from %s",
                method, path, response.status_code, duration, client_host
            )
            
            # Логируем отключения (ошибки сессии)
            if response.status_code >= 400:
                self.logger.warning(
                    "⚠️ MCP ERROR: %s %s -> %d from %s (session: %s)",
                    method, path, response.status_code, client_host, session_id[:16] if session_id != "no-session" else "none"
                )
        else:
            response = await call_next(request)
        
        return response


# ---------------------------------------------------------------------------
# FastAPI приложение — веб-часть
# ---------------------------------------------------------------------------
# Создание FastAPI приложения для веб-интерфейса
app = FastAPI(title="Image MCP Server")

# Добавляем middleware для логирования MCP подключений
mcp_logger = logging.getLogger("mcp-connections")
app.add_middleware(MCPConnectionLogger, mcp_logger=mcp_logger)


def _resolve_path(base: Path, filename: str) -> Path:
    """
    Безопасно разрешить путь, предотвращая path traversal атаки.

    Args:
        base: Базовая директория для поиска файлов
        filename: Имя файла для разрешения

    Returns:
        Path: Полный путь к файлу

    Raises:
        ValueError: Если имя файла недопустимо или путь выходит за пределы base
    """
    safe_name = safe_filename(filename)
    if not safe_name:
        raise ValueError("Invalid filename")
    path = (base / safe_name).resolve()
    if not str(path).startswith(str(base.resolve())):
        raise ValueError("Access denied")
    return path


@app.get("/health")
def health():
    """
    Проверка работоспособности сервера.

    Возвращает:
        dict: Статус сервера и пути к директориям
    """
    return {
        "status": "ok",
        "images_dir": str(IMAGE_DIR),
        "thumb_dir": str(THUMB_DIR),
    }


@app.get("/images/{filename}")
def get_image(filename: str):
    """
    Отдать оригинал изображения по имени файла.

    Args:
        filename: Имя файла изображения

    Returns:
        FileResponse: Файл изображения
        JSONResponse: Ошибка 404 если файл не найден
    """
    path = _resolve_path(IMAGE_DIR, filename)
    if not path.exists():
        return JSONResponse({"error": "Image not found"}, status_code=404)
    return FileResponse(path)


@app.get("/thumbs/{filename}")
def get_thumbnail(filename: str):
    """
    Отдать превью изображения по имени файла.

    Сначала ищет JPEG превью, если не найдено - ищет PNG.

    Args:
        filename: Имя оригинального файла

    Returns:
        FileResponse: Файл превью
        JSONResponse: Ошибка 404 если превью не найдено
    """
    path = _resolve_path(THUMB_DIR, filename)
    if not path.exists():
        # Проверяем наличие PNG превью (старый формат)
        png_path = THUMB_DIR / (Path(filename).stem + ".png")
        if png_path.exists():
            path = png_path
        else:
            return JSONResponse({"error": "Thumbnail not found"}, status_code=404)
    return FileResponse(path)


@app.get("/webp/{filename}")
def get_webp(filename: str):
    """
    Отдать WebP-копию изображения по имени файла.

    WebP файлы — оптимизированные для веба копии оригинальных изображений.

    Args:
        filename: Имя WebP файла

    Returns:
        FileResponse: Файл WebP с media_type image/webp
        JSONResponse: Ошибка 404 если файл не найден
    """
    path = _resolve_path(WEBP_DIR, filename)
    if not path.exists():
        return JSONResponse({"error": "WebP not found"}, status_code=404)
    return FileResponse(path, media_type="image/webp")


@app.get("/meta/{filename}")
def get_meta(filename: str):
    """
    Получить метаданные файла изображения.

    Args:
        filename: Имя файла

    Returns:
        dict: Метаданные файла (размер, даты создания/изменения)
        JSONResponse: Ошибка 404 если файл не найден
    """
    info = get_file_info(filename)
    if info is None:
        return JSONResponse({"error": "Image not found"}, status_code=404)
    return info


@app.get("/gallery")
def get_gallery(limit: int = 50):
    """
    Список всех доступных изображений в галерее с метаданными.

    Args:
        limit: Максимальное количество изображений (по умолчанию 50)

    Returns:
        JSONResponse: Список изображений с URL и метаданными (prompt, negative, params, description)
    """
    images = []
    for f in sorted(IMAGE_DIR.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            # Skip files inside the thumbs directory
            if str(f).startswith(str(THUMB_DIR)):
                continue
            info = get_file_info(f.name)
            if info:
                info["url"] = f"{PUBLIC_BASE_URL}/images/{f.name}"
                thumb_name = f.stem + ".jpg"
                thumb_path = THUMB_DIR / thumb_name
                if thumb_path.exists():
                    info["thumb_url"] = f"{PUBLIC_BASE_URL}/thumbs/{thumb_name}"
                images.append(info)
                if len(images) >= limit:
                    break
    return JSONResponse({"images": images, "count": len(images)})


@app.get("/api/refresh")
def api_refresh():
    """
    Вернуть обновлённый список изображений для AJAX-обновления галереи.

    Используется кнопкой обновления в интерактивной галерее.

    Returns:
        JSONResponse: Объект с массивом images и count
    """
    image_data = _build_image_data_list()
    return JSONResponse({"images": image_data, "count": len(image_data)})


@app.post("/cleanup")
def cleanup():
    """
    Удалить старые файлы (старше IMAGE_RETENTION_DAYS дней).

    Returns:
        dict: Количество удаленных файлов
    """
    removed = cleanup_old_files()
    return {"removed": removed}


@app.get("/")
def index():
    """
    Интерактивная HTML-галерея с метаданными изображений.

    Адаптирована из main.py: отображает prompt, negative prompt,
    параметры генерации и description для каждого изображения.
    Поддерживает навигацию через колёсико мыши, стрелки, миниатюры.

    Returns:
        HTMLResponse: Страница галереи
    """
    html_content = generate_gallery_html()
    return HTMLResponse(html_content)


# ---------------------------------------------------------------------------
# Запуск — два сервера: MCP (Streamable HTTP) + Web (FastAPI)
# ---------------------------------------------------------------------------
import threading
import uvicorn


def run_mcp_server():
    """
    Запускает MCP сервер на Streamable HTTP.

    Этот метод запускается в отдельном потоке и обслуживает
    запросы от LLM-клиентов через MCP протокол.
    """
    logger.info("Starting MCP server on port %d (Streamable HTTP, timeout=%ds)", 
                WEB_PORT + 1, MCP_TIMEOUT)
    mcp.run(transport="streamable-http", host=WEB_HOST, port=WEB_PORT + 1)


def main():
    """
    Запуск обоих серверов.

    Запускает MCP сервер в отдельном потоке и Web сервер в главном потоке.
    MCP сервер работает как daemon, поэтому завершается вместе с основным процессом.
    """
    logger.info("Starting Image MCP Server")
    logger.info("MCP endpoint: http://%s:%d/mcp", WEB_HOST, WEB_PORT + 1)
    logger.info("Gallery: http://%s:%d/", WEB_HOST, WEB_PORT)

    # MCP в отдельном потоке
    mcp_thread = threading.Thread(target=run_mcp_server, daemon=True)
    mcp_thread.start()

    # Web-сервер в главном потоке
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    main()