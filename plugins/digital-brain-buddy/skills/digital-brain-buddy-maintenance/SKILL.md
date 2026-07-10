---
name: digital-brain-buddy-maintenance
description: "Guided, report-only Digital Brain maintenance (DreamRun): freeze evidence, walk fenced stages, surface proposals for owner review. Never activates Alias, policy, overlay, code, SOUL, or journal changes. Prefer the deterministic CLI coordinator; model workers stay on a typed read/proposal allowlist."
---

# Digital Brain Buddy Maintenance

Use this skill for **memory hygiene / maintenance** — the product surface for
DreamRun. It is **not** the buddy conversation skill and not FEEDBACK.

Default mode for v0.3: **manual, local-only, report-only**.

## Start Here

1. Read `references/quality-control-contract.md` (capability ceiling, report
   shape, privacy, command gates).
2. Confirm this is a **private owner session**. Shared / non-owner sessions must
   not open private proposal queues, intimate evidence, or operator apply paths.
3. Prefer the deterministic coordinator CLI (never invent activation tools):

   ```bash
   uv run python scripts/digital_brain_dream.py run \
     --evidence <path-or-fixture> \
     --cutoff <ISO-8601-Z> \
     --generation-id <pinned-harness-generation-id> \
     --dry-store   # offline fixture path; omit for live quality store
   ```

4. Present the public report with **counts, ids, processing_mode**, and the three
   buckets — never raw intimate quotes.
5. On hosts with a native maintainer subagent, delegate analysis/review framing
   to `digital-brain-maintainer` (Claude/Grok). On Codex, keep the main agent on
   this skill and rely on **server-side** capability separation
   (`agents/openai.yaml` is not a hard per-worker tool boundary).

## Modes

| Mode | When | Effects |
| --- | --- | --- |
| **Report-only** (default) | Rollout / every first run | Zero activation; `auto_applied_count` is always 0 |
| **Guided maintenance** | Owner runs, then reviews one proposal at a time | Proposals wait; apply/try only via operator scripts |
| **Trusted housekeeping** | Explicit owner opt-in later | Deterministic retention only (not default here) |

There is **no scheduled run by default**, **no heartbeat**, and **no private
proposal queue in shared/non-owner sessions**.

## Command Surface

Host command: `/digital-brain-dream` (see `../../commands/digital-brain-dream.md`).

| Subcommand | Role | Gate |
| --- | --- | --- |
| `run` | Manual report-only DreamRun | Always available (local owner) |
| `status` | Last/current run stage + owner_status | Owner/private |
| `review [id]` | One proposal card (summary first) | Owner/private |
| `show <id>` | Progressive disclosure of proposal detail | Owner/private |
| `try <id>` | Operator overlay trial path | Only when operator path + phase gate exist |
| `apply <id>` | Operator Alias/effect path | Only when operator path + phase gate exist |
| `defer \| reject <id>` | Record owner decision (not activation) | Owner/private |
| `undo <receipt-id>` | Compensating operator undo | Operator scripts only |
| `history` | Counts/ids of past runs/decisions | Owner/private |
| `privacy` | Processing mode + redaction boundary | Always |

### try / apply (phase-gated)

Do **not** run try/apply from the maintainer model toolset. Document and
hand off to operator scripts only:

- Alias / typed memory effect:
  `uv run python scripts/digital_brain_apply_proposal.py apply --proposal-id …`
- Overlay trial:
  `uv run python scripts/digital_brain_activate_overlay.py …`
- Both are interactive; **no unattended `--yes`**.
- Exact-token intent such as `APPLY alias:<proposal_id>` is **intent only**,
  not authorization. Authority is minted and consumed only by those scripts.

If operator paths or phase gates are missing, refuse try/apply and keep the
proposal in `waiting_for_owner`.

## Tool Allowlist (capability ceiling)

Structural source of truth:
`digital_brain.maintenance.runner.MAINTAINER_ALLOWED_OPERATIONS`.

**Allowed (typed read / proposal / recording only):**

- `acquire_maintenance_lease`
- `renew_maintenance_lease`
- `release_maintenance_lease`
- `create_dream_run`
- `record_dream_stage`
- `create_evidence_snapshot`
- `create_finding`
- `create_proposal`
- `record_evaluation`
- `record_decision`

**Forbidden (never grant, even with “all tools”):** activation, apply/revoke
Alias, mint/consume ActivationAuthority, policy/overlay activate, publish
deployment, retention effect apply, compile/write quarantine for runtime load,
generic Cypher write, Bash-driven graph mutation, Edit/Write into plugin load
paths or SOUL.

Claude/Grok native maintainer agents: host tools limited to **Read / Grep /
Glob** (or equivalent). **Omit Bash, Edit, Write, and activation MCP.** Codex:
do not treat `agents/openai.yaml` as a hard boundary — the CLI + coordinator
API enforce the ceiling.

Operator credentials (`DIGITAL_BRAIN_COORDINATOR_SECRET`, quality/admin Neo4j
passwords) and apply scripts must **stay out** of maintainer/analyzer toolsets.

## Report Contract

Public reports and next-private-session recaps must include:

- `processing_mode` (default `report_only` / `local_only` framing)
- `run_id`, stage, owner_status
- counts: reviewed, auto_applied (0 in report-only), findings, proposals
- buckets with **count + ids only**:
  - `applied_housekeeping`
  - `waiting_for_owner`
  - `deliberately_left_alone`

**Do not** paste raw Feedback/RunEvent text, intimate journal quotes, or full
sensor payloads. Progressive disclosure: summary first; raw evidence only on
explicit owner request in a verified private/local session.

Separate owner messages for:

1. Approval (decision recorded)
2. Application (effect receipt)
3. Deployment / exposure (trial window)
4. Effectiveness (later measurement)

Never collapse those into one “done” claim.

### Next-private-session recap (template)

```text
Maintenance complete. I reviewed <N> quality signals, archived <K> expired
operational records, and left <M> ambiguous patterns alone. <P> proposals are
ready. No identity or buddy behaviour changed without you.
processing_mode=<mode>
```

## Routing Relative to Buddy Session

| Route | Owner skill | Mutates |
| --- | --- | --- |
| Buddy SKIP/READ/WRITE/FEEDBACK | `digital-brain-buddy-session` | Evidence / journals per session rules |
| Maintenance / DreamRun | **this skill** | Control-plane receipts + proposals only |

Do not fold dream activation into FEEDBACK prose or generic acks (`yes`/`ok`/👍).

## Do Not

- Do not schedule weekly/cron maintenance by default.
- Do not enable heartbeat triggers.
- Do not open private proposal queues in shared/non-owner sessions.
- Do not direct unattended identity, policy, overlay, code, SOUL, or journal
  changes — including “just this once” model-side apply.
- Do not treat exact-token intent as authorization.
- Do not claim approval implies apply, or apply implies effectiveness.
- Do not put operator apply/activate scripts or secrets into maintainer tools.
- Do not load quarantine/draft overlays into the session harness.
