"""Regression gates for issue #21 Compose URL isolation and stack recovery."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
LAUNCHER = ROOT / "plugins" / "digital-brain-buddy" / "scripts" / "compose-up.sh"


def _compose_config(**overrides: str) -> dict[str, object]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is unavailable")
    version = subprocess.run(
        [docker, "compose", "version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if version.returncode != 0:
        pytest.skip("docker compose is unavailable")

    env = os.environ.copy()
    env.pop("MCP_OLLAMA_BASE_URL", None)
    env.update(overrides)
    rendered = subprocess.run(
        [docker, "compose", "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    return json.loads(rendered.stdout)


def _run_launcher(
    tmp_path: pathlib.Path,
    *,
    cwd: pathlib.Path,
    script: pathlib.Path = LAUNCHER,
    env_overrides: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
if [ "$1" = "compose" ] && [ "$2" = "version" ]; then
  exit 0
fi
if [ "$1" = "info" ]; then
  pwd > "$FAKE_DOCKER_CWD_FILE"
  echo 1
  exit 0
fi
exit 97
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    cwd_record = tmp_path / "docker-cwd.txt"
    env = os.environ.copy()
    env.pop("DIGITAL_BRAIN_PROJECT_DIR", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_DOCKER_CWD_FILE"] = str(cwd_record)
    if env_overrides:
        env.update(env_overrides)

    completed = subprocess.run(
        ["bash", str(script)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, cwd_record


def test_compose_template_uses_container_only_ollama_override() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert 'OLLAMA_BASE_URL: "${MCP_OLLAMA_BASE_URL:-http://ollama:11434}"' in text
    assert 'OLLAMA_BASE_URL: "${OLLAMA_BASE_URL:-' not in text


def test_host_ollama_url_cannot_leak_into_mcp_container() -> None:
    rendered = _compose_config(OLLAMA_BASE_URL="http://localhost:11434")
    environment = rendered["services"]["mcp-cypher"]["environment"]
    assert environment["OLLAMA_BASE_URL"] == "http://ollama:11434"
    assert "MCP_OLLAMA_BASE_URL" not in environment


def test_explicit_mcp_ollama_override_maps_to_application_key() -> None:
    rendered = _compose_config(
        OLLAMA_BASE_URL="http://localhost:11434",
        MCP_OLLAMA_BASE_URL="http://host.docker.internal:11434",
    )
    environment = rendered["services"]["mcp-cypher"]["environment"]
    assert environment["OLLAMA_BASE_URL"] == "http://host.docker.internal:11434"
    assert "MCP_OLLAMA_BASE_URL" not in environment


def test_launcher_resolves_repo_upwards_from_workspace_subdirectory(tmp_path: pathlib.Path) -> None:
    result, cwd_record = _run_launcher(
        tmp_path,
        cwd=ROOT / "plugins" / "digital-brain-buddy",
    )
    assert result.returncode == 0
    assert cwd_record.read_text(encoding="utf-8").strip() == str(ROOT.resolve())
    assert "CLAUDE_PROJECT_DIR not set" not in result.stderr


def test_digital_project_dir_precedes_legacy_claude_and_handles_spaces(tmp_path: pathlib.Path) -> None:
    spaced_link = tmp_path / "avatar repo with spaces"
    spaced_link.symlink_to(ROOT, target_is_directory=True)
    result, cwd_record = _run_launcher(
        tmp_path,
        cwd=tmp_path,
        env_overrides={
            "DIGITAL_BRAIN_PROJECT_DIR": str(spaced_link),
            "CLAUDE_PROJECT_DIR": str(tmp_path / "invalid-claude-root"),
        },
    )
    assert result.returncode == 0
    assert cwd_record.read_text(encoding="utf-8").strip() == str(ROOT.resolve())


def test_legacy_claude_project_dir_remains_supported(tmp_path: pathlib.Path) -> None:
    result, cwd_record = _run_launcher(
        tmp_path,
        cwd=tmp_path,
        env_overrides={"CLAUDE_PROJECT_DIR": str(ROOT)},
    )
    assert result.returncode == 0
    assert cwd_record.read_text(encoding="utf-8").strip() == str(ROOT.resolve())


def test_explicit_unrelated_compose_root_is_rejected(tmp_path: pathlib.Path) -> None:
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "docker-compose.yml").write_text(
        "services:\n  neo4j: {}\n  ollama: {}\n  mcp-cypher: {}\n",
        encoding="utf-8",
    )
    (unrelated / "pyproject.toml").write_text(
        '[project]\nname = "something-else"\n', encoding="utf-8"
    )
    result, cwd_record = _run_launcher(
        tmp_path,
        cwd=tmp_path,
        env_overrides={
            "DIGITAL_BRAIN_PROJECT_DIR": str(unrelated),
            "CLAUDE_PROJECT_DIR": str(ROOT),
        },
    )
    assert result.returncode == 0
    assert not cwd_record.exists()
    assert "could not resolve a validated avatar_digital_brain checkout" in result.stderr


def test_cached_plugin_script_without_checkout_fails_safely(tmp_path: pathlib.Path) -> None:
    cache_dir = tmp_path / "plugin-cache" / "scripts"
    cache_dir.mkdir(parents=True)
    cached_script = cache_dir / "compose-up.sh"
    shutil.copy2(LAUNCHER, cached_script)

    result, cwd_record = _run_launcher(
        tmp_path,
        cwd=tmp_path,
        script=cached_script,
    )
    assert result.returncode == 0
    assert not cwd_record.exists()
    assert "could not resolve a validated avatar_digital_brain checkout" in result.stderr
