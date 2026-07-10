"""Harness and maintenance helpers for quality-plane version pinning.

Pin / session-open path is **stdlib-only** (generation, session, models, privacy)
so host agents can run ``scripts/pin_harness_generation.py`` with bare
``python3`` inside Grok/Claude/Codex without requiring the full dev venv.

Heavier dream/analyzer modules load lazily on attribute access so
``from digital_brain.maintenance.generation import …`` does not pull pydantic.
"""

from __future__ import annotations

from typing import Any

# --- Eager, stdlib-only (session pin / harness open) ---
from .generation import (
    collect_harness_generation,
    get_or_pin_session_generation,
    load_session_pin,
    pin_session_generation,
    resolve_state_dir,
    write_active_harness_pin,
)
from .session import (
    SESSION_HANDLE_SCHEMA_VERSION,
    SessionHandle,
    handle_from_public_dict,
    open_harness_session,
    resolve_handle_for_chat,
)
from .models import (
    HARNESS_SCHEMA_VERSION,
    MAINTENANCE_SCHEMA_VERSION,
    TAXONOMY_VERSION,
    ActivationAuthority,
    Decision,
    DreamRun,
    DreamStageReceipt,
    EvidenceSnapshot,
    Finding,
    HarnessGeneration,
    IllegalTransitionError,
    MaintenanceLease,
    Proposal,
    assert_legal_authority_transition,
    assert_legal_dream_stage_transition,
    assert_legal_owner_status_transition,
    assert_no_absorption_field,
    compute_generation_id,
    generation_request_fingerprint,
    is_legal_dream_stage_transition,
    stage_idempotency_key,
)
from .privacy import (
    CORRELATION_HMAC_KEY_VERSION,
    INTIMATE_FIELD_NAMES,
    REDACTION_POLICY_VERSION,
    IntimateFieldError,
    assert_no_intimate_fields,
    correlation_hmac,
    redact_evidence_record,
    redact_packet,
)

# Lazy submodule attribute → (module_path, attr_name | None for whole-module re-export map)
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    # runner
    "MAINTAINER_ALLOWED_OPERATIONS": (".runner", "MAINTAINER_ALLOWED_OPERATIONS"),
    "MAINTAINER_FORBIDDEN_OPERATIONS": (".runner", "MAINTAINER_FORBIDDEN_OPERATIONS"),
    "DreamRunCheckpoint": (".runner", "DreamRunCheckpoint"),
    "DreamRunResult": (".runner", "DreamRunResult"),
    "DreamRunner": (".runner", "DreamRunner"),
    "assert_no_activation_capability": (".runner", "assert_no_activation_capability"),
    "maintainer_tool_profile": (".runner", "maintainer_tool_profile"),
    "run_report_only_dream": (".runner", "run_report_only_dream"),
    # retention
    "BACKUP_RETENTION_LIMITATION": (".retention", "BACKUP_RETENTION_LIMITATION"),
    "RETENTION_ACTIONS": (".retention", "RETENTION_ACTIONS"),
    "RETENTION_SCHEMA_VERSION": (".retention", "RETENTION_SCHEMA_VERSION"),
    "RetentionConfig": (".retention", "RetentionConfig"),
    "RetentionPlan": (".retention", "RetentionPlan"),
    "assert_apply_permitted": (".retention", "assert_apply_permitted"),
    "compute_retention_config_digest": (".retention", "compute_retention_config_digest"),
    "default_demo_config": (".retention", "default_demo_config"),
    "load_retention_config": (".retention", "load_retention_config"),
    "select_retention_candidates": (".retention", "select_retention_candidates"),
    # snapshot
    "EvidenceItem": (".snapshot", "EvidenceItem"),
    "FrozenSnapshot": (".snapshot", "FrozenSnapshot"),
    "SnapshotPolicy": (".snapshot", "SnapshotPolicy"),
    "compute_source_ids_digest": (".snapshot", "compute_source_ids_digest"),
    "freeze_snapshot": (".snapshot", "freeze_snapshot"),
    "load_evidence_fixture": (".snapshot", "load_evidence_fixture"),
    # analyzer
    "ANALYZER_VERSION": (".analyzer", "ANALYZER_VERSION"),
    "ChangeIntent": (".analyzer", "ChangeIntent"),
    "SanitizedEvidenceSnapshot": (".analyzer", "SanitizedEvidenceSnapshot"),
    "analyze": (".analyzer", "analyze"),
    "AnalyzerFinding": (".analyzer", "Finding"),
    # evaluation
    "EVALUATOR_VERSION": (".evaluation", "EVALUATOR_VERSION"),
    "EvaluationGateError": (".evaluation", "EvaluationGateError"),
    "assert_evaluation_present_for_transition": (
        ".evaluation",
        "assert_evaluation_present_for_transition",
    ),
    "evaluate": (".evaluation", "evaluate"),
    # invariants
    "INVARIANT_CATEGORIES": (".invariants", "INVARIANT_CATEGORIES"),
    "InvariantScenario": (".invariants", "InvariantScenario"),
    "default_scenarios": (".invariants", "default_scenarios"),
    "load_scenarios": (".invariants", "load_scenarios"),
    # artifacts
    "ARTIFACT_SCHEMA_VERSION": (".artifacts", "ARTIFACT_SCHEMA_VERSION"),
    "DEFAULT_ISOLATED_VALIDATION_COMMANDS": (
        ".artifacts",
        "DEFAULT_ISOLATED_VALIDATION_COMMANDS",
    ),
    "ISOLATED_VALIDATION_COMMANDS": (".artifacts", "ISOLATED_VALIDATION_COMMANDS"),
    "IsolatedValidationError": (".artifacts", "IsolatedValidationError"),
    "IsolatedValidationResult": (".artifacts", "IsolatedValidationResult"),
    "QuarantineBundle": (".artifacts", "QuarantineBundle"),
    "ValidationCommandError": (".artifacts", "ValidationCommandError"),
    "compute_patch_sha256": (".artifacts", "compute_patch_sha256"),
    "resolve_secure_state_dir": (".artifacts", "resolve_secure_state_dir"),
    "validate_quarantine_isolated": (".artifacts", "validate_quarantine_isolated"),
    "write_quarantine_bundle": (".artifacts", "write_quarantine_bundle"),
    # compiler
    "COMPILER_VERSION": (".compiler", "COMPILER_VERSION"),
    "BaseDriftError": (".compiler", "BaseDriftError"),
    "CompileRequest": (".compiler", "CompileRequest"),
    "CompileResult": (".compiler", "CompileResult"),
    "CompilerError": (".compiler", "CompilerError"),
    "EngineeringLaneError": (".compiler", "EngineeringLaneError"),
    "compile_change_intent": (".compiler", "compile_change_intent"),
    "compile_to_quarantine": (".compiler", "compile_to_quarantine"),
    # overlay_rules
    "load_locked_rules": (".overlay_rules", "load_locked_rules"),
    "load_overlay_slots": (".overlay_rules", "load_overlay_slots"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = target
    from importlib import import_module

    mod = import_module(module_path, __name__)
    value = getattr(mod, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__))


