#!/usr/bin/env python3
"""SiliconFlow Qwen3-VL-Plus image/PDF recognition.

PDF pages are rendered under explicit resource limits. API requests are sent in
bounded batches; multi-batch output is reduced into one page-aware document
with provenance instead of being joined as unrelated text fragments.
"""
from __future__ import annotations

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
from modules.vision.reducer import VisionBatchResult, reduce_batches

activate_vendor_path()

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

VISION_API = "https://api.siliconflow.cn/v1/chat/completions"
VISION_MODEL = "Qwen/Qwen3-VL-Plus"
DEFAULT_PROMPT = "请详细描述这些图片的内容"
SUPPORTED_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.pdf'}
_MIME_MAP = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png',
             'webp': 'webp', 'bmp': 'bmp', 'gif': 'gif'}

MAX_PDF_FILE_BYTES = int(os.environ.get('VISION_MAX_PDF_FILE_BYTES', 50 * 1024 * 1024))
MAX_PDF_PAGES = int(os.environ.get('VISION_MAX_PDF_PAGES', 20))
MAX_PAGE_PIXELS = int(os.environ.get('VISION_MAX_PAGE_PIXELS', 10 * 1024 * 1024))
MAX_TOTAL_PIXELS = int(os.environ.get('VISION_MAX_TOTAL_PIXELS', 50 * 1024 * 1024))
MAX_REQUEST_BYTES = int(os.environ.get('VISION_MAX_REQUEST_BYTES', 20 * 1024 * 1024))
PDF_BATCH_SIZE = int(os.environ.get('VISION_PDF_BATCH_SIZE', 5))
RENDER_DPI = int(os.environ.get('VISION_RENDER_DPI', 150))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VISION_LOG = os.path.join(_PROJECT_ROOT, '_runtime', 'vision_usage.jsonl')


def _encode_image(path):
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    mime = _MIME_MAP.get(ext, 'png')
    with open(path, 'rb') as handle:
        return f"data:image/{mime};base64,{base64.b64encode(handle.read()).decode()}"


def _convert_pdf_to_pngs(pdf_path):
    """Render bounded PDF pages and return ``(paths, structured_info)``."""
    file_size = os.path.getsize(pdf_path)
    if file_size > MAX_PDF_FILE_BYTES:
        raise RuntimeError(
            f"PDF file too large: {file_size} bytes "
            f"(limit: {MAX_PDF_FILE_BYTES} bytes). "
            f"Set VISION_MAX_PDF_FILE_BYTES to increase the limit."
        )
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PDF recognition requires PyMuPDF. Install with: pip install PyMuPDF"
        ) from exc

    png_paths = []
    rendered_page_numbers = []
    total_pixels = 0
    pages_processed = 0
    pages_skipped = 0
    warnings_list = []
    doc = None
    total_pages = 0
    truncated = False
    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        pages_to_render = min(total_pages, MAX_PDF_PAGES)
        for page_num in range(pages_to_render):
            page = doc[page_num]
            rect = page.rect
            expected_w = int(rect.width * RENDER_DPI / 72)
            expected_h = int(rect.height * RENDER_DPI / 72)
            expected_pixels = expected_w * expected_h
            if expected_pixels > MAX_PAGE_PIXELS:
                pages_skipped += 1
                warnings_list.append(
                    f"Page {page_num + 1} skipped: {expected_pixels} pixels "
                    f"exceeds per-page limit {MAX_PAGE_PIXELS}"
                )
                continue
            if total_pixels + expected_pixels > MAX_TOTAL_PIXELS:
                pages_skipped += 1
                warnings_list.append(
                    f"Page {page_num + 1} skipped: total pixel limit "
                    f"{MAX_TOTAL_PIXELS} would be exceeded"
                )
                continue

            pix = page.get_pixmap(dpi=RENDER_DPI)
            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            pix.save(tmp.name)
            tmp.close()
            png_paths.append(tmp.name)
            rendered_page_numbers.append(page_num + 1)
            total_pixels += expected_pixels
            pages_processed += 1

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
    except Exception as exc:
        for path in png_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise RuntimeError(f"PDF 转换失败 ({pdf_path}): {exc}") from exc
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    return png_paths, {
        'processed_pages': pages_processed,
        'skipped_pages': pages_skipped,
        'total_pages': total_pages,
        'truncated': truncated,
        'warnings': warnings_list,
        'rendered_page_numbers': rendered_page_numbers,
    }


def _parse_args(argv):
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
                png_paths, _pdf_info = _convert_pdf_to_pngs(arg)
                images.extend(png_paths)
                tmp_files.extend(png_paths)
            else:
                images.append(arg)
        elif not prompt_found:
            prompt = arg
            prompt_found = True
        else:
            prompt = prompt + " " + arg
    return images, prompt, tmp_files


def _estimate_request_bytes(encoded_image_sizes, prompt):
    json_fixed_overhead = 512
    per_image_wrapper = 64
    prompt_overhead = len(prompt.encode('utf-8')) + 64
    return (
        json_fixed_overhead
        + prompt_overhead
        + per_image_wrapper * len(encoded_image_sizes)
        + sum(encoded_image_sizes)
    )


