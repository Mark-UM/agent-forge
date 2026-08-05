#!/usr/bin/env python3
"""Compatibility CLI backed by the authoritative SQLite Search cache.

The historical single-file implementation is imported as a private compatibility
module so its log/history/health/CLI contracts remain stable. Cache functions
are replaced at module load: legacy JSON is read only for one-time migration;
all new cache writes, reads and pruning use :class:`SearchCacheRepository`.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

try:
    from . import legacy_search as _legacy
except ImportError:
    import legacy_search as _legacy  # type: ignore

from modules.search.cache_store import DEFAULT_DB_PATH, SearchCacheRepository

# Re-export the historical public API before overriding the cache seam.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

LEGACY_CACHE_FILE = os.path.join(_legacy._LOG_DIR, "search_cache.json")
CACHE_FILE = str(DEFAULT_DB_PATH)
CACHE_TTL = int(_legacy.CACHE_TTL)
_CACHE_PREFIX = "history-cli:"
_CACHE_REPOSITORY: SearchCacheRepository | None = None


def _repository() -> SearchCacheRepository:
    global _CACHE_REPOSITORY
    if _CACHE_REPOSITORY is None:
        _CACHE_REPOSITORY = SearchCacheRepository(Path(CACHE_FILE))
        # Import-only migration. The legacy file is never rewritten or deleted.
        _CACHE_REPOSITORY.migrate_json_file(
            Path(LEGACY_CACHE_FILE),
            ttl_seconds=CACHE_TTL,
            migration_name="legacy_search_history_cache",
        )
    return _CACHE_REPOSITORY


def _load_cache() -> dict[str, Any]:
    """Read the old JSON format for compatibility; never use it for writes."""
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


def _save_cache(cache: Mapping[str, Any]) -> None:
    """Compatibility bulk import into SQLite; never create JSON cache files."""
    if not isinstance(cache, Mapping):
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


# Historical functions resolve these names in legacy_search.__dict__, so patch
# that namespace as well as the public wrapper namespace.
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
