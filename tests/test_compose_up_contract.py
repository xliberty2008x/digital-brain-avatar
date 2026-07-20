"""Regression gates for Compose URL isolation, Neo4j memory budget, and stack recovery."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
LAUNCHER = ROOT / "plugins" / "digital-brain-buddy" / "scripts" / "compose-up.sh"
ENV_EXAMPLE = ROOT / ".env.example"
README = ROOT / "README.md"
CYPHER_README = ROOT / "mcp_servers" / "cypher" / "README.md"

# 8 GiB — above the launcher's 6 GiB floor.
_FAKE_MEM_OK = str(8 * 1024 * 1024 * 1024)
# ~5.787 GiB — issue #23 observed Desktop total (below the 6 GiB floor).
_FAKE_MEM_LOW = str(int(5.787 * 1024 * 1024 * 1024))

_RECOVERY_MARKERS = (
    "recovery recipe",
    "NEO4J_HEAP_INITIAL_SIZE=384M",
    "NEO4J_HEAP_MAX_SIZE=768M",
    "NEO4J_PAGECACHE_SIZE=384M",
    "OLLAMA_PORT=11435",
    "compose-up.sh",
)


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
    # Drop host overrides so template defaults are visible unless set below.
    for key in (
        "NEO4J_HEAP_INITIAL_SIZE",
        "NEO4J_HEAP_MAX_SIZE",
        "NEO4J_PAGECACHE_SIZE",
        "OLLAMA_PORT",
    ):
        env.pop(key, None)
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


def _write_fake_docker(
    fake_bin: pathlib.Path,
    *,
    mem_total: str = "1",
    scenario: str = "info_only",
) -> pathlib.Path:
    """Install a fake `docker` that drives launcher branches without a real daemon."""
    fake_bin.mkdir(exist_ok=True)
    fake_docker = fake_bin / "docker"
    log_file = fake_bin / "docker-calls.log"

    if scenario == "info_only":
        body = textwrap.dedent(
            f"""\
            #!/bin/sh
            echo "$@" >> "{log_file}"
            if [ "$1" = "compose" ] && [ "$2" = "version" ]; then
              exit 0
            fi
            if [ "$1" = "info" ]; then
              pwd > "$FAKE_DOCKER_CWD_FILE"
              echo {mem_total}
              exit 0
            fi
            exit 97
            """
        )
    elif scenario == "oom_neo4j":
        # Memory OK → port probe (test hook) → compose up → neo4j wait sees OOM.
        body = textwrap.dedent(
            f"""\
            #!/bin/sh
            echo "$@" >> "{log_file}"
            if [ "$1" = "compose" ] && [ "$2" = "version" ]; then
              exit 0
            fi
            if [ "$1" = "info" ]; then
              pwd > "$FAKE_DOCKER_CWD_FILE"
              echo {mem_total}
              exit 0
            fi
            if [ "$1" = "compose" ] && [ "$2" = "up" ]; then
              exit 0
            fi
            if [ "$1" = "compose" ] && [ "$2" = "ps" ]; then
              echo "fake-neo4j-ctr"
              exit 0
            fi
            if [ "$1" = "inspect" ]; then
              # Always report neo4j OOM-killed so wait_for_service_health fails fast.
              case "$*" in
                *Running*) echo false; exit 0 ;;
                *ExitCode*) echo 137; exit 0 ;;
                *OOMKilled*) echo true; exit 0 ;;
                *Health*|*Status*) echo exited; exit 0 ;;
              esac
              echo unknown
              exit 0
            fi
            exit 97
            """
        )
    elif scenario == "compose_up_records_port":
        # Memory OK → port resolve → compose up records OLLAMA_PORT → neo4j
        # never appears so wait exits cleanly without harness pin side effects.
        body = textwrap.dedent(
            f"""\
            #!/bin/sh
            echo "$@" >> "{log_file}"
            if [ "$1" = "compose" ] && [ "$2" = "version" ]; then
              exit 0
            fi
            if [ "$1" = "info" ]; then
              pwd > "$FAKE_DOCKER_CWD_FILE"
              echo {mem_total}
              exit 0
            fi
            if [ "$1" = "compose" ] && [ "$2" = "up" ]; then
              printf '%s\\n' "${{OLLAMA_PORT:-}}" > "$FAKE_DOCKER_OLLAMA_PORT_FILE"
              exit 0
            fi
            if [ "$1" = "compose" ] && [ "$2" = "ps" ]; then
              # No container id → wait_for_service_health times out → warn_and_exit 0.
              exit 0
            fi
            exit 0
            """
        )
    else:
        raise ValueError(f"unknown fake docker scenario: {scenario}")

    fake_docker.write_text(body, encoding="utf-8")
    fake_docker.chmod(0o755)
    return log_file


def _run_launcher(
    tmp_path: pathlib.Path,
    *,
    cwd: pathlib.Path,
    script: pathlib.Path = LAUNCHER,
    env_overrides: dict[str, str] | None = None,
    mem_total: str = "1",
    scenario: str = "info_only",
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    fake_bin = tmp_path / "fake-bin"
    log_file = _write_fake_docker(fake_bin, mem_total=mem_total, scenario=scenario)

    cwd_record = tmp_path / "docker-cwd.txt"
    ollama_port_record = tmp_path / "ollama-port.txt"
    env = os.environ.copy()
    env.pop("DIGITAL_BRAIN_PROJECT_DIR", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("OLLAMA_PORT", None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_DOCKER_CWD_FILE"] = str(cwd_record)
    env["FAKE_DOCKER_OLLAMA_PORT_FILE"] = str(ollama_port_record)
    # Default: no host ports busy; tests override DIGITAL_BRAIN_TEST_PORTS_IN_USE.
    env.setdefault("DIGITAL_BRAIN_TEST_PORT_PROBE", "1")
    env.setdefault("DIGITAL_BRAIN_TEST_PORTS_IN_USE", "")
    # Fast fail paths for wait loops.
    env.setdefault("NEO4J_HEALTH_SLEEP_SECS", "0")
    env.setdefault("NEO4J_HEALTH_MAX_ATTEMPTS", "5")
    env.setdefault("OLLAMA_HEALTH_MAX_ATTEMPTS", "5")
    env.setdefault("MCP_READY_MAX_ATTEMPTS", "5")
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
    return completed, cwd_record, log_file


def _assert_recovery_recipe(text: str) -> None:
    for marker in _RECOVERY_MARKERS:
        assert marker in text, f"missing recovery marker {marker!r} in:\n{text}"


def test_compose_template_uses_container_only_ollama_override() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert 'OLLAMA_BASE_URL: "${MCP_OLLAMA_BASE_URL:-http://ollama:11434}"' in text
    assert 'OLLAMA_BASE_URL: "${OLLAMA_BASE_URL:-' not in text


def test_compose_neo4j_defaults_use_safer_oom_budget() -> None:
    """Issue #23: 512M/1G/512M OOM'd near 6 GiB; defaults must be the safer set."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert 'NEO4J_HEAP_INITIAL_SIZE:-384M' in text
    assert 'NEO4J_HEAP_MAX_SIZE:-768M' in text
    assert 'NEO4J_PAGECACHE_SIZE:-384M' in text
    # Old OOM-prone defaults must not remain as template defaults.
    assert 'NEO4J_HEAP_INITIAL_SIZE:-512M' not in text
    assert 'NEO4J_HEAP_MAX_SIZE:-1G' not in text
    assert 'NEO4J_PAGECACHE_SIZE:-512M' not in text


