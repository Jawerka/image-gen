Ниже — более точный план, с упором на минимальный diff и на то, чтобы `img2img` выглядел как естественное продолжение текущего сервера, а не как отдельная подсистема. В репозитории уже есть ровно те места, которые нужно расширять: все MCP-инструменты живут в `code/app/tools.py`, общая работа с файлами и превью — в `app/utils.py`, а пути, URL и дефолты — в `app/settings.py`. В README уже описаны существующие инструменты `generate_image`, `upscale_images`, `get_sd_upscalers`, `get_gallery`, а также структура `images/`, `thumbs/` и `webp/`. ([GitHub][1])

## Что важно сохранить без изменений

`img2img` лучше реализовать в том же стиле, что и `generate_image`: один модуль `tools.py`, тот же `requests.Session`, тот же путь сохранения файлов, те же `save_image_from_base64()` и `make_thumbnail()`, тот же формат текстового ответа с URL, без переноса base64 в контекст LLM. В текущем `generate_image` уже есть валидация, генерация seed, отправка запроса в WebUI, сохранение результата, миниатюра и запись PNG-метаданных — это и есть правильный шаблон для копирования с минимальными правками. ([GitHub][2])

## Как бы я перестроил ваш план

### 1. Расширить `tools.py` без новых модулей и классов

Не вводить отдельный `Img2ImgService`, `ImageSourceResolver` и прочие сущности. Для этого проекта выгоднее сохранить плоскую структуру: добавить только новый MCP-инструмент `img2img` и, при необходимости, один маленький приватный helper внутри того же `tools.py` для безопасного разрешения исходного изображения. Это соответствует текущей архитектуре, где весь набор инструментов уже сосредоточен в одном файле. ([GitHub][2])

### 2. В `tools.py` добавить только нужные импорты

С высокой вероятностью понадобятся:

```python
import json
from urllib.parse import urlparse
```

И, вероятно, импорт `safe_filename` из `app.utils`, потому что сейчас в `utils.py` уже есть готовая защита от path traversal. Это лучше, чем писать новую проверку имени файла вручную. ([GitHub][3])

### 3. Сначала решить источник `init_image`

Самая важная часть — не payload, а безопасное получение исходника.

Нормальная логика здесь такая:

1. Если пришёл URL, он должен принадлежать только вашему `PUBLIC_BASE_URL`.
2. Разрешать только пути вида `/images/`, `/webp/`, `/thumbs/`.
3. Дальше извлекать имя файла и читать его с диска из `IMAGE_DIR`/`WEBP_DIR`/`THUMB_DIR`, а не скачивать из сети.
4. Если пришло имя файла — пропускать через `safe_filename()` и читать только из разрешённой директории.
5. Любые внешние URL, `../`, абсолютные пути и пустые значения — сразу отклонять.

Это важнее, чем любой другой параметр, потому что именно тут обычно появляются SSRF и path traversal. В текущем коде уже есть заготовка для такой защиты: `safe_filename()` и строгая работа с `Path`. ([GitHub][3])

Пример небольшого приватного helper’а в `tools.py`:

```python
from pathlib import Path
from urllib.parse import urlparse

def _resolve_init_image_path(init_image_url: str) -> Path:
    value = init_image_url.strip()
    if not value:
        raise ValueError("init_image_url must not be empty")

    # Только свои публичные URL
    if value.startswith(PUBLIC_BASE_URL):
        parsed = urlparse(value)
        if not parsed.path.startswith(("/images/", "/webp/", "/thumbs/")):
            raise ValueError("Only /images/, /webp/ and /thumbs/ URLs are allowed")

        filename = safe_filename(Path(parsed.path).name)
        if not filename:
            raise ValueError("Invalid image filename")

        # Поддержка раздачи из уже существующих директорий
        for base_dir in (IMAGE_DIR, THUMB_DIR, WEBP_DIR):
            candidate = (base_dir / filename).resolve()
            if candidate.exists():
                return candidate

        raise FileNotFoundError(f"Image not found: {filename}")

    # Только имя файла, без путей
    filename = safe_filename(value)
    if not filename:
        raise ValueError("Invalid image filename")

    candidate = (IMAGE_DIR / filename).resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"Image not found: {filename}")

    return candidate
```

