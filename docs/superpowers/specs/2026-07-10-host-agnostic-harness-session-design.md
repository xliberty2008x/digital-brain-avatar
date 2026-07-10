# Design: Host-Agnostic Harness Session (`open_harness_session`)

**Status:** Implemented **rev 1.1** — portable session bind (H0–H4 shipped on this branch)  
**Date:** 2026-07-10  
**Repo:** `avatar_digital_brain`

**Tracked source of truth:** this file

**Related:**
- Quality / dreams: `docs/superpowers/specs/2026-07-10-self-evolving-quality-dreams-design.md`
- Generation pin library: `digital_brain/maintenance/generation.py`
- Host entrypoint (Claude-shaped today): `scripts/pin_harness_generation.py`
- SessionStart adapter: `plugins/digital-brain-buddy/scripts/compose-up.sh`
- Buddy skill pin rules: `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md`
- MCP pin resolution (container): `mcp_servers/cypher/.../quality.py` → `resolve_session_harness_generation_id`

---

## 0. Thesis

**Grok, Claude, Codex (and future hosts) are brains.**  
**The harness + memory graph are the product.**

Any brain that can talk to memory must also be able to **open a harness session**
and contribute quality evidence. Improving the harness must not depend on a
Claude-only SessionStart hook.

Today memory works host-agnostically (MCP). Quality sensors do not: pin injection
is Claude SessionStart + env (`DIGITAL_BRAIN_HARNESS_GENERATION_ID`). On hosts
without that path, buddy still reads/writes journal, but refuse-to-emit is
correct *given the current contract* — and wrong *as a product end state*.

```text
  [ Grok | Claude | Codex | … ]     ← brains (replaceable)
              │
              ▼
     open_harness_session           ← portable primitive (this design)
              │
              ▼
   harness pin + session handle     ← bound for THIS conversation
              │
     ┌────────┴────────┐
     ▼                 ▼
  memory plane      quality plane
  (journal…)        (Feedback / RunEvent → dreams)
```

**Non-goals of this design:** auto-apply Alias/overlays, auto DreamRun, silent
identity mutation, treating sticky `active/` as “my” session.

---

## 1. Problem statement

### 1.1 What works today

| Path | Status |
| --- | --- |
| Journal READ/WRITE via `digital-brain-neo4j` MCP | Host-agnostic when MCP is configured |
| Claude SessionStart → `compose-up.sh` → `pin_harness_generation.py` | Pins session + exports env via `CLAUDE_ENV_FILE` |
| Sensor emission with explicit `harness_generation_id` | Works when id is known |
| Deterministic MCP tool-outcome RunEvents | Skip cleanly when no pin resolves |

### 1.2 What fails across brains

| Failure | Root cause |
| --- | --- |
| Grok buddy: “no pin” while `active/harness_generation.json` exists | File is leftover from another session (e.g. verify); **process env empty** |
| Brain “borrows” `active/` | Wrong attribution (verify harness credited for buddy chat) |
| Skill refuses sensors without pin | Correct gate; no **portable open** to satisfy it |
| Dream attribution / trial overlays | Need generation + session binding the brain never received |
| Operator sees “pin on disk” and assumes product works | Two meanings of “pin” (see §2) |

### 1.3 Product rule (owner intent)

> No matter which brain I use, talking to memory should be able to **improve the
> harness** (quality evidence → dreams → reviewed proposals). Host differences
> may change *how session open is triggered*, not *whether quality can attach*.

---

## 2. Two meanings of “pin” (must stay distinct)

| Kind | Location | Meaning | May brains use for sensors? |
| --- | --- | --- | --- |
| **Session pin** | `$STATE/sessions/<session_id>/harness_generation.json` + env `DIGITAL_BRAIN_HARNESS_GENERATION_ID` | This conversation’s frozen harness generation | **Yes — required** |
| **Active pin** | `$STATE/active/harness_generation.{id,json}` | Last well-known id for **dual-process** MCP containers / scripts | **No as sole identity** |

