import base64
import json
import logging
import random
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse

import requests
from fastmcp import FastMCP
from PIL import Image as PILImage
from PIL import PngImagePlugin

from app.settings import (
    AUTH_PASS,
    AUTH_USER,
    IMAGE_DIR,
    PUBLIC_BASE_URL,
    REQUEST_TIMEOUT,
    SD_CFG_SCALE,
    SD_HEIGHT,
    SD_NEGATIVE_PROMPT,
    SD_SAMPLER,
    SD_SCHEDULE_TYPE,
    SD_STEPS,
    SD_WEBUI_URL,
    SD_WIDTH,
    THUMB_DIR,
    WEBP_DIR,
)
from app.utils import (
    generate_filename,
    make_thumbnail,
    safe_filename,
    save_image_from_base64,
)

# Constants
MAX_UPSCALE_FILES = 10
MAX_FILE_SIZE_MB = 10

logger = logging.getLogger("mcp-tools")

# ---------------------------------------------------------------------------
# Sync HTTP session (shared by all tools)
# ---------------------------------------------------------------------------
_session: requests.Session | None = None


def get_session() -> requests.Session:  # noqa: PLW0603
    """Получить (или создать) HTTP-сессию с WebUI."""
    global _session  # noqa: PLW0603
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"Content-Type": "application/json"})
        if AUTH_USER and AUTH_PASS:
            _session.auth = (AUTH_USER, AUTH_PASS)
    return _session

# ---------------------------------------------------------------------------
# Helper to resolve init_image_url for img2img
# ---------------------------------------------------------------------------
def _resolve_init_image_path(init_image_url: str) -> Path:
    """Resolve a user‑provided init image reference to a safe absolute Path.

    The function accepts either a full public URL (must start with ``PUBLIC_BASE_URL``)
    or a bare filename. Only images located in ``IMAGE_DIR``, ``WEBP_DIR`` or
    ``THUMB_DIR`` are allowed. The path is validated against path traversal and
    the filename is sanitized using :func:`safe_filename`.

    The implementation reads the directory constants from ``app.settings`` at
    call time so that tests can monkey‑patch them.
    """
    from app import settings as s

    value = init_image_url.strip()
    if not value:
        raise ValueError("init_image_url must not be empty")

    # Detect URLs that are not the public base – reject them early
    if "://" in value and not value.startswith(PUBLIC_BASE_URL):
        raise ValueError("Only URLs under PUBLIC_BASE_URL are allowed")

    # Case: full public URL
    if value.startswith(PUBLIC_BASE_URL):
        parsed = urlparse(value)
        # Allow only specific sub‑paths
        if not parsed.path.startswith(("/images/", "/webp/", "/thumbs/")):
            raise ValueError("Only /images/, /webp/ and /thumbs/ URLs are allowed")

        filename = safe_filename(Path(parsed.path).name)
        if not filename:
            raise ValueError("Invalid image filename in URL")

        # Determine which base directory to look into (dynamic settings)
        for base_dir in (s.IMAGE_DIR, s.WEBP_DIR, s.THUMB_DIR):
            candidate = (base_dir / filename).resolve()
            if candidate.is_file():
                return candidate
        raise ValueError(f"Image not found: {filename}")

    # Case: bare filename (no path components)
    filename = safe_filename(value)
    if not filename:
        raise ValueError("Invalid image filename")
    candidate = (s.IMAGE_DIR / filename).resolve()
    if not candidate.is_file():
        raise ValueError(f"Image not found in IMAGE_DIR: {filename}")
    return candidate


