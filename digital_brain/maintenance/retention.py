"""Owner-configured deterministic quality evidence retention.

Policy is a reviewed local structured setting (YAML/JSON). The model cannot
choose or expand the allowlist, TTLs, or actions. Automatic unattended apply
requires an explicit ``auto_apply: true`` opt-in; dry-run is always safe.

Removable raw text lives only on ``Operational:QualityPayload``. Feedback
metadata, ``request_fingerprint``, and ``raw_hmac`` stay immutable.
Redaction/archive/purge is performed only by a dedicated quality transaction
that emits an ``EffectReceipt`` — never via generic Cypher DELETE.

Backup limitation
-----------------
``BACKUP_RETENTION_LIMITATION``: historical Neo4j dumps, filesystem backups,
and exports taken *before* a receipted redaction/purge may still contain raw
``QualityPayload`` text. A graph-side receipt does **not** claim that off-graph
backups are clean. Operators must rotate or destroy pre-redaction backups
separately when intimate raw data must not exist anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from digital_brain.maintenance.models import digest_text
from digital_brain.maintenance.privacy import SENSITIVITIES

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RETENTION_SCHEMA_VERSION = "1"

RETENTION_ACTIONS: frozenset[str] = frozenset({"redact", "archive", "purge"})

# Relative to the digital-brain state directory (see generation.resolve_state_dir).
ACTIVE_RETENTION_CONFIG_REL = Path("retention") / "config.json"

# Lifecycle events written by retention apply (subset of quality.LIFECYCLE_EVENTS).
RETENTION_LIFECYCLE_EVENTS: dict[str, str] = {
    "redact": "redacted",
    "archive": "archived",
    "purge": "purged",
}

EFFECT_TYPE_BY_ACTION: dict[str, str] = {
    "redact": "retention_redact",
    "archive": "retention_archive",
    "purge": "retention_purge",
}

# Pending proposal projections that regret (revoke) may mark stale.
PENDING_PROPOSAL_STATUSES: frozenset[str] = frozenset(
    {"draft", "validated", "review_pending"}
)

BACKUP_RETENTION_LIMITATION = (
    "Historical backups and exports may predate redaction/purge and still "
    "contain raw QualityPayload text. Receipted graph retention does not "
    "scrub off-graph backups; rotate or destroy pre-redaction backups separately."
)


def _canonical_json(payload: Mapping[str, Any] | Sequence[Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def parse_iso_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_ts(value: str) -> str:
    return (
        parse_iso_utc(value)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _require_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"retention config.{key} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class RetentionConfig:
    """Reviewed local retention policy. Model cannot expand fields at runtime."""

    schema_version: str
    version: str
    sensitivity_allowlist: frozenset[str]
    ttl_seconds_by_sensitivity: Mapping[str, int]
    actions_by_sensitivity: Mapping[str, str]
    auto_apply: bool = False
    max_batch: int = 100
    notes: str | None = None  # human text; excluded from digest

    def identity_payload(self) -> dict[str, Any]:
        """Policy-affecting fields only (digest identity)."""
        ttl = {
            str(k): int(v)
            for k, v in sorted(self.ttl_seconds_by_sensitivity.items())
        }
        actions = {
            str(k): str(v)
            for k, v in sorted(self.actions_by_sensitivity.items())
        }
        return {
            "actions_by_sensitivity": actions,
            "auto_apply": bool(self.auto_apply),
            "max_batch": int(self.max_batch),
            "schema_version": str(self.schema_version),
            "sensitivity_allowlist": sorted(self.sensitivity_allowlist),
            "ttl_seconds_by_sensitivity": ttl,
            "version": str(self.version),
        }

    def digest(self) -> str:
        return compute_retention_config_digest(self)

    def to_public_dict(self) -> dict[str, Any]:
        payload = self.identity_payload()
        payload["config_digest"] = self.digest()
        if self.notes is not None:
            payload["notes"] = self.notes
        payload["backup_limitation"] = BACKUP_RETENTION_LIMITATION
        return payload

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> RetentionConfig:
        if not isinstance(data, Mapping):
            raise TypeError("retention config must be a mapping")

        schema_version = str(
            data.get("schema_version") or RETENTION_SCHEMA_VERSION
        ).strip()
        if schema_version != RETENTION_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported retention schema_version {schema_version!r}; "
                f"expected {RETENTION_SCHEMA_VERSION!r}"
            )
        version = _require_str(data, "version")

        raw_allow = data.get("sensitivity_allowlist")
        if raw_allow is None:
            raise ValueError("retention config.sensitivity_allowlist is required")
        if not isinstance(raw_allow, (list, tuple, set, frozenset)):
            raise TypeError("sensitivity_allowlist must be a list")
        allowlist: set[str] = set()
        for item in raw_allow:
            sens = str(item).strip()
            if sens not in SENSITIVITIES:
                raise ValueError(f"unknown sensitivity in allowlist: {sens!r}")
            allowlist.add(sens)
        if not allowlist:
            raise ValueError("sensitivity_allowlist must be non-empty")

        raw_ttl = data.get("ttl_seconds_by_sensitivity") or data.get(
            "ttl_by_sensitivity"
        )
        if not isinstance(raw_ttl, Mapping) or not raw_ttl:
            raise ValueError("ttl_seconds_by_sensitivity is required")
        ttl: dict[str, int] = {}
        for key, value in raw_ttl.items():
            sens = str(key).strip()
            if sens not in SENSITIVITIES:
                raise ValueError(f"unknown sensitivity in ttl map: {sens!r}")
            try:
                seconds = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"ttl for {sens!r} must be an integer") from exc
            if seconds < 0:
                raise ValueError(f"ttl for {sens!r} must be >= 0")
            ttl[sens] = seconds

        raw_actions = data.get("actions_by_sensitivity")
        if not isinstance(raw_actions, Mapping) or not raw_actions:
            raise ValueError("actions_by_sensitivity is required")
        actions: dict[str, str] = {}
        for key, value in raw_actions.items():
            sens = str(key).strip()
            if sens not in SENSITIVITIES:
                raise ValueError(f"unknown sensitivity in actions map: {sens!r}")
            action = str(value).strip()
            if action not in RETENTION_ACTIONS:
                raise ValueError(
                    f"unknown action {action!r} for {sens!r}; "
                    f"allowed={sorted(RETENTION_ACTIONS)}"
                )
            actions[sens] = action

        # Fail closed: every allowlisted sensitivity must have TTL + action.
        for sens in allowlist:
            if sens not in ttl:
                raise ValueError(
                    f"missing ttl for allowlisted sensitivity {sens!r}"
                )
            if sens not in actions:
                raise ValueError(
                    f"missing action for allowlisted sensitivity {sens!r}"
                )

        auto_apply = bool(data.get("auto_apply", False))
        max_batch_raw = data.get("max_batch", 100)
        try:
            max_batch = int(max_batch_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_batch must be an integer") from exc
        if max_batch < 1:
            raise ValueError("max_batch must be >= 1")

        notes = data.get("notes")
        if notes is not None:
            notes = str(notes)

        return cls(
            schema_version=schema_version,
            version=version,
            sensitivity_allowlist=frozenset(allowlist),
            ttl_seconds_by_sensitivity=ttl,
            actions_by_sensitivity=actions,
            auto_apply=auto_apply,
            max_batch=max_batch,
            notes=notes,
        )


def compute_retention_config_digest(
    config: RetentionConfig | Mapping[str, Any],
) -> str:
    if isinstance(config, RetentionConfig):
        identity = config.identity_payload()
    else:
        identity = RetentionConfig.from_mapping(config).identity_payload()
    return digest_text(_canonical_json(identity))


def load_retention_config(path: str | Path) -> RetentionConfig:
    """Load and validate a local retention config (JSON or YAML)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    data = _parse_config_text(text, path=p)
    return RetentionConfig.from_mapping(data)


