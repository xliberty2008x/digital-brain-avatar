FROM python:3.12-slim

WORKDIR /workspace
ENV PYTHONPATH=/workspace

# The smoke client deliberately uses the repository MCP client and bootstrap
# CLI, so the test cannot reach Neo4j directly.
RUN pip install --no-cache-dir "aiohttp>=3.9.0"

COPY digital_brain/__init__.py digital_brain/config.py /workspace/digital_brain/
COPY digital_brain/tools/__init__.py digital_brain/tools/mcp_client.py /workspace/digital_brain/tools/
COPY scripts/bootstrap_journal_chain.py /workspace/scripts/bootstrap_journal_chain.py
COPY tests/e2e/journal_append_smoke.py /workspace/tests/e2e/journal_append_smoke.py
