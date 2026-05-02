# Руководство по использованию Image MCP Server

## Оглавление

1. [Введение](#введение)
2. [Быстрый старт](#быстрый-старт)
3. [Концепции](#концепции)
4. [Использование через MCP](#использование-через-mcp)
5. [Использование через Web API](#использование-через-web-api)
6. [Настройка промптов](#настройка-промптов)
7. [Решение проблем](#решение-проблем)

---

## Введение

Image MCP Server — это сервер для генерации изображений через Stable Diffusion WebUI. Он предоставляет:

- **MCP API** (порт 8081) — для интеграции с LLM-клиентами (Cherry Studio, Claude Desktop)
- **Web API** (порт 8080) — для просмотра галереи и управления изображениями

### Ключевые особенности

- **No base64 в контексте** — MCP возвращает только URL, не засоряя контекст LLM
- **Streamable HTTP** — сервер всегда работает, клиенты подключаются по HTTP
- **Автоматические превью** — thumbnails создаются при генерации
- **Встроенная галерея** — просмотр всех картинок в браузере
- **Автоочистка** — старые файлы удаляются по таймеру
- **systemd сервис** — готово к продакшену с автозапуском

---

## Быстрый старт

### 1. Проверка статуса сервера

```bash
# Проверка работоспособности
curl http://localhost:8080/health

# Ожидаемый ответ:
# {"status":"ok","images_dir":"/root/image-gen/images","thumb_dir":"/root/image-gen/images/thumbs"}
```

### 2. Просмотр галереи

Откройте в браузере:
```
http://192.168.88.16:8080/
```

### 3. Генерация изображения через MCP

В Cherry Studio или другом MCP-клиенте вызовите инструмент:
```
generate_image(
    prompt="a beautiful sunset over mountains, digital art",
    steps=22,
    width=1024,
    height=1024
)
```

---

## Концепции

### Архитектура

```
┌──────────────────────┐
│  MCP Client          │
│  (Cherry Studio)     │
└──────────┬───────────┘
           │ HTTP
           │ http://192.168.88.16:8081/mcp
           v
┌──────────────────────────────┐
│  Image MCP Server            │
│  - FastMCP (port 8081)       │
│  - FastAPI (port 8080)       │
│       │                      │
│       │ вызывает SD WebUI    │
│       └─────────► SD WebUI   │
│                 (192.168.88.52:7860)
│                              │
│       сохраняет файлы ──┐    │
└─────────────────────────┼────┘
                          v
                 ┌──────────────────────┐
                 │ Filesystem            │
                 │ /root/image-gen/      │
                 │   images/             │
                 │   thumbs/             │
                 └──────────┬───────────┘
```

### Поток генерации

1. Клиент вызывает MCP инструмент `generate_image`
2. Сервер отправляет запрос к SD WebUI API
3. SD WebUI генерирует изображение и возвращает base64
4. Сервер декодирует base64 и сохраняет PNG файл
5. Сервер создает JPEG превью (thumbnail)
6. Сервер возвращает текстовый отчет с URL изображений
7. Клиент отображает изображение по URL

### Именование файлов

- **Оригиналы**: `sd_{uuid}.png` (например: `sd_a1b2c3d4e5f6.png`)
- **Превью**: `{имя_оригинала}.jpg` (например: `sd_a1b2c3d4e5f6.jpg`)
- **Апскейленные**: `upscaled_{имя_оригинала}.png`

---

## Использование через MCP

### Доступные инструменты

#### 1. generate_image

Генерирует изображение по текстовому описанию.

**Параметры:**
- `prompt` (обязательный) — текстовое описание желаемого изображения
- `negative_prompt` — описание того, что не должно быть на изображении
- `steps` — количество шагов диффузии (1-150, по умолчанию 22)
- `width` — ширина изображения (512-2048, по умолчанию 1024)
- `height` — высота изображения (512-2048, по умолчанию 1024)
- `cfg_scale` — масштаб следования промпту (1-30, по умолчанию 5.0)
- `sampler_name` — имя сэмплера (по умолчанию "Euler a")
- `scheduler` — тип планировщика (по умолчанию "Karras")
- `seed` — сид для воспроизводимости (-1 для случайного)
- `restore_faces` — восстанавливать ли лица (по умолчанию False)
- `tiling` — создавать ли для плитки (по умолчанию False)
- `description` — дополнительное описание для записи в метаданные PNG (по умолчанию "")

**Рекомендуемые разрешения (width x height):**
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

**Пример:**
```python
generate_image(
    prompt="a beautiful sunset over mountains, digital art, vibrant colors",
    negative_prompt="low quality, blurry, distorted",
    steps=30,
    width=1024,
    height=1024,
    cfg_scale=7.5,
    sampler_name="Euler a",
    scheduler="Karras",
    seed=-1,
    description="Sunset landscape generated for demo"
)
```

**Ответ:**
```
Image generation complete! (1 image(s))
Prompt: a beautiful sunset over mountains, digital art, vibrant colors

Image 1 (seed 1234567890):
  URL: http://192.168.88.16:8080/images/sd_a1b2c3d4e5f6.png

--- Generation Parameters ---
Steps: 30, Sampler: Euler a, CFG scale: 7.5, Size: 1024x1024
```

#### 2. upscale_images

Увеличивает разрешение изображений через SD WebUI.

> **Безопасность:** Принимаются только URL с вашего `PUBLIC_BASE_URL` (например, `http://host:8080/images/name.png`) или имена файлов из директории `IMAGE_DIR`. Произвольные внешние URL и произвольные локальные пути отклоняются для предотвращения SSRF-атак.

**Параметры:**
- `file_urls` (обязательный) — список доверенных URL или имён файлов
- `resize_mode` — режим изменения размера (0-4)
- `upscaling_resize` — множитель увеличения (по умолчанию 4)
- `upscaling_resize_w` — целевая ширина (по умолчанию 512)
- `upscaling_resize_h` — целевая высота (по умолчанию 512)
- `upscaler_1` — первый апскейлер (по умолчанию "R-ESRGAN 4x+")
- `upscaler_2` — второй апскейлер (по умолчанию "None")

**Пример:**
```python
upscale_images(
    file_urls=["http://192.168.88.16:8080/images/sd_a1b2c3d4e5f6.png"],
    upscaling_resize=4,
    upscaler_1="R-ESRGAN 4x+"
)
```

#### 3. get_sd_upscalers

Получить список доступных апскейлеров из SD WebUI.

**Пример:**
```python
get_sd_upscalers()
```

**Ответ:**
```
Available Upscalers:

  - R-ESRGAN 4x+
  - R-ESRGAN 2x+
  - None
```

#### 4. get_gallery

Получить список последних сгенерированных изображений.

**Параметры:**
- `limit` — максимальное количество изображений (по умолчанию 20, в Web API `/gallery` — 50)

**Пример:**
```python
get_gallery(limit=10)
```

---

## Использование через Web API

### Эндпоинты

#### GET /health

Проверка работоспособности.

**Пример:**
```bash
curl http://localhost:8080/health
```

**Ответ:**
```json
{
  "status": "ok",
  "images_dir": "/root/image-gen/images",
  "thumb_dir": "/root/image-gen/images/thumbs"
}
```

#### GET /images/{filename}

Отдать оригинал изображения.

**Пример:**
```bash
curl http://localhost:8080/images/sd_a1b2c3d4e5f6.png --output image.png
```

#### GET /thumbs/{filename}

Отдать превью изображения.

**Пример:**
```bash
curl http://localhost:8080/thumbs/sd_a1b2c3d4e5f6.jpg --output thumb.jpg
```

#### GET /meta/{filename}

Получить метаданные файла.

**Пример:**
```bash
curl http://localhost:8080/meta/sd_a1b2c3d4e5f6.png
```

**Ответ:**
```json
{
  "filename": "sd_a1b2c3d4e5f6.png",
  "size_bytes": 237900,
  "created": 1714567890.123,
  "modified": 1714567890.123
}
```

#### GET /gallery

Список всех доступных изображений.

**Пример:**
```bash
curl http://localhost:8080/gallery
```

**Ответ:**
```json
{
  "images": [
    {
      "filename": "sd_a1b2c3d4e5f6.png",
      "size_bytes": 237900,
      "created": 1714567890.123,
      "modified": 1714567890.123,
      "url": "http://192.168.88.16:8080/images/sd_a1b2c3d4e5f6.png",
      "thumb_url": "http://192.168.88.16:8080/thumbs/sd_a1b2c3d4e5f6.jpg"
    }
  ],
  "count": 1
}
```

#### GET /

HTML-галерея для просмотра в браузере.

**Пример:**
Откройте в браузере: `http://192.168.88.16:8080/`

#### POST /cleanup

Удалить старые файлы.

**Пример:**
```bash
curl -X POST http://localhost:8080/cleanup
```

**Ответ:**
```json
{
  "removed": 5
}
```

---

## Настройка промптов

### Позитивный промпт

Позитивный промпт описывает, что вы хотите видеть на изображении.

**Структура:**
```
[субъект], [описание], [стиль], [качество], [дополнительные детали]
```

**Примеры:**

1. **Портрет:**
```
portrait of a beautiful woman, blue eyes, blonde hair, soft lighting, cinematic lighting, 8k, high quality, masterpiece
```

2. **Пейзаж:**
```
sunset over mountains, vibrant colors, clouds, detailed background, landscape photography, 8k, high quality
```

3. **Антропоморфное:**
```
anthro cat, blue fur, green eyes, wearing armor, standing pose, dynamic angle, fantasy art, highly detailed
```

### Негативный промпт

Негативный промпт описывает, что вы НЕ хотите видеть на изображении.

**Рекомендуемые значения:**
```
low quality, worst quality, lowres, blurry, out of focus, jpeg artifacts, pixelated, noisy,
bad anatomy, bad proportions, deformed, disfigured, malformed, mutated, extra limbs,
missing limbs, extra arms, extra legs, extra fingers, missing fingers, fused fingers,
poorly drawn hands, poorly drawn face, crossed eyes, asymmetrical face, cloned face,
cropped, out of frame, duplicate, multiple heads, watermark, signature, text, logo, frame, border
```

### Параметры генерации

| Параметр | Рекомендуемое значение | Описание |
|----------|------------------------|----------|
| `steps` | 20-30 | Количество шагов диффузии. Больше = лучше качество, но дольше |
| `cfg_scale` | 5-10 | Следование промпту. Больше = строже следование |
| `width` | 512-1024 | Ширина изображения |
| `height` | 512-1024 | Высота изображения |
| `seed` | -1 | -1 для случайного, конкретное число для воспроизводимости |

---

## Решение проблем

### Сервис не запускается

**Проверьте логи:**
```bash
journalctl -u image-gen -n 50 --no-pager
```

**Типичные причины:**
- Ошибка в .env файле
- Недоступен SD WebUI
- Проблемы с правами доступа к директориям

### Ошибка подключения к SD WebUI

**Проверьте доступность:**
```bash
curl http://192.168.88.52:7860
```

**Проверьте настройки в .env:**
```bash
cat /root/image-gen/code/.env | grep SD_WEBUI_URL
```

### Нет изображений в галерее

**Проверьте права доступа:**
```bash
ls -la /root/image-gen/images/
```

**Проверьте логи сервера:**
```bash
journalctl -u image-gen -f
```

### Ошибка импорта модулей

**Убедитесь, что venv активирован:**
```bash
source /root/image-gen/code/venv/bin/activate
```

**Переустановите зависимости:**
```bash
cd /root/image-gen/code
source venv/bin/activate
pip install -r requirements.txt
```

### Изображения не сохраняются

**Проверьте свободное место на диске:**
```bash
df -h /root/image-gen/images/
```

**Проверьте права доступа к директории:**
```bash
ls -ld /root/image-gen/images/
```

---

## Дополнительная информация

- [README.md](README.md) — Основная документация проекта
- [deploy/INSTALL.md](deploy/INSTALL.md) — Инструкция по установке