def _parse_config_text(text: str, *, path: Path) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError(f"empty retention config: {path}")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PyYAML required to load retention YAML configs"
            ) from exc
        data = yaml.safe_load(stripped)
    else:
        data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError(f"retention config root must be an object: {path}")
    return data


def default_retention_config_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser().resolve() / ACTIVE_RETENTION_CONFIG_REL


# ---------------------------------------------------------------------------
# Inventory + dry-run selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetentionInventoryItem:
    """One Feedback (+ optional payload) row available for retention selection."""

    feedback_id: str
    sensitivity: str
    created_at: str
    has_payload: bool
    payload_id: str | None = None
    already_lifecycle: str | None = None  # latest redacted|archived|purged|revoked
    request_fingerprint: str | None = None
    raw_hmac: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> RetentionInventoryItem:
        fid = str(data.get("feedback_id") or data.get("id") or "").strip()
        if not fid:
            raise ValueError("inventory item requires feedback_id")
        sensitivity = str(data.get("sensitivity") or "public_ops").strip()
        created = data.get("created_at") or data.get("observed_at")
        if not created:
            raise ValueError(f"inventory item {fid!r} requires created_at")
        has_payload = bool(
            data.get("has_payload")
            if data.get("has_payload") is not None
            else data.get("payload_text")
            or data.get("raw_payload_ref")
            or data.get("payload_id")
        )
        # Explicit false for payload_text empty string after redaction.
        if data.get("payload_text") is not None:
            has_payload = bool(str(data.get("payload_text") or "").strip())
        return cls(
            feedback_id=fid,
            sensitivity=sensitivity,
            created_at=normalize_ts(str(created)),
            has_payload=has_payload,
            payload_id=(
                None
                if data.get("payload_id") is None
                and data.get("raw_payload_ref") is None
                else str(data.get("payload_id") or data.get("raw_payload_ref"))
            ),
            already_lifecycle=(
                None
                if data.get("already_lifecycle") is None
                else str(data.get("already_lifecycle"))
            ),
            request_fingerprint=(
                None
                if data.get("request_fingerprint") is None
                else str(data.get("request_fingerprint"))
            ),
            raw_hmac=(
                None if data.get("raw_hmac") is None else str(data.get("raw_hmac"))
            ),
        )


