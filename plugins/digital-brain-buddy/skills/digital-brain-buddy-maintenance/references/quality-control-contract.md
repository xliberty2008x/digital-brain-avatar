# Quality control contract (maintenance / DreamRun)

This file is the plugin-facing contract for capability ceilings, reports, and
command gates. Runtime authority lives in Python + the authenticated
coordinator API — not in skill prose alone.

## Capability ceiling

Source of truth:

```text
digital_brain.maintenance.runner.MAINTAINER_ALLOWED_OPERATIONS
digital_brain.maintenance.runner.MAINTAINER_FORBIDDEN_OPERATIONS
digital_brain.maintenance.runner.maintainer_tool_profile
digital_brain.maintenance.runner.assert_no_activation_capability
```

### Maintainer allowed operations

| Operation | Purpose |
| --- | --- |
| `acquire_maintenance_lease` | Fenced exclusive lease |
| `renew_maintenance_lease` | Lease heartbeat (coordinator, not model cron) |
| `release_maintenance_lease` | Release fence |
| `create_dream_run` | Register DreamRun |
| `record_dream_stage` | Stage receipt |
| `create_evidence_snapshot` | Freeze evidence set |
| `create_finding` | Typed finding |
| `create_proposal` | Typed proposal (quarantine path only via coordinator) |
| `record_evaluation` | Evaluation receipt (not activation) |
| `record_decision` | Owner decision record (not effect) |

`all_tools=True` still excludes every forbidden activation surface.

### Forbidden (non-exhaustive structural denylist)

- `activate_alias`, `apply_alias`, `revoke_alias`
- `mint_activation_authority`, `consume_activation_authority`
- `activate_policy`, `activate_overlay`, `publish_deployment`
- `record_effect`, `apply_effect`, `operator_activate`
- `record_retention_effect` (blocked in report-only maintainer profile)
- `compile_patch`, `write_quarantine_artifact`, `load_overlay_manifest`

Model-facing FastMCP must not register coordinator workflow or activation
tools (`COORDINATOR_FORBIDDEN_MCP_TOOL_NAMES` in
`mcp_servers/cypher/.../quality_control_api.py`).

### Host tool allowlists (Claude / Grok)

Native maintainer agents (`agents/digital-brain-maintainer.md`):

- **Allow:** `Read`, `Grep`, `Glob` (and read-only MCP if any are explicitly
  sensor-read; default: none required for report presentation).
- **Omit:** `Bash`, `Edit`, `Write`, generic graph write, activation tools.

Codex: `skills/.../agents/openai.yaml` is **not** a hard per-worker tool
boundary. Rely on server-side coordinator separation + the CLI runner.

### Operator-only paths (document, do not mount into models)

| Action | Script |
| --- | --- |
| Apply Alias / protection | `scripts/digital_brain_apply_proposal.py` |
| Overlay trial activate/rollback | `scripts/digital_brain_activate_overlay.py` |
| Report-only DreamRun | `scripts/digital_brain_dream.py` |

No unattended `--yes` apply path. Exact token `APPLY alias:<proposal_id>` is
**intent only** (`parse_apply_token`); it does not mint or consume authority.

## Processing modes

| Mode | Behaviour |
| --- | --- |
| `local_only` | Default; no external quality evidence dispatch |
| `metadata_external` | Opt-in structured/redacted counts only |
| `external_opt_in` | Per-run approved redacted packet; no raw journal dump |

Report-only DreamRun processing mode field on public packets is
`report_only` (see `DreamRunner.processing_mode`). Reports must always state
the processing boundary.

## Public report shape

`DreamRunResult.to_public_dict()` / CLI stdout must carry:

- `run_id`, `epoch`, `stage`, `owner_status`
- `processing_mode`
- `snapshot_id`, `source_ids_digest`
- `finding_ids`, `proposal_ids` (ids only)
- `report.reviewed_count`, `report.auto_applied_count`
- `report.buckets.*.count` and `report.buckets.*.ids` for:
  - `applied_housekeeping`
  - `waiting_for_owner`
  - `deliberately_left_alone`

**Never** include raw Feedback text, journal body quotes, or intimate fields
in the default report. Progressive disclosure only on owner request in a
private session.

## Lifecycle separation

These are separate durable facts / separate user messages:

1. **Observation** (Feedback / RunEvent)
2. **Finding**
3. **Proposal**
4. **Evaluation**
5. **Decision** (approve / defer / reject)
6. **EffectReceipt** (application)
7. **Deployment / ExposureWindow**
8. **Effectiveness**

Approval ≠ application ≠ deployment ≠ effectiveness.

## Session and schedule gates

- Manual `/digital-brain-dream run` is the primary trigger during rollout.
- **No scheduled run by default.**
- **No heartbeat** maintenance trigger by default.
- Shared / non-owner sessions: no private proposal queue, no intimate evidence
  disclosure, no operator apply hand-off.
- Next private session may recap applied housekeeping (when trusted
  housekeeping is enabled later), waiting proposals, and ambiguity left alone.

## Phase gates for try / apply

| Command | Minimum gate |
| --- | --- |
| `try` | Overlay operator path shipped + evaluation passed + owner private session |
| `apply` | Alias/effect operator path shipped + interactive confirm + matching hashes |
| `defer` / `reject` | Owner private; records decision only |
| `undo` | Effect receipt id + operator compensating script |

Until gates pass, surface try/apply as **blocked** and keep proposals in
`waiting_for_owner`.
