# Initiate Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-detect an empty/incomplete digital-brain-buddy install and run a progressive initiate meeting (language → intro → self + anchor person + focus + light SOUL) before normal buddy mode, with graph-only completion markers and soft post-complete hooks.

**Architecture:** Extend `digital-brain-buddy-session` with an `INITIATE` mode gated after BOOTSTRAP. Pure Python `initiation_status` derives stage from evidence (tested first). Skill docs + `references/initiate-protocol.md` teach agents the meeting script. Seeds and receipt use existing write-memory / `append_journal_entry` (`properties.kind = "initiation_complete"`). No new MCP tools, skills, or slash commands.

**Tech Stack:** Python 3.11+ (stdlib + pytest), plugin Markdown skills, Neo4j JournalEntry append via existing MCP, `SOUL.MD` local file.

**Source design:** `docs/superpowers/specs/2026-07-10-initiate-protocol-design.md`

---

## File map

| Path | Action | Responsibility |
| --- | --- | --- |
| `plugins/digital-brain-buddy/scripts/initiation_status.py` | Create | Pure status derivation + CLI print JSON |
| `tests/test_initiation_status.py` | Create | Unit tests for status machine |
| `plugins/digital-brain-buddy/assets/SOUL.template.md` | Modify | Add empty `## User overlay` section |
| `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/references/initiate-protocol.md` | Create | Stages, script, resume, soft hooks, Cypher snippets |
| `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md` | Modify | Gate, `INITIATE` routing, soft hooks |
| `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/references/subagent-prompts.md` | Modify | BOOTSTRAP initiation evidence fields |
| `plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md` | Modify | BOOTSTRAP initiation evidence section |
| `plugins/digital-brain-buddy/agents/digital-brain-reader.md` | Modify | Mention initiation evidence in BOOTSTRAP |
| `plugins/digital-brain-buddy/skills/digital-brain-buddy-identity-bootstrap/SKILL.md` | Modify | User overlay rules during initiate |
| `plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md` | Modify | Receipt property convention (short) |
| `plugins/digital-brain-buddy/README.md` | Modify | First-run initiate description |
| `plugins/digital-brain-buddy/CHANGELOG.md` | Modify | `0.4.0` entry |
| `plugins/digital-brain-buddy/version.json` | Modify | `"0.4.0"` |
| `plugins/digital-brain-buddy/.claude-plugin/plugin.json` | Modify | version + description note |
| `plugins/digital-brain-buddy/.codex-plugin/plugin.json` | Modify | version `0.4.0+codex.<timestamp>` + description |
| `.claude-plugin/marketplace.json` | Modify | digital-brain-buddy → `0.4.0` |
| `.agents/plugins/marketplace.json` | Modify | digital-brain-buddy → `0.4.0` |

---

## Locked decisions (do not reopen)

1. Progressive meeting 1: language → in-language intro → self → anchor → focus → light SOUL → receipt.
2. Auto-detect incomplete after BOOTSTRAP; no dedicated skill/command.
3. Graph markers only; resume next gap; no wipe.
4. Receipt: `append_journal_entry(..., properties={"kind": "initiation_complete"})`.
5. Soft hooks only after complete + thin graph; max one per session on low-stakes turns.
6. Bump plugin **MINOR** to `0.4.0` (session contract change).

---

### Task 1: Initiation status pure function (TDD)

**Files:**
- Create: `plugins/digital-brain-buddy/scripts/initiation_status.py`
- Create: `tests/test_initiation_status.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_initiation_status.py`:

