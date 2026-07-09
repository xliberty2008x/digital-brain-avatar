---
name: digital-brain-buddy-identity-bootstrap
description: Initialize or refine the Digital Brain buddy identity by creating or editing the bundled SOUL file, keeping only durable voice, stance, and behavioral rules rather than temporary moods.
---

# Digital Brain Buddy Identity Bootstrap

Use this skill when the user wants to initialize, rewrite, or refine the buddy persona itself.

## Start Here

1. Read `../../SOUL.MD`.

2. If the file is missing or should be reset, use `../../scripts/init_soul.py`.

3. Treat `SOUL.MD` as durable identity, not as a journal.

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

## Editing Rules

- Preserve the section structure unless the user explicitly wants a redesign.
- Keep statements short and operational.
- Prefer hard behavioral rules over vague adjectives.
- If a requested identity change conflicts with existing stance, call it out before rewriting.

## Bootstrap Command

Initialize a new SOUL file from the plugin template:

```bash
python3 ../../scripts/init_soul.py ../../SOUL.MD --force
```
