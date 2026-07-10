# Host-Agnostic Harness Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make harness session bind portable so any brain (Grok / Claude / Codex) can open a session and feed quality sensors without Claude-only SessionStart.

**Architecture:** Thin facade `open_harness_session` over existing `generation.py` pin APIs; returns `SessionHandle`. CLI and buddy skill become adapters. `active/` remains dual-process breadcrumb only — never sole brain identity.

**Tech Stack:** Python 3.12, pytest, existing `digital_brain.maintenance.generation`, plugin skill MD.

**Spec:** `docs/superpowers/specs/2026-07-10-host-agnostic-harness-session-design.md`

---

## File map

| File | Role |
| --- | --- |
| `digital_brain/maintenance/session.py` | **Create** — `SessionHandle`, `open_harness_session`, `resolve_handle_for_chat` |
| `digital_brain/maintenance/generation.py` | Optional: host-prefixed ephemeral session ids |
| `digital_brain/maintenance/__init__.py` | Export new symbols |
| `scripts/pin_harness_generation.py` | `--host`, SessionHandle-shaped `--json` |
| `tests/test_open_harness_session.py` | **Create** — hermetic H1 tests |
| `plugins/.../digital-brain-buddy-session/SKILL.md` | Step 0 portable open; forbid active/-only |
| `docs/superpowers/specs/2026-07-10-host-agnostic-harness-session-design.md` | Already written (H0) |

Also in this branch (spec complete): H3 active/ session match in quality.py; H4
`/digital-brain-session` command. Deferred: full MCP-server-side open (digests
need host FS).

---

### Task 1: SessionHandle + open_harness_session library

**Files:**
- Create: `digital_brain/maintenance/session.py`
- Modify: `digital_brain/maintenance/__init__.py`
- Test: `tests/test_open_harness_session.py`

- [x] **Step 1: Write failing tests** for open / resume / force_new / never active-only / no SOUL body

- [x] **Step 2: Implement `session.py`**

```python
# SessionHandle dataclass + to_public_dict()
# open_harness_session(...) -> SessionHandle  # pin only; skip_record default True
# resolve_handle_for_chat(...)  # env + sessions/<id>/ only; NEVER active/ alone
```

- [x] **Step 3: Export from `__init__.py`**

- [x] **Step 4: Run tests** `uv run --group dev pytest tests/test_open_harness_session.py -v`

---

### Task 2: CLI host + JSON handle parity

**Files:**
- Modify: `scripts/pin_harness_generation.py`

- [x] **Step 1: Add `--host`**, emit SessionHandle fields in `--json` (`harness_generation_id`, `mode`, `host`, `schema_version`, keep legacy `generation_id` alias)

- [x] **Step 2: Test via subprocess or unit import of summary helper**

---

### Task 3: Buddy skill step 0

**Files:**
- Modify: `plugins/digital-brain-buddy/skills/digital-brain-buddy-session/SKILL.md`

- [x] **Step 1: Rewrite Harness Generation Pin section** as portable `open_harness_session` step 0 for all hosts

- [x] **Step 2: Explicit forbid** adopting `active/` alone; memory continues without pin; sensors require handle

---

### Task 4: Regression + existing harness tests

- [x] **Step 1:** `uv run --group dev pytest tests/test_open_harness_session.py tests/test_harness_generation.py -v` (34 passed)

- [ ] **Step 2:** Commit when green (user approval if needed)

---

## Acceptance (from design §14)

1. Without Claude SessionStart: open → handle → can call sensors with id  
2. Concurrent sessions use distinct session_ids  
3. Leftover verify `active/` not adopted as handle  
4. Claude path still works via existing CLI  
5. Sensors refuse without handle; journal works  
6. Open never activates Alias/overlay  
