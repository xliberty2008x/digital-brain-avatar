FROM python:3.12-slim

WORKDIR /workspace
ENV PYTHONPATH=/workspace:/workspace/mcp_servers/cypher/src

ARG E2E_PROFILE=journal

# Journal smoke uses the repository MCP client and bootstrap CLI only.
# Dreams profile also needs pytest + neo4j for optional role smoke and
# in-container replay of the fake-store gates.
RUN if [ "$E2E_PROFILE" = "dreams" ]; then \
      pip install --no-cache-dir "aiohttp>=3.9.0" "pytest>=8.0" "neo4j>=5.26.0" "pydantic>=2.0"; \
    else \
      pip install --no-cache-dir "aiohttp>=3.9.0"; \
    fi

# Shared minimal package surface
COPY digital_brain/__init__.py digital_brain/config.py /workspace/digital_brain/
COPY digital_brain/tools/__init__.py digital_brain/tools/mcp_client.py /workspace/digital_brain/tools/
COPY scripts/bootstrap_journal_chain.py /workspace/scripts/bootstrap_journal_chain.py
COPY tests/e2e/journal_append_smoke.py /workspace/tests/e2e/journal_append_smoke.py

# Dreams gate surface (harmless for journal profile if unused)
COPY digital_brain/maintenance/ /workspace/digital_brain/maintenance/
COPY mcp_servers/cypher/src/digital_brain_mcp_cypher/ /workspace/mcp_servers/cypher/src/digital_brain_mcp_cypher/
COPY plugins/digital-brain-buddy/quality/ /workspace/plugins/digital-brain-buddy/quality/
COPY plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md \
     /workspace/plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md
COPY tests/fixtures/dreams/ /workspace/tests/fixtures/dreams/
COPY tests/e2e/quality_control_smoke.py /workspace/tests/e2e/quality_control_smoke.py
COPY tests/e2e/dream_workflow_smoke.py /workspace/tests/e2e/dream_workflow_smoke.py
COPY tests/e2e/dream_crash_recovery.py /workspace/tests/e2e/dream_crash_recovery.py
COPY tests/test_mcp_cypher_maintenance.py /workspace/tests/test_mcp_cypher_maintenance.py
COPY tests/test_mcp_cypher_quality.py /workspace/tests/test_mcp_cypher_quality.py
COPY tests/test_overlay_activation.py /workspace/tests/test_overlay_activation.py
COPY tests/test_alias_effects.py /workspace/tests/test_alias_effects.py
COPY scripts/init_quality_roles.py /workspace/scripts/init_quality_roles.py
COPY scripts/init-quality-roles.cypher /workspace/scripts/init-quality-roles.cypher
COPY pyproject.toml /workspace/pyproject.toml
