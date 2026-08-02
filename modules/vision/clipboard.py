#!/usr/bin/env python3
"""从剪贴板读取图片并调用视觉模型识别。"""
import sys
import os
import tempfile
import subprocess
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

try:
    from PIL import ImageGrab
except ImportError:
    print("[错误: 需要安装 Pillow 库，请执行: pip install Pillow]")
    sys.exit(1)

# 使用 sys.executable 而非硬编码 python3
PYTHON = sys.executable

prompt = "请详细描述这张图片的内容"
if len(sys.argv) > 1:
    prompt = " ".join(sys.argv[1:])

img = ImageGrab.grabclipboard()

if img is None:
    print("[剪贴板中没有图片，请先截图]")
    sys.exit(1)

# 存为临时文件
with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
    img.save(f, format='PNG')
    tmp_path = f.name

try:
    vision_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recognize.py')
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    # 用列表传参避免 shell 注入
    result = subprocess.run(
        [PYTHON, vision_py, tmp_path, prompt],
        capture_output=True, timeout=120,
        env=env
    )
    stdout = result.stdout.decode('utf-8') if result.stdout else ''
    stderr = result.stderr.decode('utf-8') if result.stderr else ''
    if result.returncode != 0:
        print(f"recognize.py 错误: {stderr}")
        sys.exit(1)
    print(stdout, end='')
finally:
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
