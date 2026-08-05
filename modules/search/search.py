#!/usr/bin/env python3
"""Compatibility CLI backed by the authoritative SQLite Search cache.

The historical single-file implementation is imported as a private compatibility
module so its log/history/health/CLI contracts remain stable. Cache functions
are replaced at module load: legacy JSON is read only for one-time migration;
all default-path cache writes, reads and pruning use
:class:`SearchCacheRepository`.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

try:
    from . import legacy_search as _legacy
except ImportError:
    import legacy_search as _legacy  # type: ignore

from modules.search.cache_store import DEFAULT_DB_PATH, SearchCacheRepository

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

LEGACY_CACHE_FILE = os.path.join(_legacy._LOG_DIR, "search_cache.json")
_DEFAULT_CACHE_FILE = str(DEFAULT_DB_PATH)
CACHE_FILE = _DEFAULT_CACHE_FILE
CACHE_TTL = int(_legacy.CACHE_TTL)
_CACHE_PREFIX = "history-cli:"
_CACHE_REPOSITORY: SearchCacheRepository | None = None
_CACHE_REPOSITORY_PATH: Path | None = None


def _repository() -> SearchCacheRepository:
    global _CACHE_REPOSITORY, _CACHE_REPOSITORY_PATH
    current_path = Path(CACHE_FILE)
    if _CACHE_REPOSITORY is None or _CACHE_REPOSITORY_PATH != current_path:
        _CACHE_REPOSITORY = SearchCacheRepository(current_path)
        _CACHE_REPOSITORY_PATH = current_path
        if current_path.resolve() == Path(DEFAULT_DB_PATH).resolve():
            _CACHE_REPOSITORY.migrate_json_file(
                Path(LEGACY_CACHE_FILE),
                ttl_seconds=CACHE_TTL,
                migration_name="legacy_search_history_cache",
            )
    return _CACHE_REPOSITORY


def _load_cache() -> dict[str, Any]:
    """Read the old JSON format for migration/debug compatibility only."""
    if not os.path.isfile(LEGACY_CACHE_FILE):
        return {}
    try:
        with open(LEGACY_CACHE_FILE, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _coerce_retrieved_at(entry: Mapping[str, Any]) -> str:
    raw = entry.get("retrieved_at", entry.get("cached_at"))
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), timezone.utc).isoformat()
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                timezone.utc
            ).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_compat_write(cache: Mapping[str, Any], path: Path) -> None:
    """Support historical tests/tools that explicitly inject a JSON path.

    The default production path is SQLite. This branch is reachable only after
    a caller deliberately replaces ``CACHE_FILE`` with a non-default ``.json``
    path, preserving the old atomic-write contract without recreating the
    retired production state source.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(path) + ".tmp")
    try:
        temporary.write_text(
            json.dumps(dict(cache), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _save_cache(cache: Mapping[str, Any]) -> None:
    if not isinstance(cache, Mapping):
        return
    current_path = Path(CACHE_FILE)
    if (
        current_path.suffix.lower() == ".json"
        and current_path.resolve() != Path(DEFAULT_DB_PATH).resolve()
    ):
        _atomic_json_compat_write(cache, current_path)
        return

    repository = _repository()
    for raw_key, raw_entry in cache.items():
        if not isinstance(raw_entry, Mapping):
            continue
        payload = dict(raw_entry)
        payload.pop("cached_at", None)
        repository.put_result(
            _CACHE_PREFIX + str(raw_key),
            payload,
            retrieved_at=_coerce_retrieved_at(raw_entry),
            ttl_seconds=CACHE_TTL,
        )


def _cache_record_key(query: str, location: str, layer_hint: str = "") -> str:
    return _CACHE_PREFIX + _legacy._cache_key(query, location, layer_hint)


def cache_get(args) -> int:
    record = _repository().get_result(
        _cache_record_key(
            args.query,
            args.location or "unknown",
            args.layer_hint or "",
        )
    )
    if record is None:
        return 0
    entry = record.payload
    print(json.dumps({
        "hit": True,
        "query": entry.get("query", ""),
        "layers_used": entry.get("layers_used", []),
        "results_count": entry.get("results_count", 0),
        "top_results": entry.get("top_results", [])[:10],
        "score": entry.get("score", 0),
        "cached_at": record.cached_at,
        "retrieved_at": record.retrieved_at,
    }, ensure_ascii=False, indent=2))
    return 0


def cache_clean(args) -> int:
    del args
    repository = _repository()
    deleted = repository.prune()
    remaining = repository.count(namespace="result")
    if deleted == 0 and remaining == 0:
        print("缓存为空，无需清理")
    else:
        print(f"已清理 {deleted} 条过期缓存，剩余 {remaining} 条")
    return 0


def cache_store(
    query,
    location,
    layer_hint,
    layers_used,
    results_count,
    top_results,
    score,
):
    now = datetime.now(timezone.utc).isoformat()
    repository = _repository()
    repository.put_result(
        _cache_record_key(query, location, layer_hint),
        {
            "query": query,
            "layers_used": list(layers_used or []),
            "results_count": max(0, int(results_count or 0)),
            "top_results": list(top_results or [])[:10],
            "score": float(score or 0),
            "retrieved_at": now,
        },
        retrieved_at=now,
        ttl_seconds=CACHE_TTL,
    )
    if repository.count(namespace="result") > 500:
        repository.prune()


for _name, _value in {
    "CACHE_FILE": CACHE_FILE,
    "_load_cache": _load_cache,
    "_save_cache": _save_cache,
    "cache_get": cache_get,
    "cache_clean": cache_clean,
    "cache_store": cache_store,
}.items():
    setattr(_legacy, _name, _value)
    globals()[_name] = _value


def main() -> int:
    return int(_legacy._cli() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
