"""
Utility functions for working with images and files.

This module contains helper functions for:
- Generating unique filenames
- Safe filename processing
- Saving images from base64
- Creating thumbnails
- Cleaning up old files
- Extracting image metadata
"""

import logging
import time
import uuid
from pathlib import Path

from PIL import Image

from app.settings import IMAGE_DIR, IMAGE_RETENTION_DAYS, THUMB_DIR, WEBP_DIR

logger = logging.getLogger(__name__)


def generate_filename(prefix: str = "sd", extension: str = "png") -> str:
    """
    Generate a unique filename with UUID.

    Generates filename in format: {prefix}_{uuid}.{extension}
    This guarantees uniqueness even with parallel generation.

    Args:
        prefix: Filename prefix (default "sd")
        extension: File extension (default "png")

    Returns:
        str: Unique filename

    Example:
        >>> generate_filename("test", "jpg")
        'test_a1b2c3d4e5f6.jpg'
    """
    return f"{prefix}_{uuid.uuid4().hex}.{extension}"


def safe_filename(filename: str) -> str:
    """
    Check and sanitize filename from dangerous characters.

    Prevents path traversal attacks and other attempts to access
    files outside allowed directories.

    Allowed characters:
        - Latin letters (a-z, A-Z)
        - Digits (0-9)
        - Underscores (_)
        - Hyphens (-)
        - Dot (.)

    Args:
        filename: Filename to check

    Returns:
        str: Sanitized filename or empty string if invalid

    Example:
        >>> safe_filename("../../../etc/passwd")
        ''
        >>> safe_filename("image_001.png")
        'image_001.png'
    """
    # Remove any path traversal attempts
    safe = Path(filename).name
    
    # Reject if result is empty or just dots (., .., etc.)
    if not safe or all(c == '.' for c in safe):
        return ""
    
    # Allow only alphanumeric characters, dots and underscores
    allowed_chars = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        "_-."
    )
    result = "".join(c for c in safe if c in allowed_chars)
    
    # Final validation: must not be empty after sanitization
    if not result:
        return ""
    
    return result


def save_image(data: bytes, filename: str | None = None) -> str:
    """
    Save image to IMAGE_DIR.

    Args:
        data: Binary image data
        filename: Filename (if None, generated automatically)

    Returns:
        str: Saved filename

    Example:
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
    Decode base64 and save image.

    Supports two formats:
        - Clean base64: "iVBORw0KGgoAAAANSUhEUg..."
        - Data URL: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."

    Args:
        b64_data: Base64-encoded image string
        filename: Filename (if None, generated automatically)

    Returns:
        str: Saved filename

    Example:
        >>> save_image_from_base64("iVBORw0KGgoAAAANSUhEUg...")
        'sd_a1b2c3d4e5f6.png'
    """
    if "," in b64_data:
        _, b64 = b64_data.split(",", 1)
    else:
        b64 = b64_data
    img_bytes = __import__("base64").b64decode(b64)
    return save_image(img_bytes, filename)


def make_thumbnail(
    filename: str,
    max_size: tuple[int, int] = (512, 512),
    quality: int = 85,
) -> str | None:
    """
    Create JPEG thumbnail for image.

    Creates a resized copy of the image preserving aspect ratio.
    Thumbnail is always saved as JPEG with given quality.

    Args:
        filename: Original filename
        max_size: Maximum thumbnail size (default 512x512)
        quality: JPEG quality (1-100, default 85)

    Returns:
        str | None: Thumbnail filename or None on error

    Example:
        >>> make_thumbnail("image.png")
        'image.jpg'
    """
    src = IMAGE_DIR / filename
    if not src.exists():
        logger.error("Cannot create thumbnail: %s not found", src)
        return None

    # Thumbnail always JPEG
    thumb_name = Path(filename).stem + ".jpg"
    dst = THUMB_DIR / thumb_name

    try:
        with Image.open(src) as img:
            # Resize preserving aspect ratio
            img.thumbnail(max_size)
            # Convert RGBA to RGB for JPEG
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")  # noqa: PLW2901
            # Save as JPEG
            img.save(dst, "JPEG", quality=quality)

        logger.info("Created thumbnail: %s", thumb_name)
        return thumb_name
    except Exception as e:
        logger.error("Failed to create thumbnail for %s: %s", filename, e)
        return None


