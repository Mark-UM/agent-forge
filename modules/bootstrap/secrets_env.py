"""Emit the small, documented secret set consumed by start-opencode.bat."""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRETS_FILE = PROJECT_ROOT / "markconfig" / "secrets.json"
OUTPUT_KEYS = (
    "DEEPSEEK_API_KEY",
    "SILICONFLOW_API_KEY",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "SERPER_API_KEY",
)


def load_startup_secrets(path: Path = SECRETS_FILE) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("secrets.json must contain a JSON object")
    deepseek = data.get("DEEPSEEK_API_KEY") or data.get("ANTHROPIC_AUTH_TOKEN", "")
    values = {key: data.get(key, "") for key in OUTPUT_KEYS}
    values["DEEPSEEK_API_KEY"] = deepseek
    for key, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        if any(character in value for character in "\r\n\""):
            raise ValueError(f"{key} contains characters unsafe for batch export")
    return values


def main() -> int:
    for key, value in load_startup_secrets().items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