```python
"""Unit tests for initiate-protocol status derivation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "digital-brain-buddy"
    / "scripts"
    / "initiation_status.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("initiation_status", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def status_mod():
    return _load_module()


def test_empty_graph_missing_language(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": False,
            "has_self": False,
            "has_anchor_person": False,
            "has_focus": False,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 0,
            "topic_count": 0,
        }
    )
    assert result["status"] == "missing_language"
    assert result["complete"] is False
    assert result["mode"] == "INITIATE"
    assert result["graph_thin"] is True


def test_language_only_missing_self(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": False,
            "has_anchor_person": False,
            "has_focus": False,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 0,
            "topic_count": 0,
        }
    )
    assert result["status"] == "missing_self"
    assert result["next_step"] == "self"


def test_self_only_missing_anchor(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": False,
            "has_focus": False,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 0,
            "topic_count": 0,
        }
    )
    assert result["status"] == "missing_anchor_person"
    assert result["next_step"] == "anchor_person"


def test_self_and_anchor_missing_focus(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": False,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 1,
            "topic_count": 0,
        }
    )
    assert result["status"] == "missing_focus"
    assert result["next_step"] == "focus"


def test_seeds_missing_overlay(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": True,
            "has_soul_overlay_beyond_language": False,
            "has_receipt": False,
            "non_self_person_count": 1,
            "topic_count": 1,
        }
    )
    assert result["status"] == "missing_soul_overlay"
    assert result["next_step"] == "soul_overlay"


def test_seeds_and_overlay_missing_receipt(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": True,
            "has_soul_overlay_beyond_language": True,
            "has_receipt": False,
            "non_self_person_count": 1,
            "topic_count": 1,
        }
    )
    assert result["status"] == "missing_receipt"
    assert result["next_step"] == "receipt"
    assert result["mode"] == "INITIATE"


def test_complete_thin_graph(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": True,
            "has_soul_overlay_beyond_language": True,
            "has_receipt": True,
            "non_self_person_count": 1,
            "topic_count": 1,
        }
    )
    assert result["status"] == "complete"
    assert result["complete"] is True
    assert result["mode"] == "NORMAL"
    assert result["graph_thin"] is True
    assert result["soft_hooks_allowed"] is True


def test_complete_not_thin(status_mod):
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": True,
            "has_anchor_person": True,
            "has_focus": True,
            "has_soul_overlay_beyond_language": True,
            "has_receipt": True,
            "non_self_person_count": 3,
            "topic_count": 2,
        }
    )
    assert result["status"] == "complete"
    assert result["graph_thin"] is False
    assert result["soft_hooks_allowed"] is False


def test_receipt_without_self_is_not_complete(status_mod):
    """Corrupt/partial graphs: receipt alone must not skip seeds."""
    result = status_mod.compute_initiation_status(
        {
            "has_language": True,
            "has_self": False,
            "has_anchor_person": False,
            "has_focus": False,
            "has_soul_overlay_beyond_language": True,
            "has_receipt": True,
            "non_self_person_count": 0,
            "topic_count": 0,
        }
    )
    assert result["complete"] is False
    assert result["status"] == "missing_self"
```

- [ ] **Step 2: Run tests — expect fail**

```bash
uv run --group dev python -m pytest tests/test_initiation_status.py -v
```

Expected: import/file errors or `AttributeError` (module missing).

- [ ] **Step 3: Implement `initiation_status.py`**

Create `plugins/digital-brain-buddy/scripts/initiation_status.py`:

