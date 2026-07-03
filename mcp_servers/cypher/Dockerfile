FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/models/huggingface

COPY pyproject.toml README.md /app/
COPY src/ /app/src/

ARG EMBEDDING_EXTRAS=""
RUN if [ -n "$EMBEDDING_EXTRAS" ]; then \
      pip install --no-cache-dir -e ".[${EMBEDDING_EXTRAS}]"; \
    else \
      pip install --no-cache-dir -e .; \
    fi

EXPOSE 8000

CMD ["digital-brain-mcp-cypher"]
