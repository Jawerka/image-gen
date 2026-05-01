"""
MCP инструменты для генерации изображений.

Этот модуль содержит все инструменты для работы со Stable Diffusion WebUI:
- generate_image: Генерация изображений по текстовому описанию
- upscale_images: Увеличение разрешения изображений
- get_sd_upscalers: Получение списка апскейлеров
- get_gallery: Получение списка сгенерированных изображений

Каждый инструмент регистрируется в FastMCP сервере и доступен через MCP протокол.
"""

import base64
import logging
import re
import requests
from pathlib import Path
from typing import List

from PIL import Image as PILImage, PngImagePlugin
from fastmcp import FastMCP

from app.settings import (
    SD_WEBUI_URL, AUTH_USER, AUTH_PASS, REQUEST_TIMEOUT,
    PUBLIC_BASE_URL, IMAGE_DIR, THUMB_DIR,
    SD_STEPS, SD_WIDTH, SD_HEIGHT, SD_CFG_SCALE,
    SD_NEGATIVE_PROMPT, SD_SEED, SD_SAMPLER, SD_SCHEDULE_TYPE,
)
from app.utils import (
    generate_filename, save_image, make_thumbnail,
    save_image_from_base64,
)

logger = logging.getLogger("mcp-tools")

# ---------------------------------------------------------------------------
# Sync HTTP session (shared by all tools)
# ---------------------------------------------------------------------------
_session: requests.Session | None = None


def get_session() -> requests.Session:
    """Получить (или создать) HTTP-сессию с WebUI."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"Content-Type": "application/json"})
        if AUTH_USER and AUTH_PASS:
            _session.auth = (AUTH_USER, AUTH_PASS)
    return _session


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
        scheduler: str = "Karras",
        seed: int = -1,
        restore_faces: bool = False,
        tiling: bool = False,
        description: str = "",
    ) -> str:
        """
        Генерирует изображение через Stable Diffusion WebUI.

        Args:
            prompt: Текстовое описание желаемого изображения
            negative_prompt: Текстовое описание того, что не должно быть на изображении
            steps: Количество шагов диффузии (1-150, по умолчанию 22)
            width: Ширина изображения в пикселях (512-2048, по умолчанию 1024)
            height: Высота изображения в пикселях (512-2048, по умолчанию 1024)
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
        import random

        logger.info("🎨 MCP TOOL CALL: generate_image(prompt=%r)", prompt[:80])

        # Валидация параметров
        if not (1 <= steps <= 150):
            raise ValueError("steps must be in range 1 to 150")
        if not (512 <= width <= 2048):
            raise ValueError("width must be in range 512 to 2048")
        if not (512 <= height <= 2048):
            raise ValueError("height must be in range 512 to 2048")
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
            except Exception:
                pass

        # Обработка сгенерированных изображений
        all_results = []
        for img_b64 in images_b64:
            filename = save_image_from_base64(img_b64)
            thumb_name = make_thumbnail(filename)

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
            except Exception:
                pass

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
    # upscale_images
    # ------------------------------------------------------------------
    @mcp.tool()
    def upscale_images(
        file_urls: List[str],
        resize_mode: int = 0,
        upscaling_resize: int = 4,
        upscaling_resize_w: int = 512,
        upscaling_resize_h: int = 512,
        upscaler_1: str = "R-ESRGAN 4x+",
        upscaler_2: str = "None",
    ) -> str:
        """
        Апскейлит изображения через WebUI.

        Загружает изображения по URL или локальным путям,
        отправляет на апскейл в SD WebUI и сохраняет результаты.

        Args:
            file_urls: Список URL или путей к изображениям
            resize_mode: Режим изменения размера (0-4)
            upscaling_resize: Множитель увеличения (по умолчанию 4)
            upscaling_resize_w: Целевая ширина (по умолчанию 512)
            upscaling_resize_h: Целевая высота (по умолчанию 512)
            upscaler_1: Первый апскейлер (по умолчанию "R-ESRGAN 4x+")
            upscaler_2: Второй апскейлер (по умолчанию "None")

        Returns:
            str: Текстовый отчет с URL апскейленных изображений

        Raises:
            RuntimeError: Если WebUI не вернул изображения
        """
        logger.info("upscale_images: %d file(s)", len(file_urls))

        if not file_urls:
            return "Error: No files provided for upscaling."

        # Загрузка файлов
        url_pattern = re.compile(r"^https?://")
        image_list = []
        original_names = []
        for url_or_path in file_urls:
            if url_pattern.match(url_or_path):
                # Загрузка по URL
                resp = requests.get(url_or_path, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                img_data = resp.content
                name = Path(url_or_path).name
            else:
                # Локальный путь
                p = Path(url_or_path).expanduser().resolve()
                img_data = p.read_bytes()
                name = p.name
            b64 = base64.b64encode(img_data).decode("utf-8")
            image_list.append({"data": b64, "name": name})
            original_names.append(name)

        # Отправка на WebUI для апскейла
        payload = {
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
            "imageList": image_list,
        }

        session = get_session()
        resp = session.post(
            f"{SD_WEBUI_URL}/sdapi/v1/extra-batch-images",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        upscaled_b64_list = resp.json().get("images", [])

        if not upscaled_b64_list:
            raise RuntimeError("No images returned from upscaling endpoint")

        # Обработка результатов
        results = []
        for idx, img_b64 in enumerate(upscaled_b64_list):
            orig_name = original_names[idx] if idx < len(original_names) else "image.png"
            filename = generate_filename(prefix=f"upscaled_{Path(orig_name).stem}")
            save_image_from_base64(img_b64, filename)
            thumb_name = make_thumbnail(filename)

            img_url = f"{PUBLIC_BASE_URL}/images/{filename}"
            thumb_url = f"{PUBLIC_BASE_URL}/thumbs/{thumb_name}" if thumb_name else ""
            results.append({
                "filename": filename,
                "url": img_url,
                "thumb_url": thumb_url,
            })

        # Формирование отчета
        result_lines = [
            f"Upscale complete! ({len(results)} image(s))",
            "",
        ]
        for i, r in enumerate(results, 1):
            result_lines.append(f"Upscaled {i}:")
            result_lines.append(f"  URL: {r['url']}")
            if r["thumb_url"]:
                result_lines.append(f"  Thumbnail: {r['thumb_url']}")
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
        Получить список последних сгенерированных изображений.

        Args:
            limit: Максимальное количество изображений (по умолчанию 20)

        Returns:
            str: Список изображений с URL и размером
        """
        images = []
        for f in sorted(IMAGE_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                img_url = f"{PUBLIC_BASE_URL}/images/{f.name}"
                thumb_name = f.stem + ".jpg"
                thumb_path = THUMB_DIR / thumb_name
                thumb_url = f"{PUBLIC_BASE_URL}/thumbs/{thumb_name}" if thumb_path.exists() else img_url
                images.append({
                    "name": f.name,
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