def test_rendered_compose_neo4j_memory_defaults() -> None:
    rendered = _compose_config()
    environment = rendered["services"]["neo4j"]["environment"]
    assert environment["NEO4J_server_memory_heap_initial__size"] == "384M"
    assert environment["NEO4J_server_memory_heap_max__size"] == "768M"
    assert environment["NEO4J_server_memory_pagecache_size"] == "384M"


def test_rendered_compose_neo4j_memory_overrides_win() -> None:
    rendered = _compose_config(
        NEO4J_HEAP_INITIAL_SIZE="512M",
        NEO4J_HEAP_MAX_SIZE="1G",
        NEO4J_PAGECACHE_SIZE="512M",
    )
    environment = rendered["services"]["neo4j"]["environment"]
    assert environment["NEO4J_server_memory_heap_initial__size"] == "512M"
    assert environment["NEO4J_server_memory_heap_max__size"] == "1G"
    assert environment["NEO4J_server_memory_pagecache_size"] == "512M"


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


def test_launcher_resolves_repo_upwards_from_workspace_subdirectory(
    tmp_path: pathlib.Path,
) -> None:
    result, cwd_record, _ = _run_launcher(
        tmp_path,
        cwd=ROOT / "plugins" / "digital-brain-buddy",
    )
    assert result.returncode == 0
    assert cwd_record.read_text(encoding="utf-8").strip() == str(ROOT.resolve())
    assert "CLAUDE_PROJECT_DIR not set" not in result.stderr