```python
#!/usr/bin/env python3
"""Pure initiation status derivation for digital-brain-buddy.

Agents and tests share these rules. Evidence is collected from BOOTSTRAP + SOUL;
this module does not call Neo4j.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

# Graph is "thin" when still at meeting-1 minimum density.
THIN_MAX_NON_SELF_PERSONS = 1
THIN_MAX_TOPICS = 1

# Ordered stages: first failed predicate wins.
_STAGES: tuple[tuple[str, str, str], ...] = (
    # status_key, evidence_flag, next_step
    ("missing_language", "has_language", "language"),
    ("missing_self", "has_self", "self"),
    ("missing_anchor_person", "has_anchor_person", "anchor_person"),
    ("missing_focus", "has_focus", "focus"),
    ("missing_soul_overlay", "has_soul_overlay_beyond_language", "soul_overlay"),
    ("missing_receipt", "has_receipt", "receipt"),
)

_REQUIRED_BOOLS = (
    "has_language",
    "has_self",
    "has_anchor_person",
    "has_focus",
    "has_soul_overlay_beyond_language",
    "has_receipt",
)


def compute_initiation_status(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return initiation status from a flat evidence map.

    Required boolean keys: has_language, has_self, has_anchor_person, has_focus,
    has_soul_overlay_beyond_language, has_receipt.

    Optional ints: non_self_person_count, topic_count (default 0).
    """
    for key in _REQUIRED_BOOLS:
        if key not in evidence:
            raise KeyError(f"missing evidence key: {key}")
        if not isinstance(evidence[key], bool):
            raise TypeError(f"{key} must be bool")

    non_self = int(evidence.get("non_self_person_count") or 0)
    topics = int(evidence.get("topic_count") or 0)

    for status_key, flag, next_step in _STAGES:
        if not evidence[flag]:
            return {
                "status": status_key,
                "complete": False,
                "mode": "INITIATE",
                "next_step": next_step,
                "graph_thin": True,
                "soft_hooks_allowed": False,
            }

    graph_thin = (
        non_self <= THIN_MAX_NON_SELF_PERSONS or topics <= THIN_MAX_TOPICS
    )
    return {
        "status": "complete",
        "complete": True,
        "mode": "NORMAL",
        "next_step": None,
        "graph_thin": graph_thin,
        "soft_hooks_allowed": graph_thin,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute initiation status from JSON evidence.")
    parser.add_argument(
        "evidence_json",
        nargs="?",
        help='JSON object evidence, e.g. \'{"has_language":true,...}\'',
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Path to JSON file with evidence",
    )
    args = parser.parse_args()
    if args.file:
        raw = args.file.read_text(encoding="utf-8")
    elif args.evidence_json:
        raw = args.evidence_json
    else:
        raise SystemExit("Provide evidence_json or --file")
    evidence = json.loads(raw)
    print(json.dumps(compute_initiation_status(evidence), indent=2, sort_keys=True))


# Path used only by CLI
from pathlib import Path  # noqa: E402  — kept after main for script clarity in plan; move to top in real file


if __name__ == "__main__":
    main()
```

**Implementation note for the engineer:** Move `from pathlib import Path` to the top of the file with the other imports (the plan block above is illustrative; do not leave the late import). Final import block:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping
```

- [ ] **Step 4: Run tests — expect pass**

```bash
uv run --group dev python -m pytest tests/test_initiation_status.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_initiation_status.py plugins/digital-brain-buddy/scripts/initiation_status.py
git commit -m "feat(buddy): add pure initiation status derivation"
```

---

### Task 2: SOUL template User overlay

**Files:**
- Modify: `plugins/digital-brain-buddy/assets/SOUL.template.md`

- [ ] **Step 1: Append User overlay section**

After the existing `## Response shape` section, append:

```markdown

## User overlay

Filled during initiate (and refined later). Leave blanks until set.

- Preferred language:
- How hard to push:
- What to protect (user-specific):
- Hard boundaries (never):
```

Do not remove or rewrite Core / Tone / How to think / What to protect / What not to do / Response shape.

- [ ] **Step 2: Confirm template still has Core buddy rules**

```bash
rg -n "This is a buddy|User overlay|Preferred language" plugins/digital-brain-buddy/assets/SOUL.template.md
```

Expected: both original Core line and new User overlay lines present.

- [ ] **Step 3: Commit**

```bash
git add plugins/digital-brain-buddy/assets/SOUL.template.md
git commit -m "feat(buddy): add User overlay section to SOUL template"
```

---

### Task 3: `initiate-protocol.md` reference

**Files:**
- Create: `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/references/initiate-protocol.md`

- [ ] **Step 1: Write the full protocol reference**

Create the file with this content (do not shorten markers or write path):

```markdown
# Initiate Protocol

Source of truth for empty/incomplete buddy first run. Session skill routes here
when `initiation_status.mode == INITIATE`.

Pure status rules also live in
`../../../scripts/initiation_status.py` (must stay aligned with this doc).

## When to run

After SOUL load and mandatory BOOTSTRAP, before the first normal buddy reply:

1. Build evidence (see below).
2. Compute status via the same rules as `compute_initiation_status` (or run
   `python3 ../../../scripts/initiation_status.py '<json>'` if helpful).
3. If `complete` is false → `INITIATE` mode for this conversation.
4. If `complete` is true → normal SKIP/READ/WRITE; soft hooks only if
   `soft_hooks_allowed`.

## Evidence → flags

| Flag | How to set true |
| --- | --- |
| `has_language` | SOUL `## User overlay` has non-empty Preferred language |
| `has_self` | Person with `relation = "self"` (or equivalent) exists |
| `has_anchor_person` | At least one non-self Person with a clear relation/role |
| `has_focus` | At least one Topic treated as current focus |
| `has_soul_overlay_beyond_language` | User overlay has at least one of: how hard to push / protect / hard boundaries filled |
| `has_receipt` | JournalEntry with `kind = "initiation_complete"` exists |
| `non_self_person_count` | Count of Person nodes without relation self |
| `topic_count` | Count of Topic nodes |