@dataclass(frozen=True)
class RetentionCandidate:
    feedback_id: str
    sensitivity: str
    action: str
    payload_id: str | None
    age_seconds: int
    ttl_seconds: int
    effect_type: str
    lifecycle_event: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "sensitivity": self.sensitivity,
            "action": self.action,
            "payload_id": self.payload_id,
            "age_seconds": self.age_seconds,
            "ttl_seconds": self.ttl_seconds,
            "effect_type": self.effect_type,
            "lifecycle_event": self.lifecycle_event,
        }


@dataclass
class RetentionPlan:
    """Dry-run result: counts and selected candidates (no raw payload text)."""

    config_digest: str
    schema_version: str
    version: str
    auto_apply: bool
    selected: list[RetentionCandidate] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "config_digest": self.config_digest,
            "schema_version": self.schema_version,
            "version": self.version,
            "auto_apply": self.auto_apply,
            "counts": dict(self.counts),
            "selected": [c.to_public_dict() for c in self.selected],
            "backup_limitation": BACKUP_RETENTION_LIMITATION,
        }


def select_retention_candidates(
    items: Iterable[Mapping[str, Any] | RetentionInventoryItem],
    config: RetentionConfig,
    *,
    now: datetime | str | None = None,
) -> RetentionPlan:
    """Deterministic dry-run selection. Never mutates storage."""
    if now is None:
        now_dt = _now_utc()
    elif isinstance(now, datetime):
        now_dt = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        now_dt = now_dt.astimezone(timezone.utc).replace(microsecond=0)
    else:
        now_dt = parse_iso_utc(str(now)).replace(microsecond=0)

    skipped_no_payload = 0
    skipped_sensitivity = 0
    skipped_ttl = 0
    skipped_already_done = 0
    selected: list[RetentionCandidate] = []
    by_sensitivity: dict[str, int] = {}
    by_action: dict[str, int] = {"redact": 0, "archive": 0, "purge": 0}

    parsed: list[RetentionInventoryItem] = []
    for raw in items:
        if isinstance(raw, RetentionInventoryItem):
            parsed.append(raw)
        else:
            parsed.append(RetentionInventoryItem.from_mapping(raw))
    parsed.sort(key=lambda i: (i.created_at, i.feedback_id))

    terminal_events = frozenset(RETENTION_LIFECYCLE_EVENTS.values())

    for item in parsed:
        if not item.has_payload:
            skipped_no_payload += 1
            continue
        if item.sensitivity not in config.sensitivity_allowlist:
            skipped_sensitivity += 1
            continue
        if item.already_lifecycle in terminal_events:
            # Already receipted for a retention terminal lifecycle.
            skipped_already_done += 1
            continue

        ttl = config.ttl_seconds_by_sensitivity.get(item.sensitivity)
        action = config.actions_by_sensitivity.get(item.sensitivity)
        if ttl is None or action is None:
            # Fail closed — should not happen if config validated.
            skipped_sensitivity += 1
            continue

        created = parse_iso_utc(item.created_at)
        age = int((now_dt - created).total_seconds())
        if age < int(ttl):
            skipped_ttl += 1
            continue

        cand = RetentionCandidate(
            feedback_id=item.feedback_id,
            sensitivity=item.sensitivity,
            action=action,
            payload_id=item.payload_id,
            age_seconds=age,
            ttl_seconds=int(ttl),
            effect_type=EFFECT_TYPE_BY_ACTION[action],
            lifecycle_event=RETENTION_LIFECYCLE_EVENTS[action],
        )
        selected.append(cand)
        by_sensitivity[item.sensitivity] = by_sensitivity.get(item.sensitivity, 0) + 1
        by_action[action] = by_action.get(action, 0) + 1

    # Cap batch for apply; dry-run still reports full selected then truncated.
    truncated = selected[: int(config.max_batch)]
    counts = {
        "selected": len(selected),
        "selected_batch": len(truncated),
        "skipped_no_payload": skipped_no_payload,
        "skipped_sensitivity": skipped_sensitivity,
        "skipped_ttl": skipped_ttl,
        "skipped_already_done": skipped_already_done,
        "by_sensitivity": by_sensitivity,
        "by_action": by_action,
        "max_batch": int(config.max_batch),
    }
    return RetentionPlan(
        config_digest=config.digest(),
        schema_version=config.schema_version,
        version=config.version,
        auto_apply=bool(config.auto_apply),
        selected=truncated,
        counts=counts,
    )


