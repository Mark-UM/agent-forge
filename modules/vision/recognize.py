#!/usr/bin/env python3
"""SiliconFlow Qwen3-VL-Plus 视觉识别 — 支持多图/PDF。

Usage:
  python recognize.py <img1> [img2] ... [prompt]
  python recognize.py <single_image>
"""
import sys
import os
import json
import base64
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from modules.bootstrap.dependencies import activate_vendor_path

activate_vendor_path()

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 常量 ────────────────────────────────────────────────────────
VISION_API = "https://api.siliconflow.cn/v1/chat/completions"
VISION_MODEL = "Qwen/Qwen3-VL-Plus"
DEFAULT_PROMPT = "请详细描述这些图片的内容"
SUPPORTED_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.pdf'}
_MIME_MAP = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png',
             'webp': 'webp', 'bmp': 'bmp', 'gif': 'gif'}

# V1 fix: PDF resource limits (configurable via env)
MAX_PDF_FILE_BYTES = int(os.environ.get('VISION_MAX_PDF_FILE_BYTES', 50 * 1024 * 1024))  # 50 MB
MAX_PDF_PAGES = int(os.environ.get('VISION_MAX_PDF_PAGES', 20))  # Max pages per PDF
MAX_PAGE_PIXELS = int(os.environ.get('VISION_MAX_PAGE_PIXELS', 10 * 1024 * 1024))  # 10 MP per page
MAX_TOTAL_PIXELS = int(os.environ.get('VISION_MAX_TOTAL_PIXELS', 50 * 1024 * 1024))  # 50 MP total
MAX_REQUEST_BYTES = int(os.environ.get('VISION_MAX_REQUEST_BYTES', 20 * 1024 * 1024))  # 20 MB per API call
PDF_BATCH_SIZE = int(os.environ.get('VISION_PDF_BATCH_SIZE', 5))  # Pages per API call
RENDER_DPI = int(os.environ.get('VISION_RENDER_DPI', 150))  # PDF render DPI

# 日志路径：项目根/_runtime/vision_usage.jsonl
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VISION_LOG = os.path.join(_PROJECT_ROOT, '_runtime', 'vision_usage.jsonl')


def _encode_image(path):
    """读取图片并返回 data URL。"""
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    mime = _MIME_MAP.get(ext, 'png')
    with open(path, 'rb') as f:
        return f"data:image/{mime};base64,{base64.b64encode(f.read()).decode()}"


def _convert_pdf_to_pngs(pdf_path):
    """R2-6: 将 PDF 各页转为临时 PNG，带资源限制和分批处理。

    R2-6 fixes:
        - Uses try/finally to close the PDF document (no doc.close() leak).
        - Does NOT call sys.exit() — raises RuntimeError instead.
        - Returns structured info alongside PNG paths for the caller.

    Resource limits (V1 fix):
        - MAX_PDF_FILE_BYTES: Max PDF file size (default 50 MB)
        - MAX_PDF_PAGES: Max pages to render (default 20)
        - MAX_PAGE_PIXELS: Max pixels per page (default 10 MP)
        - MAX_TOTAL_PIXELS: Max total pixels across all pages (default 50 MP)
        - RENDER_DPI: DPI for rendering (default 150)

    Returns:
        tuple: (png_paths, info_dict)
            - png_paths: list of PNG file paths
            - info_dict: {processed_pages, skipped_pages, total_pages, truncated, warnings}

    Raises:
        RuntimeError: If PDF file exceeds MAX_PDF_FILE_BYTES, or if PyMuPDF unavailable
    """
    # V1: Check file size before opening
    file_size = os.path.getsize(pdf_path)
    if file_size > MAX_PDF_FILE_BYTES:
        raise RuntimeError(
            f"PDF file too large: {file_size} bytes "
            f"(limit: {MAX_PDF_FILE_BYTES} bytes). "
            f"Set VISION_MAX_PDF_FILE_BYTES to increase the limit."
        )

    # R2-6: Library functions must NOT call sys.exit(). Raise instead.
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError(
            "PDF recognition requires PyMuPDF. Install with: pip install PyMuPDF"
        )

    png_paths = []
    total_pixels = 0
    pages_processed = 0
    pages_skipped = 0
    warnings_list = []
    doc = None

    # R2-6: Use try/finally to ensure the PDF document is always closed.
    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)

        # V1: Limit number of pages
        pages_to_render = min(total_pages, MAX_PDF_PAGES)

        for page_num in range(pages_to_render):
            page = doc[page_num]

            # V1: Calculate expected pixel dimensions before rendering
            rect = page.rect
            expected_w = int(rect.width * RENDER_DPI / 72)
            expected_h = int(rect.height * RENDER_DPI / 72)
            expected_pixels = expected_w * expected_h

            # V1: Check per-page pixel limit
            if expected_pixels > MAX_PAGE_PIXELS:
                pages_skipped += 1
                warnings_list.append(
                    f"Page {page_num + 1} skipped: {expected_pixels} pixels "
                    f"exceeds per-page limit {MAX_PAGE_PIXELS}"
                )
                continue

            # V1: Check total pixel limit
            if total_pixels + expected_pixels > MAX_TOTAL_PIXELS:
                pages_skipped += 1
                warnings_list.append(
                    f"Page {page_num + 1} skipped: total pixel limit "
                    f"{MAX_TOTAL_PIXELS} would be exceeded"
                )
                continue

            # Render the page
            pix = page.get_pixmap(dpi=RENDER_DPI)
            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            pix.save(tmp.name)
            tmp.close()
            png_paths.append(tmp.name)
            total_pixels += expected_pixels
            pages_processed += 1

        # V1: Log limit info if pages were skipped
        truncated = pages_to_render < total_pages
        if pages_skipped > 0 or truncated:
            import warnings
            warnings.warn(
                f"PDF processing limited: {pages_processed} pages rendered, "
                f"{pages_skipped} pages skipped (pixel/page limits), "
                f"{total_pages - pages_to_render} pages truncated (page count limit). "
                f"Adjust VISION_MAX_PDF_PAGES, VISION_MAX_PAGE_PIXELS, "
                f"or VISION_MAX_TOTAL_PIXELS to process more.",
                UserWarning,
                stacklevel=2,
            )

    except Exception as e:
        # 清理已生成的临时文件
        for p in png_paths:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise RuntimeError(f"PDF 转换失败 ({pdf_path}): {e}")
    finally:
        # R2-6: Always close the PDF document, even on error.
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    info = {
        'processed_pages': pages_processed,
        'skipped_pages': pages_skipped,
        'total_pages': total_pages if 'total_pages' in dir() else 0,
        'truncated': truncated if 'truncated' in dir() else False,
        'warnings': warnings_list,
    }
    return png_paths, info


