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
    """将 PDF 各页转为临时 PNG，返回 PNG 路径列表。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("错误: PDF 识别需要安装 PyMuPDF，请执行: pip install PyMuPDF")
        sys.exit(1)

    png_paths = []
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=150)
            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            pix.save(tmp.name)
            tmp.close()
            png_paths.append(tmp.name)
        doc.close()
    except Exception as e:
        # 清理已生成的临时文件
        for p in png_paths:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise RuntimeError(f"PDF 转换失败 ({pdf_path}): {e}")
    return png_paths


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
                png_paths = _convert_pdf_to_pngs(arg)
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


def _call_vision_api(images, prompt):
    """调用 SiliconFlow 视觉 API。返回 (text, usage_dict)。"""
    content = [{"type": "text", "text": prompt}]
    for img_path in images:
        content.append({"type": "image_url", "image_url": {"url": _encode_image(img_path)}})

    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 2048,
        "temperature": 0.3,
    }

    api_key = os.environ.get('SILICONFLOW_API_KEY', '')
    if not api_key:
        raise RuntimeError("环境变量 SILICONFLOW_API_KEY 未设置")

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
