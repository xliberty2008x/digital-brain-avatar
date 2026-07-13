"""Static plugin contract tests for the FEEDBACK route.

Locks skill/agent docs to the design rules:
- FEEDBACK route exists alongside SKIP/READ/WRITE
- one confirmation prompt budget per turn
- generic-ack rejection (never activation)
- claim_false is propose-only (no life-memory mutation)
- Alias activation never from prose / model-facing MCP
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "digital-brain-buddy"
SESSION_SKILL = (
    PLUGIN / "skills" / "digital-brain-buddy-session" / "SKILL.md"
)
SUBAGENT_PROMPTS = (
    PLUGIN
    / "skills"
    / "digital-brain-buddy-session"
    / "references"
    / "subagent-prompts.md"
)
GRAPH_SKILL = PLUGIN / "skills" / "digital-brain-buddy-graph-mcp" / "SKILL.md"
RUNTIME_PATTERNS = (
    PLUGIN
    / "skills"
    / "digital-brain-buddy-graph-mcp"
    / "references"
    / "runtime-patterns.md"
)
READ_SKILL = PLUGIN / "skills" / "digital-brain-buddy-read-memory" / "SKILL.md"
WRITE_SKILL = PLUGIN / "skills" / "digital-brain-buddy-write-memory" / "SKILL.md"
READER_AGENT = PLUGIN / "agents" / "digital-brain-reader.md"
WRITER_AGENT = PLUGIN / "agents" / "digital-brain-writer.md"
ENTITY_CHECK_AGENT = PLUGIN / "agents" / "digital-brain-entity-check.md"
APPLY_SCRIPT = ROOT / "scripts" / "digital_brain_apply_proposal.py"
ALIAS_EFFECTS = ROOT / "digital_brain" / "maintenance" / "alias_effects.py"
ENTITY_RESOLVER = ROOT / "digital_brain" / "services" / "entity_resolver.py"
QUALITY_CONTROL = (
    ROOT
    / "mcp_servers"
    / "cypher"
    / "src"
    / "digital_brain_mcp_cypher"
    / "quality_control_api.py"
)
SERVER_PY = (
    ROOT
    / "mcp_servers"
    / "cypher"
    / "src"
    / "digital_brain_mcp_cypher"
    / "server.py"
)


def _read(path: pathlib.Path) -> str:
    assert path.is_file(), f"missing required file: {path}"
    return path.read_text(encoding="utf-8")


def test_session_skill_declares_feedback_route():
    text = _read(SESSION_SKILL)
    # Routing enum includes FEEDBACK
    assert re.search(r"`FEEDBACK`", text)
    assert re.search(r"`SKIP`", text)
    assert re.search(r"`READ`", text)
    assert re.search(r"`WRITE`", text)
    # Dedicated FEEDBACK section
    assert "## FEEDBACK Route" in text or "### FEEDBACK" in text or "## FEEDBACK" in text


def test_one_prompt_budget_documented():
    text = _read(SESSION_SKILL)
    assert re.search(
        r"one confirmation prompt|max one confirmation|One confirmation prompt",
        text,
        re.IGNORECASE,
    )


def test_generic_ack_rejection_documented():
    session = _read(SESSION_SKILL)
    # Must reject generic acks as activation
    for token in ("yes", "ok", "👍"):
        assert token in session.lower() or token in session
    assert re.search(r"never activate|never applies|never.*activat", session, re.I)
    # Helper in code agrees
    from digital_brain.maintenance.alias_effects import is_generic_ack

    assert is_generic_ack("yes")
    assert is_generic_ack("ok")
    assert is_generic_ack("👍")
    assert is_generic_ack("sure")
    assert is_generic_ack("go ahead")
    assert not is_generic_ack("APPLY alias:prop-1")
    assert not is_generic_ack("not CarPlace — CarID")


def test_claim_false_propose_only():
    session = _read(SESSION_SKILL)
    assert "claim_false" in session
    assert re.search(r"propose-only|propose only", session, re.I)
    from digital_brain.maintenance.alias_effects import (
        claim_false_may_mutate_life_memory,
        proposal_may_activate_from_prose,
    )

    assert claim_false_may_mutate_life_memory() is False
    assert proposal_may_activate_from_prose() is False


def test_feedback_kinds_and_create_feedback_path():
    session = _read(SESSION_SKILL)
    for kind in ("entity_wrong", "claim_false", "miss", "invent", "praise"):
        assert kind in session
    assert "create_feedback" in session
    assert "praise" in session
    assert re.search(r"counter only|counter-only", session, re.I)


def test_feedback_create_feedback_required_fields_and_enums_documented():
    """Skill embeds exact create_feedback contract so thin hosts skip tools/list."""
    session = _read(SESSION_SKILL)
    prompts = _read(SUBAGENT_PROMPTS)
    combined = session + "\n" + prompts
    for field in ("id", "kind", "sensitivity", "harness_generation_id"):
        assert field in session, f"session skill missing required field {field}"
        assert field in prompts, f"subagent prompts missing required field {field}"
    for kind in ("entity_wrong", "claim_false", "miss", "invent", "praise"):
        assert kind in combined
    for sensitivity in ("public_ops", "personal", "intimate"):
        assert sensitivity in combined
    # Forbidden alias kwargs must be called out so agents stop inventing them.
    for alias in ("summary", "detail"):
        assert alias in session
    assert re.search(r"redacted_summary", session)
    assert "mcp_client.create_feedback" in session or "tools.mcp_client.create_feedback" in session


def test_feedback_mandatory_gotcha_step_and_forbids_journal_as_gotcha():
    session = _read(SESSION_SKILL)
    prompts = _read(SUBAGENT_PROMPTS)
    combined = session + "\n" + prompts
    assert re.search(r"gotcha staged:", combined)
    assert re.search(r"parked:\s*sensor down", combined)
    assert re.search(r"journal-as-gotcha|journal as gotcha|not.*gotcha", combined, re.I)
    assert re.search(
        r"must not.*(?:journal|append_journal)|(?:journal|append_journal).*not.*(?:gotcha|sensor|substitut)",
        combined,
        re.I,
    )
    # Durable quality-plane seed (Feedback / optional RunEvent), not chat-only.
    assert "create_feedback" in combined
    assert re.search(r"record_run_event|task_outcome.*corrected|recurrence_key", combined)
    assert re.search(r"Durable gotcha|gotcha step|gotcha candidate|stage a durable", combined, re.I)


def test_apply_token_is_intent_not_authority():
    session = _read(SESSION_SKILL)
    assert "APPLY alias:" in session
    assert "digital_brain_apply_proposal.py" in session
    from digital_brain.maintenance.alias_effects import parse_apply_token

    assert parse_apply_token("APPLY alias:prop-42") == "prop-42"
    assert parse_apply_token("yes") is None


def test_subagents_forbidden_from_activation():
    for path in (
        SUBAGENT_PROMPTS,
        READ_SKILL,
        WRITE_SKILL,
        READER_AGENT,
        WRITER_AGENT,
        ENTITY_CHECK_AGENT,
    ):
        text = _read(path)
        # Must mention not creating/activating Alias or authority
        assert re.search(
            r"Alias|ActivationAuthority|never create|operator-only|propose-only",
            text,
            re.I,
        ), f"{path} must document activation boundary"


def test_graph_skill_and_runtime_patterns_scoped_alias():
    graph = _read(GRAPH_SKILL)
    patterns = _read(RUNTIME_PATTERNS)
    assert re.search(r"scoped|namespace|normalized", graph + patterns, re.I)
    assert re.search(r"operator-only|operator only", graph + patterns, re.I)
    assert "Alias→Alias" in patterns or "Alias-to-Alias" in patterns or "never Alias" in (
        graph + patterns
    )


def test_entity_resolver_is_scoped_and_active_aware():
    src = _read(ENTITY_RESOLVER)
    assert "normalized_from" in src or "normalized" in src
    assert "status" in src
    assert "canonical_id" in src
    assert "ORDER BY" in src
    # Strict scoped path requires non-null scope fields (fail-closed).
    assert "a.namespace IS NOT NULL" in src
    assert "a.entity_type IS NOT NULL" in src
    assert "a.normalized_from IS NOT NULL" in src
    # Legacy fallback is env-gated, not primary path.
    assert "DIGITAL_BRAIN_ALIAS_LEGACY_LOOKUP" in src
    # Must not use soft entity_type IS NULL OR match as primary path.
    assert "a.entity_type IS NULL OR a.entity_type" not in src
    tree = ast.parse(src)
    assert tree is not None


def test_operator_script_has_no_yes_flag():
    src = _read(APPLY_SCRIPT)
    # Explicit refusal of unattended flags
    assert "--yes" in src  # mentioned as forbidden
    assert "no unattended" in src.lower() or "no unattended apply" in src.lower()
    # argparse must not register --yes as a real option
    assert "add_argument(\"--yes\"" not in src
    assert "add_argument('--yes'" not in src
    assert "add_argument(\"-y\"" not in src


def test_activation_not_on_model_facing_mcp_or_coordinator_ops():
    api = _read(QUALITY_CONTROL)
    # Forbidden tool names include activation surfaces
    for name in (
        "activate_alias",
        "apply_alias",
        "mint_activation_authority",
        "revoke_alias",
    ):
        assert name in api
        assert "COORDINATOR_FORBIDDEN_MCP_TOOL_NAMES" in api
    # These must NOT be in COORDINATOR_OPERATIONS set
    # Parse the frozenset roughly
    ops_match = re.search(
        r"COORDINATOR_OPERATIONS: frozenset\[str\] = frozenset\(\s*\{([^}]+)\}",
        api,
        re.S,
    )
    assert ops_match, "COORDINATOR_OPERATIONS not found"
    ops_body = ops_match.group(1)
    for forbidden in (
        "activate_alias",
        "apply_alias",
        "mint_activation_authority",
        "revoke_alias",
        "consume_activation_authority",
    ):
        assert f'"{forbidden}"' not in ops_body
        assert f"'{forbidden}'" not in ops_body

    server = _read(SERVER_PY)
    # server should not register apply_alias as a tool name string in tool defs
    assert "@mcp.tool" in server or "tool(" in server
    assert "def apply_alias" not in server
    assert "def mint_activation_authority" not in server


def test_alias_effects_module_exports_operator_store():
    src = _read(ALIAS_EFFECTS)
    assert "class AliasEffectStore" in src
    assert "mint_activation_authority" in src
    assert "get_authority_receipt" in src
    assert "apply_alias" in src
    assert "revoke_alias" in src
    assert "set_entity_protection" in src
    assert "audit_aliases" in src
    assert "compensat" in src.lower() or "revoked" in src
