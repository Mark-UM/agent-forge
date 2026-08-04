from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from modules.search.cache_store import SearchCacheRepository


BASE_TIME = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)


def test_cache_hit_updates_access_only(tmp_path: Path) -> None:
    repository = SearchCacheRepository(tmp_path / "cache.db")
    written = repository.put_result(
        "query-key",
        {"results": [1], "retrieved_at": BASE_TIME.isoformat()},
        retrieved_at=BASE_TIME.isoformat(),
        ttl_seconds=3600,
        now=BASE_TIME + timedelta(minutes=1),
    )
    hit = repository.get_result(
        "query-key",
        now=BASE_TIME + timedelta(minutes=20),
    )

    assert hit is not None
    assert hit.payload == {"results": [1], "retrieved_at": BASE_TIME.isoformat()}
    assert hit.retrieved_at == BASE_TIME.isoformat()
    assert hit.cached_at == written.cached_at
    assert hit.expires_at == written.expires_at
    assert hit.last_accessed_at == (BASE_TIME + timedelta(minutes=20)).isoformat()


def test_cache_hit_does_not_extend_expiry(tmp_path: Path) -> None:
    repository = SearchCacheRepository(tmp_path / "cache.db")
    repository.put_result(
        "short-lived",
        {"results": [1]},
        retrieved_at=BASE_TIME.isoformat(),
        ttl_seconds=60,
        now=BASE_TIME,
    )
    assert repository.get_result(
        "short-lived", now=BASE_TIME + timedelta(seconds=30)
    ) is not None
    assert repository.get_result(
        "short-lived", now=BASE_TIME + timedelta(seconds=61)
    ) is None
    assert repository.count() == 0


def test_provider_entries_are_isolated_by_provider(tmp_path: Path) -> None:
    repository = SearchCacheRepository(tmp_path / "cache.db")
    repository.put_provider(
        "serper",
        "same-key",
        {"results": ["web"]},
        retrieved_at=BASE_TIME.isoformat(),
        now=BASE_TIME,
    )
    repository.put_provider(
        "arxiv",
        "same-key",
        {"results": ["paper"]},
        retrieved_at=BASE_TIME.isoformat(),
        now=BASE_TIME,
    )

    assert repository.get_provider(
        "serper", "same-key", now=BASE_TIME
    ).payload["results"] == ["web"]
    assert repository.get_provider(
        "arxiv", "same-key", now=BASE_TIME
    ).payload["results"] == ["paper"]
    assert repository.count(namespace="provider") == 2


def test_prune_removes_only_expired_entries(tmp_path: Path) -> None:
    repository = SearchCacheRepository(tmp_path / "cache.db")
    repository.put_result(
        "expired",
        {"value": 1},
        retrieved_at=BASE_TIME.isoformat(),
        ttl_seconds=60,
        now=BASE_TIME,
    )
    repository.put_result(
        "fresh",
        {"value": 2},
        retrieved_at=(BASE_TIME + timedelta(minutes=10)).isoformat(),
        ttl_seconds=3600,
        now=BASE_TIME + timedelta(minutes=10),
    )

    deleted = repository.prune(now=BASE_TIME + timedelta(minutes=20))
    assert deleted == 1
    assert repository.count() == 1
    assert repository.get_result(
        "fresh", now=BASE_TIME + timedelta(minutes=20)
    ).payload["value"] == 2


def test_legacy_json_migration_is_idempotent_and_non_destructive(
    tmp_path: Path,
) -> None:
    repository = SearchCacheRepository(tmp_path / "cache.db")
    legacy = tmp_path / "pipeline_cache.json"
    original = {
        "entries": {
            "one": {
                "results": [1],
                "retrieved_at": BASE_TIME.isoformat(),
            },
            "two": {
                "results": [2],
                "retrieved_at": BASE_TIME.isoformat(),
            },
        }
    }
    legacy.write_text(json.dumps(original), encoding="utf-8")

    first = repository.migrate_json_file(
        legacy,
        ttl_seconds=31_536_000,
    )
    second = repository.migrate_json_file(
        legacy,
        ttl_seconds=31_536_000,
    )

    assert first["migrated"] is True
    assert first["imported_count"] == 2
    assert second["migrated"] is False
    assert second["reason"] == "already_migrated"
    assert repository.count(namespace="result") == 2
    assert json.loads(legacy.read_text(encoding="utf-8")) == original


def test_changed_legacy_file_is_imported_again(tmp_path: Path) -> None:
    repository = SearchCacheRepository(tmp_path / "cache.db")
    legacy = tmp_path / "search_cache.json"
    legacy.write_text(
        json.dumps({"one": {"results": [1]}}),
        encoding="utf-8",
    )
    repository.migrate_json_file(legacy)
    legacy.write_text(
        json.dumps(
            {
                "one": {"results": [1]},
                "two": {"results": [2]},
            }
        ),
        encoding="utf-8",
    )

    report = repository.migrate_json_file(legacy)
    assert report["migrated"] is True
    assert report["imported_count"] == 2
    assert repository.count(namespace="result") == 2


def test_invalid_legacy_json_is_reported_without_raising(tmp_path: Path) -> None:
    repository = SearchCacheRepository(tmp_path / "cache.db")
    legacy = tmp_path / "broken.json"
    legacy.write_text("{broken", encoding="utf-8")
    report = repository.migrate_json_file(legacy)
    assert report["migrated"] is False
    assert report["reason"].startswith("invalid_json")
    assert repository.count() == 0


def test_result_adapters_match_search_service_callbacks(tmp_path: Path) -> None:
    repository = SearchCacheRepository(tmp_path / "cache.db")
    get, store = repository.result_adapters(ttl_seconds=3600)
    assert get("missing") is None

    store(
        "adapter-key",
        {
            "results": [{"title": "Result"}],
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    restored = get("adapter-key")
    assert restored["results"][0]["title"] == "Result"
