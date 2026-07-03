# Digital Brain Cypher MCP

Local MCP server for Digital Brain Neo4j access. It preserves the current tool
contract expected by the ADK agents:

- `get_neo4j_schema`
- `read_neo4j_cypher`
- `write_neo4j_cypher`

When `embed_text` is supplied, the server generates a local embedding and
injects it into Cypher params as `$embedding`.

## Embedding Providers

Use `EMBEDDING_PROVIDER=ollama` for Ollama's `/api/embed` endpoint, or
`EMBEDDING_PROVIDER=huggingface` for local `sentence-transformers`.

Recommended first benchmark:

```bash
ollama pull bge-m3
EMBEDDING_PROVIDER=ollama EMBEDDING_MODEL=bge-m3 docker compose up mcp-cypher
```

Fallback lightweight baseline:

```bash
EMBEDDING_PROVIDER=huggingface EMBEDDING_MODEL=intfloat/multilingual-e5-small EMBEDDING_DIMENSIONS=384 docker compose up mcp-cypher
```
