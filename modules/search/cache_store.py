"""Authoritative SQLite cache repository for Search.

The repository stores final pipeline results and provider-level results in one
schema. Reading a cache entry updates only ``last_accessed_at``; it never
rewrites ``retrieved_at`` or extends ``expires_at``. Legacy JSON cache files can
be imported idempotently and remain untouched after migration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "_runtime" / "search" / "cache.db"
LEGACY_PIPELINE_CACHE = PROJECT_ROOT / "_runtime" / "search" / "pipeline_cache.json"
LEGACY_SEARCH_CACHE = PROJECT_ROOT / "_runtime" / "search" / "search_cache.json"
SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 3600


class SearchCacheError(RuntimeError):
    pass


@dataclass(frozen=True)
class CacheRecord:
    namespace: str
    cache_key: str
    provider: str
    payload: Mapping[str, Any]
    retrieved_at: str
    cached_at: str
    last_accessed_at: str
    expires_at: str
    schema_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "cache_key": self.cache_key,
            "provider": self.provider,
            "payload": dict(self.payload),
            "retrieved_at": self.retrieved_at,
            "cached_at": self.cached_at,
            "last_accessed_at": self.last_accessed_at,
            "expires_at": self.expires_at,
            "schema_version": self.schema_version,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_payload(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SearchCacheError("cache payload must be JSON serializable") from exc


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(path.resolve()).encode("utf-8"))
    digest.update(path.read_bytes())
    return digest.hexdigest()


class SearchCacheRepository:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS search_cache_entries (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    cached_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    PRIMARY KEY(namespace, cache_key, provider)
                )"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_search_cache_expiry
                   ON search_cache_entries(expires_at)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS search_cache_migrations (
                    migration_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    imported_count INTEGER NOT NULL,
                    migrated_at TEXT NOT NULL
                )"""
            )
            connection.commit()

    def _put(
        self,
        *,
        namespace: str,
        cache_key: str,
        provider: str,
        payload: Mapping[str, Any],
        retrieved_at: str | None,
        ttl_seconds: int,
        now: Optional[datetime] = None,
    ) -> CacheRecord:
        if not namespace or not cache_key:
            raise ValueError("namespace and cache_key are required")
        if not 1 <= int(ttl_seconds) <= 31_536_000:
            raise ValueError("ttl_seconds must be between 1 and 31536000")
        current = (now or _utc_now()).astimezone(timezone.utc)
        retrieved = _parse_timestamp(retrieved_at, current)
        cached_at = current.isoformat()
        expires_at = (retrieved + timedelta(seconds=int(ttl_seconds))).isoformat()
        payload_json = _json_payload(payload)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO search_cache_entries
                   (namespace, cache_key, provider, payload_json, retrieved_at,
                    cached_at, last_accessed_at, expires_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(namespace, cache_key, provider) DO UPDATE SET
                     payload_json = excluded.payload_json,
                     retrieved_at = excluded.retrieved_at,
                     cached_at = excluded.cached_at,
                     last_accessed_at = excluded.last_accessed_at,
                     expires_at = excluded.expires_at,
                     schema_version = excluded.schema_version""",
                (
                    namespace,
                    cache_key,
                    provider,
                    payload_json,
                    retrieved.isoformat(),
                    cached_at,
                    cached_at,
                    expires_at,
                    SCHEMA_VERSION,
                ),
            )
            connection.commit()
        record = self._get_record(
            namespace=namespace,
            cache_key=cache_key,
            provider=provider,
            now=current,
            update_access=False,
            allow_expired=True,
        )
        if record is None:
            raise SearchCacheError("cache record disappeared after write")
        return record

    def _get_record(
        self,
        *,
        namespace: str,
        cache_key: str,
        provider: str,
        now: Optional[datetime] = None,
        update_access: bool = True,
        allow_expired: bool = False,
    ) -> Optional[CacheRecord]:
        current = (now or _utc_now()).astimezone(timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM search_cache_entries
                   WHERE namespace = ? AND cache_key = ? AND provider = ?""",
                (namespace, cache_key, provider),
            ).fetchone()
            if row is None:
                return None
            expires = _parse_timestamp(row["expires_at"], current)
            if not allow_expired and expires <= current:
                connection.execute(
                    """DELETE FROM search_cache_entries
                       WHERE namespace = ? AND cache_key = ? AND provider = ?""",
                    (namespace, cache_key, provider),
                )
                connection.commit()
                return None
            accessed_at = str(row["last_accessed_at"])
            if update_access:
                accessed_at = current.isoformat()
                connection.execute(
                    """UPDATE search_cache_entries SET last_accessed_at = ?
                       WHERE namespace = ? AND cache_key = ? AND provider = ?""",
                    (accessed_at, namespace, cache_key, provider),
                )
                connection.commit()
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise SearchCacheError("stored cache payload is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SearchCacheError("stored cache payload must be an object")
        return CacheRecord(
            namespace=str(row["namespace"]),
            cache_key=str(row["cache_key"]),
            provider=str(row["provider"]),
            payload=payload,
            retrieved_at=str(row["retrieved_at"]),
            cached_at=str(row["cached_at"]),
            last_accessed_at=accessed_at,
            expires_at=str(row["expires_at"]),
            schema_version=int(row["schema_version"]),
        )

    def put_result(
        self,
        cache_key: str,
        payload: Mapping[str, Any],
        *,
        retrieved_at: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: Optional[datetime] = None,
    ) -> CacheRecord:
        return self._put(
            namespace="result",
            cache_key=cache_key,
            provider="",
            payload=payload,
            retrieved_at=retrieved_at,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def get_result(
        self,
        cache_key: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[CacheRecord]:
        return self._get_record(
            namespace="result",
            cache_key=cache_key,
            provider="",
            now=now,
        )

    def put_provider(
        self,
        provider: str,
        cache_key: str,
        payload: Mapping[str, Any],
        *,
        retrieved_at: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: Optional[datetime] = None,
    ) -> CacheRecord:
        if not provider.strip():
            raise ValueError("provider is required")
        return self._put(
            namespace="provider",
            cache_key=cache_key,
            provider=provider.strip(),
            payload=payload,
            retrieved_at=retrieved_at,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def get_provider(
        self,
        provider: str,
        cache_key: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[CacheRecord]:
        return self._get_record(
            namespace="provider",
            cache_key=cache_key,
            provider=provider.strip(),
            now=now,
        )

    def prune(self, *, now: Optional[datetime] = None) -> int:
        current = (now or _utc_now()).astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM search_cache_entries WHERE expires_at <= ?",
                (current,),
            )
            connection.commit()
            return max(0, cursor.rowcount)

    def count(self, *, namespace: Optional[str] = None) -> int:
        with self._connect() as connection:
            if namespace is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM search_cache_entries"
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT COUNT(*) AS total FROM search_cache_entries
                       WHERE namespace = ?""",
                    (namespace,),
                ).fetchone()
        return int(row["total"])

    def migrate_json_file(
        self,
        path: Path | str,
        *,
        namespace: str = "result",
        provider: str = "",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        source = Path(path)
        if not source.is_file():
            return {
                "source": str(source),
                "migrated": False,
                "imported_count": 0,
                "reason": "missing",
            }
        fingerprint = _file_fingerprint(source)
        migration_key = hashlib.sha256(
            f"{namespace}:{provider}:{source.resolve()}".encode("utf-8")
        ).hexdigest()
        with self._connect() as connection:
            prior = connection.execute(
                """SELECT source_fingerprint, imported_count
                   FROM search_cache_migrations WHERE migration_key = ?""",
                (migration_key,),
            ).fetchone()
        if prior is not None and prior["source_fingerprint"] == fingerprint:
            return {
                "source": str(source),
                "migrated": False,
                "imported_count": int(prior["imported_count"]),
                "reason": "already_migrated",
            }
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "source": str(source),
                "migrated": False,
                "imported_count": 0,
                "reason": f"invalid_json:{type(exc).__name__}",
            }
        entries: Mapping[str, Any]
        if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
            entries = raw["entries"]
        elif isinstance(raw, dict):
            entries = raw
        else:
            return {
                "source": str(source),
                "migrated": False,
                "imported_count": 0,
                "reason": "unsupported_shape",
            }

        imported = 0
        for key, value in entries.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            retrieved_at = value.get("retrieved_at") or value.get("cached_at")
            if namespace == "provider":
                self.put_provider(
                    provider,
                    key,
                    value,
                    retrieved_at=retrieved_at,
                    ttl_seconds=ttl_seconds,
                )
            else:
                self.put_result(
                    key,
                    value,
                    retrieved_at=retrieved_at,
                    ttl_seconds=ttl_seconds,
                )
            imported += 1

        with self._connect() as connection:
            connection.execute(
                """INSERT INTO search_cache_migrations
                   (migration_key, source_path, source_fingerprint,
                    imported_count, migrated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(migration_key) DO UPDATE SET
                     source_fingerprint = excluded.source_fingerprint,
                     imported_count = excluded.imported_count,
                     migrated_at = excluded.migrated_at""",
                (
                    migration_key,
                    str(source.resolve()),
                    fingerprint,
                    imported,
                    _utc_now().isoformat(),
                ),
            )
            connection.commit()
        return {
            "source": str(source),
            "migrated": True,
            "imported_count": imported,
            "reason": "ok",
        }

    def migrate_legacy_defaults(self) -> list[dict[str, Any]]:
        return [
            self.migrate_json_file(LEGACY_PIPELINE_CACHE, namespace="result"),
            self.migrate_json_file(LEGACY_SEARCH_CACHE, namespace="result"),
        ]

    def result_adapters(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> tuple[
        Callable[[str], Optional[dict[str, Any]]],
        Callable[[str, dict[str, Any]], None],
    ]:
        """Return callbacks compatible with SearchService/SearchPipeline."""

        def get(cache_key: str) -> Optional[dict[str, Any]]:
            record = self.get_result(cache_key)
            return dict(record.payload) if record is not None else None

        def store(cache_key: str, payload: dict[str, Any]) -> None:
            retrieved_at = payload.get("retrieved_at")
            self.put_result(
                cache_key,
                payload,
                retrieved_at=(
                    str(retrieved_at) if retrieved_at is not None else None
                ),
                ttl_seconds=ttl_seconds,
            )

        return get, store


__all__ = [
    "CacheRecord",
    "DEFAULT_DB_PATH",
    "DEFAULT_TTL_SECONDS",
    "LEGACY_PIPELINE_CACHE",
    "LEGACY_SEARCH_CACHE",
    "SCHEMA_VERSION",
    "SearchCacheError",
    "SearchCacheRepository",
]
