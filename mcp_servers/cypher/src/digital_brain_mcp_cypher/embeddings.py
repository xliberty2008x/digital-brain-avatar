"""Pluggable local embedding providers."""

from __future__ import annotations

import json
import os
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

    @classmethod
    def from_env(cls) -> "EmbeddingConfig":
        provider = os.getenv("EMBEDDING_PROVIDER", "ollama").strip().lower()
        model = os.getenv("EMBEDDING_MODEL", "bge-m3").strip()
        dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
        return cls(provider=provider, model=model, dimensions=dimensions, ollama_url=ollama_url)


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
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc

        embeddings = data.get("embeddings")
        if not embeddings:
            raise RuntimeError("Ollama embedding response did not include embeddings")
        return _validate_dimensions(embeddings[0], self.config.dimensions)


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