def ensure_webp(filename: str, quality: int = 80) -> str | None:
    """
    Ensure presence of WebP copy of image.

    Checks for WebP copy in cache. If not present, loads original
    image, converts to WebP and saves.

    Args:
        filename: Original filename (png, jpg, jpeg)
        quality: WebP quality 1-100 (default 80 - good balance size/quality)

    Returns:
        str | None: WebP filename (without path) or None on error

    Example:
        >>> ensure_webp("image.png")
        'image.webp'
    """
    # Determine WebP filename
    webp_name = Path(filename).stem + ".webp"
    webp_path = WEBP_DIR / webp_name
    src_path = IMAGE_DIR / filename

    # Check if WebP copy already exists
    if webp_path.exists():
        # If original is newer than WebP copy - regenerate
        if src_path.exists() and src_path.stat().st_mtime > webp_path.stat().st_mtime:
            logger.info("WebP cache stale for %s, regenerating", filename)
        else:
            return webp_name

    # Check original existence
    if not src_path.exists():
        logger.error("Cannot create WebP: %s not found", src_path)
        return None

    try:
        with Image.open(src_path) as img:
            # Convert RGBA to RGB if needed
            if img.mode in ("P", "LA"):
                img = img.convert("RGBA")  # noqa: PLW2901
            # Save as WebP
            img.save(webp_path, "WEBP", quality=quality)

        logger.info("Created WebP: %s", webp_name)
        return webp_name
    except Exception as e:
        logger.error("Failed to create WebP for %s: %s", filename, e)
        return None


def cleanup_old_files() -> int:
    """
    Delete files older than IMAGE_RETENTION_DAYS days.

    Deletes files from IMAGE_DIR and THUMB_DIR that are older
    than the specified retention period.

    Returns:
        int: Number of deleted files

    Example:
        >>> cleanup_old_files()
        5
    """
    now = time.time()
    cutoff = now - (IMAGE_RETENTION_DAYS * 86400)  # 86400 seconds in a day
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
    Extract metadata from image (prompt, negative prompt, parameters, description).

    Universal parser: supports standard PNG parameters from SD WebUI,
    as well as custom Description field.

    Args:
        img_path: Path to image file

    Returns:
        dict | None: Dictionary with metadata or None on error
    """
    try:
        with Image.open(img_path) as img:
            parameters_raw = img.info.get("parameters", "").strip()
            description_raw = img.info.get("Description", "").strip()

            # If standard parameters exist - parse them
            if parameters_raw:
                lines = parameters_raw.splitlines()
                prompt_lines = []
                negative_prompt = ""
                other_lines = []
                in_negative = False

                for raw_line in lines:
                    stripped_line = raw_line.strip()
                    if not stripped_line:
                        continue

                    if stripped_line.lower().startswith("negative prompt:"):
                        in_negative = True
                        negative_prompt = stripped_line.split(":", 1)[1].strip()
                    elif in_negative and ":" not in stripped_line:
                        negative_prompt += ", " + stripped_line
                    elif in_negative and ":" in stripped_line:
                        in_negative = False
                        other_lines.append(stripped_line)
                    elif not in_negative and ":" not in stripped_line:
                        prompt_lines.append(stripped_line)
                    else:
                        other_lines.append(stripped_line)

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

            # If no parameters but Description exists - try to parse Description
            if description_raw:
                lines = description_raw.splitlines()
                prompt_lines = []
                negative_prompt = ""
                other_lines = []
                in_negative = False

                for raw_line in lines:
                    stripped_line = raw_line.strip()
                    if not stripped_line:
                        continue

                    if stripped_line.lower().startswith("negative prompt:"):
                        in_negative = True
                        negative_prompt = stripped_line.split(":", 1)[1].strip()
                    elif in_negative and ":" not in stripped_line:
                        negative_prompt += ", " + stripped_line
                    elif in_negative and ":" in stripped_line:
                        in_negative = False
                        other_lines.append(stripped_line)
                    elif not in_negative and ":" not in stripped_line:
                        prompt_lines.append(stripped_line)
                    else:
                        other_lines.append(stripped_line)

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

            # Neither parameters nor Description
            return {
                "prompt": "",
                "negative": "",
                "params": "",
                "description": "",
            }

    except Exception as e:
        logger.error("Error extracting metadata from %s: %s", img_path, e)
        return None


def resolve_image_path(filename: str) -> Path:
    """
    Safely resolve a filename or relative path against IMAGE_DIR.
    
    Prevents path traversal attacks by ensuring the resolved path
    stays within IMAGE_DIR.
    
    Args:
        filename: Filename or relative path (e.g., "image.png" or "subdir/image.png")
        
    Returns:
        Path: Resolved absolute path
        
    Raises:
        ValueError: If path traversal is detected
    """
    path = (IMAGE_DIR / filename).resolve()
    if not str(path).startswith(str(IMAGE_DIR.resolve())):
        raise ValueError("Path traversal detected")
    return path


def get_file_info(filename: str) -> dict | None:
    """
    Get information about image file including PNG metadata.

    Args:
        filename: Filename or relative path (e.g., "image.png" or "subdir/image.png")

    Returns:
        dict | None: Dictionary with metadata or None if file not found

    Example:
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
    try:
        path = resolve_image_path(filename)
    except ValueError:
        return None
    
    if not path.exists():
        return None
        
    stat = path.stat()
    base_info = {
        "filename": filename,
        "size_bytes": stat.st_size,
        "created": stat.st_ctime,
        "modified": stat.st_mtime,
    }

    # Extract image metadata (prompt, negative, params, description)
    meta = extract_image_metadata(path)
    if meta:
        base_info.update(meta)

    return base_info
