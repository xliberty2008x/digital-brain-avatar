# Module 2: Authorization strategies in ADK

How to think about “who is allowed to call which tool” in this project.

## 1. Agent-auth (identity of the agent / system)

The agent has its own system identity (service principal, machine JWT, etc.).

- **How it works:** when the agent writes to Neo4j, it presents *its* token.
  The backend trusts the writer-agent service account.
- **Good for:** background jobs and system automation that should not require a
  human click on every step.
- **Risk:** if the agent is compromised, it has whatever power that system
  identity was granted — independent of which end-user prompted it.

## 2. User-auth (identity of the human)

The agent is only a messenger. It acts with the **user’s** delegated rights.

- **How it works:** external actions (post to Telegram, call GitHub) use an
  OAuth token that belongs to the user, obtained via a consent screen.
- **Good for:** least privilege — the agent cannot do more than the user can.
- **Mechanics:** classic OAuth / OIDC delegated access.

## 3. What this project does

| Path | Auth model today |
| --- | --- |
| Local Compose MCP + buddy plugin | **None** — loopback trust; see `SECURITY.md` |
| Experimental JWT helpers | Learning / future multi-user building blocks |
| Target multi-user design | Prefer **user-auth** for user data; **agent-auth** only for fixed system tools |

When designing a tool, ask: should this always run as the system (agent-auth),
or must it depend on *who* is chatting (user-auth)?

## Related

- Module 1: JWT basics  
- Module 3: OAuth / OIDC flows  
- `docs/architecture/auth_architecture.md`  
