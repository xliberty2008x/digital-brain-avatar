"""Focused unit tests for bounded local embedding failures."""

from __future__ import annotations

from io import BytesIO
import pathlib
import socket
import sys
import urllib.error

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_servers" / "cypher" / "src"))

from digital_brain_mcp_cypher.embeddings import (  # noqa: E402
    EmbeddingConfig,
    EmbeddingRequestError,
    OllamaEmbeddingProvider,
)


def _config() -> EmbeddingConfig:
    return EmbeddingConfig(
        provider="ollama",
        model="bge-m3",
        dimensions=2,
        ollama_url="http://ollama:11434",
        ollama_timeout_seconds=7.5,
    )


def test_ollama_timeout_defaults_to_twenty_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_EMBEDDING_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OLLAMA_TIMEOUT_SECONDS", raising=False)

    assert EmbeddingConfig.from_env().ollama_timeout_seconds == 20.0


def test_ollama_timeout_uses_explicit_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_EMBEDDING_TIMEOUT_SECONDS", "7.5")

    assert EmbeddingConfig.from_env().ollama_timeout_seconds == 7.5


def test_ollama_http_error_body_is_bounded_and_classified_as_oom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"out of memory while loading bge-m3\n" + (b"x" * 2000)

    def raise_http_error(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://ollama:11434/api/embed",
            500,
            "Internal Server Error",
            None,
            BytesIO(body),
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

    with pytest.raises(EmbeddingRequestError) as raised:
        OllamaEmbeddingProvider(_config()).embed("probe")

    assert raised.value.reason == "oom"
    assert "out of memory" in str(raised.value)
    assert len(str(raised.value)) < 600


def test_ollama_timeout_is_classified_without_upstream_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*_args, **_kwargs):
        raise socket.timeout("slow upstream")

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)

    with pytest.raises(EmbeddingRequestError) as raised:
        OllamaEmbeddingProvider(_config()).embed("probe")

    assert raised.value.reason == "timeout"
    assert str(raised.value) == "Ollama embedding request timed out"


def test_ollama_urlopen_uses_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, float] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"embeddings": [[0.1, 0.2]]}'

    def open_with_timeout(_request, *, timeout: float):
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_with_timeout)

    assert OllamaEmbeddingProvider(_config()).embed("probe") == [0.1, 0.2]
    assert observed == {"timeout": 7.5}
