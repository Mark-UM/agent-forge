"""Search package production wiring.

Importing the package replaces the legacy bare SearchService singleton builder
with the canonical production factory. Dependency-injected tests remain free to
construct SearchService directly.
"""
from __future__ import annotations

from modules.search import service as _service


def get_search_service():
    if _service._service_singleton is None:
        from modules.search.factory import build_search_service

        _service._service_singleton = build_search_service()
    return _service._service_singleton


def reset_search_service() -> None:
    _service._service_singleton = None


_service.get_search_service = get_search_service
_service.reset_search_service = reset_search_service

__all__ = ["get_search_service", "reset_search_service"]
