#!/usr/bin/env python3
"""Read a clipboard image and pass it to the Vision CLI on explicit invocation."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--help"] or args == ["-h"]:
        print("Usage: python -m modules.vision.clipboard [prompt]")
        print("Recognize the current clipboard image with the Vision CLI.")
        return 0

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from modules.bootstrap.dependencies import activate_vendor_path

    activate_vendor_path()
    try:
        from PIL import ImageGrab
    except ImportError:
        print("[错误: 需要安装 Pillow 库，请执行: pip install Pillow]")
        return 1

    try:
        image = ImageGrab.grabclipboard()
    except OSError as exc:
        print(f"[无法读取剪贴板: {type(exc).__name__}]", file=sys.stderr)
        return 1
    if image is None:
        print("[剪贴板中没有图片，请先截图]")
        return 1

    prompt = " ".join(args) if args else "请详细描述这张图片的内容"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporary:
        image_path = Path(temporary.name)

    try:
        image.save(image_path, format="PNG")
        recognizer = Path(__file__).resolve().with_name("recognize.py")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, str(recognizer), str(image_path), prompt],
            capture_output=True,
            timeout=120,
            env=env,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.returncode != 0:
            print(f"recognize.py 错误: {stderr}")
            return 1
        print(stdout, end="")
        return 0
    finally:
        image_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
