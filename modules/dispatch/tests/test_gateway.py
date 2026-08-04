from __future__ import annotations

import json
import urllib.error

import pytest

from modules.dispatch.gateway import (
    FLASH_MODEL,
    PRO_MODEL,
    GatewayRequest,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelGatewayValidationError,
    normalize_messages,
    redact_text,
    route_model,
)


BASE_ENV = {
    "DEEPSEEK_API_KEY": "secret-deepseek-key",
    "DEEPSEEK_BASE_URL": "http://127.0.0.1:9000",
    "AGENT_FORGE_MODEL_ALLOW_PRIVATE": "1",
}


@pytest.mark.parametrize(
    ("task_type", "expected"),
    [
        ("implementation", PRO_MODEL),
        ("planner", PRO_MODEL),
        ("unknown-new-task", PRO_MODEL),
        ("classify", FLASH_MODEL),
        ("summarize", FLASH_MODEL),
        ("action_extract", FLASH_MODEL),
        ("review", FLASH_MODEL),
    ],
)
def test_route_model(task_type: str, expected: str) -> None:
    assert route_model(task_type) == expected


def test_explicit_model_aliases_are_bounded() -> None:
    assert route_model("implementation", "pro") == PRO_MODEL
    assert route_model("implementation", "flash") == FLASH_MODEL
    with pytest.raises(ModelGatewayValidationError, match="unsupported model"):
        route_model("implementation", "arbitrary-model")


def test_redact_text_removes_environment_and_common_secret_forms() -> None:
    text = (
        "token=secret-deepseek-key sk-abcdefghijklmnop "
        "Authorization: Bearer abcdefghijklmnop api_key=visible-secret"
    )
    redacted = redact_text(text, env=BASE_ENV)
    assert "secret-deepseek-key" not in redacted
    assert "sk-abcdefghijklmnop" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "visible-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_normalize_messages_validates_roles_and_redacts() -> None:
    messages = normalize_messages(
        [
            {"role": "system", "content": "keep secrets private"},
            {"role": "user", "content": "key=secret-deepseek-key"},
        ],
        env=BASE_ENV,
    )
    assert messages[1]["content"] == "key=[REDACTED]"

    with pytest.raises(ModelGatewayValidationError, match="invalid role"):
        normalize_messages([{"role": "root", "content": "x"}], env=BASE_ENV)


def test_successful_invoke_uses_routed_model_and_redacted_body() -> None:
    observed = {}

    def transport(url, headers, body, timeout):
        observed.update(
            {
                "url": url,
                "headers": dict(headers),
                "body": json.loads(body.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return {
            "id": "response-1",
            "choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }

    response = ModelGateway(env=BASE_ENV, transport=transport).invoke(
        GatewayRequest(
            task_type="classify",
            messages=(
                {"role": "user", "content": "secret-deepseek-key classify this"},
            ),
            timeout_seconds=12,
            max_retries=0,
        ),
        record_run=False,
    )

    assert response.success is True
    assert response.model == FLASH_MODEL
    assert response.content == "done"
    assert response.raw_id == "response-1"
    assert response.attempts == 1
    assert observed["url"].endswith("/chat/completions")
    assert observed["headers"]["Authorization"] == "Bearer secret-deepseek-key"
    assert observed["body"]["model"] == FLASH_MODEL
    assert "secret-deepseek-key" not in observed["body"]["messages"][0]["content"]
    assert observed["timeout"] == 12


def test_retryable_failure_retries_with_bounded_backoff() -> None:
    attempts = []
    sleeps = []

    def transport(url, headers, body, timeout):
        attempts.append(url)
        if len(attempts) < 3:
            raise urllib.error.URLError("temporary")
        return {"choices": [{"message": {"content": "recovered"}}]}

    response = ModelGateway(
        env=BASE_ENV,
        transport=transport,
        sleep_fn=sleeps.append,
    ).invoke(
        GatewayRequest(
            task_type="planner",
            messages=({"role": "user", "content": "plan"},),
            max_retries=2,
        ),
        record_run=False,
    )

    assert response.success is True
    assert response.content == "recovered"
    assert response.attempts == 3
    assert sleeps == [1, 2]


def test_non_retryable_http_error_returns_failure_without_retry() -> None:
    attempts = []

    def transport(url, headers, body, timeout):
        attempts.append(url)
        raise urllib.error.HTTPError(url, 400, "bad request", {}, None)

    response = ModelGateway(env=BASE_ENV, transport=transport).invoke(
        GatewayRequest(
            task_type="summarize",
            messages=({"role": "user", "content": "summarize"},),
            max_retries=3,
        ),
        record_run=False,
    )

    assert response.success is False
    assert response.attempts == 1
    assert response.error_code == "model_error"
    assert len(attempts) == 1


def test_invalid_response_is_reported_as_failure() -> None:
    response = ModelGateway(
        env=BASE_ENV,
        transport=lambda *args: {"choices": []},
    ).invoke(
        GatewayRequest(
            task_type="review",
            messages=({"role": "user", "content": "review"},),
            max_retries=0,
        ),
        record_run=False,
    )
    assert response.success is False
    assert "no choices" in response.error


def test_missing_api_key_is_configuration_error() -> None:
    with pytest.raises(ModelGatewayConfigurationError, match="DEEPSEEK_API_KEY"):
        ModelGateway(
            env={
                "DEEPSEEK_BASE_URL": "http://127.0.0.1:9000",
                "AGENT_FORGE_MODEL_ALLOW_PRIVATE": "1",
            },
            transport=lambda *args: {},
        ).invoke(
            GatewayRequest(
                task_type="review",
                messages=({"role": "user", "content": "review"},),
            )
        )


def test_run_record_is_returned_without_message_content_or_secrets() -> None:
    response = ModelGateway(
        env=BASE_ENV,
        transport=lambda *args: {
            "choices": [{"message": {"content": "ok"}}],
        },
    ).invoke(
        GatewayRequest(
            task_type="implementation",
            messages=(
                {"role": "user", "content": "secret-deepseek-key implement"},
            ),
            max_retries=0,
        ),
        record_run=True,
    )

    assert response.success is True
    assert response.run is not None
    serialized = json.dumps(response.run.to_dict())
    assert "secret-deepseek-key" not in serialized
    assert "implement" not in serialized
    assert response.run.metadata["message_count"] == 1
