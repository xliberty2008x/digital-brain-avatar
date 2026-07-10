"""Deterministic compiler: ChangeIntent → quarantined overlay artifact.

Early versions render typed additive rules into named extension slots from
repository-owned templates. The model does not emit arbitrary deployable
Markdown. Core skill/code diffs route to the engineering lane until separately
approved. Compilation never writes plugin load paths or active-overlays.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from digital_brain.maintenance.analyzer import (
    ChangeIntent,
    assert_no_injection,
    contains_injection,
)
from digital_brain.maintenance.artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    COMPILER_VERSION_DEFAULT,
    QuarantineBundle,
    build_manifest,
    write_quarantine_bundle,
)
from digital_brain.maintenance.models import digest_bytes, digest_text
from digital_brain.maintenance.overlay_rules import (
    MAX_ARTIFACT_BYTES,
    OverlayRulesError,
    OverlaySlot,
    assert_path_allowed,
    assert_rule_not_locked,
    load_locked_rules,
    load_overlay_slots,
    render_additive_rule_body,
    wrap_in_slot_markers,
)

COMPILER_VERSION = COMPILER_VERSION_DEFAULT
COMPILER_SCHEMA_VERSION = ARTIFACT_SCHEMA_VERSION

# Operations the overlay compiler accepts for behaviour overlays.
OVERLAY_COMPILE_OPERATIONS: frozenset[str] = frozenset({"add_rule"})
# Effect types that compile into quarantine overlays.
OVERLAY_EFFECT_TYPES: frozenset[str] = frozenset({"overlay_rule"})

# Frontmatter / include / tool injection patterns for artifact bodies.
_FORBIDDEN_ARTIFACT_PATTERNS: tuple[str, ...] = (
    "---\n",
    "\n---\n",
    "```yaml",
    "```yml",
    "{% include",
    "{%include",
    "{{ include",
    "#include ",
    "include::",
    "<script",
    "javascript:",
    "run_terminal_command",
    "write_neo4j_cypher",
    "apply_alias",
    "mint_activation_authority",
    "activate_overlay",
)


class CompilerError(ValueError):
    """Raised when a ChangeIntent cannot be compiled into a quarantine artifact."""


class BaseDriftError(CompilerError):
    """Raised when the measured base commit/hashes differ from the declared base."""


class EngineeringLaneError(CompilerError):
    """Raised when engineering/core diffs are presented for overlay compilation."""


@dataclass(frozen=True)
class CompileRequest:
    """Inputs required for a deterministic compile (all explicit for reproducibility)."""

    intent: ChangeIntent
    proposal_id: str
    base_commit: str
    before_hashes: Mapping[str, str]
    measured_base_commit: str | None = None
    measured_before_hashes: Mapping[str, str] | None = None
    evaluation: Mapping[str, Any] | None = None
    existing_rule_ids: frozenset[str] = frozenset()
    expected_plugin_generation: str | None = None
    rollback_ref: str | None = None
    lease_epoch: int | None = None
    run_id: str | None = None
    plugin_root: str | Path | None = None
    metadata_id: str | None = None


@dataclass(frozen=True)
class CompileResult:
    artifact_md: str
    manifest: dict[str, Any]
    intent_payload: dict[str, Any]
    evaluation: dict[str, Any]
    patch_sha256: str
    target_file: str | None
    extension_slot: str
    rule_id: str
    compiler_version: str
    schema_version: str


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _intent_payload(intent: ChangeIntent) -> dict[str, Any]:
    if hasattr(intent, "model_dump"):
        return dict(intent.model_dump())
    return dict(intent)  # type: ignore[arg-type]


def _assert_no_artifact_injection(text: str) -> None:
    assert_no_injection(text, field="artifact_md")
    lowered = text.lower()
    for pat in _FORBIDDEN_ARTIFACT_PATTERNS:
        if pat.lower() in lowered:
            raise CompilerError(f"artifact_injection_or_frontmatter:{pat!r}")
    # YAML frontmatter at start.
    stripped = text.lstrip()
    if stripped.startswith("---"):
        raise CompilerError("artifact_yaml_frontmatter_forbidden")


def _assert_overlay_eligible(intent: ChangeIntent) -> None:
    if intent.lane == "engineering" or intent.effect_type in {
        "engineering_patch",
        "file_issue",
        "infra_note",
        "test_gap",
    }:
        raise EngineeringLaneError(
            "engineering_lane_requires_separate_approval:"
            f"{intent.lane}:{intent.effect_type}"
        )
    if intent.effect_type not in OVERLAY_EFFECT_TYPES:
        raise CompilerError(f"effect_type_not_compilable:{intent.effect_type}")
    if intent.operation not in OVERLAY_COMPILE_OPERATIONS:
        if intent.operation in {"revise_rule", "delete", "remove_rule"}:
            raise CompilerError(f"operation_forbidden:{intent.operation}")
        raise CompilerError(f"operation_not_compilable:{intent.operation}")
    if intent.lane != "behaviour":
        raise CompilerError(f"lane_not_compilable:{intent.lane}")
    if not intent.extension_slot:
        raise CompilerError("extension_slot_required")
    if intent.extension_slot == "engineering_note":
        raise EngineeringLaneError("engineering_note_not_overlay_deployable")


def _check_base_drift(
    *,
    base_commit: str,
    before_hashes: Mapping[str, str],
    measured_base_commit: str | None,
    measured_before_hashes: Mapping[str, str] | None,
) -> None:
    """Stop on base drift — never rebase or three-way merge."""
    if measured_base_commit is not None and measured_base_commit != base_commit:
        raise BaseDriftError(
            f"base_commit_drift:declared={base_commit}:measured={measured_base_commit}"
        )
    if measured_before_hashes is None:
        return
    declared = {str(k): str(v) for k, v in before_hashes.items()}
    measured = {str(k): str(v) for k, v in measured_before_hashes.items()}
    # Every declared path must match measured; extra measured keys are ok only
    # if not in declared (we only care about target-file before hashes).
    for path, digest in declared.items():
        if path not in measured:
            raise BaseDriftError(f"base_hash_missing:{path}")
        if measured[path] != digest:
            raise BaseDriftError(
                f"base_hash_drift:{path}:declared={digest}:measured={measured[path]}"
            )


def _resolve_slot(
    intent: ChangeIntent,
    *,
    plugin_root: str | Path | None,
) -> OverlaySlot:
    root = str(plugin_root) if plugin_root is not None else None
    registry = load_overlay_slots(root)
    assert intent.extension_slot is not None
    slot = registry.require_known(intent.extension_slot)
    if intent.operation not in slot.operations:
        raise CompilerError(
            f"operation_not_allowed_for_slot:{intent.operation}:{slot.id}"
        )
    # Target skill consistency when both declared.
    if (
        intent.target_skill
        and slot.target_skill
        and intent.target_skill != slot.target_skill
    ):
        raise CompilerError(
            f"target_skill_mismatch:{intent.target_skill}:{slot.target_skill}"
        )
    return slot


def _assert_no_conflicts(
    *,
    rule_id: str,
    existing_rule_ids: frozenset[str],
    plugin_root: str | Path | None,
) -> None:
    root = str(plugin_root) if plugin_root is not None else None
    locked = load_locked_rules(root)
    assert_rule_not_locked(rule_id, locked)
    if rule_id in existing_rule_ids:
        raise CompilerError(f"rule_id_conflict:{rule_id}")


def compile_change_intent(request: CompileRequest) -> CompileResult:
    """Compile a typed ChangeIntent into deterministic overlay artifact content.

    Pure with respect to the filesystem except reading repository-owned slot
    registries. Does not write quarantine — use :func:`compile_to_quarantine`.
    """
    intent = request.intent
    if not isinstance(intent, ChangeIntent):
        intent = ChangeIntent.model_validate(intent)

    _assert_overlay_eligible(intent)
    _check_base_drift(
        base_commit=request.base_commit,
        before_hashes=request.before_hashes,
        measured_base_commit=request.measured_base_commit,
        measured_before_hashes=request.measured_before_hashes,
    )

    plugin_root = request.plugin_root
    slot = _resolve_slot(intent, plugin_root=plugin_root)
    _assert_no_conflicts(
        rule_id=intent.rule_id,
        existing_rule_ids=request.existing_rule_ids,
        plugin_root=plugin_root,
    )

    locked = load_locked_rules(str(plugin_root) if plugin_root else None)
    target_file = slot.target_file
    if intent.target_ref and intent.target_ref.startswith("path:"):
        # Explicit path targets are rejected unless they match the slot file.
        ref_path = intent.target_ref[len("path:") :]
        assert_path_allowed(ref_path, locked)
        if target_file and ref_path != target_file:
            raise CompilerError(f"target_file_mismatch:{ref_path}:{target_file}")
    assert_path_allowed(target_file, locked)

    # Before hashes must include the exact target file when known.
    before_hashes = {str(k): str(v) for k, v in request.before_hashes.items()}
    if target_file and target_file not in before_hashes:
        raise CompilerError(f"before_hash_required_for_target:{target_file}")

    body = render_additive_rule_body(
        rule_id=intent.rule_id,
        summary=intent.summary,
        expected_outcome=intent.expected_outcome,
        extension_slot=slot.id,
        evidence_ids=intent.evidence_ids,
    )
    artifact_md = wrap_in_slot_markers(slot, body)
    _assert_no_artifact_injection(artifact_md)
    if len(artifact_md.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise CompilerError("artifact_size_overflow")

    intent_payload = _intent_payload(intent)
    evaluation = dict(request.evaluation or {})

    # Deterministic patch digest over intent + artifact + evaluation + binding.
    binding = {
        "base_commit": request.base_commit,
        "before_hashes": dict(sorted(before_hashes.items())),
        "compiler_version": COMPILER_VERSION,
        "extension_slot": slot.id,
        "proposal_id": request.proposal_id,
        "rule_id": intent.rule_id,
        "schema_version": COMPILER_SCHEMA_VERSION,
        "target_file": target_file,
        "target_skill": intent.target_skill or slot.target_skill,
    }
    patch_sha256 = digest_text(
        _canonical_json(
            {
                "artifact_md": digest_bytes(artifact_md.encode("utf-8")),
                "binding": binding,
                "evaluation": evaluation,
                "intent": intent_payload,
            }
        )
    )

    manifest = build_manifest(
        proposal_id=request.proposal_id,
        dream_id=intent.dream_id,
        evidence_snapshot_id=intent.snapshot_id,
        target_skill=intent.target_skill or slot.target_skill,
        extension_slot=slot.id,
        rule_id=intent.rule_id,
        base_commit=request.base_commit,
        before_hashes=before_hashes,
        target_file=target_file,
        compiler_version=COMPILER_VERSION,
        schema_version=COMPILER_SCHEMA_VERSION,
        patch_sha256=patch_sha256,
        artifact_relpath=f"dreams/quarantine/{intent.dream_id}/{request.proposal_id}/artifact.md",
        expected_plugin_generation=request.expected_plugin_generation,
        rollback_ref=request.rollback_ref,
        target_path_allowlist=[target_file] if target_file else [],
        lease_epoch=request.lease_epoch,
        run_id=request.run_id,
    )

    return CompileResult(
        artifact_md=artifact_md,
        manifest=manifest,
        intent_payload=intent_payload,
        evaluation=evaluation,
        patch_sha256=patch_sha256,
        target_file=target_file,
        extension_slot=slot.id,
        rule_id=intent.rule_id,
        compiler_version=COMPILER_VERSION,
        schema_version=COMPILER_SCHEMA_VERSION,
    )


def compile_to_quarantine(
    request: CompileRequest,
    *,
    state_dir: str | Path,
    repo_root: str | Path | None = None,
) -> QuarantineBundle:
    """Compile and write an immutable quarantine bundle under the secure state dir."""
    result = compile_change_intent(request)
    return write_quarantine_bundle(
        state_dir=state_dir,
        dream_id=request.intent.dream_id,
        proposal_id=request.proposal_id,
        intent=result.intent_payload,
        artifact_md=result.artifact_md,
        manifest=result.manifest,
        evaluation=result.evaluation,
        metadata_id=request.metadata_id,
        repo_root=repo_root,
    )


def measure_target_before_hashes(
    *,
    plugin_root: str | Path,
    target_files: Sequence[str],
) -> dict[str, str]:
    """Hash exact target files under the plugin root (no symlink follow for safety)."""
    root = Path(plugin_root).expanduser().resolve()
    out: dict[str, str] = {}
    for rel in target_files:
        assert_path_allowed(rel)
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CompilerError(f"path_traversal_forbidden:{rel}") from exc
        if path.is_symlink():
            raise CompilerError(f"symlink_target_forbidden:{rel}")
        if not path.is_file():
            # Missing file still gets an empty digest so drift can be detected.
            out[rel] = digest_bytes(b"")
            continue
        out[rel] = digest_bytes(path.read_bytes())
    return out