### 4. `img2img` должен быть почти копией `generate_image`, но с другим payload

Ваш текущий `generate_image` уже строит payload, отправляет его на `/sdapi/v1/txt2img`, сохраняет изображения, создаёт превью и возвращает текстовый отчёт. Для `img2img` лучше сохранить тот же каркас, но заменить только три вещи:

* endpoint: `/sdapi/v1/img2img`
* обязательное поле: `init_images: [base64(init_image)]`
* сохранение метаданных: использовать `parameters` и `info` из ответа, без отдельного `/png-info` запроса, потому что этот endpoint уже возвращает нужные данные

Пример каркаса:

```python
@mcp.tool()
def img2img(
    prompt: str,
    init_image_url: str,
    negative_prompt: str = "",
    steps: int = 22,
    width: int = 1024,
    height: int = 1024,
    cfg_scale: float = 5.0,
    sampler_name: str = "Euler a",
    scheduler: str = "Karras",
    seed: int = -1,
    denoising_strength: float = 0.75,
    restore_faces: bool = False,
    tiling: bool = False,
    resize_mode: int = 0,
    description: str = "",
) -> str:
    if not prompt.strip():
        raise ValueError("prompt must not be empty")

    if not (1 <= steps <= 150):
        raise ValueError("steps must be in range 1 to 150")
    if not (512 <= width <= 2048):
        raise ValueError("width must be in range 512 to 2048")
    if not (512 <= height <= 2048):
        raise ValueError("height must be in range 512 to 2048")
    if not (1 <= cfg_scale <= 30):
        raise ValueError("cfg_scale must be in range 1 to 30")
    if not (0.0 <= denoising_strength <= 1.0):
        raise ValueError("denoising_strength must be in range 0.0 to 1.0")
    if resize_mode not in (0, 1, 2, 3):
        raise ValueError("resize_mode must be in range 0 to 3")

    current_seed = seed if seed != -1 else random.randint(0, 2**32 - 1)

    init_path = _resolve_init_image_path(init_image_url)
    init_b64 = base64.b64encode(init_path.read_bytes()).decode("utf-8")

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
        "init_images": [init_b64],
        "resize_mode": resize_mode,
        "denoising_strength": denoising_strength,
        "image_cfg_scale": cfg_scale,
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
    parameters = data.get("parameters", {})
    info_text = data.get("info", "")

    if not images_b64:
        return "Error: No images generated by the WebUI API."

    results = []
    for img_b64 in images_b64:
        filename = save_image_from_base64(img_b64)
        thumb_name = make_thumbnail(filename)

        try:
            img_path = IMAGE_DIR / filename
            img = PILImage.open(img_path)
            meta = PngImagePlugin.PngInfo()

            meta.add_text("parameters", json.dumps(parameters, ensure_ascii=False, indent=2))
            if info_text:
                meta.add_text("info", info_text)
            if description:
                meta.add_text("Description", description)
            meta.add_text("Init image", init_path.name)

            img.save(img_path, pnginfo=meta)
        except Exception:
            pass

        results.append({
            "filename": filename,
            "url": f"{PUBLIC_BASE_URL}/images/{filename}",
            "thumb_url": f"{PUBLIC_BASE_URL}/thumbs/{thumb_name}" if thumb_name else "",
            "seed": current_seed,
        })

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
        if r["thumb_url"]:
            lines.append(f"  Thumbnail: {r['thumb_url']}")
        lines.append("")

    lines.append("--- Generation Parameters ---")
    lines.append(info_text or json.dumps(parameters, ensure_ascii=False, indent=2))
    return "\n".join(lines)
```

### 5. Не копировать лишнее из `txt2img`

Есть один важный упрощающий момент: для `img2img` не нужен дополнительный запрос к `/sdapi/v1/png-info`, который сейчас используется в `generate_image` для извлечения параметров из картинки. Ваш endpoint уже возвращает `parameters` и `info`, поэтому лучше сразу сохранить именно их. Это уменьшит количество запросов и уберёт лишний сетевой шаг.

### 6. Отдельно проверить формат ответа

Формат результата лучше оставить максимально похожим на текущий `generate_image`, но добавить только то, что важно для `img2img`:

* исходное изображение;
* `denoising_strength`;
* `resize_mode`;
* итоговые URL;
* при наличии — thumbnail URL;
* блок параметров в конце.