### Graph probes (read-only Cypher examples)

```cypher
// self
MATCH (p:Person)
WHERE toLower(coalesce(p.relation, '')) = 'self'
RETURN p.id AS id, p.name AS name
LIMIT 5

// receipt
MATCH (j:JournalEntry {kind: 'initiation_complete'})
RETURN j.id AS id, j.timestamp AS timestamp
ORDER BY j.timestamp DESC
LIMIT 1

// non-self people
MATCH (p:Person)
WHERE toLower(coalesce(p.relation, '')) <> 'self'
RETURN count(p) AS non_self_person_count

// topics
MATCH (t:Topic)
RETURN count(t) AS topic_count
```

## Status → next_step

| status | next_step | Agent action |
| --- | --- | --- |
| `missing_language` | `language` | Ask language once (or detect from user message); write SOUL Preferred language; then full intro in that language |
| `missing_self` | `self` | If language already set: one-line re-orient (not full intro). Ask name; create self Person |
| `missing_anchor_person` | `anchor_person` | Ask one important person + relation; create Person |
| `missing_focus` | `focus` | Ask current focus; create Topic |
| `missing_soul_overlay` | `soul_overlay` | Ask push style / protect / hard nos; edit User overlay |
| `missing_receipt` | `receipt` | Summarize; append receipt; announce normal mode |
| `complete` | null | Normal buddy |

## Opening order (first incomplete session)

1. **Language** — detect or ask; persist to SOUL immediately; all further initiate text in that language.
2. **Short intro** (2–4 sentences in that language) — buddy = personal graph memory + direct stance; brain is empty; this meeting only seeds you, one person, one focus, light stance; then normal chat.
3. **Q&A** for remaining gaps only.

On **resume** (language already set): skip full intro; one-line re-orient; next missing Q&A only.

## Writes

- Self: `Person` with `relation: "self"`, name = what user wants to be called.
- Anchor: `Person` with `relation` (partner, friend, …); link to self when schema allows.
- Focus: `Topic` with clear name; link / MENTIONS as appropriate.
- Receipt: **only** via `append_journal_entry`:
  - mint UUID `append_key`
  - `get_journal_chain_head` → `expected_version`
  - `append_journal_entry(append_key, content, timestamp, expected_version, properties={"kind": "initiation_complete"})`
  - timeout → `get_journal_append_receipt` with same key
  - then idempotent MENTIONS to self, anchor, focus
- Never create JournalEntry/FOLLOWS with raw Cypher.
- Never invent people/topics the user refused.

## Tone

Same buddy DNA: direct, compact, not therapist. One question at a time.
If user dumps multiple answers, extract all present fields and only ask for gaps.

## Soft hooks (only when complete and soft_hooks_allowed)

- At most **one** per session.
- Only on SKIP / low-stakes turns — never mid-crisis, dense WRITE, or focused READ.
- Examples: second person, second focus, stance refinement.
- Persist via normal WRITE path.

## Error handling

- MCP/graph down: do not mark complete; tell user to run `/digital-brain-up` or compose-up.
- Write failure: leave incomplete; resume re-reads graph; merge idempotently.
- User refuses a step: stay on that stage; end session incomplete if needed.
```

- [ ] **Step 2: Quick alignment check**

```bash
rg -n "initiation_complete|missing_language|soft_hooks" \
  plugins/digital-brain-buddy/skills/digital-brain-buddy-session/references/initiate-protocol.md \
  plugins/digital-brain-buddy/scripts/initiation_status.py
```

Expected: both files mention receipt kind and stage names consistently.

- [ ] **Step 3: Commit**

```bash
git add plugins/digital-brain-buddy/skills/digital-brain-buddy-session/references/initiate-protocol.md
git commit -m "docs(buddy): add initiate-protocol session reference"
```

---

### Task 4: Session skill gate and routing

**Files:**
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md`

