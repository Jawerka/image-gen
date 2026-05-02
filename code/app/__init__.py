"""
Пакет приложения Image MCP Server.

Модули:
    - settings.py: Настройки приложения из .env
    - utils.py: Утилиты для работы с файлами и изображениями
    - tools.py: MCP инструменты для генерации и управления
    - server.py: Единый сервер (MCP + Web)
    - web_server.py: Генератор HTML-галереи

Использование:
    from app.server import main
    main()
"""

from app.tools import register_image_tools

__all__ = [
    "register_image_tools",
]
