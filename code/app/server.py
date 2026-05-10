#!/usr/bin/env python3
"""
Unified server: MCP (streamable HTTP) + Web (FastAPI) in one process.

Architecture:
  - MCP endpoint: `/mcp` (streamable HTTP transport)
  - Web endpoints: `/images`, `/thumbs`, `/webp`, `/meta`, `/gallery`, `/`

Overview:
    This module runs two servers concurrently:
    1. MCP server (FastMCP) for LLM clients over streamable HTTP (port 8081).
    2. Web server (FastAPI) for the interactive gallery and a small REST API
       (port 8080).

The MCP server runs in a background thread while the web server runs in the
main thread.
"""

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware

from app.settings import (
    IMAGE_DIR,
    MAX_SESSIONS,
    MCP_TIMEOUT,
    PUBLIC_BASE_URL,
    SESSION_MAX_AGE_SECONDS,
    THUMB_DIR,
    WEB_HOST,
    WEB_PORT,
    WEBP_DIR,
    validate_settings,
)
from app.tools import register_image_tools
from app.utils import cleanup_old_files, get_file_info, safe_filename
from app.web_server import _build_image_data_list, generate_gallery_html

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("image-server")

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
# FastMCP v3.x reads the port from the environment.
os.environ["FASTMCP_PORT"] = str(WEB_PORT + 1)

# Create the MCP server instance. It will be reachable at:
# http://<host>:8081/mcp
mcp = FastMCP("image-gen-pro")
# Register all MCP tools.
register_image_tools(mcp)

# ---------------------------------------------------------------------------
# Middleware: MCP connection logging
# ---------------------------------------------------------------------------
class MCPConnectionLogger(BaseHTTPMiddleware):
    """Log MCP connections and request/response metrics."""

    def __init__(self, app, mcp_logger):
        super().__init__(app)
        self.logger = mcp_logger
        # Track active sessions with a bounded in-memory map.
        self.active_sessions: dict[str, dict] = {}

    def _prune_expired_sessions(self) -> None:
        """Drop idle sessions and enforce MAX_SESSIONS."""
        now = time.time()
        expired = [
            sid for sid, info in self.active_sessions.items()
            if now - info.get("last_request", info.get("connected_at", 0)) > SESSION_MAX_AGE_SECONDS
        ]
        for sid in expired:
            self.active_sessions.pop(sid, None)
        # If still too many, delete oldest sessions by connected_at.
        if len(self.active_sessions) > MAX_SESSIONS:
            sorted_sessions = sorted(
                self.active_sessions.items(),
                key=lambda x: x[1].get("connected_at", 0),
            )
            for sid, _ in sorted_sessions[: len(self.active_sessions) - MAX_SESSIONS]:
                self.active_sessions.pop(sid, None)

    async def dispatch(self, request: Request, call_next):
        # Only log MCP endpoint traffic.
        if request.url.path.startswith("/mcp"):
            client_host = request.client.host if request.client else "unknown"
            client_port = request.client.port if request.client else 0
            method = request.method
            path = request.url.path

            # Session ID comes from the MCP headers (if present).
            session_id = request.headers.get("mcp-session-id", "no-session")

            # New connection: POST without a session id (initial handshake).
            if method == "POST" and session_id == "no-session":
                self.logger.info(
                    "🔌 NEW MCP CONNECTION from %s:%d",
                    client_host, client_port
                )
            elif session_id != "no-session":
                # Periodically prune idle sessions.
                if len(self.active_sessions) % 20 == 0:
                    self._prune_expired_sessions()

                # Track session lifecycle.
                if session_id not in self.active_sessions:
                    self.active_sessions[session_id] = {
                        "client": f"{client_host}:{client_port}",
                        "connected_at": time.time(),
                        "request_count": 0,
                    }
                    self.logger.info(
                        "🔑 NEW MCP SESSION: %s from %s:%d",
                        session_id[:16], client_host, client_port,
                    )
                else:
                    self.active_sessions[session_id]["request_count"] += 1
                    self.active_sessions[session_id]["last_request"] = time.time()

                # Tool call logging (debug-level).
                sess = self.active_sessions.get(session_id)
                if sess:
                    self.logger.debug(
                        "📨 MCP REQUEST session=%s... requests=%d from %s",
                        session_id[:16], sess["request_count"], client_host,
                    )

            # Measure end-to-end request time; call_next must be invoked once.
            start_time = time.time()
            
            try:
                response = await call_next(request)
            except Exception:
                self.logger.exception("MCP Request failed")
                raise
            
            duration = time.time() - start_time

            # Log response summary.
            self.logger.info(
                "📤 MCP RESPONSE: %s %s -> %d (%.2fs) from %s",
                method, path, response.status_code, duration, client_host
            )

            # Log errors (often correspond to session disconnects/timeouts).
            if response.status_code >= 400:
                self.logger.warning(
                    "⚠️ MCP ERROR: %s %s -> %d from %s (session: %s)",
                    method, path, response.status_code, client_host, session_id[:16] if session_id != "no-session" else "none"
                )
            
            return response
        else:
            return await call_next(request)