def assert_apply_permitted(
    config: RetentionConfig,
    *,
    automatic: bool,
    owner_initiated: bool = False,
) -> None:
    """Raise when apply is not permitted.

    - Dry-run never calls this.
    - Automatic/unattended apply requires ``config.auto_apply``.
    - Explicit owner-initiated apply is allowed without auto_apply (policy still
      binds action/sensitivity).
    """
    if automatic and not config.auto_apply:
        raise PermissionError(
            "retention_auto_apply_disabled: set auto_apply true in the "
            "reviewed local retention config to enable unattended apply"
        )
    if not automatic and not owner_initiated:
        raise PermissionError(
            "retention_apply_requires_owner_or_auto: pass owner_initiated=True "
            "for manual apply, or enable auto_apply for unattended housekeeping"
        )


def effect_key_for(
    *,
    action: str,
    feedback_id: str,
    config_digest: str,
) -> str:
    """Stable idempotency key bound to policy digest prefix."""
    prefix = config_digest[:16]
    return f"ret:{action}:{feedback_id}:{prefix}"


def build_retention_effect_identity(
    *,
    effect_id: str,
    effect_key: str,
    effect_type: str,
    feedback_id: str,
    action: str,
    config_digest: str,
    before_ref: str,
    after_ref: str,
) -> dict[str, Any]:
    return {
        "action": action,
        "after_ref": after_ref,
        "before_ref": before_ref,
        "config_digest": config_digest,
        "effect_id": effect_id,
        "effect_key": effect_key,
        "effect_type": effect_type,
        "feedback_id": feedback_id,
    }


def default_demo_config(
    *,
    auto_apply: bool = False,
    intimate_ttl_seconds: int = 0,
    personal_ttl_seconds: int = 0,
    public_ttl_seconds: int = 86_400,
) -> RetentionConfig:
    """Test/demo config. Intimate/personal default to immediate eligibility."""
    return RetentionConfig(
        schema_version=RETENTION_SCHEMA_VERSION,
        version="1.0.0-test",
        sensitivity_allowlist=frozenset({"intimate", "personal", "public_ops"}),
        ttl_seconds_by_sensitivity={
            "intimate": intimate_ttl_seconds,
            "personal": personal_ttl_seconds,
            "public_ops": public_ttl_seconds,
        },
        actions_by_sensitivity={
            "intimate": "purge",
            "personal": "redact",
            "public_ops": "archive",
        },
        auto_apply=auto_apply,
        max_batch=50,
        notes="demo/test only",
    )


__all__ = [
    "ACTIVE_RETENTION_CONFIG_REL",
    "BACKUP_RETENTION_LIMITATION",
    "EFFECT_TYPE_BY_ACTION",
    "PENDING_PROPOSAL_STATUSES",
    "RETENTION_ACTIONS",
    "RETENTION_LIFECYCLE_EVENTS",
    "RETENTION_SCHEMA_VERSION",
    "RetentionCandidate",
    "RetentionConfig",
    "RetentionInventoryItem",
    "RetentionPlan",
    "assert_apply_permitted",
    "build_retention_effect_identity",
    "compute_retention_config_digest",
    "default_demo_config",
    "default_retention_config_path",
    "effect_key_for",
    "load_retention_config",
    "normalize_ts",
    "parse_iso_utc",
    "select_retention_candidates",
]
