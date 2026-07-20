"""Static plugin package contract tests for v0.4 initiate + maintenance surface.

Locks:
- version sync across host/cache manifests (incl. .agents marketplace)
- initiate protocol surface (status helper, protocol ref, session gate, SOUL overlay)
- INITIATE gate coexists with FEEDBACK / harness pin
- maintenance skill + maintainer agent presence and tool allowlists
- skill never directs unattended identity/policy/overlay/code/SOUL/journal
- exact-token APPLY intent is not authorization
- Codex yaml is not claimed as a hard tool boundary
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "digital-brain-buddy"
VERSION_JSON = PLUGIN / "version.json"
CLAUDE_PLUGIN = PLUGIN / ".claude-plugin" / "plugin.json"
CODEX_PLUGIN = PLUGIN / ".codex-plugin" / "plugin.json"
CLAUDE_MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
AGENTS_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"
MAINT_SKILL = (
    PLUGIN / "skills" / "digital-brain-buddy-maintenance" / "SKILL.md"
)
MAINT_YAML = (
    PLUGIN
    / "skills"
    / "digital-brain-buddy-maintenance"
    / "agents"
    / "openai.yaml"
)
QC_CONTRACT = (
    PLUGIN
    / "skills"
    / "digital-brain-buddy-maintenance"
    / "references"
    / "quality-control-contract.md"
)
MAINTAINER_AGENT = PLUGIN / "agents" / "digital-brain-maintainer.md"
DREAM_CMD = PLUGIN / "commands" / "digital-brain-dream.md"
SESSION_SKILL = (
    PLUGIN / "skills" / "digital-brain-buddy-session" / "SKILL.md"
)
READ_SKILL = PLUGIN / "skills" / "digital-brain-buddy-read-memory" / "SKILL.md"
WRITE_SKILL = PLUGIN / "skills" / "digital-brain-buddy-write-memory" / "SKILL.md"
CHANGELOG = PLUGIN / "CHANGELOG.md"
INITIATE_PROTOCOL = (
    PLUGIN
    / "skills"
    / "digital-brain-buddy-session"
    / "references"
    / "initiate-protocol.md"
)
INITIATION_STATUS = PLUGIN / "scripts" / "initiation_status.py"
SOUL_TEMPLATE = PLUGIN / "assets" / "SOUL.template.md"


def _read(path: pathlib.Path) -> str:
    assert path.is_file(), f"missing required file: {path}"
    return path.read_text(encoding="utf-8")


def _load_json(path: pathlib.Path) -> object:
    assert path.is_file(), f"missing required json: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_version_is_0_6_1():
    raw = _load_json(VERSION_JSON)
    assert raw == "0.6.1"


def test_host_manifests_share_base_version():
    base = _load_json(VERSION_JSON)
    assert isinstance(base, str)

    claude = _load_json(CLAUDE_PLUGIN)
    assert claude["version"] == base

    codex = _load_json(CODEX_PLUGIN)
    codex_ver = codex["version"]
    assert codex_ver == base or codex_ver.startswith(f"{base}+codex.")
    # Fresh Codex cache suffix required for 0.3 contract change
    assert re.match(rf"^{re.escape(base)}\+codex\.\d{{14}}$", codex_ver), (
        f"expected fresh BASE+codex.YYYYMMDDHHMMSS, got {codex_ver!r}"
    )

    market = _load_json(CLAUDE_MARKETPLACE)
    entry = next(
        p for p in market["plugins"] if p["name"] == "digital-brain-buddy"
    )
    assert entry["version"] == base

    agents_market = _load_json(AGENTS_MARKETPLACE)
    agents_entry = next(
        p
        for p in agents_market["plugins"]
        if p["name"] == "digital-brain-buddy"
    )
    assert agents_entry["version"] == base


def test_maintenance_surface_files_exist():
    for path in (
        MAINT_SKILL,
        MAINT_YAML,
        QC_CONTRACT,
        MAINTAINER_AGENT,
        DREAM_CMD,
    ):
        assert path.is_file(), path


def test_changelog_mentions_0_5_0():
    text = _read(CHANGELOG)
    assert "## 0.6.1" in text
    assert "## 0.6.0" in text
    assert "## 0.5.0" in text
    assert "## 0.4.0" in text
    assert "## 0.3.0" in text
    assert "gotcha" in text.lower() or "FEEDBACK" in text
    assert "maintenance" in text.lower() or "DreamRun" in text
    assert "384M" in text or "OOM" in text or "#23" in text


def test_maintainer_agent_omits_bash_edit_write_activation():
    text = _read(MAINTAINER_AGENT)
    # Frontmatter tools allowlist
    tools_match = re.search(
        r"^tools:\s*(.+)$", text, re.MULTILINE
    )
    assert tools_match, "maintainer agent must declare tools frontmatter"
    tools_line = tools_match.group(1).strip()
    # Parse simple CSV or JSON list
    if tools_line.startswith("["):
        tools = {t.strip(" \"'") for t in tools_line.strip("[]").split(",")}
    else:
        tools = {t.strip() for t in tools_line.split(",") if t.strip()}
    assert "Read" in tools
    assert "Grep" in tools or "Glob" in tools
    for forbidden in ("Bash", "Edit", "Write", "NotebookEdit"):
        assert forbidden not in tools, f"{forbidden} must be omitted from tools"
    # Body must also forbid activation surfaces
    assert re.search(r"no Bash|Omit.*Bash|without Bash", text, re.I) or (
        "Bash" in text and re.search(r"omit|must not|never", text, re.I)
    )
    for surface in (
        "apply_alias",
        "activate_overlay",
        "mint_activation_authority",
        "ActivationAuthority",
    ):
        assert surface in text


def test_maintainer_allowlist_matches_runner():
    from digital_brain.maintenance.runner import (
        MAINTAINER_ALLOWED_OPERATIONS,
        MAINTAINER_FORBIDDEN_OPERATIONS,
        assert_no_activation_capability,
        maintainer_tool_profile,
    )

    skill = _read(MAINT_SKILL)
    contract = _read(QC_CONTRACT)
    combined = skill + "\n" + contract
    for op in sorted(MAINTAINER_ALLOWED_OPERATIONS):
        assert op in combined, f"allowed op {op} missing from skill/contract"
    for op in (
        "apply_alias",
        "activate_alias",
        "mint_activation_authority",
        "activate_overlay",
        "activate_policy",
    ):
        assert op in MAINTAINER_FORBIDDEN_OPERATIONS or op in combined
        assert op in combined

    tools = maintainer_tool_profile(all_tools=True)
    assert tools == MAINTAINER_ALLOWED_OPERATIONS
    assert_no_activation_capability(tools)


def test_skill_never_directs_unattended_identity_or_soul_changes():
    skill = _read(MAINT_SKILL)
    contract = _read(QC_CONTRACT)
    agent = _read(MAINTAINER_AGENT)
    combined = "\n".join([skill, contract, agent])

    # Must explicitly forbid unattended / automatic activation of these
    for forbidden_target in (
        "identity",
        "policy",
        "overlay",
        "SOUL",
        "journal",
    ):
        assert forbidden_target in combined or forbidden_target.lower() in combined.lower()

    assert re.search(
        r"never.*unattended|no unattended|Do not direct unattended|"
        r"never activates|report-only",
        combined,
        re.I,
    )
    # Must not ship an unattended --yes apply recommendation as the path
    assert "unattended `--yes`" in combined or "no unattended" in combined.lower()
    assert re.search(r"--yes", combined)
    # Positive instruction that activation is operator-only / interactive
    assert re.search(r"operator", combined, re.I)
    assert re.search(r"report-only|report only", combined, re.I)

    # Must not instruct models to edit SOUL or write journals as maintenance
    assert not re.search(
        r"(?i)edit\s+SOUL\.MD|rewrite\s+SOUL|append_journal_entry",
        skill,
    )


def test_exact_token_intent_is_not_authorization():
    session = _read(SESSION_SKILL)
    skill = _read(MAINT_SKILL)
    dream = _read(DREAM_CMD)
    combined = "\n".join([session, skill, dream])

    assert "APPLY alias:" in combined
    assert re.search(
        r"intent only|not authorization|not\s+authorization",
        combined,
        re.I,
    )
    assert "digital_brain_apply_proposal.py" in combined

    from digital_brain.maintenance.alias_effects import parse_apply_token

    assert parse_apply_token("APPLY alias:prop-42") == "prop-42"
    assert parse_apply_token("yes") is None
    assert parse_apply_token("ok") is None


def test_codex_yaml_not_hard_boundary():
    yaml_text = _read(MAINT_YAML)
    skill = _read(MAINT_SKILL)
    agent = _read(MAINTAINER_AGENT)
    combined = "\n".join([yaml_text, skill, agent])
    assert re.search(
        r"not a hard|not.*hard per-worker|server-side capability",
        combined,
        re.I,
    )


def test_session_read_write_point_at_maintenance_boundary():
    session = _read(SESSION_SKILL)
    assert "digital-brain-buddy-maintenance" in session
    assert "digital-brain-maintainer" in session

    read = _read(READ_SKILL)
    write = _read(WRITE_SKILL)
    assert re.search(r"maintenance|DreamRun", read + write, re.I)
    assert re.search(
        r"operator|report-only|never.*activat",
        read + write,
        re.I,
    )


def test_mcp_json_is_valid():
    mcp = _load_json(PLUGIN / ".mcp.json")
    assert "mcpServers" in mcp
    assert "digital-brain-neo4j" in mcp["mcpServers"]


def test_initiate_surface_files_exist():
    assert INITIATION_STATUS.is_file()
    assert INITIATE_PROTOCOL.is_file()


def test_session_skill_initiate_gate_and_feedback_coexist():
    session = _read(SESSION_SKILL)
    protocol = _read(INITIATE_PROTOCOL)
    for text in (session, protocol):
        assert "INITIATE" in text
        assert "initiation_complete" in text
    # Gate must not erase 0.3 FEEDBACK surface
    assert re.search(r"`FEEDBACK`", session)
    assert "## FEEDBACK Route" in session
    assert "harness_generation_id" in session or "HARNESS_GENERATION" in session
    # Explicit coexistence after INITIATE handoff wording
    combined = session + "\n" + protocol
    assert re.search(
        r"FEEDBACK remains|does not suppress FEEDBACK|"
        r"Does \*\*not\*\* suppress FEEDBACK|SKIP / READ / WRITE / FEEDBACK",
        combined,
        re.I,
    )


def test_soul_template_has_user_overlay():
    text = _read(SOUL_TEMPLATE)
    assert "## User overlay" in text
    assert "Preferred language" in text


def test_bootstrap_requires_initiation_evidence():
    read = _read(READ_SKILL)
    assert "initiation_evidence" in read
    assert "initiation_complete" in read
