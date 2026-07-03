#!/usr/bin/env python
"""Quick local embedding benchmark for candidate models."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request


SAMPLES = [
    "конфлікт з батьком про роботу і складні рішення",
    "EPAM onboarding and project interviews after receiving an offer",
    "Digital Brain Neo4j MCP local embeddings journal memory",
]


def embed_ollama(base_url: str, model: str, text: str) -> list[float]:
    payload = json.dumps({"model": model, "input": text}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["embeddings"][0]


def embed_huggingface(model: str, text: str) -> list[float]:
    from sentence_transformers import SentenceTransformer

    if not hasattr(embed_huggingface, "_models"):
        embed_huggingface._models = {}
    models = embed_huggingface._models
    if model not in models:
        models[model] = SentenceTransformer(model)
    vector = models[model].encode(text, normalize_embeddings=True)
    return vector.tolist() if hasattr(vector, "tolist") else list(vector)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "huggingface"), default="ollama")
    parser.add_argument("--model", default="bge-m3")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = []
    for sample in SAMPLES:
        started = time.perf_counter()
        if args.provider == "ollama":
            vector = embed_ollama(args.ollama_url, args.model, sample)
        else:
            vector = embed_huggingface(args.model, sample)
        rows.append(
            {
                "provider": args.provider,
                "model": args.model,
                "dimensions": len(vector),
                "seconds": round(time.perf_counter() - started, 3),
                "sample": sample,
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
