"""
Application settings loaded from environment variables (.env).

This module defines all configurable settings for the application. On import,
it automatically loads variables from a local `.env` file (if present).

Settings layout:
    1. Paths and directories
    2. Stable Diffusion WebUI connection
    3. Default generation parameters
    4. Web server settings
    5. File cleanup / retention
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_logger = logging.getLogger("settings")

# Load environment variables from `.env` (if present).
load_dotenv()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int, *, min_val: int | None = None, max_val: int | None = None) -> int:
    """Read an int from env safely.

    If parsing fails, the function returns `default` and logs a warning. If
    `min_val` / `max_val` are provided, the value is clamped into the range.
    """
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
    """Read a float from env safely.

    If parsing fails, the function returns `default` and logs a warning. If
    `min_val` / `max_val` are provided, the value is clamped into the range.
    """
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
# Paths and directories
# ---------------------------------------------------------------------------
# Project base directory
# Default: /root/image-gen/code
BASE_DIR = Path(os.getenv("BASE_DIR", "/root/image-gen/code"))

# Image directory (can be outside the project)
# Default: /root/image-gen/images (BASE_DIR parent)
IMAGE_DIR = Path(os.getenv("IMAGE_DIR", str(BASE_DIR.parent / "images")))

# Thumbnail directory (inside IMAGE_DIR)
THUMB_DIR = IMAGE_DIR / "thumbs"

# WebP cache directory (inside IMAGE_DIR)
WEBP_DIR = IMAGE_DIR / "webp"

# Create directories on import to ensure the app can start cleanly.
for _dir in (IMAGE_DIR, THUMB_DIR, WEBP_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Stable Diffusion WebUI connection
# ---------------------------------------------------------------------------
# Default: http://127.0.0.1:7860
SD_WEBUI_URL = os.getenv("SD_WEBUI_URL", "http://127.0.0.1:7860")

# Optional basic auth credentials.
AUTH_USER = os.getenv("SD_AUTH_USER")

AUTH_PASS = os.getenv("SD_AUTH_PASS")

# WebUI request timeout (seconds). Default: 600 (10 minutes).
REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 600, min_val=10, max_val=3600)  # seconds

# MCP server timeout for streamable HTTP (seconds). Default: 900 (15 minutes).
# It should be greater than REQUEST_TIMEOUT.
MCP_TIMEOUT = _env_int("MCP_TIMEOUT", 900, min_val=10, max_val=7200)  # seconds

# ---------------------------------------------------------------------------
# Default generation parameters
# ---------------------------------------------------------------------------
SD_NEGATIVE_PROMPT = os.getenv("SD_NEGATIVE_PROMPT", "")

# Diffusion steps (1-150).
SD_STEPS = _env_int("SD_STEPS", 22, min_val=1, max_val=150)

# Sampler name (depends on your WebUI setup).
SD_SAMPLER = os.getenv("SD_SAMPLER", "Euler a")

# Scheduler type (depends on your WebUI setup).
SD_SCHEDULE_TYPE = os.getenv("SD_SCHEDULE_TYPE", "Karras")

# CFG scale (1-30).
SD_CFG_SCALE = _env_float("SD_CFG_SCALE", 5.0, min_val=1.0, max_val=30.0)

# Seed for reproducibility (-1 for random).
SD_SEED = _env_int("SD_SEED", -1, min_val=-1)

# Image width in pixels (768-2048).
SD_WIDTH = _env_int("SD_WIDTH", 1040, min_val=768, max_val=2048)

# Image height in pixels (768-2048).
SD_HEIGHT = _env_int("SD_HEIGHT", 1160, min_val=768, max_val=2048)

# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")

# Port for serving the gallery and REST API.
WEB_PORT = _env_int("WEB_PORT", 8080, min_val=1024, max_val=65535)

# Public base URL used to generate links returned by MCP tools.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://localhost:{WEB_PORT}")

# ---------------------------------------------------------------------------
# MCP session tracking
# ---------------------------------------------------------------------------
MAX_SESSIONS = _env_int("MAX_SESSIONS", 500, min_val=10, max_val=10000)

# Session idle TTL (seconds).
SESSION_MAX_AGE_SECONDS = _env_int("SESSION_MAX_AGE_SECONDS", 3600, min_val=60, max_val=86400)

# ---------------------------------------------------------------------------
# File retention (days)
# ---------------------------------------------------------------------------
IMAGE_RETENTION_DAYS = _env_int("IMAGE_RETENTION_DAYS", 3, min_val=1, max_val=365)


def validate_settings() -> None:
    """Validate critical relationships between settings.

    This is called at startup and logs warnings for suspicious configurations.
    """
    if MCP_TIMEOUT <= REQUEST_TIMEOUT:
        _logger.warning(
            "MCP_TIMEOUT (%ds) should be greater than REQUEST_TIMEOUT (%ds)",
            MCP_TIMEOUT, REQUEST_TIMEOUT,
        )