# ---------------------------------------------------------------------------
# Web server (FastAPI)
# ---------------------------------------------------------------------------
app = FastAPI(title="Image MCP Server")

# Add MCP logging middleware.
mcp_logger = logging.getLogger("mcp-connections")
app.add_middleware(MCPConnectionLogger, mcp_logger=mcp_logger)


@app.on_event("startup")
async def startup_event():
    """
    Ensure storage directories exist at startup.
    """
    for path in [IMAGE_DIR, THUMB_DIR, WEBP_DIR]:
        path.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            raise RuntimeError(f"Failed to create {path}")
        logger.info("Directory ready: %s", path)


def _ok(data=None) -> JSONResponse:
    payload: dict = {"status": "ok"}
    if data is not None:
        payload["data"] = data
    return JSONResponse(payload, status_code=200)


def _error(status_code: int, message: str, data=None) -> JSONResponse:
    payload: dict = {"status": "error", "error": message}
    if data is not None:
        payload["data"] = data
    return JSONResponse(payload, status_code=status_code)


def _resolve_path(base: Path, filename: str, *, must_exist: bool = False) -> Path:
    """
    Resolve a user-provided filename against a base directory safely.

    Args:
        base: Base directory to resolve against.
        filename: User-provided filename.

    Returns:
        Path: An absolute resolved path under the base directory.

    Raises:
        HTTPException: For invalid names or forbidden access. If `must_exist` is
            True, missing files also raise a 404.
    """
    safe_name = safe_filename(filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    resolved_base = base.resolve()
    candidate = resolved_base / safe_name

    # If the path exists and is a symlink, resolve the final target and ensure it
    # still stays under the base directory. This prevents symlink escapes like
    # IMAGE_DIR/foo.png -> /etc/passwd.
    if candidate.exists():
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except OSError:
            raise HTTPException(status_code=404, detail="File not found")

        if candidate.is_symlink() and not resolved_candidate.is_relative_to(resolved_base):
            raise HTTPException(status_code=404, detail="File not found")

        if not resolved_candidate.is_relative_to(resolved_base):
            raise HTTPException(status_code=403, detail="Access denied")
        return resolved_candidate

    # Missing file: still return a safe path, unless the caller requires existence.
    if not candidate.is_relative_to(resolved_base):
        raise HTTPException(status_code=403, detail="Access denied")
    if must_exist:
        raise HTTPException(status_code=404, detail="File not found")
    return candidate


@app.get("/health")
def health():
    """
    Health check endpoint.

    Returns:
        dict: Server status and basic disk info.
    """
    total, used, free = shutil.disk_usage("/")
    return {
        "status": "ok",
        "images_dir": str(IMAGE_DIR),
        "thumb_dir": str(THUMB_DIR),
        "disk_free_mb": free // (1024 * 1024),
    }


@app.get("/images/{filename}")
def get_image(filename: str):
    """
    Serve the original image by filename.

    Args:
        filename: Image filename (no directory components).

    Returns:
        FileResponse on success; otherwise a JSON error envelope.
    """
    try:
        path = _resolve_path(IMAGE_DIR, filename, must_exist=True)
        return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})
    except HTTPException as exc:
        return _error(exc.status_code, str(exc.detail))