`active/` remains a **side effect** of a successful session pin (for mcp-cypher
instrumentation that cannot see host env). It is **not** a ticket for an
unrelated brain process.

Claude SessionStart and `pin_session_generation` already write both. The bug is
not “missing files”; it is “brain process never joined a session.”

---

## 3. Goals and non-goals

### Goals

1. **Portable session open** — one contract every brain can invoke.
2. **Stable session identity** — every quality event attributes to the right chat.
3. **Idempotent resume** — same host session reloads the same pin mid-conversation.
4. **No silent steal** — never adopt a foreign session’s pin from `active/` alone.
5. **Thin host adapters** — Claude keeps SessionStart; Grok/Codex use skill step 0 / tool / slash.
6. **Fail closed on sensors** without a session handle; memory path remains available.
7. **Compatible** with existing `HarnessGeneration`, Feedback, RunEvent, DreamRun, ActivationAuthority.

### Non-goals

- Unattended apply of proposals / overlays.
- Replacing DreamRun or operator scripts.
- Requiring Claude hooks on non-Claude hosts.
- Making `active/` authoritative for model-facing emission.
- Storing SOUL body in pins, MCP args, or sensor payloads.

---

## 4. Core primitive: `open_harness_session`

### 4.1 Definition

`open_harness_session` is the **host-agnostic session bind**:

```text
inputs  →  (optional host_session_id, mode, host_label, force_new, …)
effects →  mint/resolve session_id
        →  get_or_pin harness generation (recollect only when force_new / new id)
        →  write sessions/<id>/harness_generation.json (+ .env)
        →  refresh active/ as dual-process breadcrumb
        →  best-effort record_harness_generation on quality plane
        →  return SessionHandle (public fields only)
```

It is the **same semantics** as today’s pin path (`resolve_session_binding` +
`get_or_pin_session_generation` + optional MCP record), exposed as a **first-class
API** rather than only as a Claude SessionStart side effect.

### 4.2 SessionHandle (return contract)

Public JSON (no SOUL content). Example:

```json
{
  "schema_version": 1,
  "session_id": "grok-20260710T120000Z-a1b2c3d4",
  "harness_generation_id": "hg-…",
  "pin_path": "/Users/…/.local/state/digital-brain/sessions/grok-…/harness_generation.json",
  "state_dir": "/Users/…/.local/state/digital-brain",
  "mode": "opened",
  "force_new": true,
  "host": "grok",
  "plugin_version": "0.3.0",
  "record_outcome": "recorded|skipped|failed|unknown",
  "overlay_pin_path": null,
  "created_at": "2026-07-10T12:00:00Z"
}
```

| Field | Notes |
| --- | --- |
| `session_id` | Filesystem-safe; stable for this conversation when host supplies id |
| `harness_generation_id` | Frozen for session; pass unchanged into every Feedback/RunEvent |
| `mode` | `opened` \| `resumed` \| `recollected` |
| `host` | Free-text label: `claude` \| `grok` \| `codex` \| `unknown` |
| `record_outcome` | MCP quality plane best-effort; pin still valid if record fails |

### 4.3 Modes

| Mode | When | Behavior |
| --- | --- | --- |
| **open / startup** | New conversation | Mint or accept host session id; **force recollect** pin |
| **resume** | Same host session continues (compact/resume) | Reload existing pin for that `session_id`; do not rehash mid-session |
| **clear** | User/host resets context | New force-new pin (same as Claude `clear`) |
| **status** | Diagnostic | Return handle if env/session pin known; never invent from `active/` alone for “mine” |

Aligns with existing Claude hook sources: `startup|clear` → force-new;
`resume|compact` → reload (`FORCE_NEW_HOOK_SOURCES` / `RELOAD_HOOK_SOURCES` in
`generation.py`).

### 4.4 Who mints `session_id`

Priority (same as `resolve_session_binding`, extended):

