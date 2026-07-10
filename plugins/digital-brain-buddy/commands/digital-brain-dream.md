---
name: digital-brain-dream
description: Manual Digital Brain maintenance (DreamRun) — report-only by default; review proposals with progressive disclosure; operator-gated try/apply only.
---

# /digital-brain-dream

Guided maintenance for the avatar_digital_brain quality control plane.
Follow skill `digital-brain-buddy-maintenance` and
`skills/digital-brain-buddy-maintenance/references/quality-control-contract.md`.

**Defaults:** local-only, report-only, manual trigger. No schedule. No heartbeat.
Shared/non-owner sessions: refuse private proposal queues and intimate evidence.

## Subcommands

Parse the user argument after `/digital-brain-dream` as one of:

### run

Execute a **report-only** DreamRun via the deterministic CLI (preferred over
ad-hoc model Cypher):

```bash
uv run python scripts/digital_brain_dream.py run \
  --evidence <evidence.json> \
  --cutoff <ISO-8601-Z> \
  --generation-id "${DIGITAL_BRAIN_HARNESS_GENERATION_ID}" \
  --dry-store
```

Omit `--dry-store` only when quality/admin Neo4j credentials are available to
the **operator host**, not to a maintainer subagent toolset.

Relay the public report: `processing_mode`, counts, bucket ids — **not** raw
quotes.

### status

Summarize the latest known DreamRun (`run_id`, stage, owner_status,
processing_mode, proposal counts). If unknown, say so and offer `run`.

### review [proposal-id]

Show **one** proposal review card: title/kind, why now, strength/counterevidence
summary, blast radius, trial/undo path, evidence band — ids and digests only.
If no id, pick the next `waiting_for_owner` proposal.

### show \<proposal-id\>

Progressive disclosure: more detail than `review`, still without dumping raw
intimate sensor text unless the owner explicitly requests raw evidence in a
verified private session.

### try \<proposal-id\>

**Phase-gated.** Only when the overlay operator path and evaluation gate exist.
Hand off to the operator script (interactive); do **not** activate from the
model toolset:

```bash
uv run python scripts/digital_brain_activate_overlay.py --help
```

If gates are missing, refuse and leave the proposal waiting.

### apply \<proposal-id\>

**Phase-gated.** Only for typed Alias/effect proposals with operator path:

```bash
uv run python scripts/digital_brain_apply_proposal.py apply \
  --proposal-id <proposal-id> --approver <owner>
```

Exact-token chat intent `APPLY alias:<proposal_id>` is **intent only**, not
authorization. Never treat `yes` / `ok` / 👍 as apply. No unattended `--yes`.

If gates are missing, refuse and leave the proposal waiting.

### defer | reject \<proposal-id\>

Record owner decision only (not activation). Prefer `record_decision` via the
coordinator/CLI path when available; otherwise document the decision for the
operator and keep runtime state unchanged.

### undo \<receipt-id\>

Operator compensating path only (Alias revoke / overlay rollback scripts).
Never invent undo Cypher.

### history

List recent runs/decisions as **counts and ids** (and processing modes), not
payload dumps.

### privacy

State processing mode defaults (`local_only` / report-only), redaction rules,
and that external dispatch requires explicit opt-in. Confirm shared sessions
cannot inspect private proposal queues.

## Message separation

Always keep **approval**, **application**, **deployment/exposure**, and
**effectiveness** as separate user-facing statements.

## Do not

- Do not schedule maintenance or enable heartbeat from this command.
- Do not mount operator secrets into maintainer workers.
- Do not edit SOUL, skills, overlays, or journals as part of `run`.
- Do not claim the system self-evolved because a report finished.