Так клиентам будет проще парсить ответ, а LLM не придётся переучиваться на новый формат.

### 7. Тесты лучше написать по трём уровням

У вас уже есть папка `tests/`, так что логично добавить туда проверки без усложнения архитектуры. Для `img2img` я бы сделал три типа тестов:

1. **Unit** — `_resolve_init_image_path()`:

   * принимает имя файла из `IMAGE_DIR`;
   * принимает URL вида `PUBLIC_BASE_URL/images/...`;
   * отклоняет `../secret.png`;
   * отклоняет внешний URL.

2. **Validation** — параметры:

   * `denoising_strength < 0` / `> 1`;
   * `resize_mode` вне диапазона;
   * пустой `prompt`;
   * пустой `init_image_url`.

3. **Integration with mock**:

   * WebUI возвращает один base64 image;
   * инструмент сохраняет файл;
   * создаёт thumbnail;
   * в ответе есть URL и seed.

## Что я бы добавил как улучшение, но без смены архитектуры

Самые полезные улучшения здесь — не новые сущности, а более строгие ограничения:

* читать исходник только из собственных публичных путей, без внешнего HTTP;
* в метаданные писать `parameters`, `info`, `Init image`, `Description`;
* использовать `send_images=True` и `save_images=False`, чтобы не плодить дубликаты на стороне WebUI;
* оставить дефолты и стиль валидации такими же, как у `generate_image`, чтобы поведение было предсказуемым. ([GitHub][2])

## Итоговая версия плана

1. Добавить `img2img` в список инструментов в докстринге `tools.py`.
2. Импортировать `safe_filename`, `json`, `urlparse`.
3. Добавить один приватный helper для безопасного разрешения `init_image_url` в локальный `Path`.
4. Реализовать `@mcp.tool()` `img2img` в том же стиле, что `generate_image`.
5. Валидировать `prompt`, `steps`, `width`, `height`, `cfg_scale`, `denoising_strength`, `resize_mode`.
6. Формировать payload для `/sdapi/v1/img2img` через `init_images`.
7. Сохранять результат через существующие `save_image_from_base64()` и `make_thumbnail()`.
8. Писать PNG-метаданные из `parameters` и `info` ответа API.
9. Возвращать текстовый отчёт с URL, thumbnail и параметрами.
10. Добавить тесты на валидацию, source resolution и сохранение результата.

Если дальше пойдёт реализация, лучше всего править только `code/app/tools.py` и не трогать соседние модули без необходимости.

[1]: https://github.com/Jawerka/image-gen/tree/master "GitHub - Jawerka/image-gen · GitHub"
[2]: https://raw.githubusercontent.com/Jawerka/image-gen/master/code/app/tools.py "raw.githubusercontent.com"
[3]: https://raw.githubusercontent.com/Jawerka/image-gen/master/code/app/utils.py "raw.githubusercontent.com"


По умолчанию базовые настройки для img2img должны быть такими.

Steps: 22, Sampler: Euler a, Schedule type: Simple, CFG scale: 5, Seed: -1, Size: <<ИСХОДНЫЙ РАЗМЕР ИЗОБРАЖЕНИЯ>>,  Denoising strength: 0.52, 

Диапазоны Denoising strength должны быть от 0.20 до 0.92

Дай инструкцию для LLM которая будет означать примерно такую ранжировку в зависимости от необходимости изменений
0.20-0.36 - это значение Denoising strength подходит для увеличения изображения или каких-то минимальных изменений.
0.37-0.48 - это значение Denoising strength подходит для внесения мелких правок и небольшого изменения стиля изображения. Чистая косметика, которая позволяет где-то немного улучшить или изменить изображения, влияние тут больше чем в первом случае
0.49-0.62 - это значение Denoising strength подходит для средних изменений в изображении. Большое влияние стиля, некоторые изменения деталей изображения
0.63-0.74 - это значение Denoising strength подходит для больших изменений в изображении. Тут уже происходят изменения в анатомии персонажей, стиль полностью может быть изменен, детали изменяются и могут изменяться хаотично.
0.75-0.92 - это значение Denoising strength подходит для очень больших изменений в изображении. Тут может происходить полная замена деталей, ракурсов и стилей. 