@app.get("/thumbs/{filename}")
def get_thumbnail(filename: str):
    """
    Serve a thumbnail image by filename.

    The server prefers JPEG thumbnails but supports legacy PNG thumbnails.

    Args:
        filename: Thumbnail filename.

    Returns:
        FileResponse on success; otherwise a JSON error envelope.
    """
    try:
        path = _resolve_path(THUMB_DIR, filename, must_exist=True)
    except HTTPException:
        # Back-compat: try legacy PNG thumbs when JPG is missing.
        try:
            path = _resolve_path(THUMB_DIR, f"{Path(filename).stem}.png", must_exist=True)
        except HTTPException as exc:
            return _error(exc.status_code, "Thumbnail not found")

    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/webp/{filename}")
def get_webp(filename: str):
    """
    Serve a WebP copy of an image by filename.

    Args:
        filename: WebP filename.

    Returns:
        FileResponse on success; otherwise a JSON error envelope.
    """
    try:
        path = _resolve_path(WEBP_DIR, filename, must_exist=True)
        return FileResponse(
            path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except HTTPException as exc:
        return _error(exc.status_code, str(exc.detail))


@app.get("/meta/{filename}")
def get_meta(filename: str):
    """
    Return metadata for an image.

    Args:
        filename: Image filename.

    Returns:
        JSON envelope with file metadata.
    """
    try:
        path = _resolve_path(IMAGE_DIR, filename, must_exist=True)
        rel_path = path.relative_to(IMAGE_DIR.resolve())
        info = get_file_info(str(rel_path))
        if info is None:
            return _error(404, "Image not found")
        return _ok(info)
    except HTTPException as exc:
        return _error(exc.status_code, str(exc.detail))


@app.get("/gallery")
def get_gallery(limit: int = 50):
    """
    List images in the gallery along with metadata.

    Args:
        limit: Maximum number of images to return (default 50).

    Returns:
        JSON envelope containing `{images, count}` where each entry includes URLs and metadata.
    """
    images = []
    image_dir_resolved = IMAGE_DIR.resolve()
    for f in sorted(IMAGE_DIR.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            # Skip files inside the thumbs/webp directories
            resolved = f.resolve()
            if str(resolved).startswith(str(THUMB_DIR.resolve())):
                continue
            if str(resolved).startswith(str(WEBP_DIR.resolve())):
                continue
            # Relative path from IMAGE_DIR avoids name collisions across subfolders.
            rel_path = resolved.relative_to(image_dir_resolved)
            # Normalize path separators for URL generation.
            rel_path_str = str(rel_path).replace("\\", "/")
            # Use full path for metadata lookup to avoid collisions
            info = get_file_info(f)
            if info:
                # Ensure the response includes the filename as expected by tests
                info["filename"] = f.name
                # Add size information in both bytes and kilobytes for compatibility
                size_bytes = info.get("size")
                if size_bytes is not None:
                    info["size_bytes"] = size_bytes
                    # Round to one decimal place for kilobytes, matching other parts of the codebase
                    info["size_kb"] = round(size_bytes / 1024, 1)
                # URL-encode the relative path to support subdirectories safely.
                info["url"] = f"{PUBLIC_BASE_URL}/images/{quote(rel_path_str)}"
                thumb_name = f.stem + ".jpg"
                thumb_path = THUMB_DIR / thumb_name
                if thumb_path.exists():
                    info["thumb_url"] = f"{PUBLIC_BASE_URL}/thumbs/{quote(thumb_name)}"
                images.append(info)
                if len(images) >= limit:
                    break
    return _ok({"images": images, "count": len(images)})


@app.get("/api/refresh")
def api_refresh():
    """
    Return a refreshed image list for the interactive gallery.

    Returns:
        JSON envelope containing `{images, count}`.
    """
    image_data = _build_image_data_list()
    return _ok({"images": image_data, "count": len(image_data)})


@app.delete("/api/delete/{filename}")
def delete_image(filename: str):
    """
    Delete an image and its derived files (thumbnail, WebP).

    Args:
        filename: Image filename.

    Returns:
        JSON envelope describing what was deleted.
    """
    try:
        original_path = _resolve_path(IMAGE_DIR, filename, must_exist=True)

        deleted_files: list[str] = []
        errors: list[str] = []

        # 1) Delete original
        try:
            original_path.unlink()
            deleted_files.append(f"Original: {filename}")
        except Exception as e:
            errors.append(f"Failed to delete original: {e}")

        # 2) Delete thumbnails (JPG and legacy PNG)
        stem = original_path.stem
        thumb_jpg = THUMB_DIR / f"{stem}.jpg"
        thumb_png = THUMB_DIR / f"{stem}.png"
        
        for thumb_path in [thumb_jpg, thumb_png]:
            if thumb_path.exists():
                try:
                    thumb_path.unlink()
                    deleted_files.append(f"Thumbnail: {thumb_path.name}")
                except Exception as e:
                    errors.append(f"Failed to delete thumbnail {thumb_path.name}: {e}")

        # 3) Delete WebP copy
        from app.utils import ensure_webp
        webp_name = ensure_webp(filename)
        if webp_name:
            webp_path = WEBP_DIR / webp_name
            if webp_path.exists():
                try:
                    webp_path.unlink()
                    deleted_files.append(f"WebP: {webp_name}")
                except Exception as e:
                    errors.append(f"Failed to delete WebP: {e}")

        if errors:
            logger.warning("Partial delete for %s: %s", filename, errors)
            return _error(
                200,
                "Partial delete",
                data={"deleted": deleted_files, "errors": errors},
            )

        logger.info("Deleted image %s: %s", filename, deleted_files)
        return _ok({"deleted": deleted_files})

    except HTTPException as exc:
        message = "Image not found" if exc.status_code == 404 else str(exc.detail)
        return _error(exc.status_code, message)
    except Exception:
        logger.exception("Error deleting image %s", filename)
        return _error(500, "Internal server error")


@app.post("/cleanup")
def cleanup():
    """Remove old files (older than ``IMAGE_RETENTION_DAYS`` days).

    The original implementation returned the key ``deleted`` which does not
    match the test suite expectation. The response now uses the ``removed``
    key to indicate the number of files that were cleaned up.
    """
    from app.settings import IMAGE_RETENTION_DAYS
    # ``cleanup_old_files`` returns the count of removed files.
    removed = cleanup_old_files()
    return _ok({"removed": removed, "retention_days": IMAGE_RETENTION_DAYS})


@app.get("/")
def index():
    """
    Render the interactive HTML gallery.

    Returns:
        HTMLResponse: The gallery page.
    """
    html_content = generate_gallery_html()
    return HTMLResponse(html_content)


# ---------------------------------------------------------------------------
# Entrypoint: run MCP (streamable HTTP) + Web (FastAPI)
# ---------------------------------------------------------------------------


def run_mcp_server():
    """
    Run the MCP server over streamable HTTP.

    This function is executed in a background thread.
    """
    logger.info("Starting MCP server on port %d (Streamable HTTP, timeout=%ds)",
                WEB_PORT + 1, MCP_TIMEOUT)
    mcp.run(transport="streamable-http", host=WEB_HOST, port=WEB_PORT + 1)


def main():
    """
    Start both servers.

    The MCP server is started in a daemon thread, so it exits when the main
    process exits.
    """
    # Validate settings before startup.
    validate_settings()

    logger.info("Starting Image MCP Server")
    logger.info("MCP endpoint: http://%s:%d/mcp", WEB_HOST, WEB_PORT + 1)
    logger.info("Gallery: http://%s:%d/", WEB_HOST, WEB_PORT)

    # MCP runs in a background thread.
    mcp_thread = threading.Thread(target=run_mcp_server, daemon=True)
    mcp_thread.start()

    # Web server runs in the main thread.
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    main()