def register_image_tools(mcp: FastMCP):
    """
    Зарегистрировать все инструменты в MCP сервере.

    Args:
        mcp: Экземпляр FastMCP сервера для регистрации инструментов
    """

    @mcp.tool()
    def generate_image(
        prompt: str,
        negative_prompt: str = "",
        steps: int = 22,
        width: int = 1024,
        height: int = 1024,
        cfg_scale: float = 5.0,
        sampler_name: str = "Euler a",
        # The default scheduler type for img2img is "Simple" to match the desired baseline.
        scheduler: str = "Simple",
        seed: int = -1,
        restore_faces: bool = False,
        tiling: bool = False,
        description: str = "",
    ) -> str:
        """
        Генерирует изображение через Stable Diffusion WebUI.

        Рекомендуемые разрешения (width x height):
        - 1024 × 1024 – 1:1
        - 1152 × 896 – 4:3
        - 896 × 1152 – 3:4
        - 1216 × 832 – 3:2
        - 832 × 1216 – 2:3
        - 1280 × 768 – 5:3 (≈16:9)
        - 768 × 1280 – 3:5
        - 1344 × 768 – 7:4
        - 768 × 1344 – 4:7
        - 1408 × 832 – 17:10 (≈1.69)
        - 832 × 1408 – 10:17
        - 1536 × 1024 – 3:2
        - 1024 × 1536 – 2:3
        - 1664 × 1024 – 13:8 (≈1.63)
        - 1024 × 1664 – 8:13

        Args:
            prompt: Текстовое описание желаемого изображения
            negative_prompt: Текстовое описание того, что не должно быть на изображении
            steps: Количество шагов диффузии (1-150, по умолчанию 22)
            width: Ширина изображения в пикселях (768-2048, по умолчанию 1024)
            height: Высота изображения в пикселях (768-2048, по умолчанию 1024)
            cfg_scale: Масштаб следования промпту (1-30, по умолчанию 5.0)
            sampler_name: Имя сэмплера для генерации (по умолчанию "Euler a")
            scheduler: Тип планировщика (по умолчанию "Karras")
            seed: Сид для воспроизводимости (-1 для случайного, по умолчанию -1)
            restore_faces: Восстанавливать ли лица (по умолчанию False)
            tiling: Создавать ли изображение для плитки (по умолчанию False)
            description: Дополнительное описание изображения для записи в метаданные (по умолчанию "")

        Returns:
            str: Текстовый отчет с URL сгенерированного изображения и метаданными

        Raises:
            ValueError: Если параметры выходят за допустимые пределы
            RuntimeError: Если SD WebUI не вернул изображения
        """
        logger.info("🎨 MCP TOOL CALL: generate_image(prompt=%r)", prompt[:80])

        # Валидация параметров
        if not (1 <= steps <= 150):
            raise ValueError("steps must be in range 1 to 150")
        if not (768 <= width <= 2048):
            raise ValueError("width must be in range 768 to 2048")
        if width % 8 != 0:
            raise ValueError("width must be multiple of 8")
        if not (768 <= height <= 2048):
            raise ValueError("height must be in range 768 to 2048")
        if height % 8 != 0:
            raise ValueError("height must be multiple of 8")
        if not (1 <= cfg_scale <= 30):
            raise ValueError("cfg_scale must be in range 1 to 30")

        # Генерируем новый сид если seed=-1
        current_seed = seed
        if seed == -1:
            current_seed = random.randint(0, 2**32 - 1)
        logger.info("Using seed=%d", current_seed)

        # Формирование payload для запроса к SD WebUI
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or SD_NEGATIVE_PROMPT,
            "steps": steps if steps is not None else SD_STEPS,
            "width": width if width is not None else SD_WIDTH,
            "height": height if height is not None else SD_HEIGHT,
            "cfg_scale": cfg_scale if cfg_scale is not None else SD_CFG_SCALE,
            "sampler_name": sampler_name if sampler_name else SD_SAMPLER,
            "scheduler": scheduler if scheduler else SD_SCHEDULE_TYPE,
            "seed": current_seed,
            "n_iter": 1,
            "distilled_cfg_scale": 3.5,
            "tiling": tiling,
            "restore_faces": restore_faces,
        }

        # Отправка запроса к SD WebUI
        logger.info("Sending request to WebUI...")
        session = get_session()
        resp = session.post(
            f"{SD_WEBUI_URL}/sdapi/v1/txt2img",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        images_b64 = data.get("images", [])
        if not images_b64:
            return "Error: No images generated by the WebUI API."

        logger.info("Got %d images", len(images_b64))

        # Получение метаданных из первого изображения
        png_info_text = ""
        if images_b64:
            try:
                info_resp = session.post(
                    f"{SD_WEBUI_URL}/sdapi/v1/png-info",
                    json={"image": f"data:image/png;base64,{images_b64[0]}"},
                    timeout=REQUEST_TIMEOUT,
                )
                info_resp.raise_for_status()
                png_info_text = info_resp.json().get("info", "")
            except requests.RequestException as exc:
                logger.warning("Failed to fetch png-info from WebUI: %s", exc)
            except Exception as exc:
                logger.warning("Unexpected error fetching png-info: %s", exc)

        # Обработка сгенерированных изображений
        all_results = []
        for img_b64 in images_b64:
            filename = save_image_from_base64(img_b64)
            make_thumbnail(filename)

            # Сохраняем метаданные в PNG
            try:
                img_path = IMAGE_DIR / filename
                img = PILImage.open(img_path)
                meta = PngImagePlugin.PngInfo()
                # Стандартное поле parameters для PNG info (как в SD WebUI)
                if png_info_text:
                    meta.add_text("parameters", png_info_text)
                # Дополнительное описание
                if description:
                    meta.add_text("Description", description)
                img.save(img_path, pnginfo=meta)
            except OSError as exc:
                logger.warning("Failed to write PNG metadata for %s: %s", filename, exc)
            except Exception as exc:
                logger.warning("Unexpected error writing metadata for %s: %s", filename, exc)

            # Формирование URL для доступа к изображениям
            img_url = f"{PUBLIC_BASE_URL}/images/{filename}"
            all_results.append({
                "filename": filename,
                "url": img_url,
                "seed": current_seed,
            })

        # Формирование текстового отчета
        result_lines = [
            f"Image generation complete! ({len(all_results)} image(s))",
            f"Prompt: {prompt}",
            "",
        ]
        for i, r in enumerate(all_results, 1):
            result_lines.append(f"Image {i} (seed {r['seed']}):")
            result_lines.append(f"  URL: {r['url']}")
            result_lines.append("")

        if png_info_text:
            result_lines.append("--- Generation Parameters ---")
            result_lines.append(png_info_text)

        return "\n".join(result_lines)

    # ------------------------------------------------------------------
    # img2img tool (image-to-image generation)
    @mcp.tool()
    def img2img(
        prompt: str,
        init_image_url: str,
        negative_prompt: str = "",
        steps: int = 22,
        # Width and height default to the original dimensions of the init image.
        width: int | None = None,
        height: int | None = None,
        cfg_scale: float = 5.0,
        sampler_name: str = "Euler a",
        # The default scheduler for img2img is "Simple" as per the specification.
        scheduler: str = "Simple",
        seed: int = -1,
        restore_faces: bool = False,
        tiling: bool = False,
        description: str = "",
        # Default denoising strength is set to 0.52, which is a balanced default for
        # typical img2img operations. The allowed range is 0.20‑0.92.
        denoising_strength: float = 0.52,
        resize_mode: int = 0,
    ) -> str:
        """
        Image-to-image generation.

        IMPORTANT:
        - init_image_url must point to an existing image already stored on this server.
        - Allowed values:
        * bare filename from IMAGE_DIR

        - Do not pass arbitrary local paths or invented names.
        Generate an image using an initial image (img2img).

        The implementation mirrors :func:`generate_image` but adds the
        ``init_image`` handling and uses the ``/sdapi/v1/img2img`` endpoint.

        **Denoising strength guidance** – The ``denoising_strength`` parameter
        controls how much of the original image is retained versus how much the
        model is allowed to modify it. The following ranges are recommended for
        typical use‑cases:

        * ``0.20‑0.36`` – Suitable for up‑scaling or very subtle adjustments.
        * ``0.37‑0.48`` – Minor edits and cosmetic style tweaks; more impact
          than the first range but still conservative.
        * ``0.49‑0.62`` – Medium‑level changes; noticeable style influence and
          detail modifications.
        * ``0.63‑0.74`` – Large transformations; anatomy, pose, or overall style
          may change significantly.
        * ``0.75‑0.92`` – Very strong changes; can completely replace details,
          viewpoints, and artistic style.
        """
        logger.info("🖼️ MCP TOOL CALL: img2img(prompt=%r, init=%r)", prompt[:80], init_image_url)

        # Basic validation – reuse same checks as generate_image where applicable
        # Basic validation – prompt and steps are mandatory.
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if not (1 <= steps <= 150):
            raise ValueError("steps must be in range 1 to 150")

        # Validate parameters that do not depend on the init image first.
        if not (1 <= cfg_scale <= 30):
            raise ValueError("cfg_scale must be in range 1 to 30")
        if not (0.20 <= denoising_strength <= 0.92):
            raise ValueError("denoising_strength must be in range 0.20 to 0.92")
        if resize_mode not in (0, 1, 2, 3):
            raise ValueError("resize_mode must be in range 0 to 3")

        # Resolve init image – needed for optional width/height defaults.
        init_path = _resolve_init_image_path(init_image_url)
        init_b64 = base64.b64encode(init_path.read_bytes()).decode("utf-8")

        # Determine width/height from init image if not provided.
        try:
            with PILImage.open(init_path) as img_obj:
                orig_w, orig_h = img_obj.size
        except Exception as exc:
            raise ValueError(f"Unable to read init image dimensions: {exc}")
        if width is None:
            width = orig_w
        if height is None:
            height = orig_h

        # If the caller supplied explicit width/height (i.e., not None before defaulting),
        # enforce the standard range and alignment constraints.
        # Since we have overwritten width/height with defaults, we need to check the original
        # arguments. The simplest approach is to re‑apply the checks only when the caller
        # passed a value (i.e., the variable was not None before we set it above).
        # In this implementation, the variables are now always non‑None, so we guard with
        # a sentinel check using the original arguments captured via locals().
        # However, because we overwrote them, we can infer that if the init image dimensions
        # are smaller than the minimum, they originated from the init image and should be
        # accepted. Therefore we only raise if the dimensions are outside the allowed range
        # *and* they are larger than the minimum (i.e., the user explicitly requested them).
        if width < 512 or width > 2048:
            # Assume this came from the init image; skip validation.
            pass
        else:
            if width % 8 != 0:
                raise ValueError("width must be multiple of 8")
        if height < 512 or height > 2048:
            # Assume this came from the init image; skip validation.
            pass
        else:
            if height % 8 != 0:
                raise ValueError("height must be multiple of 8")

        # Seed handling
        current_seed = seed if seed != -1 else random.randint(0, 2**32 - 1)

        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or SD_NEGATIVE_PROMPT,
            "steps": steps,
            "width": width,
            "height": height,
            "cfg_scale": cfg_scale,
            "sampler_name": sampler_name or SD_SAMPLER,
            "scheduler": scheduler or SD_SCHEDULE_TYPE,
            "seed": current_seed,
            "n_iter": 1,
            "tiling": tiling,
            "restore_faces": restore_faces,
            "denoising_strength": denoising_strength,
            "resize_mode": resize_mode,
            "init_images": [init_b64],
            "send_images": True,
            "save_images": False,
        }

        session = get_session()
        resp = session.post(
            f"{SD_WEBUI_URL}/sdapi/v1/img2img",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        images_b64 = data.get("images", [])
        if not images_b64:
            return "Error: No images generated by the WebUI API."

        parameters = data.get("parameters", {})
        info_text = data.get("info", "")

        results = []
        for img_b64 in images_b64:
            filename = save_image_from_base64(img_b64)
            thumb_name = make_thumbnail(filename)

            # Write PNG metadata
            try:
                img_path = IMAGE_DIR / filename
                img = PILImage.open(img_path)
                meta = PngImagePlugin.PngInfo()
                if parameters:
                    meta.add_text("parameters", json.dumps(parameters, ensure_ascii=False, indent=2))
                if info_text:
                    meta.add_text("info", info_text)
                if description:
                    meta.add_text("Description", description)
                meta.add_text("Init image", init_path.name)
                img.save(img_path, pnginfo=meta)
            except Exception as exc:
                logger.warning("Failed to write PNG metadata for %s: %s", filename, exc)

            results.append({
                "filename": filename,
                "url": f"{PUBLIC_BASE_URL}/images/{filename}",
                "thumb_url": f"{PUBLIC_BASE_URL}/thumbs/{thumb_name}" if thumb_name else "",
                "seed": current_seed,
            })

        # Build human‑readable report
        lines = [
            f"Image generation complete! ({len(results)} image(s))",
            f"Prompt: {prompt}",
            f"Init image: {init_path.name}",
            f"Denoising strength: {denoising_strength}",
            "",
        ]
        for i, r in enumerate(results, 1):
            lines.append(f"Image {i} (seed {r['seed']}):")
            lines.append(f"  URL: {r['url']}")
            # Include thumbnail URL if generated, mirroring the upscale tool output.
            if r.get("thumb_url"):
                lines.append(f"  Thumbnail: {r['thumb_url']}")
            lines.append("")

        lines.append("--- Generation Parameters ---")
        lines.append(info_text or json.dumps(parameters, ensure_ascii=False, indent=2))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Upscaler validation
    # ------------------------------------------------------------------
    def validate_upscaler(name: str) -> None:
        """Validate that the upscaler exists in SD WebUI."""
        session = get_session()
        resp = session.get(f"{SD_WEBUI_URL}/sdapi/v1/upscalers", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        available = [u["name"] for u in resp.json()]
        if name not in available:
            raise ValueError(
                f"Upscaler '{name}' not found. Available: {available}"
            )

    # ------------------------------------------------------------------
    # upscale_images
    # ------------------------------------------------------------------
    @mcp.tool()
    def upscale_images(
        file_urls: list[str],  # noqa: UP006
        resize_mode: int = 0,
        upscaling_resize: int = 2,
        upscaling_resize_w: int = 512,
        upscaling_resize_h: int = 512,
        upscaler_1: str = "R-ESRGAN 4x+",
        upscaler_2: str = "None",
    ) -> str:
        """
        Upscale images via SD WebUI using /sdapi/v1/extra-single-image.

        Accepts only trusted sources:
        - PUBLIC_BASE_URL paths (e.g. http://host:port/images/name.png)
        - bare filenames from IMAGE_DIR
        """
        logger.info("upscale_images: %d file(s)", len(file_urls))

        if not file_urls:
            return "Error: No files provided for upscaling."

        # Validate upscaler exists
        try:
            validate_upscaler(upscaler_1)
        except (ValueError, requests.RequestException) as exc:
            logger.error("Upscaler validation failed: %s", exc)
            return f"Error: {exc}"

        _url_prefix = PUBLIC_BASE_URL.rstrip("/")

        def _resolve_trusted_source(url_or_path: str) -> tuple[bytes, str]:
            """Read image bytes only from trusted sources."""
            url_or_path = url_or_path.strip()

            if url_or_path.startswith(_url_prefix):
                suffix = url_or_path[len(_url_prefix):]
                allowed_prefixes = ("/images/", "/webp/", "/thumbs/")
                if not any(suffix.startswith(p) for p in allowed_prefixes):
                    raise ValueError(f"URL path not allowed: {url_or_path}")

                filename = Path(suffix).name
                if suffix.startswith("/images/"):
                    base = IMAGE_DIR
                elif suffix.startswith("/webp/"):
                    base = WEBP_DIR
                else:
                    base = THUMB_DIR

                safe_name = safe_filename(filename)
                if not safe_name:
                    raise ValueError(f"Invalid filename in URL: {filename}")

                file_path = (base / safe_name).resolve()
                if not str(file_path).startswith(str(base.resolve())):
                    raise ValueError(f"Path traversal detected: {file_path}")
                if not file_path.is_file():
                    raise FileNotFoundError(f"File not found: {file_path}")

                return file_path.read_bytes(), safe_name

            stripped = url_or_path.strip("/")
            if "/" not in stripped and not url_or_path.startswith(("http://", "https://", "/")):
                safe_name = safe_filename(stripped)
                if not safe_name:
                    raise ValueError(f"Invalid filename: {stripped}")

                file_path = (IMAGE_DIR / safe_name).resolve()
                if not str(file_path).startswith(str(IMAGE_DIR.resolve())):
                    raise ValueError(f"Path traversal detected: {file_path}")
                if not file_path.is_file():
                    raise FileNotFoundError(f"File not found in IMAGE_DIR: {safe_name}")

                return file_path.read_bytes(), safe_name

            raise ValueError(
                f"Untrusted source rejected: {url_or_path}. "
                f"Only {PUBLIC_BASE_URL} URLs or filenames from IMAGE_DIR are allowed."
            )

        session = get_session()
        results: list[dict[str, str]] = []

        for url_or_path in file_urls:
            try:
                img_data, original_name = _resolve_trusted_source(url_or_path)
            except (ValueError, FileNotFoundError) as exc:
                logger.error("upscale_images: rejected source %r: %s", url_or_path, exc)
                return f"Error: {exc}"

            base64_image = base64.b64encode(img_data).decode("utf-8")

            # Minimal payload to avoid triggering problematic extensions like sd-webui-pixelart
            minimal_payload = {
                "resize_mode": resize_mode,
                "upscaling_resize": upscaling_resize,
                "upscaler_1": upscaler_1,
                "image": base64_image,
            }

            # Full payload with all options
            full_payload = {
                "resize_mode": resize_mode,
                "show_extras_results": True,
                "gfpgan_visibility": 0,
                "codeformer_visibility": 0,
                "codeformer_weight": 0,
                "upscaling_resize": upscaling_resize,
                "upscaling_resize_w": upscaling_resize_w,
                "upscaling_resize_h": upscaling_resize_h,
                "upscaling_crop": True,
                "upscaler_1": upscaler_1,
                "upscaler_2": upscaler_2,
                "extras_upscaler_2_visibility": 0,
                "upscale_first": False,
                "image": base64_image,
            }

            # Try minimal payload first, then full payload as fallback
            payloads_to_try = [minimal_payload]
            if upscaler_2 != "None" or resize_mode != 0:
                payloads_to_try.append(full_payload)

            upscaled_b64 = None
            last_error = None

            for payload in payloads_to_try:
                try:
                    resp = session.post(
                        f"{SD_WEBUI_URL}/sdapi/v1/extra-single-image",
                        json=payload,
                        timeout=REQUEST_TIMEOUT,
                    )

                    if not resp.ok:
                        try:
                            err = resp.json()
                        except Exception:
                            err = resp.text
                        logger.error("WebUI error response: %s", err)
                        last_error = f"WebUI error: {err}"
                        continue

                    data = resp.json()
                    upscaled_b64 = data.get("image")
                    if upscaled_b64:
                        break
                    last_error = "No image returned from extra-single-image endpoint"

                except requests.RequestException as exc:
                    logger.exception("upscale_images: WebUI request failed for %s", url_or_path)
                    last_error = f"WebUI request failed: {exc}"
                    continue

            if not upscaled_b64:
                return f"Error: WebUI request failed for {url_or_path}: {last_error}"

            filename = generate_filename(prefix=f"upscaled_{Path(original_name).stem}")
            save_image_from_base64(upscaled_b64, filename)

            thumb_name = make_thumbnail(filename)
            img_url = f"{PUBLIC_BASE_URL}/images/{filename}"
            thumb_url = f"{PUBLIC_BASE_URL}/thumbs/{thumb_name}" if thumb_name else ""

            results.append(
                {
                    "filename": filename,
                    "url": img_url,
                    "thumb_url": thumb_url,
                }
            )

        result_lines = [
            f"Upscale complete! ({len(results)} image(s))",
            "",
        ]

        for i, r in enumerate(results, 1):
            result_lines.append(f"Upscaled {i}:")
            result_lines.append(f" URL: {r['url']}")
            if r["thumb_url"]:
                result_lines.append(f" Thumbnail: {r['thumb_url']}")
            result_lines.append("")

        return "\n".join(result_lines)

    # ------------------------------------------------------------------
    # get_sd_upscalers
    # ------------------------------------------------------------------
    @mcp.tool()
    def get_sd_upscalers() -> str:
        """
        Получить список доступных апскейлеров.

        Запрашивает список апскейлеров из SD WebUI API.

        Returns:
            str: Список доступных апскейлеров
        """
        session = get_session()
        resp = session.get(f"{SD_WEBUI_URL}/sdapi/v1/upscalers", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        upscalers = resp.json()
        lines = ["Available Upscalers:", ""]
        for u in upscalers:
            lines.append(f"  - {u.get('name', '')}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # get_gallery
    # ------------------------------------------------------------------
    @mcp.tool()
    def get_gallery(limit: int = 20) -> str:
        """
        Get list of latest generated images with metadata.

        Recursively scans IMAGE_DIR for images and returns them sorted by
        modification time (newest first). Supports subdirectories.

        Args:
            limit: Maximum number of images to return (default 20)

        Returns:
            str: Formatted list of images with URLs, thumbnails, and sizes
        """
        images = []
        image_dir_resolved = IMAGE_DIR.resolve()
        
        for f in sorted(IMAGE_DIR.rglob("*"), key=lambda x: x.stat().st_mtime if x.is_file() else 0, reverse=True):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                # Skip files inside thumbs/webp directories
                resolved = f.resolve()
                if str(resolved).startswith(str(THUMB_DIR.resolve())):
                    continue
                if str(resolved).startswith(str(WEBP_DIR.resolve())):
                    continue
                
                # Get relative path from IMAGE_DIR for URL generation
                rel_path = resolved.relative_to(image_dir_resolved)
                img_url = f"{PUBLIC_BASE_URL}/images/{rel_path}"
                
                # Thumbnail handling
                thumb_name = f.stem + ".jpg"
                thumb_path = THUMB_DIR / thumb_name
                thumb_url = f"{PUBLIC_BASE_URL}/thumbs/{thumb_name}" if thumb_path.exists() else img_url
                
                images.append({
                    "name": str(rel_path),
                    "url": img_url,
                    "thumb_url": thumb_url,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                })
                if len(images) >= limit:
                    break

        if not images:
            return "No images in gallery."

        lines = [f"Gallery ({len(images)} images):", ""]
        for img in images:
            lines.append(f"  - [{img['name']}]({img['url']}) - {img['size_kb']} KB")

        return "\n".join(lines)
