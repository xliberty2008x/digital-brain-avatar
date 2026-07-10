# ADK Context Flow: How context moves between agents

## Core concepts

ADK has **two** systems for sharing information between agents:

| System | What it is | Who sees it | When to use |
| --- | --- | --- | --- |
| **Session state** | Key-value store | All agents in the session | Structured handoffs |
| **Conversation history** | Message history | All by default; can disable | Conversational context |

---

## 1. Session state (`ctx.session.state`)

A shared dictionary. Agents can read and write.

### Writing state

```python
# Option 1: output_key (automatic)
router_agent = LlmAgent(
    output_key="routing_decision"  # agent reply stored under this key
)

# Option 2: manual in orchestrator
ctx.session.state["clarify_missing"] = ["event", "who"]
```

### Reading state

```python
# In orchestrator Python
decision = ctx.session.state.get("routing_decision")
missing = ctx.session.state.get("clarify_missing")

# In LlmAgent instruction placeholders
instruction = "Current missing info: {clarify_missing}"
```

### Rules of thumb

- Prefer small structured payloads (JSON-like dicts / short strings).
- Do **not** put secrets or JWTs in session state (see auth architecture).
- Clean or overwrite keys that would confuse later turns.

---

## 2. Conversation history

ADK keeps the message thread. Sub-agents may inherit or exclude history depending
on configuration.

- Use history for natural language continuity.
- Prefer session state for machine-readable routing decisions.
- Large tool dumps should be summarized into state instead of left raw in history.

---

## 3. Digital Brain multi-agent pattern (historical MVP)

Typical WRITE path (conceptually):

1. **Router** → `routing_decision` in state (`SKIP` | `READ` | `WRITE`)
2. **Extractor** → entities / mood / search query in state
3. **Retriever** → related graph context in state
4. **Writer** → structured write plan (no raw secrets)
5. **Executor** → MCP tools (today: append protocol, not raw journal Cypher)

Live instructions live in `digital_brain/agents/*.py`. Journal persistence must
use the server-owned append tools documented in `mcp_servers/cypher/README.md`.

---

## 4. Callbacks and context hygiene

Callbacks under `digital_brain/callbacks/` may:

- Sanitize unsafe Cypher patterns  
- Guard journal chain writes  
- Rate-limit or strip oversized tool results  

They operate on tool args / results, not as a substitute for auth.

---

## 5. Practical guidance

| Goal | Prefer |
| --- | --- |
| Pass a route enum | Session state |
| Remember what the user just said | History |
| Share retrieved node IDs | Session state |
| Hide tokens from the model | ContextVar / server layer only |

---

## Related docs

- `docs/architecture/auth_architecture.md` — token policy (design)
- `docs/AGENT_PROMPTS.md` — historical prompt sketches
- `docs/GRAPH_SCHEMA_CONTRACT.md` — graph constitution
