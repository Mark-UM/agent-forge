import json

import pytest

from modules.bootstrap.secrets_env import load_startup_secrets


def test_load_startup_secrets_prefers_deepseek_and_supports_legacy_fallback(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text(
        json.dumps({"DEEPSEEK_API_KEY": "new", "ANTHROPIC_AUTH_TOKEN": "old"}),
        encoding="utf-8",
    )
    assert load_startup_secrets(path)["DEEPSEEK_API_KEY"] == "new"

    path.write_text(json.dumps({"ANTHROPIC_AUTH_TOKEN": "old"}), encoding="utf-8")
    assert load_startup_secrets(path)["DEEPSEEK_API_KEY"] == "old"


def test_load_startup_secrets_rejects_non_string_or_multiline_values(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"SERPER_API_KEY": 42}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a string"):
        load_startup_secrets(path)

    path.write_text(json.dumps({"SERPER_API_KEY": "first\nsecond"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        load_startup_secrets(path)
