"""
Image MCP Server application package.

Modules:
    - settings.py: Settings loaded from `.env` / environment variables
    - utils.py: File and image utilities
    - tools.py: MCP tools for generation and management
    - server.py: Combined server (MCP + Web)
    - web_server.py: HTML gallery generator

Usage:
    from app.server import main
    main()
"""

from app.tools import register_image_tools

__all__ = [
    "register_image_tools",
]