def _reduce_api_batches(batch_results):
    """Reduce two or more API batches, preserving the one-batch wire format."""
    if not batch_results:
        return ""
    if len(batch_results) == 1:
        return batch_results[0].text
    try:
        expected_pages = max(batch.page_end for batch in batch_results)
        return reduce_batches(
            batch_results,
            expected_page_count=expected_pages,
        ).text
    except Exception as exc:
        import warnings
        warnings.warn(
            f"Vision cross-batch reduction failed; using compatibility join: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return "\n\n---\n\n".join(batch.text for batch in batch_results)


def _call_vision_api(images, prompt):
    """Call the API in bounded batches and reduce multi-batch output."""
    api_key = os.environ.get('SILICONFLOW_API_KEY', '')
    if not api_key:
        raise RuntimeError("环境变量 SILICONFLOW_API_KEY 未设置")

    batch_results = []
    total_usage = {'prompt_tokens': 0, 'completion_tokens': 0}
    batches_processed = 0
    skipped_images = []
    current_batch_paths = []
    current_batch_sizes = []
    next_page = 1

    def send_current(paths, batch_prompt):
        nonlocal batches_processed, next_page
        text, usage = _call_vision_api_batch(paths, batch_prompt, api_key)
        page_start = next_page
        page_end = next_page + len(paths) - 1
        batch_results.append(
            VisionBatchResult(
                page_start=page_start,
                page_end=page_end,
                text=text,
            )
        )
        next_page = page_end + 1
        total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
        total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
        batches_processed += 1

    for img_path in images:
        encoded = _encode_image(img_path)
        encoded_size = len(encoded)
        if _estimate_request_bytes([encoded_size], prompt) > MAX_REQUEST_BYTES:
            skipped_images.append({
                'path': img_path,
                'reason': 'single_image_exceeds_max_request_bytes',
                'encoded_size': encoded_size,
                'limit': MAX_REQUEST_BYTES,
            })
            del encoded
            continue

        tentative_sizes = current_batch_sizes + [encoded_size]
        if (_estimate_request_bytes(tentative_sizes, prompt) > MAX_REQUEST_BYTES
                and current_batch_paths):
            batch_prompt = (
                f"{prompt} (Batch {batches_processed + 1})"
                if batches_processed > 0 or len(images) > PDF_BATCH_SIZE
                else prompt
            )
            send_current(current_batch_paths, batch_prompt)
            current_batch_paths = []
            current_batch_sizes = []

        current_batch_paths.append(img_path)
        current_batch_sizes.append(encoded_size)
        del encoded

        if len(current_batch_paths) >= PDF_BATCH_SIZE:
            batch_prompt = (
                f"{prompt} (Batch {batches_processed + 1})"
                if batches_processed > 0 or len(images) > PDF_BATCH_SIZE
                else prompt
            )
            send_current(current_batch_paths, batch_prompt)
            current_batch_paths = []
            current_batch_sizes = []

    if current_batch_paths:
        batch_prompt = (
            f"{prompt} (Batch {batches_processed + 1})"
            if batches_processed > 0 else prompt
        )
        send_current(current_batch_paths, batch_prompt)

    if skipped_images:
        import warnings
        for skipped in skipped_images:
            warnings.warn(
                f"Image {skipped['path']} skipped: {skipped['reason']} "
                f"(size={skipped['encoded_size']}, limit={skipped['limit']})",
                UserWarning,
                stacklevel=2,
            )

    return _reduce_api_batches(batch_results), total_usage


def _call_vision_api_batch(images, prompt, api_key):
    content = [{"type": "text", "text": prompt}]
    for img_path in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": _encode_image(img_path)},
        })
    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 2048,
        "temperature": 0.3,
    }
    request = urllib.request.Request(
        VISION_API,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"API 错误 {exc.code}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    return data['choices'][0]['message']['content'], data.get('usage', {})


def _log_usage(usage):
    log_entry = {
        'model': VISION_MODEL,
        'input_tokens': usage.get('prompt_tokens', 0),
        'output_tokens': usage.get('completion_tokens', 0),
        'timestamp': __import__('datetime').datetime.now().isoformat(),
    }
    try:
        os.makedirs(os.path.dirname(VISION_LOG), exist_ok=True)
        with open(VISION_LOG, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    except OSError:
        pass


def main():
    if not os.environ.get('SILICONFLOW_API_KEY', ''):
        print("错误: 请设置环境变量 SILICONFLOW_API_KEY")
        sys.exit(1)
    if len(sys.argv) < 2:
        print("用法: python recognize.py <图片路径1> [图片路径2] ... [提示词]")
        sys.exit(1)

    images, prompt, tmp_files = _parse_args(sys.argv[1:])
    if not images:
        print("错误: 未找到有效图片路径")
        sys.exit(1)
    try:
        text, usage = _call_vision_api(images, prompt)
        _log_usage(usage)
        print(text)
        if usage.get('prompt_tokens') or usage.get('completion_tokens'):
            print(f"\n--- vision: {usage.get('prompt_tokens', 0)} in / "
                  f"{usage.get('completion_tokens', 0)} out")
    except Exception as exc:
        print(f"错误: {exc}")
        sys.exit(1)
    finally:
        for tmp_file in tmp_files:
            try:
                os.unlink(tmp_file)
            except OSError:
                pass


if __name__ == "__main__":
    main()
