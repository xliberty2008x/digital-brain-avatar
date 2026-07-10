"""Privacy helpers for dream evidence and report packets.

Raw intimate Feedback payloads must never enter analyzer or owner report
packets. Low-entropy correlation uses a local keyed HMAC (not plain hashes).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Iterable, Mapping, MutableMapping

# Redaction policy bound into EvidenceSnapshot.redaction_policy_version.
REDACTION_POLICY_VERSION = "1"

# Keyed correlation MAC version (independent of quality sensor raw_hmac).
CORRELATION_HMAC_KEY_VERSION = "hmac-sha256-v1"

# Env for local correlation key material (never ship raw low-entropy ids).
CORRELATION_HMAC_KEY_ENV = "DIGITAL_BRAIN_CORRELATION_HMAC_KEY"

SENSITIVITIES: frozenset[str] = frozenset({"public_ops", "personal", "intimate"})
SENSITIVITY_RANK: dict[str, int] = {
    "public_ops": 0,
    "personal": 1,
    "intimate": 2,
}

# Field names that must never appear in analyzer / report packets.
INTIMATE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "raw_payload",
        "payload_text",
        "raw_text",
        "intimate_quote",
        "quote",
        "quotes",
        "soul_content",
        "soul_text",
        "soul",
        "SOUL",
        "journal_text",
        "journal_body",
        "body",
        "transcript",
        "message_text",
        "personal_note",
        "private_note",
        "freeform_text",
        "content",
    }
)

# Metadata-safe keys retained after redaction (ids/counts/digests only).
ANALYZER_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "evidence_id",
        "evidence_label",
        "label",
        "role",
        "evidence_hash",
        "correlation_hmac",
        "hmac_key_version",
        "sensitivity",
        "kind",
        "route",
        "tool",
        "tool_outcome",
        "task_outcome",
        "error_class",
        "observed_at",
        "created_at",
        "cutoff_at",
        "eligible_exposure",
        "revoked",
        "class_key",
        "lane",
        "evidence_strength",
        "harness_generation_id",
        "taxonomy_version",
        "schema_version",
        "source_counts",
        "source_counts_json",
        "source_ids_digest",
        "count",
        "ids",
        "counts",
        "redaction_policy_version",
        "redacted_summary",  # bounded ops summary only when not intimate
        "decision_point",
        "approach",
        "outcome_source",
        "recurrence_key",
    }
)


class IntimateFieldError(ValueError):
    """Raised when an intimate/raw field is found in a sanitized packet."""

    def __init__(self, path: str, field: str):
        self.path = path
        self.field = field
        super().__init__(f"intimate_field_present:{path}.{field}")


def resolve_correlation_key(
    key: bytes | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> bytes:
    """Resolve local HMAC key material for correlation digests.

    Preference order: explicit ``key``, then env
    ``DIGITAL_BRAIN_CORRELATION_HMAC_KEY``. Raises when neither is set so
    low-entropy plain hashes are never silently substituted.
    """
    if key is not None:
        if isinstance(key, bytes):
            if not key:
                raise ValueError("correlation key must be non-empty")
            return key
        text = str(key)
        if not text:
            raise ValueError("correlation key must be non-empty")
        return text.encode("utf-8")

    environ = env if env is not None else os.environ
    raw = environ.get(CORRELATION_HMAC_KEY_ENV)
    if raw is None or not str(raw).strip():
        raise ValueError(
            f"correlation key required (pass key= or set {CORRELATION_HMAC_KEY_ENV})"
        )
    return str(raw).strip().encode("utf-8")


def correlation_hmac(
    value: str,
    *,
    key: bytes | str,
    key_version: str = CORRELATION_HMAC_KEY_VERSION,
) -> str:
    """Keyed HMAC-SHA256 for retained low-entropy correlations.

    Unlike sensor ``raw_hmac`` (content-binding digest), this requires local
    key material so plain hashes of short identifiers are not retained.
    """
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    key_bytes = resolve_correlation_key(key)
    version = str(key_version or CORRELATION_HMAC_KEY_VERSION)
    message = f"{version}\0{value}".encode("utf-8")
    return hmac.new(key_bytes, message, hashlib.sha256).hexdigest()


def sensitivity_rank(sensitivity: str | None) -> int:
    if sensitivity is None:
        return SENSITIVITY_RANK["public_ops"]
    return SENSITIVITY_RANK.get(str(sensitivity).strip(), SENSITIVITY_RANK["public_ops"])


def max_sensitivity(values: Iterable[str | None]) -> str:
    best = "public_ops"
    best_rank = -1
    for value in values:
        rank = sensitivity_rank(value)
        if rank > best_rank:
            best_rank = rank
            best = str(value or "public_ops").strip() or "public_ops"
    if best not in SENSITIVITIES:
        return "public_ops"
    return best


def is_intimate_field_name(name: str) -> bool:
    return str(name) in INTIMATE_FIELD_NAMES


def contains_intimate_fields(
    payload: Any,
    *,
    path: str = "$",
) -> list[str]:
    """Return dotted paths of forbidden intimate/raw fields."""
    found: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_s = str(key)
            child = f"{path}.{key_s}"
            if is_intimate_field_name(key_s):
                found.append(child)
            found.extend(contains_intimate_fields(value, path=child))
    elif isinstance(payload, (list, tuple)):
        for i, item in enumerate(payload):
            found.extend(contains_intimate_fields(item, path=f"{path}[{i}]"))
    return found


def assert_no_intimate_fields(payload: Any, *, path: str = "$") -> None:
    hits = contains_intimate_fields(payload, path=path)
    if hits:
        # Report the first hit with a stable reason code for tests.
        first = hits[0]
        field = first.rsplit(".", 1)[-1]
        raise IntimateFieldError(first.rsplit(".", 1)[0], field)


def _strip_intimate_keys(record: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        key_s = str(key)
        if is_intimate_field_name(key_s):
            continue
        if isinstance(value, Mapping):
            out[key_s] = _strip_intimate_keys(value)
        elif isinstance(value, list):
            cleaned: list[Any] = []
            for item in value:
                if isinstance(item, Mapping):
                    cleaned.append(_strip_intimate_keys(item))
                else:
                    cleaned.append(item)
            out[key_s] = cleaned
        else:
            out[key_s] = value
    return out


def redact_evidence_record(
    record: Mapping[str, Any],
    *,
    correlation_key: bytes | str | None = None,
    include_redacted_summary: bool = True,
) -> dict[str, Any]:
    """Project one evidence item for analyzer/report use.

    Drops raw/intimate fields. For ``intimate`` sensitivity, also drops free-form
    summaries. Optionally attaches a keyed correlation HMAC of the evidence id.
    """
    if not isinstance(record, Mapping):
        raise TypeError("record must be a mapping")

    cleaned = _strip_intimate_keys(record)
    sensitivity = str(cleaned.get("sensitivity") or "public_ops").strip()
    if sensitivity not in SENSITIVITIES:
        sensitivity = "public_ops"
    cleaned["sensitivity"] = sensitivity

    # Intimate: no free-form text in packets (counts/ids/hashes only).
    if sensitivity == "intimate" or not include_redacted_summary:
        cleaned.pop("redacted_summary", None)

    evidence_id = cleaned.get("id") or cleaned.get("evidence_id")
    if correlation_key is not None and evidence_id is not None:
        cleaned["correlation_hmac"] = correlation_hmac(
            str(evidence_id), key=correlation_key
        )
        cleaned["hmac_key_version"] = CORRELATION_HMAC_KEY_VERSION

    # Bound to analyzer allowlist for defense in depth.
    projected: dict[str, Any] = {}
    for key, value in cleaned.items():
        if key in ANALYZER_ALLOWED_KEYS or key in {
            "observed_at",
            "created_at",
            "role",
            "evidence_label",
            "label",
            "eligible_exposure",
            "is_counterevidence",
            "revoked",
            "request_fingerprint",
        }:
            projected[key] = value
    return projected


def redact_packet(
    packet: Mapping[str, Any],
    *,
    correlation_key: bytes | str | None = None,
) -> dict[str, Any]:
    """Deep-clean a report/analyzer packet and fail closed on intimate leaks."""
    if not isinstance(packet, Mapping):
        raise TypeError("packet must be a mapping")

    def _walk(node: Any) -> Any:
        if isinstance(node, Mapping):
            # Evidence-like dicts go through field-level redaction.
            if any(
                k in node
                for k in ("raw_payload", "payload_text", "sensitivity", "evidence_id")
            ):
                return redact_evidence_record(
                    node, correlation_key=correlation_key
                )
            return {str(k): _walk(v) for k, v in node.items() if not is_intimate_field_name(str(k))}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    redacted = _walk(dict(packet))
    assert_no_intimate_fields(redacted)
    return redacted


def sanitize_mutable(
    target: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """In-place strip of intimate keys (for defensive cleanup)."""
    for key in list(target.keys()):
        if is_intimate_field_name(str(key)):
            del target[key]
        else:
            value = target[key]
            if isinstance(value, MutableMapping):
                sanitize_mutable(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, MutableMapping):
                        sanitize_mutable(item)
    return target
