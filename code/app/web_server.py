"""
Генератор HTML-галереи для раздачи изображений по HTTP.

Этот модуль содержит только функции для генерации HTML-галереи.
Основной веб-сервер находится в server.py.
"""

import html
import json
import logging
import time
from string import Template

from app.settings import IMAGE_DIR, THUMB_DIR, WEBP_DIR
from app.utils import ensure_webp, extract_image_metadata

logger = logging.getLogger(__name__)


# ===========================================================================
# Встроенный HTML-шаблон
# ===========================================================================

gallery_html_template = Template(r"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Галерея</title>
  <style>
    body {
      background: #0e0e0e;
      color: #eee;
      font-family: "Segoe UI", sans-serif;
      margin: 0;
      display: flex;
      flex-direction: column;
      height: 100vh;
    }
    .main-container {
      display: flex;
      flex: 1;
      overflow: hidden;
    }
    .image-container {
      flex: 3;
      display: flex;
      justify-content: center;
      align-items: center;
      position: relative;
      padding: 10px;
      overflow: hidden;
    }
    .image-container img {
      max-width: 100%;
      max-height: 100%;
      border-radius: 12px;
      transition: transform 0.2s;
      cursor: zoom-in;
    }
    .info-panel {
      width: 47%;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 10px;
      box-sizing: border-box;
      height: 100%;
      position: relative;
    }
    .text-block {
      position: relative;
      flex: 1;
      display: flex;
      flex-direction: column;
    }
    .info-panel textarea {
      background: #1e1e1e;
      color: white;
      border: none;
      padding: 10px 34px 10px 10px;
      border-radius: 8px;
      resize: none;
      width: 100%;
      font-family: inherit;
      flex: 1;
      box-sizing: border-box;
      min-height: 0;
    }
    .text-block.prompt { flex: 5; }
    .text-block.negative { flex: 2; }
    .text-block.params { flex: 3; }
    .copy-btn {
      position: absolute;
      top: 6px;
      right: 8px;
      background: none;
      border: none;
      color: #aaa;
      font-size: 16px;
      cursor: pointer;
      z-index: 2;
      opacity: 0.3;
      transition: opacity 0.2s;
      padding: 0;
    }
    .text-block:hover .copy-btn {
      opacity: 1;
    }
    .copy-all-btn {
      position: absolute;
      right: 10px;
      bottom: 10px;
      z-index: 2;
      background: none;
      border: none;
      color: #aaa;
      font-size: 16px;
      cursor: pointer;
      opacity: 0.3;
      transition: opacity 0.2s;
    }
    .copy-all-btn:hover {
      opacity: 1;
    }
    .thumbnail-strip {
      height: 110px;
      background-color: #1a1a1a;
      display: flex;
      align-items: center;
      overflow-x: auto;
      padding: 2px;
      box-sizing: border-box;
      user-select: none;
      cursor: grab;
    }
    .thumbnail-strip img {
      height: 80px;
      margin-right: 10px;
      border-radius: 6px;
      transition: transform 0.2s, border 0.2s;
      border: 2px solid transparent;
      object-fit: cover;
    }
    .thumbnail-strip img:hover {
      transform: scale(1.1);
      border-color: #3ea6ff;
    }
    .arrow {
      font-size: 32px;
      color: #eee;
      background: rgba(255, 255, 255, 0.05);
      border-radius: 50%;
      width: 48px;
      height: 48px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s, transform 0.2s;
      margin: 0 10px;
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      opacity: 0;
      pointer-events: none;
      cursor: pointer;
      z-index: 10;
    }
    .arrow:hover {
      background: rgba(255, 255, 255, 0.15);
      transform: translateY(-50%) scale(1.1);
    }
    .arrow.show {
      opacity: 1;
      pointer-events: auto;
    }
    #left-arrow { left: 10px; }
    #right-arrow { right: 10px; }
    .download-btn {
      position: absolute;
      top: 10px;
      left: 10px;
      background: rgba(255, 255, 255, 0.05);
      border: none;
      color: #eee;
      font-size: 20px;
      cursor: pointer;
      z-index: 10;
      opacity: 0.3;
      transition: opacity 0.2s, background 0.2s, transform 0.2s;
      border-radius: 50%;
      width: 44px;
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .download-btn:hover {
      opacity: 1;
      background: rgba(255, 255, 255, 0.15);
      transform: scale(1.1);
    }
    .refresh-btn {
      position: absolute;
      top: 10px;
      left: 64px;
      background: rgba(255, 255, 255, 0.05);
      border: none;
      color: #eee;
      font-size: 20px;
      cursor: pointer;
      z-index: 10;
      opacity: 0.3;
      transition: opacity 0.2s, background 0.2s, transform 0.2s;
      border-radius: 50%;
      width: 44px;
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .refresh-btn:hover {
      opacity: 1;
      background: rgba(255, 255, 255, 0.15);
      transform: scale(1.1);
    }
    .refresh-btn.spinning {
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    ::-webkit-scrollbar {
      width: 8px;
    }
    ::-webkit-scrollbar-thumb {
      background: #333;
      border-radius: 4px;
    }
    ::-webkit-scrollbar-track {
      background: #1a1a1a;
    }
    .image-wrapper {
      position: relative;
      display: flex;
      justify-content: center;
      align-items: center;
      width: 100%;
      height: 100%;
    }
    .fullscreen-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.8);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
    }
    .fullscreen-overlay img {
      max-width: 90%;
      max-height: 90%;
      cursor: zoom-out;
    }
  </style>
</head>
<body>
  <div class="main-container">
    <div class="image-container" id="image-container">
      <div class="image-wrapper">
        <button class="download-btn" id="download-btn" title="Скачать оригинал">⬇</button>
        <button class="refresh-btn" id="refresh-btn" title="Обновить галерею">🔄</button>
        <span class="arrow" id="left-arrow">&#8592;</span>
        <img id="main-image" src="${first_image}" alt="Generated image">
        <span class="arrow" id="right-arrow">&#8594;</span>
      </div>
    </div>
    <div class="info-panel">
      <div class="text-block prompt">
        <button class="copy-btn" onclick="copyText('prompt-text')">📋</button>
        <textarea id="prompt-text" readonly>${first_prompt}</textarea>
      </div>
      <div class="text-block negative">
        <button class="copy-btn" onclick="copyText('negative-text')">📋</button>
        <textarea id="negative-text" readonly>${first_negative}</textarea>
      </div>
      <div class="text-block params">
        <button class="copy-btn" onclick="copyText('params-text')">📋</button>
        <textarea id="params-text" readonly>${first_params}</textarea>
      </div>
      <button class="copy-all-btn" onclick="copyAll()">📋 Copy all</button>
    </div>
  </div>

  <div class="thumbnail-strip" id="thumbnail-strip">
    ${thumbnail_html}
  </div>

  <div class="fullscreen-overlay" id="fullscreen-overlay">
    <img id="fullscreen-image" src="${first_image}" alt="Fullscreen image">
  </div>

    <script>
      const images = ${image_data_json};
      let filteredImages = images.slice();
      let currentIndex = 0;

      const img = document.getElementById('main-image');
      const container = document.getElementById('image-container');
      const leftArrow = document.getElementById('left-arrow');
      const rightArrow = document.getElementById('right-arrow');
      const fullscreenOverlay = document.getElementById('fullscreen-overlay');
      const fullscreenImage = document.getElementById('fullscreen-image');
      const downloadBtn = document.getElementById('download-btn');
      const refreshBtn = document.getElementById('refresh-btn');

      function downloadCurrentImage() {
        if (!filteredImages.length) return;
        const data = filteredImages[currentIndex];
        const url = data.original_src || data.src;
        const filename = url.split('/').pop();
        fetch(url)
          .then(response => response.blob())
          .then(blob => {
            const blobUrl = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = blobUrl;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(blobUrl);
            document.body.removeChild(a);
            flashButton(downloadBtn, true);
          })
          .catch(() => {
            flashButton(downloadBtn, false);
          });
      }

      downloadBtn.addEventListener('click', downloadCurrentImage);

      function refreshGallery() {
        if (refreshBtn.classList.contains('spinning')) return;
        refreshBtn.classList.add('spinning');
        fetch('/api/refresh')
          .then(response => response.json())
          .then(data => {
            filteredImages = data.images || [];
            if (filteredImages.length > 0) {
              if (currentIndex >= filteredImages.length) {
                currentIndex = 0;
              }
              renderThumbnails();
              updateContent();
            }
            flashButton(refreshBtn, true);
          })
          .catch(() => {
            flashButton(refreshBtn, false);
          })
          .finally(() => {
            refreshBtn.classList.remove('spinning');
          });
      }

      refreshBtn.addEventListener('click', refreshGallery);

      function sanitizeHash(str) {
        return str.replace(/[^a-zA-Z0-9\\-_]/g, '_');
      }

      function renderThumbnails() {
        const strip = document.getElementById('thumbnail-strip');
        strip.innerHTML = '';
        filteredImages.forEach((item, index) => {
          const thumb = document.createElement('img');
          thumb.src = item.thumb_src || item.src;
          thumb.onclick = () => {
            currentIndex = index;
            updateContent();
            location.hash = sanitizeHash(filteredImages[currentIndex].src);
          };
          strip.appendChild(thumb);
        });
      }

      function updateContent() {
        if (!filteredImages.length) return;
        const data = filteredImages[currentIndex];
        img.src = data.src;
        fullscreenImage.src = data.src;
        img.style.transform = 'scale(1)';
        img.style.cursor = 'zoom-in';
        document.getElementById('prompt-text').value = data.prompt || '';
        document.getElementById('negative-text').value = data.negative || '';
        document.getElementById('params-text').value = data.params || '';

        location.hash = sanitizeHash(data.src);

        const thumbnails = document.querySelectorAll('#thumbnail-strip img');
        thumbnails.forEach((thumb, i) => {
          thumb.style.borderColor = i === currentIndex ? '#3ea6ff' : 'transparent';
          if (i === currentIndex) {
            thumb.scrollIntoView({
              behavior: 'auto',
              inline: 'center',
              block: 'nearest'
            });
          }
        });
      }

      leftArrow.onclick = () => {
        currentIndex = (currentIndex - 1 + filteredImages.length) % filteredImages.length;
        updateContent();
      };

      rightArrow.onclick = () => {
        currentIndex = (currentIndex + 1) % filteredImages.length;
        updateContent();
      };

      container.addEventListener('mouseenter', () => {
        leftArrow.classList.add('show');
        rightArrow.classList.add('show');
      });

      container.addEventListener('mouseleave', () => {
        leftArrow.classList.remove('show');
        rightArrow.classList.remove('show');
      });

      document.addEventListener('wheel', function(e) {
        const thumbnailStrip = document.getElementById('thumbnail-strip');
        const isOverThumbnailStrip = thumbnailStrip.contains(e.target);
        const isOverTextArea = e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT';

        if (isOverTextArea) {
          e.preventDefault();
          e.target.scrollTop += e.deltaY;
        } else if (isOverThumbnailStrip) {
          e.preventDefault();
          thumbnailStrip.scrollLeft += e.deltaY;
        } else {
          e.preventDefault();
          currentIndex = e.deltaY > 0
            ? (currentIndex + 1) % filteredImages.length
            : (currentIndex - 1 + filteredImages.length) % filteredImages.length;
          updateContent();
        }
      }, { passive: false });

      img.addEventListener('click', () => {
        fullscreenOverlay.style.display = 'flex';
        fullscreenImage.src = img.src;
      });

      fullscreenOverlay.addEventListener('click', () => {
        fullscreenOverlay.style.display = 'none';
      });

      function flashButton(btn, success) {
        const original = btn.textContent;
        btn.textContent = success ? '✓' : '✗';
        btn.style.color = success ? '#4caf50' : '#f44336';
        btn.style.opacity = '1';
        setTimeout(() => {
          btn.textContent = original;
          btn.style.color = '';
          btn.style.opacity = '';
        }, 1200);
      }

      function copyText(id) {
        const el = document.getElementById(id);
        const btn = el.previousElementSibling;
        if (!navigator.clipboard) {
          el.select();
          document.execCommand('copy');
          flashButton(btn, true);
          return;
        }
        navigator.clipboard.writeText(el.value).then(() => {
          flashButton(btn, true);
        }).catch(() => {
          flashButton(btn, false);
        });
      }

      function copyAll() {
        const prompt = document.getElementById('prompt-text').value;
        const negative = document.getElementById('negative-text').value;
        const params = document.getElementById('params-text').value;
        const parts = [];
        if (prompt) parts.push(prompt);
        if (negative) parts.push('Negative prompt: ' + negative);
        if (params) parts.push(params);
        const combined = parts.join('\\n');
        const btn = document.querySelector('.copy-all-btn');
        if (!navigator.clipboard) {
          const ta = document.createElement('textarea');
          ta.value = combined;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          flashButton(btn, true);
          return;
        }
        navigator.clipboard.writeText(combined).then(() => {
          flashButton(btn, true);
        }).catch(() => {
          flashButton(btn, false);
        });
      }

      window.addEventListener('load', () => {
        if (location.hash) {
          const targetHash = location.hash.substring(1);
          const index = filteredImages.findIndex(img => sanitizeHash(img.src) === targetHash);
          if (index !== -1) {
            currentIndex = index;
          }
        }
      });

      renderThumbnails();
      updateContent();
    </script>
</body>
</html>
""")


def generate_gallery_html() -> str:
    """
    Сгенерировать интерактивную HTML-галерею.

    Returns:
        str: HTML-код страницы галереи
    """
    start_time = time.time()
    image_data = _build_image_data_list()

    if not image_data:
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Галерея</title></head>"
            "<body style='background:#0e0e0e;color:#eee;font-family:sans-serif;text-align:center;padding:50px;'>"
            "<h1>Нет изображений</h1><p>Сгенерируйте изображения, чтобы они появились здесь.</p></body></html>"
        )

    # Генерация миниатюр
    thumbnail_html = "".join(
        f'<img src="{item["src"]}" alt="Preview {i}">'
        for i, item in enumerate(image_data)
    )

    first_image = image_data[0]
    image_data_json = json.dumps(image_data, indent=4, ensure_ascii=False).replace("</script>", "<\\/script>")

    def escape_for_textarea(text):
        """Экранирование данных для безопасной вставки в <textarea>."""
        if not text:
            return ''
        return html.escape(text, quote=False)

    html_content = gallery_html_template.substitute(
        first_image=first_image['src'],
        first_prompt=escape_for_textarea(first_image.get('prompt') or ''),
        first_negative=escape_for_textarea(first_image.get('negative') or ''),
        first_params=escape_for_textarea(first_image.get('params') or ''),
        thumbnail_html=thumbnail_html,
        image_data_json=image_data_json,
    )

    logger.info("Gallery rendered in %.2f seconds with %d images", time.time() - start_time, len(image_data))
    return html_content


def _build_image_data_list() -> list:
    """
    Построить список данных изображений для галереи.

    Returns:
        list: Список словарей с данными изображений
    """
    image_data = []
    supported = {'.png', '.jpg', '.jpeg', '.webp'}
    all_files = sorted(
        (f for f in IMAGE_DIR.rglob("*")
         if f.is_file()
         and f.suffix.lower() in supported
         and not str(f).startswith(str(THUMB_DIR))
         and not str(f).startswith(str(WEBP_DIR))),
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )
    for f in all_files:
        meta = extract_image_metadata(f)
        if meta is None:
            meta = {"prompt": "", "negative": "", "params": ""}
        meta["original_src"] = f"/images/{f.name}"
        webp_name = ensure_webp(f.name)
        if webp_name:
            meta["src"] = f"/webp/{webp_name}"
        else:
            meta["src"] = meta["original_src"]
        thumb_name = f.stem + ".jpg"
        thumb_path = THUMB_DIR / thumb_name
        if thumb_path.exists():
            meta["thumb_src"] = f"/thumbs/{thumb_name}"
        else:
            meta["thumb_src"] = meta["src"]
        image_data.append(meta)
    return image_data
