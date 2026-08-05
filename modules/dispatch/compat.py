"""Thin compatibility helpers for migrating legacy urllib model callers.

Business modules retain their existing monkeypatchable ``urllib.request`` seam
while routing model policy, redaction, retries, and response semantics through
:mod:`modules.dispatch.gateway`. New modules should use the gateway directly.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Iterable, Mapping, Optional
import urllib.request

from modules.dispatch.gateway import (
    GatewayRequest,
    GatewayResponse,
    ModelGateway,
)

UrlOpen = Callable[..., Any]


def invoke_with_urlopen(
    *,
    task_type: str,
    messages: Iterable[Mapping[str, Any]],
    urlopen: UrlOpen,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout_seconds: float = 90.0,
    max_retries: int = 0,
    metadata: Optional[Mapping[str, Any]] = None,
    extra_body: Optional[Mapping[str, Any]] = None,
    record_run: bool = False,
    env: Optional[Mapping[str, str]] = None,
) -> GatewayResponse:
    """Invoke the gateway through a caller-supplied urllib ``urlopen``.

    Supplying ``urlopen`` at call time preserves existing unit tests that patch
    a module-local ``urllib.request.urlopen``. The API key is copied into an
    isolated environment mapping and is never placed in metadata.
    """

    environment = dict(os.environ if env is None else env)
    if api_key is not None:
        environment["DEEPSEEK_API_KEY"] = api_key

    def transport(url, headers, body, timeout):
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            # Bare TimeoutError stringifies to an empty value. Preserve a
            # stable diagnostic so legacy callers can retain timeout-specific
            # fallback messages while the Gateway still owns semantics.
            raise TimeoutError(
                f"model transport timeout after {timeout:g}s"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("model response must be a JSON object")
        return payload

    return ModelGateway(env=environment, transport=transport).invoke(
        GatewayRequest(
            task_type=task_type,
            messages=tuple(dict(message) for message in messages),
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            metadata=metadata or {},
            extra_body=extra_body or {},
        ),
        record_run=record_run,
    )


__all__ = ["invoke_with_urlopen"]