__all__ = [
    "ANALYZER_VERSION",
    "ARTIFACT_SCHEMA_VERSION",
    "BACKUP_RETENTION_LIMITATION",
    "COMPILER_VERSION",
    "CORRELATION_HMAC_KEY_VERSION",
    "DEFAULT_ISOLATED_VALIDATION_COMMANDS",
    "EVALUATOR_VERSION",
    "HARNESS_SCHEMA_VERSION",
    "INTIMATE_FIELD_NAMES",
    "INVARIANT_CATEGORIES",
    "ISOLATED_VALIDATION_COMMANDS",
    "MAINTENANCE_SCHEMA_VERSION",
    "MAINTAINER_ALLOWED_OPERATIONS",
    "MAINTAINER_FORBIDDEN_OPERATIONS",
    "REDACTION_POLICY_VERSION",
    "RETENTION_ACTIONS",
    "RETENTION_SCHEMA_VERSION",
    "TAXONOMY_VERSION",
    "ActivationAuthority",
    "AnalyzerFinding",
    "BaseDriftError",
    "ChangeIntent",
    "CompileRequest",
    "CompileResult",
    "CompilerError",
    "Decision",
    "DreamRun",
    "DreamRunCheckpoint",
    "DreamRunResult",
    "DreamRunner",
    "DreamStageReceipt",
    "EngineeringLaneError",
    "EvaluationGateError",
    "EvidenceItem",
    "EvidenceSnapshot",
    "Finding",
    "FrozenSnapshot",
    "HarnessGeneration",
    "IllegalTransitionError",
    "IntimateFieldError",
    "InvariantScenario",
    "IsolatedValidationError",
    "IsolatedValidationResult",
    "MaintenanceLease",
    "Proposal",
    "QuarantineBundle",
    "RetentionConfig",
    "RetentionPlan",
    "SanitizedEvidenceSnapshot",
    "SnapshotPolicy",
    "ValidationCommandError",
    "analyze",
    "assert_apply_permitted",
    "assert_evaluation_present_for_transition",
    "assert_legal_authority_transition",
    "assert_legal_dream_stage_transition",
    "assert_legal_owner_status_transition",
    "assert_no_absorption_field",
    "assert_no_activation_capability",
    "assert_no_intimate_fields",
    "collect_harness_generation",
    "compile_change_intent",
    "compile_to_quarantine",
    "compute_generation_id",
    "compute_patch_sha256",
    "compute_retention_config_digest",
    "compute_source_ids_digest",
    "correlation_hmac",
    "default_demo_config",
    "default_scenarios",
    "evaluate",
    "freeze_snapshot",
    "generation_request_fingerprint",
    "get_or_pin_session_generation",
    "is_legal_dream_stage_transition",
    "load_evidence_fixture",
    "load_locked_rules",
    "load_overlay_slots",
    "load_retention_config",
    "load_scenarios",
    "load_session_pin",
    "maintainer_tool_profile",
    "open_harness_session",
    "pin_session_generation",
    "redact_evidence_record",
    "redact_packet",
    "resolve_handle_for_chat",
    "resolve_secure_state_dir",
    "resolve_state_dir",
    "run_report_only_dream",
    "select_retention_candidates",
    "SESSION_HANDLE_SCHEMA_VERSION",
    "SessionHandle",
    "handle_from_public_dict",
    "stage_idempotency_key",
    "validate_quarantine_isolated",
    "write_active_harness_pin",
    "write_quarantine_bundle",
]