- [ ] **Step 1: Update Start Here**

After the existing SOUL init bullet and before/within the BOOTSTRAP section, insert rules equivalent to:

```markdown
8. After the mandatory BOOTSTRAP pack on a new buddy conversation, derive
   initiation evidence and status using
   `references/initiate-protocol.md` (rules must match
   `../../scripts/initiation_status.py`).

   Evidence includes: SOUL language + overlay fields; self Person
   (`relation = "self"`); non-self people; Topics; JournalEntry with
   `kind = "initiation_complete"`.

9. If status is not `complete`, set session mode to `INITIATE` (or
   `INITIATE_RESUME` when any seed already exists). Do **not** open with
   normal buddy “thin memory” chat. Follow `references/initiate-protocol.md`:
   language → intro (or one-line re-orient) → next missing Q&A → seed writes →
   receipt. Only after `complete` switch to SKIP/READ/WRITE.

10. If status is `complete`, use normal Routing below. When
    `soft_hooks_allowed` / graph is thin, at most one soft progressive
    question per session on SKIP/low-stakes turns (see initiate-protocol).
```

Renumber surrounding list items if needed so the list stays coherent. Keep existing BOOTSTRAP people-map requirements.

- [ ] **Step 2: Extend Routing section**

Add `INITIATE` to the classify list:

```markdown
- `INITIATE`: empty or incomplete initiation (see `references/initiate-protocol.md`).
  Takes priority over SKIP/READ/WRITE until status is `complete`.
- `SKIP`: ...
- `READ`: ...
- `WRITE`: ...
```

- [ ] **Step 3: Subagent Mode — main agent owns initiate**

Under Main session agent owns, add:

```markdown
  - computing initiation_status after BOOTSTRAP and running INITIATE dialogue
  - light SOUL User overlay edits (identity-bootstrap rules)
```

Reader owns: add initiation evidence fields to BOOTSTRAP list (self flag, receipt, counts).

- [ ] **Step 4: Commit**

```bash
git add plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md
git commit -m "feat(buddy): gate session on initiation status after bootstrap"
```

---

### Task 5: BOOTSTRAP evidence in reader path

**Files:**
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/references/subagent-prompts.md`
- Modify: `plugins/digital-brain-buddy/agents/digital-brain-reader.md`

- [ ] **Step 1: Extend Bootstrap Evidence Pack in read-memory SKILL**

After item 5 (`recent_baseline`), add:

```markdown
6. `initiation_evidence` (always on BOOTSTRAP):
   - `self_person`: Person with relation self, or null
   - `anchor_candidates`: non-self Person list (id, name, relation) — parent
     decides if an anchor already exists
   - `focus_topics`: Topic list (id, name) or empty
   - `initiation_receipt`: JournalEntry with `kind = "initiation_complete"`
     (id, timestamp) or null
   - `non_self_person_count`, `topic_count`
   Do not invent missing entities. Parent session maps these to
   `initiation_status` flags (SOUL language/overlay are parent-local).
```

Update Output Shape bullets to include `initiation_evidence` when BOOTSTRAP.

- [ ] **Step 2: Extend Startup Reader Worker prompt**

In `subagent-prompts.md`, under Startup Reader Worker “What I need from you”, add:

```text
- include initiation_evidence: self Person (relation=self), non-self people,
  topics, JournalEntry kind=initiation_complete if any, and counts
```

Under Output contract, add:

```text
- initiation_evidence
```

- [ ] **Step 3: Update `agents/digital-brain-reader.md`**

In the description or body where BOOTSTRAP is listed, add that BOOTSTRAP must return `initiation_evidence` (self, people counts, topics, initiation_complete receipt).

- [ ] **Step 4: Commit**

```bash
git add \
  plugins/digital-brain-buddy/skills/digital-brain-buddy-read-memory/SKILL.md \
  plugins/digital-brain-buddy/skills/digital-brain-buddy-session/references/subagent-prompts.md \
  plugins/digital-brain-buddy/agents/digital-brain-reader.md