1. **Explicit argument** to `open_harness_session` / `--session-id`
2. **Env** `DIGITAL_BRAIN_SESSION_ID` (operator or prior open in this process)
3. **Host-native session id** when the adapter can read it  
   - Claude: hook stdin `session_id`  
   - Grok: host-provided chat/session id if exposed; else synthetic  
   - Codex: host session id if exposed; else synthetic
4. **Synthetic ephemeral** via `new_ephemeral_session_id()` (or host-prefixed variant)

**Never** use global sticky `current` as a production session key across opens.

**Synthetic id shape (recommended):**

```text
{host}-{utc_compact}-{random_or_pid}
e.g. grok-20260710T120530Z-7f3a9c2b
```

Prefixing by host makes multi-brain concurrent sessions auditable without
claiming host-native UUID stability.

### 4.5 What the brain must hold after open

For the rest of **this** conversation, the brain (main agent + any subagent
prompts) must treat as sticky:

| Token | Source |
| --- | --- |
| `session_id` | SessionHandle |
| `harness_generation_id` | SessionHandle |
| `pin_path` | SessionHandle (optional but useful) |

**Injection ranks (best → acceptable):**

1. Host injects into process env for all tools (Claude `CLAUDE_ENV_FILE` pattern)
2. Agent copies ids into every sensor tool call + subagent prompts (Grok/Codex default)
3. Local shell after script open sources `sessions/<id>/harness_generation.env` for **that** shell only — insufficient alone if agent tools do not inherit shell

If only (2) works on a host, that is still a valid harness session. Env is a
convenience, not the only truth — the **handle** is.

### 4.6 Forbidden behaviors

- Adopt `active/` generation id without knowing its `session_id` equals **this** chat
- Recompute digests mid-session and change `harness_generation_id`
- Emit Feedback/RunEvent with a generation id from a different session
- Treat open as apply/activation of overlays or Alias
- Put SOUL body into handle, pin file, or MCP payloads

---

## 5. Surfaces (same contract, different adapters)

```text
                    open_harness_session (core library)
                              ▲
           ┌──────────────────┼──────────────────┐
           │                  │                  │
   CLI / script        MCP tool (optional)   Skill step 0
 pin_harness…          open_harness_session  buddy-session
           ▲                  ▲                  ▲
           │                  │                  │
   Claude SessionStart    any brain w/ MCP    Grok / Codex turn 0
   compose-up.sh          (if implemented)    explicit ritual
```

### 5.1 Core library (source of truth)

Extend / wrap existing modules — do not fork pin logic:

- `digital_brain.maintenance.generation`  
  - `resolve_session_binding`  
  - `get_or_pin_session_generation`  
  - `pin_session_generation` / `load_session_pin`  
  - `write_active_harness_pin` (side effect only)
- New thin facade (name bikeshed):  
  `digital_brain.maintenance.session.open_harness_session(...) -> SessionHandle`

Optional: also pin active overlays via existing `pin_session_active_overlays`
when manifest exists (same as compose-up Task 11 path).

### 5.2 CLI (already almost there)

`scripts/pin_harness_generation.py` remains the operator/host entrypoint.

**Gaps to close for portability:**

| Gap | Change |
| --- | --- |
| Claude-named docs only | Document as host-agnostic; add `--host grok|claude|codex` |
| `--json` summary | Ensure matches SessionHandle schema |
| Env export | Already sets process env + optional `CLAUDE_ENV_FILE`; add generic `harness_generation.env` load instructions for non-Claude |
| Active pin dual-use | Keep writing active/; document “not a session ticket for brains” |

Claude `compose-up.sh` becomes **one adapter** that calls this CLI — not the
definition of session open.

### 5.3 Buddy skill (mandatory step 0 on all brains)

Update `digital-brain-buddy-session` so **before sensors** (and ideally before
first user-facing reply when quality is desired):

