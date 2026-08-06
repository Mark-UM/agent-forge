from __future__ import annotations

from email.message import Message
import io

import pytest

from modules.common.security import NetworkPolicy, UnsafeNetworkTarget
from modules.mcp import secure_fetch_mcp


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        final_url: str = "https://example.test/final",
        content_type: str = "text/plain; charset=utf-8",
        status: int = 200,
    ) -> None:
        self._stream = io.BytesIO(payload)
        self._final_url = final_url
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(payload))

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self._final_url

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests = []

    def open(self, request, timeout):  # noqa: ANN001
        self.requests.append((request, timeout))
        return self.response


def _offline_policy() -> NetworkPolicy:
    return NetworkPolicy(
        allowed_hosts=("example.test",),
        resolve_dns=False,
    )


def test_fetch_rejects_loopback_before_opening() -> None:
    opener = FakeOpener(FakeResponse(b"unused"))

    with pytest.raises(UnsafeNetworkTarget):
        secure_fetch_mcp.fetch_url("http://127.0.0.1/admin", opener=opener)

    assert opener.requests == []


def test_fetch_revalidates_final_url() -> None:
    opener = FakeOpener(
        FakeResponse(b"secret", final_url="https://unexpected.test/private")
    )

    with pytest.raises(UnsafeNetworkTarget, match="not allowed"):
        secure_fetch_mcp.fetch_url(
            "https://example.test/start",
            opener=opener,
            policy=_offline_policy(),
        )


def test_fetch_preserves_bounded_plain_text_contract() -> None:
    opener = FakeOpener(FakeResponse(b"abcdefghij"))

    result = secure_fetch_mcp.fetch_url(
        "https://example.test/start",
        max_length=4,
        opener=opener,
        policy=_offline_policy(),
    )

    assert result["content"] == "abcd"
    assert result["truncated"] is True
    assert result["total_length"] == 10
    assert result["bytes_read"] == 10
    assert result["network_policy"] == "public-only"


def test_fetch_converts_html_using_existing_converter() -> None:
    opener = FakeOpener(
        FakeResponse(
            b"<html><body><h1>Title</h1><p>Hello</p></body></html>",
            content_type="text/html; charset=utf-8",
        )
    )

    result = secure_fetch_mcp.fetch_url(
        "https://example.test/start",
        opener=opener,
        policy=_offline_policy(),
    )

    assert "# Title" in result["content"]
    assert "Hello" in result["content"]


def test_fetch_raw_mode_skips_html_conversion() -> None:
    payload = b"<p>Hello</p>"
    opener = FakeOpener(
        FakeResponse(payload, content_type="text/html; charset=utf-8")
    )

    result = secure_fetch_mcp.fetch_url(
        "https://example.test/start",
        raw=True,
        opener=opener,
        policy=_offline_policy(),
    )

    assert result["content"] == payload.decode()


def test_fetch_rejects_excessive_pagination_before_opening() -> None:
    opener = FakeOpener(FakeResponse(b"unused"))

    with pytest.raises(ValueError, match="MAX_START_INDEX"):
        secure_fetch_mcp.fetch_url(
            "https://example.test/start",
            start_index=secure_fetch_mcp.MAX_START_INDEX + 1,
            opener=opener,
            policy=_offline_policy(),
        )

    assert opener.requests == []
