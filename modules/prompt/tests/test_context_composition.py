from __future__ import annotations

from pathlib import Path

from modules.prompt import composer
from modules.prompt.context_state import (
    load_context_state,
    project_fingerprint,
    write_context_state,
)


def _signals(**overrides) -> dict:
    data = {
        "languages": [],
        "directories": [],
        "session_length": "short",
        "task_hints": [],
        "has_test_files": False,
        "has_spec_docs": False,
        "has_ui_files": False,
        "project_contexts": [],
        "python_packages": [],
        "has_tower_stack": False,
        "has_threejs": False,
    }
    data.update(overrides)
    return data


def _prompt_tree(root: Path) -> Path:
    prompts = root / ".opencode" / "prompts"
    (prompts / "contexts").mkdir(parents=True)
    (prompts / "profiles").mkdir(parents=True)
    (prompts / "base.md").write_text("BASE BODY", encoding="utf-8")
    (prompts / "profiles" / "default.md").write_text(
        "PROFILE BODY", encoding="utf-8"
    )
    return prompts


def test_context_state_has_project_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    payload = write_context_state(
        _signals(), path=path, project_root=tmp_path
    )
    assert payload["schema_version"] == 2
    assert payload["project_fingerprint"] == project_fingerprint(tmp_path)
    assert load_context_state(path=path, project_root=tmp_path) == payload


def test_resolve_contexts_explicit_first_and_deduplicated() -> None:
    assert composer.resolve_contexts(
        ["python", "secrets"],
        ["threejs-game-loop", "python"],
    ) == ["python", "secrets", "threejs-game-loop"]


def test_load_detected_contexts_filters_missing_prompt_files(
    tmp_path: Path,
) -> None:
    prompts = _prompt_tree(tmp_path)
    (prompts / "contexts" / "python.md").write_text("PYTHON", encoding="utf-8")
    state_path = tmp_path / "context-signals.json"
    write_context_state(
        _signals(languages=["python"], project_contexts=["missing-project"]),
        path=state_path,
        project_root=tmp_path,
    )

    contexts, metadata = composer.load_detected_contexts(
        signals_path=state_path,
        project_root=tmp_path,
        prompts_dir=prompts,
    )

    assert "python" in contexts
    assert "missing-project" not in contexts
    assert "missing-project" in metadata["ignored_contexts"]


def test_compose_loads_fresh_detected_contexts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompts = _prompt_tree(tmp_path)
    for name in ("python", "threejs-game-loop", "flash-role", "anti-patterns", "naming-contract"):
        (prompts / "contexts" / f"{name}.md").write_text(
            f"CONTEXT {name}", encoding="utf-8"
        )
    state_path = tmp_path / "context-signals.json"
    write_context_state(
        _signals(
            languages=["python"],
            project_contexts=["threejs-game-loop"],
            has_threejs=True,
        ),
        path=state_path,
        project_root=tmp_path,
    )

    monkeypatch.setattr(composer, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(composer, "PROMPTS_DIR", prompts)
    result = composer.compose(
        contexts=["python"],
        use_detected_contexts=True,
        signals_path=state_path,
    )

    assert result["metadata"]["contexts"][0] == "python"
    assert result["metadata"]["contexts"].count("python") == 1
    assert "threejs-game-loop" in result["metadata"]["contexts"]
    assert "CONTEXT threejs-game-loop" in result["prompt"]