```text
1. Resolve handle:
   a. If env DIGITAL_BRAIN_HARNESS_GENERATION_ID + DIGITAL_BRAIN_SESSION_ID set
      → resume/status (use as handle)
   b. Else if sessions/<known_host_session_id>/ pin exists
      → resume
   c. Else → open_harness_session (CLI or MCP)
2. Keep handle sticky for the conversation
3. Memory bootstrap (existing BOOTSTRAP pack) — independent of sensors
4. Sensors only with handle.harness_generation_id
```

**Important:** Memory BOOTSTRAP may proceed without pin (today’s Grok behavior).  
**Sensors and FEEDBACK quality path must not** invent an id or steal `active/`.

If open fails (no repo, no state dir, sandbox): continue as **memory-only**
buddy; say once (internal or on quality-related ask) that quality loop is
unavailable — same honesty as current “no pin” messaging.

### 5.4 Optional MCP tool: `open_harness_session`

**Why optional:** Pin collection needs **host filesystem** (state dir, SOUL hash,
git digests, plugin version). MCP server often runs in Docker and may not see
the host plugin tree the same way SessionStart does.

| Option | Pros | Cons |
| --- | --- | --- |
| **A. Host-only open** (CLI/skill; MCP only `record_harness_generation`) | Matches today; correct digests | Brain must run local script |
| **B. MCP open with host-mounted state** | One tool list for all brains | Needs careful mount + trust; digests from container may be wrong |
| **C. Split** — MCP records generation payload produced by host | Clean dual-plane | Two steps |

**Recommendation for rev 1:** **A + explicit skill step 0**, with **C** already
partially present (`record_harness_generation` after local collect). Defer full
MCP-side open until mounts and digest correctness are specified.

Model-facing MCP must **not** gain apply/activation powers via this tool.

### 5.5 Host adapters

| Host | Trigger | session_id source | Env injection |
| --- | --- | --- | --- |
| **Claude Code** | SessionStart → `compose-up.sh` (unchanged semantics) | Hook `session_id` | `CLAUDE_ENV_FILE` + process |
| **Grok** | Skill step 0 on buddy session / explicit “open harness” | Host chat id if any, else synthetic `grok-…` | Prefer tool-arg stickiness; env if host supports |
| **Codex** | Skill step 0 or project bootstrap | Host session id or synthetic `codex-…` | Same as Grok |
| **Manual / CI** | `pin_harness_generation.py --session-id …` | Explicit | Shell export |

---

## 6. Grok turn-0 ritual (concrete)

### 6.1 Happy path

```text
User: /digital-brain-buddy-session  (or first buddy turn)
Agent:
  1. open_harness_session
       --host grok
       --session-id <host_id or omit for synthetic>
       [--force-new if new chat]
  2. Parse SessionHandle JSON
  3. Remember generation_id + session_id for all later sensor calls
  4. Proceed BOOTSTRAP memory + conversation
  5. On fail/empty/success+approach or FEEDBACK:
       pass harness_generation_id from handle (never recompute)
```

### 6.2 What “improve the harness” means on Grok after this

| Event | Action |
| --- | --- |
| Tool fail / empty READ | `record_run_event` with handle’s generation id |
| Gotcha approach | same (model_advisory if model-emitted) |
| User correction | `create_feedback` with same generation id |
| Later DreamRun | evidence is attributable; still report-only until operator apply |

No automatic dream apply. No SOUL rewrite. Same ceilings as Claude.

### 6.3 Failure modes on Grok

| Situation | Behavior |
| --- | --- |
| Cannot run local pin script (no repo / sandbox) | Memory-only; sensors off |
| Pin script ok, MCP record fails | Handle still valid locally; `record_outcome=failed`; sensors may still write if MCP quality tools accept generation id |
| Agent forgets handle mid-chat | Reload `sessions/<session_id>/` only if session_id known; else re-open with force_new and accept split attribution (log honestly) |
| Stale `active/` from verify run | **Ignore** for handle resolution |

---

## 7. Resolution rules (hardened)

### 7.1 For the **brain / skill** (model-facing)

