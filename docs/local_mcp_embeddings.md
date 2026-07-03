# Local MCP And Embeddings

This runtime removes the Google-hosted Neo4j MCP/embedding dependency while
keeping the current ADK/Gemini agent layer.

## Start Local Runtime

```bash
cp .env.example .env.local
docker compose --profile ollama up -d neo4j ollama
docker compose exec ollama ollama pull bge-m3
docker compose --profile ollama up -d mcp-cypher
```

`mcp-memory` is gated behind the opt-in `memory` compose profile and is not
wired up yet: its build context (`./mcp-neo4j/servers/mcp-neo4j-memory`) is a
submodule reference with no `.gitmodules` entry, so the directory is empty on
a fresh checkout. Populate that submodule yourself before enabling it:

```bash
docker compose --profile memory up -d mcp-memory
```

The app defaults to:

```text
DIGITAL_BRAIN_MCP_URL=http://localhost:8000/api/mcp/
```

## Model Candidates

Benchmark before full backfill:

- `bge-m3` through Ollama: default quality candidate, 1024 dimensions.
- `intfloat/multilingual-e5-small` through Hugging Face: lightweight baseline, 384 dimensions.
- `intfloat/multilingual-e5-large-instruct` through Hugging Face: stronger E5 option, 1024 dimensions.
- `nomic-embed-text` and `mxbai-embed-large` through Ollama: practical local alternatives.

The default `mcp-cypher` image is built for Ollama and does not install the
large Hugging Face/PyTorch stack. To test Hugging Face locally, build with:

```bash
EMBEDDING_EXTRAS=huggingface docker compose build mcp-cypher
```

Vector indexes must match the selected model dimension. Do not mix embeddings
from different dimensions in the same Neo4j vector index.

## Benchmark, Index, Backfill

```bash
python scripts/benchmark_embedding_models.py --provider ollama --model bge-m3
python scripts/recreate_vector_index.py --dimensions 1024
python scripts/backfill_embeddings.py --dry-run --label JournalEntry
python scripts/backfill_embeddings.py --label JournalEntry --batch-size 25
python scripts/probe_embedding_quality.py --limit 5
```

The fixed quality probes cover father/family, EPAM/work, swimming, Digital
Brain, and AI dependency memories. Treat the probe output as a small acceptance
suite before changing the selected embedding model.
