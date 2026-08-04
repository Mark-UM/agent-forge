"""Unified DeepSeek-compatible model gateway.

All direct model requests should pass through this module so routing, secret
handling, input redaction, timeouts, retries, telemetry, and result semantics
remain consistent across Prompt, Search, Orchestrator, and review workflows.

The gateway uses only the Python standard library and the shared Agent Forge
security/Run primitives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
import urllib.error
import urllib.parse
import urllib.request

from modules.common.run import EventLevel, EventType, RunRecorder, RunResult
from modules.common.security import (
    NetworkPolicy,
    build_safe_url_opener,
    validate_outbound_url,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_RETRIES = 2
MAX_MESSAGE_CHARS = 500_000
MAX_RESPONSE_BYTES = 5_000_000
PRO_MODEL = "deepseek-reasoner"
FLASH_MODEL = "deepseek-chat"

PRO_TASKS = frozenset(
    {
        "core",
        "implementation",
        "coding",
        "planner",
        "planning",
        "refactor",
        "architecture",
        "debug",
        "analysis",
        "research",
    }
)
FLASH_TASKS = frozenset(
    {
        "classify",
        "classification",
        "scoring",
        "i18n",
        "summarize",
        "summary",
        "aggregator",
        "action_extraction",
        "action_extract",
        "review",
        "verification",
        "verify",
    }
)

_SECRET_ENV_NAMES = (
    "DEEPSEEK_API_KEY",
    "SERPER_API_KEY",
    "SILICONFLOW_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "BROWSER_USE_API_KEY",
    "GITHUB_TOKEN",
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
    re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*([^\s,;]{6,})"
    ),
)


class ModelGatewayError(RuntimeError):
    pass


class ModelGatewayValidationError(ModelGatewayError):
    pass


class ModelGatewayConfigurationError(ModelGatewayError):
    pass


@dataclass(frozen=True)
class GatewayRequest:
    task_type: str
    messages: tuple[dict[str, str], ...]
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayResponse:
    success: bool
    model: str
    content: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    attempts: int = 0
    duration_ms: int = 0
    timestamp: str = ""
    run: Optional[RunResult] = None
    raw_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "model": self.model,
            "content": self.content,
            "usage": dict(self.usage),
            "error": self.error,
            "error_code": self.error_code,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
            "run": self.run.to_dict() if self.run else None,
            "raw_id": self.raw_id,
        }


Transport = Callable[[str, Mapping[str, str], bytes, float], dict[str, Any]]


def route_model(task_type: str, requested_model: Optional[str] = None) -> str:
    """Choose the canonical Pro/Flash model for a task.

    Explicit model requests are accepted only for the two supported DeepSeek
    models, preventing arbitrary endpoint/model drift through business modules.
    Unknown task types fail safe to Pro.
    """

    if requested_model:
        normalized = requested_model.strip()
        aliases = {
            "pro": PRO_MODEL,
            "reasoner": PRO_MODEL,
            "deepseek-v4-pro": PRO_MODEL,
            PRO_MODEL: PRO_MODEL,
            "flash": FLASH_MODEL,
            "chat": FLASH_MODEL,
            "deepseek-v4-flash": FLASH_MODEL,
            FLASH_MODEL: FLASH_MODEL,
        }
        if normalized not in aliases:
            raise ModelGatewayValidationError(
                f"unsupported model {requested_model!r}; use Pro or Flash"
            )
        return aliases[normalized]

    task = (task_type or "").strip().lower().replace("-", "_")
    if task in FLASH_TASKS:
        return FLASH_MODEL
    return PRO_MODEL


def _secret_values(env: Mapping[str, str]) -> list[str]:
    return sorted(
        {
            value.strip()
            for name in _SECRET_ENV_NAMES
            if (value := env.get(name, "")).strip() and len(value.strip()) >= 6
        },
        key=len,
        reverse=True,
    )


def redact_text(text: str, *, env: Mapping[str, str] | None = None) -> str:
    """Redact known environment secrets and common token forms."""

    if not isinstance(text, str):
        raise ModelGatewayValidationError("message content must be a string")
    environment = os.environ if env is None else env
    redacted = text
    for secret in _secret_values(environment):
        redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def normalize_messages(
    messages: Iterable[Mapping[str, Any]],
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], ...]:
    output: list[dict[str, str]] = []
    total = 0
    allowed_roles = {"system", "user", "assistant", "tool"}
    for index, raw in enumerate(messages):
        if not isinstance(raw, Mapping):
            raise ModelGatewayValidationError(
                f"message {index} must be an object"
            )
        role = str(raw.get("role", "")).strip().lower()
        if role not in allowed_roles:
            raise ModelGatewayValidationError(
                f"message {index} has invalid role {role!r}"
            )
        content = raw.get("content")
        if not isinstance(content, str):
            raise ModelGatewayValidationError(
                f"message {index} content must be a string"
            )
        content = redact_text(content, env=env)
        total += len(content)
        if total > MAX_MESSAGE_CHARS:
            raise ModelGatewayValidationError(
                f"messages exceed {MAX_MESSAGE_CHARS} characters"
            )
        output.append({"role": role, "content": content})
    if not output:
        raise ModelGatewayValidationError("at least one message is required")
    return tuple(output)


def _base_url(env: Mapping[str, str]) -> str:
    value = env.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        raise ModelGatewayConfigurationError("DEEPSEEK_BASE_URL is empty")
    allow_private = env.get("AGENT_FORGE_MODEL_ALLOW_PRIVATE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    validate_outbound_url(
        value,
        policy=NetworkPolicy(allow_private=allow_private),
    )
    return value


def _endpoint(env: Mapping[str, str]) -> str:
    return _base_url(env) + "/chat/completions"


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    allow_private = os.environ.get(
        "AGENT_FORGE_MODEL_ALLOW_PRIVATE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    policy = NetworkPolicy(
        allowed_hosts=(parsed.hostname,) if parsed.hostname else (),
        allow_private=allow_private,
    )
    opener = build_safe_url_opener(policy)
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelGatewayError("model response exceeded safety limit")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ModelGatewayError("model response must be a JSON object")
        return payload


def _validate_request(request: GatewayRequest) -> None:
    if not isinstance(request, GatewayRequest):
        raise ModelGatewayValidationError("request must be GatewayRequest")
    if not request.task_type or not request.task_type.strip():
        raise ModelGatewayValidationError("task_type is required")
    if not 1 <= request.timeout_seconds <= 300:
        raise ModelGatewayValidationError(
            "timeout_seconds must be between 1 and 300"
        )
    if not 0 <= request.max_retries <= 5:
        raise ModelGatewayValidationError("max_retries must be between 0 and 5")
    if request.temperature is not None and not 0 <= request.temperature <= 2:
        raise ModelGatewayValidationError("temperature must be between 0 and 2")
    if request.max_tokens is not None and not 1 <= request.max_tokens <= 131_072:
        raise ModelGatewayValidationError(
            "max_tokens must be between 1 and 131072"
        )


def _extract_content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelGatewayError("model response has no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ModelGatewayError("model response choice is invalid")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise ModelGatewayError("model response message is invalid")
    content = message.get("content")
    if not isinstance(content, str):
        raise ModelGatewayError("model response content is not a string")
    return content


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
        ),
    )


class ModelGateway:
    """Canonical DeepSeek-compatible request gateway."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        transport: Optional[Transport] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._env = os.environ if env is None else env
        self._transport = transport or _default_transport
        self._sleep = sleep_fn

    def invoke(
        self,
        request: GatewayRequest,
        *,
        record_run: bool = True,
    ) -> GatewayResponse:
        _validate_request(request)
        model = route_model(request.task_type, request.model)
        messages = normalize_messages(request.messages, env=self._env)
        api_key = self._env.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ModelGatewayConfigurationError(
                "DEEPSEEK_API_KEY is required for model invocation"
            )
        endpoint = _endpoint(self._env)
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        started = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        recorder: Optional[RunRecorder] = None
        if record_run:
            recorder = RunRecorder(
                run_type="model",
                metadata={
                    "task_type": request.task_type,
                    "model": model,
                    "message_count": len(messages),
                    **dict(request.metadata),
                },
            )
            recorder.__enter__()
            recorder.event(
                EventType.RUN_STARTED,
                payload={"model": model, "task_type": request.task_type},
            )

        attempts = 0
        last_error: Optional[BaseException] = None
        response_payload: Optional[dict[str, Any]] = None
        try:
            if recorder is not None:
                step_context = recorder.step("model_request")
                step = step_context.__enter__()
            else:
                step_context = None
                step = None
            try:
                for attempt in range(request.max_retries + 1):
                    attempts = attempt + 1
                    try:
                        response_payload = self._transport(
                            endpoint,
                            headers,
                            body,
                            request.timeout_seconds,
                        )
                        last_error = None
                        break
                    except BaseException as exc:
                        last_error = exc
                        if attempt >= request.max_retries or not _retryable(exc):
                            raise
                        delay = min(2**attempt, 8)
                        if recorder is not None:
                            recorder.event(
                                EventType.DEGRADED_ENTERED,
                                level=EventLevel.WARN,
                                payload={
                                    "attempt": attempts,
                                    "retry_in_seconds": delay,
                                    "error_type": type(exc).__name__,
                                },
                            )
                        self._sleep(delay)
                if response_payload is None:
                    raise ModelGatewayError("model transport returned no response")
                content = _extract_content(response_payload)
                if step is not None:
                    step.succeed(
                        data={
                            "model": model,
                            "attempts": attempts,
                            "response_chars": len(content),
                        }
                    )
            except BaseException as exc:
                if step is not None:
                    step.fail(
                        exc,
                        code="model_request_failed",
                        details={"model": model, "attempts": attempts},
                    )
                raise
            finally:
                if step_context is not None:
                    step_context.__exit__(
                        type(last_error) if last_error else None,
                        last_error,
                        last_error.__traceback__ if last_error else None,
                    )

            run_result = None
            if recorder is not None:
                recorder.__exit__(None, None, None)
                run_result = recorder.result
            return GatewayResponse(
                success=True,
                model=model,
                content=content,
                usage=(
                    response_payload.get("usage", {})
                    if isinstance(response_payload.get("usage"), Mapping)
                    else {}
                ),
                attempts=attempts,
                duration_ms=int((time.monotonic() - started) * 1000),
                timestamp=timestamp,
                run=run_result,
                raw_id=(
                    str(response_payload.get("id"))
                    if response_payload.get("id") is not None
                    else None
                ),
            )
        except BaseException as exc:
            run_result = None
            if recorder is not None:
                recorder.__exit__(type(exc), exc, exc.__traceback__)
                run_result = recorder.result
            return GatewayResponse(
                success=False,
                model=model,
                error=str(exc),
                error_code=(
                    "retryable_model_error" if _retryable(exc) else "model_error"
                ),
                attempts=attempts,
                duration_ms=int((time.monotonic() - started) * 1000),
                timestamp=timestamp,
                run=run_result,
            )


def invoke(
    *,
    task_type: str,
    messages: Iterable[Mapping[str, Any]],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    metadata: Optional[Mapping[str, Any]] = None,
    record_run: bool = True,
) -> GatewayResponse:
    """Convenience entrypoint used by business modules."""

    return ModelGateway().invoke(
        GatewayRequest(
            task_type=task_type,
            messages=tuple(dict(message) for message in messages),
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            metadata=metadata or {},
        ),
        record_run=record_run,
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "FLASH_MODEL",
    "GatewayRequest",
    "GatewayResponse",
    "ModelGateway",
    "ModelGatewayConfigurationError",
    "ModelGatewayError",
    "ModelGatewayValidationError",
    "PRO_MODEL",
    "invoke",
    "normalize_messages",
    "redact_text",
    "route_model",
]
