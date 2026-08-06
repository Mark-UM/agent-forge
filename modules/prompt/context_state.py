"""Versioned persistence and freshness checks for detected prompt contexts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 2
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = _PROJECT_ROOT / "_runtime" / "prompt" / "context-signals.json"
_FINGERPRINT_FILES = (
    "package.json",
    "requirements.txt",
    "requirements.lock.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    ".tower-stack",
)


def project_fingerprint(project_root: Path = _PROJECT_ROOT) -> str:
    digest = hashlib.sha256()
    root = project_root.resolve()
    digest.update(str(root).encode("utf-8"))
    for relative in _FINGERPRINT_FILES:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        if not path.is_file():
            digest.update(b"<missing>")
            continue
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    contexts_dir = root / ".opencode" / "prompts" / "contexts"
    if contexts_dir.is_dir():
        for path in sorted(contexts_dir.glob("*.md")):
            digest.update(path.name.encode("utf-8"))
            try:
                stat = path.stat()
                digest.update(str(stat.st_size).encode("ascii"))
                digest.update(str(stat.st_mtime_ns).encode("ascii"))
            except OSError:
                digest.update(b"<unreadable>")
    return digest.hexdigest()


def _dedupe(items) -> list[str]:  # noqa: ANN001
    output: list[str] = []
    seen: set[str] = set()
    for raw in items or []:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        output.append(value)
        seen.add(value)
    return output


def suggested_contexts(signals: dict) -> list[str]:
    from modules.prompt.context import suggest_contexts

    project_contexts = signals.get("project_contexts", [])
    general_contexts = suggest_contexts(signals)
    # Specific project contexts precede general/language contexts.
    return _dedupe([*project_contexts, *general_contexts])


def write_context_state(
    signals: dict,
    *,
    path: Path = DEFAULT_STATE_PATH,
    project_root: Path = _PROJECT_ROOT,
) -> dict:
    contexts = suggested_contexts(signals)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "project_root": str(project_root.resolve()),
        "project_fingerprint": project_fingerprint(project_root),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "signals": signals,
        "contexts": contexts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return payload


def detect_context_state(
    *,
    message: str = "",
    file_paths: Optional[list[str]] = None,
    turn_count: int = 0,
    path: Path = DEFAULT_STATE_PATH,
    project_root: Path = _PROJECT_ROOT,
) -> dict:
    from modules.prompt.context import detect_full_context_signals

    signals = detect_full_context_signals(
        message,
        file_paths=file_paths,
        turn_count=turn_count,
        project_root=project_root,
    )
    # detect_full_context_signals writes the legacy v1 shape; replace it
    # immediately with the authoritative v2 payload.
    return write_context_state(signals, path=path, project_root=project_root)


def load_context_state(
    *,
    path: Path = DEFAULT_STATE_PATH,
    project_root: Path = _PROJECT_ROOT,
    refresh: bool = False,
) -> dict:
    expected_root = str(project_root.resolve())
    expected_fingerprint = project_fingerprint(project_root)
    if not refresh and path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if (
            isinstance(payload, dict)
            and payload.get("schema_version") == SCHEMA_VERSION
            and payload.get("project_root") == expected_root
            and payload.get("project_fingerprint") == expected_fingerprint
            and isinstance(payload.get("signals"), dict)
            and isinstance(payload.get("contexts"), list)
        ):
            return payload
    return detect_context_state(path=path, project_root=project_root)


__all__ = [
    "DEFAULT_STATE_PATH",
    "SCHEMA_VERSION",
    "detect_context_state",
    "load_context_state",
    "project_fingerprint",
    "suggested_contexts",
    "write_context_state",
]