def _parse_args(argv):
    """分离图片路径和提示词。返回 (images, prompt, tmp_files)。"""
    images = []
    prompt = DEFAULT_PROMPT
    prompt_found = False
    tmp_files = []

    for arg in argv:
        if os.path.exists(arg):
            ext = os.path.splitext(arg)[1].lower()
            if ext not in SUPPORTED_EXTS:
                print(f"错误: 不支持的文件类型 {ext}（支持: {', '.join(SUPPORTED_EXTS)}）")
                sys.exit(1)

            if ext == '.pdf':
                # R2-6: _convert_pdf_to_pngs now returns (png_paths, info_dict)
                png_paths, _pdf_info = _convert_pdf_to_pngs(arg)
                images.extend(png_paths)
                tmp_files.extend(png_paths)
            else:
                images.append(arg)
        else:
            # 第一个非文件参数视为提示词
            if not prompt_found:
                prompt = arg
                prompt_found = True
            else:
                prompt = prompt + " " + arg

    return images, prompt, tmp_files


def _estimate_request_bytes(encoded_image_sizes, prompt):
    """R2-6: 计算完整请求字节数，包含 Base64 + Data URL Prefix + Prompt + JSON Wrapper + Metadata。

    Args:
        encoded_image_sizes: list of int — each image's data URL length (bytes)
        prompt: str — prompt text

    Returns:
        int: estimated total request payload size in bytes
    """
    # JSON wrapper overhead: {"model":...,"messages":[{"role":"user","content":[...]}],...}
    # Approximate fixed overhead + per-image wrapper
    json_fixed_overhead = 512  # model, max_tokens, temperature, structure
    per_image_wrapper = 64  # {"type":"image_url","image_url":{"url":""}}
    prompt_overhead = len(prompt.encode('utf-8')) + 64  # {"type":"text","text":""}
    images_overhead = sum(per_image_wrapper for _ in encoded_image_sizes)
    return json_fixed_overhead + prompt_overhead + images_overhead + sum(encoded_image_sizes)