git commit -m "feat(buddy): include initiation evidence in BOOTSTRAP packs"
```

---

### Task 6: Identity bootstrap + write-memory receipt convention

**Files:**
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-identity-bootstrap/SKILL.md`
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md`

- [ ] **Step 1: Identity bootstrap — User overlay**

Add a section:

```markdown
## User overlay (initiate)

During first-run initiate, prefer filling `## User overlay` only:

- Preferred language
- How hard to push
- What to protect (user-specific)
- Hard boundaries (never)

Do not rewrite Core/Tone sections unless the user explicitly asks for a full
identity redesign. Template ships with an empty User overlay section.
```

- [ ] **Step 2: Write-memory — initiation receipt**

In write-memory SKILL, under write rules or a short new subsection:

```markdown
## Initiation receipt

When the session agent finishes meeting-1 seeds, append one JournalEntry with:

```json
{ "kind": "initiation_complete" }
```

passed as `properties` to `append_journal_entry`. Content should briefly name
self, anchor person, and focus. Then MENTIONS those three nodes. Do not use raw
Cypher to create the entry. Detection: `MATCH (j:JournalEntry {kind: 'initiation_complete'})`.
```

- [ ] **Step 3: Commit**

```bash
git add \
  plugins/digital-brain-buddy/skills/digital-brain-buddy-identity-bootstrap/SKILL.md \
  plugins/digital-brain-buddy/skills/digital-brain-buddy-write-memory/SKILL.md
git commit -m "feat(buddy): document initiate SOUL overlay and receipt writes"
```

---

### Task 7: README, CHANGELOG, version bump to 0.4.0

**Files:**
- Modify: `plugins/digital-brain-buddy/README.md`
- Modify: `plugins/digital-brain-buddy/CHANGELOG.md`
- Modify: `plugins/digital-brain-buddy/version.json`
- Modify: `plugins/digital-brain-buddy/.claude-plugin/plugin.json`
- Modify: `plugins/digital-brain-buddy/.codex-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `.agents/plugins/marketplace.json`

- [ ] **Step 1: README first-run section**

After Install section (or inside What you get), add:

```markdown
## First run (initiate)

On a fresh install the graph is empty and `SOUL.MD` starts from the neutral
template. The **first buddy session auto-enters initiate mode**:

1. Language preference
2. Short intro in that language
3. Seed: you (self), one important person, one current focus
4. Light SOUL User overlay
5. Initiation receipt in the journal (`kind: initiation_complete`)

Later sessions are normal buddy mode. If the graph is still thin, the buddy may
ask at most one soft gap question per session. Incomplete meetings **resume**
from the next missing piece (no wipe in this version).

Details: skill `digital-brain-buddy-session` → `references/initiate-protocol.md`.
```

Update the version line that says currently `0.2.0` / whatever is listed to `0.4.0`.

- [ ] **Step 2: CHANGELOG**

Prepend:

```markdown
## 0.4.0 — 2026-07-10

Initiate protocol for empty buddy first run.

- Auto-detect incomplete initiation after BOOTSTRAP; session mode `INITIATE`
- Progressive meeting: language → intro → self + anchor person + focus + light SOUL
- Graph markers + `JournalEntry.kind = initiation_complete` receipt
- Resume from next missing piece; soft progressive hooks when graph is thin
- Pure status helper: `scripts/initiation_status.py`
- SOUL template `## User overlay` section
```

- [ ] **Step 3: Version files**

Set all to `0.4.0` except Codex:

| File | Value |
| --- | --- |
| `version.json` | `"0.4.0"` |
| `.claude-plugin/plugin.json` → `version` | `0.4.0` |
| `.codex-plugin/plugin.json` → `version` | `0.4.0+codex.YYYYMMDDHHMMSS` (use current UTC timestamp) |
| `.claude-plugin/marketplace.json` entry for digital-brain-buddy | `0.4.0` |
| `.agents/plugins/marketplace.json` entry for digital-brain-buddy | `0.4.0` |

Generate Codex suffix:

```bash
date -u +%Y%m%d%H%M%S
```

Optionally refresh Codex `longDescription` / defaultPrompt to mention initiate once.

- [ ] **Step 4: Verify version alignment**

```bash
python3 - <<'PY'
import json
from pathlib import Path
root = Path('.')
v = json.loads((root/'plugins/digital-brain-buddy/version.json').read_text())
assert v == '0.4.0', v
claude = json.loads((root/'plugins/digital-brain-buddy/.claude-plugin/plugin.json').read_text())
assert claude['version'] == '0.4.0'
codex = json.loads((root/'plugins/digital-brain-buddy/.codex-plugin/plugin.json').read_text())
assert codex['version'].startswith('0.4.0')
for mp in ['.claude-plugin/marketplace.json', '.agents/plugins/marketplace.json']:
    data = json.loads((root/mp).read_text())
    plugins = data.get('plugins') or data
    # structure may be list or dict — find digital-brain-buddy and assert 0.4.0
    found = False
    def walk(obj):
        global found
        if isinstance(obj, dict):
            if obj.get('name') == 'digital-brain-buddy' or obj.get('id') == 'digital-brain-buddy':
                assert str(obj.get('version','')).startswith('0.4.0'), (mp, obj)
                found = True
            for x in obj.values():
                walk(x)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)
    walk(data)
    assert found, f'no digital-brain-buddy in {mp}'