def test_digital_project_dir_precedes_legacy_claude_and_handles_spaces(
    tmp_path: pathlib.Path,
) -> None:
    spaced_link = tmp_path / "avatar repo with spaces"
    spaced_link.symlink_to(ROOT, target_is_directory=True)
    result, cwd_record, _ = _run_launcher(
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
    result, cwd_record, _ = _run_launcher(
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
    result, cwd_record, _ = _run_launcher(
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


def test_cached_plugin_script_without_checkout_fails_safely(
    tmp_path: pathlib.Path,
) -> None:
    cache_dir = tmp_path / "plugin-cache" / "scripts"
    cache_dir.mkdir(parents=True)
    cached_script = cache_dir / "compose-up.sh"
    shutil.copy2(LAUNCHER, cached_script)

    result, cwd_record, _ = _run_launcher(
        tmp_path,
        cwd=tmp_path,
        script=cached_script,
    )
    assert result.returncode == 0
    assert not cwd_record.exists()
    assert "could not resolve a validated avatar_digital_brain checkout" in result.stderr


def test_launcher_low_docker_memory_emits_recovery_recipe_and_refuses(
    tmp_path: pathlib.Path,
) -> None:
    result, cwd_record, log_file = _run_launcher(
        tmp_path,
        cwd=ROOT,
        mem_total=_FAKE_MEM_LOW,
        scenario="info_only",
        env_overrides={"CLAUDE_PROJECT_DIR": str(ROOT)},
    )
    assert result.returncode == 0
    assert cwd_record.exists()
    combined = result.stderr + result.stdout
    assert "MiB available" in combined
    assert "allocate at least 6 GiB" in combined
    assert "ready for writes" not in combined
    _assert_recovery_recipe(combined)
    # Must not have attempted compose up after the memory gate.
    log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    assert "compose up" not in log


def test_launcher_reentry_reuses_existing_compose_ollama_port(
    tmp_path: pathlib.Path,
) -> None:
    """SessionStart re-run must not treat our own publish as a foreign clash."""
    ollama_port_file = tmp_path / "ollama-port.txt"
    result, _, log_file = _run_launcher(
        tmp_path,
        cwd=ROOT,
        mem_total=_FAKE_MEM_OK,
        scenario="compose_up_records_port",
        env_overrides={
            "CLAUDE_PROJECT_DIR": str(ROOT),
            "DIGITAL_BRAIN_TEST_PORT_PROBE": "1",
            # Both default and fallback look "busy" (our stack + host ollama).
            "DIGITAL_BRAIN_TEST_PORTS_IN_USE": "11434,11435",
            # Compose already publishes on 11435 from a prior remap.
            "DIGITAL_BRAIN_TEST_COMPOSE_OLLAMA_PORT": "11435",
            "FAKE_DOCKER_OLLAMA_PORT_FILE": str(ollama_port_file),
        },
    )
    assert result.returncode == 0
    combined = result.stderr + result.stdout
    assert "reusing existing compose Ollama publish on 127.0.0.1:11435" in combined
    assert "already in use" not in combined
    assert "refusing to start ollama" not in combined
    assert ollama_port_file.exists(), "compose up did not record OLLAMA_PORT"
    assert ollama_port_file.read_text(encoding="utf-8").strip() == "11435"
    assert "compose up" in log_file.read_text(encoding="utf-8")


def test_launcher_port_clash_remaps_ollama_port(tmp_path: pathlib.Path) -> None:
    ollama_port_file = tmp_path / "ollama-port.txt"
    result, _, log_file = _run_launcher(
        tmp_path,
        cwd=ROOT,
        mem_total=_FAKE_MEM_OK,
        scenario="compose_up_records_port",
        env_overrides={
            "CLAUDE_PROJECT_DIR": str(ROOT),
            "DIGITAL_BRAIN_TEST_PORT_PROBE": "1",
            "DIGITAL_BRAIN_TEST_PORTS_IN_USE": "11434",
            "FAKE_DOCKER_OLLAMA_PORT_FILE": str(ollama_port_file),
        },
    )
    assert result.returncode == 0
    combined = result.stderr + result.stdout
    assert "publishing compose Ollama on 127.0.0.1:11435" in combined
    assert "http://ollama:11434" in combined
    # Fake docker records the exported OLLAMA_PORT seen by compose up.
    assert ollama_port_file.exists(), "compose up did not record OLLAMA_PORT"
    assert ollama_port_file.read_text(encoding="utf-8").strip() == "11435"
    log = log_file.read_text(encoding="utf-8")
    assert "compose up" in log


def test_launcher_explicit_busy_ollama_port_refuses(tmp_path: pathlib.Path) -> None:
    result, _, log_file = _run_launcher(
        tmp_path,
        cwd=ROOT,
        mem_total=_FAKE_MEM_OK,
        scenario="compose_up_records_port",
        env_overrides={
            "CLAUDE_PROJECT_DIR": str(ROOT),
            "OLLAMA_PORT": "11434",
            "DIGITAL_BRAIN_TEST_PORT_PROBE": "1",
            "DIGITAL_BRAIN_TEST_PORTS_IN_USE": "11434",
        },
    )
    assert result.returncode == 0
    combined = result.stderr + result.stdout
    assert "already in use" in combined
    assert "empty model list" in combined or "compose volume holds bge-m3" in combined
    _assert_recovery_recipe(combined)
    assert "ready for writes" not in combined
    log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    assert "compose up" not in log


def test_launcher_neo4j_oom_exit_emits_recovery_recipe(
    tmp_path: pathlib.Path,
) -> None:
    result, _, _ = _run_launcher(
        tmp_path,
        cwd=ROOT,
        mem_total=_FAKE_MEM_OK,
        scenario="oom_neo4j",
        env_overrides={
            "CLAUDE_PROJECT_DIR": str(ROOT),
            "DIGITAL_BRAIN_TEST_PORT_PROBE": "1",
            "DIGITAL_BRAIN_TEST_PORTS_IN_USE": "",
            "NEO4J_HEALTH_MAX_ATTEMPTS": "5",
            "NEO4J_HEALTH_SLEEP_SECS": "0",
        },
    )
    assert result.returncode == 0
    combined = result.stderr + result.stdout
    assert "exit=137" in combined or "exit 137" in combined
    assert "OOMKilled=true" in combined or "OOM-killed" in combined
    _assert_recovery_recipe(combined)
    assert "ready for writes" not in combined


def test_operator_docs_share_recovery_recipe_and_memory_floor() -> None:
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    cypher = CYPHER_README.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")

    def _has_safer_budget(blob: str) -> bool:
        # Accept env form (384M) or prose form (384 MiB).
        return ("384M" in blob or "384 MiB" in blob) and (
            "768M" in blob or "768 MiB" in blob
        )

    for blob in (env_text, readme, cypher, launcher):
        assert "6 GiB" in blob
        assert _has_safer_budget(blob), f"missing safer Neo4j budget in blob head: {blob[:200]!r}"

    assert "NEO4J_HEAP_INITIAL_SIZE=384M" in env_text
    assert "OLLAMA_PORT" in env_text
    assert "recovery" in readme.lower() or "OOM" in readme
    assert "OLLAMA_PORT=11435" in readme or "OLLAMA_PORT" in readme
    assert "print_recovery_recipe" in launcher
    assert "resolve_ollama_publish_port" in launcher