def _call_vision_api(images, prompt):
    """R2-6: 调用 SiliconFlow 视觉 API，流式批处理。

    R2-6 修复点：
        - 不再一次性 Base64 编码全部图片；
        - 逐图编码并累积到当前 Batch；
        - 达到 MAX_REQUEST_BYTES 或 PDF_BATCH_SIZE 上限立即发送；
        - 发送后释放已编码图片内存；
        - 请求大小计算包含 Base64 + Data URL Prefix + Prompt + JSON Wrapper + Metadata。

    Args:
        images: List of image file paths.
        prompt: Text prompt for the API.

    Returns:
        tuple: (combined_text, total_usage_dict)

    Raises:
        RuntimeError: If SILICONFLOW_API_KEY is not set.
    """
    api_key = os.environ.get('SILICONFLOW_API_KEY', '')
    if not api_key:
        raise RuntimeError("环境变量 SILICONFLOW_API_KEY 未设置")

    results = []
    total_usage = {'prompt_tokens': 0, 'completion_tokens': 0}
    batches_processed = 0
    skipped_images = []

    # R2-6: 流式批处理 — 逐图编码，不预编码全部
    current_batch_paths = []
    current_batch_sizes = []  # 已编码图片的 data URL 字节大小

    for img_path in images:
        # 逐图编码（仅当前图片）
        encoded = _encode_image(img_path)
        encoded_size = len(encoded)

        # 单图超限：结构化跳过
        if _estimate_request_bytes([encoded_size], prompt) > MAX_REQUEST_BYTES:
            skipped_images.append({
                'path': img_path,
                'reason': 'single_image_exceeds_max_request_bytes',
                'encoded_size': encoded_size,
                'limit': MAX_REQUEST_BYTES,
            })
            # 释放当前图片编码
            del encoded
            continue

        # 加入当前 batch 后是否超限
        tentative_sizes = current_batch_sizes + [encoded_size]
        tentative_bytes = _estimate_request_bytes(tentative_sizes, prompt)
        if tentative_bytes > MAX_REQUEST_BYTES and current_batch_paths:
            # 当前 batch 已满 — 发送
            batch_prompt = f"{prompt} (Batch {batches_processed + 1})" if batches_processed > 0 or len(images) > PDF_BATCH_SIZE else prompt
            text, usage = _call_vision_api_batch(current_batch_paths, batch_prompt, api_key)
            results.append(text)
            total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
            total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
            batches_processed += 1
            # 释放当前 batch
            current_batch_paths = []
            current_batch_sizes = []

        # 加入新 batch
        current_batch_paths.append(img_path)
        current_batch_sizes.append(encoded_size)
        # 释放编码内容（batch 调用时再重新编码，避免持有全部编码）
        del encoded

        # 达到 PDF_BATCH_SIZE 上限 — 发送
        if len(current_batch_paths) >= PDF_BATCH_SIZE:
            batch_prompt = f"{prompt} (Batch {batches_processed + 1})" if batches_processed > 0 or len(images) > PDF_BATCH_SIZE else prompt
            text, usage = _call_vision_api_batch(current_batch_paths, batch_prompt, api_key)
            results.append(text)
            total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
            total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
            batches_processed += 1
            current_batch_paths = []
            current_batch_sizes = []

    # 处理剩余图片
    if current_batch_paths:
        batch_prompt = f"{prompt} (Batch {batches_processed + 1})" if batches_processed > 0 else prompt
        text, usage = _call_vision_api_batch(current_batch_paths, batch_prompt, api_key)
        results.append(text)
        total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
        total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
        batches_processed += 1

    # 跳过的图片以 warnings 形式输出
    if skipped_images:
        import warnings
        for skip in skipped_images:
            warnings.warn(
                f"Image {skip['path']} skipped: {skip['reason']} "
                f"(size={skip['encoded_size']}, limit={skip['limit']})",
                UserWarning,
                stacklevel=2,
            )

    combined_text = "\n\n---\n\n".join(results) if len(results) > 1 else (results[0] if results else "")
    return combined_text, total_usage


def _call_vision_api_batch(images, prompt, api_key):
    """Call the vision API with a single batch of images.

    Args:
        images: List of image file paths
        prompt: Text prompt for the API
        api_key: SiliconFlow API key

    Returns:
        tuple: (text, usage_dict)
    """
    content = [{"type": "text", "text": prompt}]
    for img_path in images:
        content.append({"type": "image_url", "image_url": {"url": _encode_image(img_path)}})

    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 2048,
        "temperature": 0.3,
    }

    req = urllib.request.Request(
        VISION_API,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API 错误 {e.code}: {e.read().decode('utf-8', errors='replace')}")

    text = data['choices'][0]['message']['content']
    usage = data.get('usage', {})
    return text, usage


def _log_usage(usage):
    """记录 token 使用量到 JSONL 日志。"""
    log_entry = {
        'model': VISION_MODEL,
        'input_tokens': usage.get('prompt_tokens', 0),
        'output_tokens': usage.get('completion_tokens', 0),
        'timestamp': __import__('datetime').datetime.now().isoformat(),
    }
    try:
        os.makedirs(os.path.dirname(VISION_LOG), exist_ok=True)
        with open(VISION_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    except OSError:
        pass  # 日志失败不影响主流程


def main():
    # ── 前置检查 ────────────────────────────────────────────
    if not os.environ.get('SILICONFLOW_API_KEY', ''):
        print("错误: 请设置环境变量 SILICONFLOW_API_KEY")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("用法: python recognize.py <图片路径1> [图片路径2] ... [提示词]")
        sys.exit(1)

    # ── 解析参数 ────────────────────────────────────────────
    images, prompt, tmp_files = _parse_args(sys.argv[1:])

    if not images:
        print("错误: 未找到有效图片路径")
        sys.exit(1)

    # ── 调用 API ────────────────────────────────────────────
    try:
        text, usage = _call_vision_api(images, prompt)
        _log_usage(usage)
        print(text)
        if usage.get('prompt_tokens') or usage.get('completion_tokens'):
            print(f"\n--- vision: {usage.get('prompt_tokens', 0)} in / "
                  f"{usage.get('completion_tokens', 0)} out")
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)
    finally:
        # 清理 PDF 临时文件
        for tmp_file in tmp_files:
            try:
                os.unlink(tmp_file)
            except OSError:
                pass


if __name__ == "__main__":
    main()
