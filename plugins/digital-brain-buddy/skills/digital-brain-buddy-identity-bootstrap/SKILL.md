---
name: digital-brain-buddy-identity-bootstrap
description: Initialize or refine the Digital Brain buddy identity by creating or editing the local SOUL file from the shipped template, keeping only durable voice, stance, and behavioral rules rather than temporary moods.
---

# Digital Brain Buddy Identity Bootstrap

Use this skill when the user wants to initialize, rewrite, or refine the buddy persona itself.

## Start Here

1. Check for a local identity file at `../../SOUL.MD`.
   - This path is **per-user / local**. It is not shipped as the author's personal
     identity and should not be committed to git.
2. If the file is missing, create it from the template (do not invent a persona cold):

```bash
python3 ../../scripts/init_soul.py ../../SOUL.MD
```

3. If a reset is requested, use `--force` only after confirming with the user:

```bash
python3 ../../scripts/init_soul.py ../../SOUL.MD --force
```

4. Then read `../../SOUL.MD` and refine it.
5. Treat `SOUL.MD` as durable identity, not as a journal.

Shipped neutral defaults live in `../../assets/SOUL.template.md` only.

## What Belongs In `SOUL.MD`

- stable tone
- durable conversational stance
- recurring values
- what the buddy protects
- what the buddy must never do
- response shape

## What Does Not Belong In `SOUL.MD`

- temporary moods
- one-off incidents
- short-term fears unless they became a repeated identity pattern
- raw event history that belongs in graph memory

## User overlay (initiate)

During first-run initiate, prefer filling `## User overlay` only:

- Preferred language
- How hard to push
- What to protect (user-specific)
- Hard boundaries (never)

Do not rewrite Core/Tone sections unless the user explicitly asks for a full
identity redesign. Template ships with an empty User overlay section.

## Editing Rules

- Preserve the section structure unless the user explicitly wants a redesign.
- Keep statements short and operational.
- Prefer hard behavioral rules over vague adjectives.
- If a requested identity change conflicts with existing stance, call it out before rewriting.