print('versions ok')
PY
```

Expected: `versions ok`.

- [ ] **Step 5: Run full unit subset for this feature + quick sanity**

```bash
uv run --group dev python -m pytest tests/test_initiation_status.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add \
  plugins/digital-brain-buddy/README.md \
  plugins/digital-brain-buddy/CHANGELOG.md \
  plugins/digital-brain-buddy/version.json \
  plugins/digital-brain-buddy/.claude-plugin/plugin.json \
  plugins/digital-brain-buddy/.codex-plugin/plugin.json \
  .claude-plugin/marketplace.json \
  .agents/plugins/marketplace.json
git commit -m "release(buddy): 0.4.0 initiate protocol packaging"
```

---

### Task 8: Spec coverage self-check + final verification

**Files:** none new (verification only)

- [ ] **Step 1: Map spec → tasks**

Confirm each design goal has a task:

| Spec requirement | Task |
| --- | --- |
| Auto-detect incomplete | 1, 4, 5 |
| Language → intro → Q&A | 3, 4 |
| Self + anchor + focus + light SOUL | 3, 6 |
| Graph markers + receipt kind | 1, 3, 6 |
| Resume next gap | 3, 4 |
| Soft hooks post-complete | 1, 3, 4 |
| No new skill/command | (all — session extension only) |
| Version / README | 7 |
| SOUL template overlay | 2 |

- [ ] **Step 2: Run tests once more**

```bash
uv run --group dev python -m pytest tests/test_initiation_status.py -v
```

- [ ] **Step 3: Grep for contract strings**

```bash
rg -n "INITIATE|initiation_complete|User overlay|initiate-protocol" \
  plugins/digital-brain-buddy/skills \
  plugins/digital-brain-buddy/assets \
  plugins/digital-brain-buddy/scripts/initiation_status.py \
  plugins/digital-brain-buddy/README.md
```

Expected: hits in session skill, initiate-protocol, template, status script, README.

- [ ] **Step 4: Mark plan complete in PR/summary**

No further code; ready for host install smoke (manual): empty Neo4j + missing SOUL → first session should enter language ask.

---

## Testing summary

| Test | Command |
| --- | --- |
| Status unit tests | `uv run --group dev python -m pytest tests/test_initiation_status.py -v` |
| CLI smoke | `python3 plugins/digital-brain-buddy/scripts/initiation_status.py '{"has_language":false,"has_self":false,"has_anchor_person":false,"has_focus":false,"has_soul_overlay_beyond_language":false,"has_receipt":false}'` |
| Version alignment | Python assert script in Task 7 |

Manual (post-merge, not blocking unit plan):

1. Wipe or use empty Neo4j; remove local `SOUL.MD` or re-init from template.
2. Start buddy session → expect language question, not empty-memory monologue.
3. Partial fill, new session → resume next gap.
4. Complete checklist → next session normal buddy.

---

## Out of scope (do not implement in this plan)

- `/digital-brain-initiate` command or wipe/reset
- Dedicated initiate skill packaging
- MCP schema migrations for `kind` (free property on append is enough)
- ADK multi-agent parity for initiate
- Changing compose-up / SessionStart hooks
```