```text
resolve_handle_for_this_chat():
  1. explicit handle in conversation state
  2. env DIGITAL_BRAIN_HARNESS_GENERATION_ID + DIGITAL_BRAIN_SESSION_ID
  3. load sessions/<DIGITAL_BRAIN_SESSION_ID or host_session_id>/harness_generation.json
  4. else open_harness_session (mint)
  NEVER: active/ alone
```

### 7.2 For **MCP container instrumentation** (server-side tool outcomes)

Keep existing `resolve_session_harness_generation_id` order for **best-effort
server-side** emit (explicit → env → pin path → active/), because the container
often has no brain conversation state.

**Tighten (rev 1 or 1.1):**

- Prefer active pin only when `active` JSON includes `session_id` **and**
  caller supplies matching `session_ref` / session id — else skip.
- Or document active/ as “last writer wins for dual-process only” and require
  brains to pass **explicit** `harness_generation_id` on model-facing
  `create_feedback` / `record_run_event`.

**Recommendation:** Model-facing tools should **require explicit**
`harness_generation_id` when available; server-side auto-instrumentation may
use active/ with session_id match. Do not teach skills to read active/.

### 7.3 Dual-process truth

```text
Brain process          MCP container
    │                       │
    ├─ open → sessions/id   │
    ├─ write active/  ──────► may read active/ for instrumentation
    └─ pass explicit id ───► create_feedback / record_run_event
```

Explicit id always wins.

---

## 8. Lifecycle diagram

```text
Brain conversation starts
        │
        ▼
 open_harness_session ──────────────► SessionHandle
        │                    │
        │                    ├── sessions/<id>/harness_generation.json
        │                    ├── sessions/<id>/harness_generation.env
        │                    ├── active/ (breadcrumb)
        │                    └── optional record_harness_generation
        ▼
 Memory plane (any time)          Quality plane (only with handle)
  BOOTSTRAP / READ / WRITE         FEEDBACK / RunEvent
        │                                │
        ▼                                ▼
   JournalEntry …              Evidence → DreamRun → proposals
                                             │
                                             ▼
                                    operator apply (unchanged)
```

---

## 9. API sketch (library)

```python
@dataclass(frozen=True)
class SessionHandle:
    schema_version: int
    session_id: str
    harness_generation_id: str
    pin_path: str
    state_dir: str
    mode: str  # opened | resumed | recollected
    force_new: bool
    host: str
    plugin_version: str
    record_outcome: str
    overlay_pin_path: str | None
    created_at: str | None

def open_harness_session(
    *,
    session_id: str | None = None,
    host: str = "unknown",
    hook_source: str | None = None,  # startup|resume|clear|compact|None
    force_new: bool | None = None,
    state_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
    plugin_root: str | Path | None = None,
    soul_path: str | Path | None = None,
    skip_record: bool = False,
    pin_overlays: bool = True,
) -> SessionHandle:
    """Idempotent session bind. Never reads SOUL body into the handle."""
    ...
```

CLI:

```bash
python scripts/pin_harness_generation.py \
  --host grok \
  --session-id "$HOST_SESSION_OR_OMIT" \
  --json
# stdout: SessionHandle
```

Optional later MCP (not rev 1 required):

```text
open_harness_session(session_id?, host?, force_new?) -> SessionHandle
```

only if server can collect digests correctly.

---

## 10. Skill / command UX

| Surface | Behavior |
| --- | --- |
| Buddy skill step 0 | Auto open/resume; silent if ok; one-line status if quality unavailable |
| `/digital-brain-up` | Existing compose + pin (Claude); remains valid adapter |
| `/digital-brain-session` (new, optional) | Explicit open/status/resume for any host |
| `/digital-brain-dream` | Unchanged; prefers current handle’s generation for reports |

User-facing language: **“harness session”** / **“quality sensors on”** — not
“Claude pin.”

---

## 11. Security and trust boundaries

Unchanged from quality design:

| Action | Who |
| --- | --- |
| Open session / pin digests | Any host that can run pin script / skill |
| Emit Feedback / RunEvent | Session with handle |
| DreamRun report | Manual / owner-gated |
| Apply Alias / overlay trial | Operator scripts + authority only |

Open session is **not** activation authority. It only binds observation
attribution.

Sandbox: if the brain cannot write `$DIGITAL_BRAIN_STATE_DIR`, open fails closed
for quality; memory MCP may still work.

---

## 12. Compatibility with existing code

| Existing | Action |
| --- | --- |
| `resolve_session_binding` | Reuse; add host-prefix synthetic ids if desired |
| `get_or_pin_session_generation` | Core of open |
| `pin_harness_generation.py` | Implement / wrap SessionHandle JSON |
| `compose-up.sh` | Call same core; document as Claude adapter |
| Buddy skill pin section | Rewrite as open_harness_session step 0 |
| `resolve_session_harness_generation_id` | Keep for MCP; tighten active/ (§7.2) |
| Dreams / apply / overlays | No change to activation model |

---

## 13. Rollout plan

### Milestone H0 — Spec + contracts (this doc)

- Agree SessionHandle schema and resolve rules
- Document two pin meanings + multi-brain thesis

### Milestone H1 — Library + CLI parity

- `open_harness_session` facade + SessionHandle
- CLI `--json` / `--host` aligned
- Tests: open → resume → force_new; never load SOUL body; synthetic id shape
- Tests: skill-level resolve never uses active/ alone

### Milestone H2 — Skill portability

- Buddy skill step 0 on all hosts
- Subagent prompts require pasted generation_id
- Memory-only degraded mode documented

### Milestone H3 — MCP resolution harden

- Model-facing sensors prefer/require explicit generation id
- active/ match session_id or skip for auto-instrumentation

### Milestone H4 — Host polish (optional)

- Grok/Codex slash command or native session-start hook if platform allows
- Optional MCP open if digest collection is proven correct in container

---

## 14. Acceptance criteria

1. On a host **without** Claude SessionStart, after skill step 0 / CLI open:
   - `sessions/<id>/harness_generation.json` exists
   - brain holds `harness_generation_id`
   - `create_feedback` / `record_run_event` succeed with that id
2. Concurrent Claude + Grok sessions do not share one sticky `current` pin as
   their session key; each has its own `session_id`.
3. Leftover `active/` from `milestone-*-verify-*` is **not** adopted as the
   Grok chat handle.
4. Claude SessionStart path still pins and exports env (no regression).
5. Without successful open, sensors still refuse; journal still works.
6. Open never activates Alias/overlay/SOUL.

---

## 15. Open questions

1. **Host chat id stability on Grok** — if the product does not expose a stable
   session id, is synthetic-per-open enough? (Yes for rev 1; resume across
   process restarts needs either host id or user-supplied token.)
2. **Should open be blocking for buddy start?** — Recommend: non-blocking for
   memory; blocking only for quality routes.
3. **Multi-user machines** — state dir is per OS user; host label in session_id
   is enough for rev 1.
4. **MCP open in-container** — defer until digest fidelity proven.
5. **active/ tightening** — Resolved in rev 1.1: foreign sessions never match;
   MCP dual-process uses last-writer only with
   `DIGITAL_BRAIN_ALLOW_UNSCOPED_ACTIVE_PIN=1` on the mcp-cypher service.

---

## 16. Summary

| Principle | Implication |
| --- | --- |
| Brains are replaceable | Pin must not be Claude-only |
| Harness improves on any brain | Portable `open_harness_session` |
| Two pin meanings | Session pin = ticket; active/ = breadcrumb |
| Memory ≠ quality attach | MCP alone is not a harness session |
| Safety ceilings stay | Open ≠ apply; dreams stay report-first |

**One sentence:**  
`open_harness_session` makes “this conversation is bound to a frozen harness
generation” a **portable harness primitive**, so every brain that touches memory
can feed the quality loop without lying about attribution.
