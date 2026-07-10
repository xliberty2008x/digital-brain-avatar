"""Pluggable local embedding providers."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    dimensions: int
    ollama_url: str
    ollama_timeout_seconds: float = 20.0

    @classmethod
    def from_env(cls) -> "EmbeddingConfig":
        provider = os.getenv("EMBEDDING_PROVIDER", "ollama").strip().lower()
        model = os.getenv("EMBEDDING_MODEL", "bge-m3").strip()
        dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
        # Keep the more explicit name as the documented setting, while accepting
        # the shorter alias for existing local setups.
        raw_timeout = os.getenv(
            "OLLAMA_EMBEDDING_TIMEOUT_SECONDS",
            os.getenv("OLLAMA_TIMEOUT_SECONDS", "20"),
        )
        try:
            ollama_timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("OLLAMA_EMBEDDING_TIMEOUT_SECONDS must be a positive number") from exc
        if ollama_timeout_seconds <= 0:
            raise ValueError("OLLAMA_EMBEDDING_TIMEOUT_SECONDS must be a positive number")
        return cls(
            provider=provider,
            model=model,
            dimensions=dimensions,
            ollama_url=ollama_url,
            ollama_timeout_seconds=ollama_timeout_seconds,
        )


class EmbeddingRequestError(RuntimeError):
    """A bounded, classifiable failure returned by an embedding provider."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


class EmbeddingProvider:
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(self, config: EmbeddingConfig):
        self.config = config

    def embed(self, text: str) -> list[float]:
        payload = json.dumps({"model": self.config.model, "input": text}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.ollama_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.config.ollama_timeout_seconds,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Ollama often reports useful OOM/model-load diagnostics in the
            # response body. Keep it intentionally bounded so an upstream
            # proxy cannot turn an error into an unbounded tool response.
            body = _bounded_http_error_body(exc)
            reason = "oom" if _looks_like_oom(body) else "http_error"
            detail = f": {body}" if body else ""
            raise EmbeddingRequestError(
                f"Ollama embedding HTTP {exc.code}{detail}",
                reason=reason,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise EmbeddingRequestError(
                "Ollama embedding request timed out",
                reason="timeout",
            ) from exc
        except urllib.error.URLError as exc:
            reason = "timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "network"
            raise EmbeddingRequestError(
                f"Ollama embedding request failed: {_bounded_error_text(exc.reason)}",
                reason=reason,
            ) from exc

        embeddings = data.get("embeddings")
        if not embeddings:
            error = _bounded_error_text(data.get("error"))
            if error:
                reason = "oom" if _looks_like_oom(error) else "response_error"
                raise EmbeddingRequestError(
                    f"Ollama embedding response error: {error}",
                    reason=reason,
                )
            raise EmbeddingRequestError(
                "Ollama embedding response did not include embeddings",
                reason="invalid_response",
            )
        return _validate_dimensions(embeddings[0], self.config.dimensions)


def _bounded_error_text(value: object, limit: int = 512) -> str:
    """Return a one-line diagnostic suitable for an MCP tool error."""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def _bounded_http_error_body(error: urllib.error.HTTPError) -> str:
    try:
        raw = error.read(512)
    except OSError:
        return ""
    return _bounded_error_text(raw.decode("utf-8", errors="replace"))


def _looks_like_oom(value: str) -> bool:
    lowered = value.lower()
    return "out of memory" in lowered or "oom" in lowered or "not enough memory" in lowered


class HuggingFaceEmbeddingProvider(EmbeddingProvider):
    def __init__(self, config: EmbeddingConfig):
        self.config = config
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("Install sentence-transformers to use EMBEDDING_PROVIDER=huggingface") from exc
        self.model = SentenceTransformer(config.model)

    def embed(self, text: str) -> list[float]:
        vector = self.model.encode(text, normalize_embeddings=True)
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        return _validate_dimensions(vector, self.config.dimensions)


def _validate_dimensions(vector: list[float], expected: int) -> list[float]:
    if len(vector) != expected:
        raise ValueError(f"Embedding dimension mismatch: got {len(vector)}, expected {expected}")
    return [float(value) for value in vector]


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    config = EmbeddingConfig.from_env()
    if config.provider == "ollama":
        return OllamaEmbeddingProvider(config)
    if config.provider == "huggingface":
        return HuggingFaceEmbeddingProvider(config)
    raise ValueError("EMBEDDING_PROVIDER must be `ollama` or `huggingface`")


def embed_text(text: str | None) -> list[float] | None:
    if text is None or not str(text).strip():
        return None
    return get_embedding_provider().embed(str(text))
