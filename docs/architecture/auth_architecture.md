# Digital Brain Authentication and Authorization Architecture

> **Status:** design / incomplete. Local buddy + MCP stacks today do **not**
> implement multi-user OAuth. Experimental JWT helpers live under
> `digital_brain/security/`. See root `SECURITY.md`.

## 1. Overview

Target design: many users access a shared agent surface without the agent itself
handling tokens or login UX. Security logic stays in the orchestrator / runtime
layer.

## 2. Architectural decisions

| Question | Decision |
| --- | --- |
| Where does the user log in? | Web UI |
| How does the user authenticate? | OAuth via Entra/Okta (or similar IdP) |
| Where is user profile data stored? | At the IdP |
| Where are external OAuth tokens (e.g. GitHub)? | IdP / token vault |
| Agents per user? | Session-per-agent |
| Does the agent need an API key? | **No** |
| How is the agent identified? | Runtime metadata (ADK) |

### 2.1 Token storage policy

| Token | Storage | Agent access |
| --- | --- | --- |
| Internal JWT | ContextVar (runtime memory) | **No** — agent must not see it |
| OAuth tokens (GitHub, etc.) | IdP token vault | **No** |
| User session | Server-side (Redis / provider) | **No** |

**Principle:** the agent never stores tokens in `state`, memory, or tools args.
OAuth exchange is server-side only.

## 3. User flow

1. User opens Digital Brain web UI  
2. User clicks “Login with Microsoft/Google”  
3. Browser redirects to Entra/Okta  
4. User authenticates at the IdP  
5. IdP redirects with authorization code  
6. Server exchanges code for ID + access tokens  
7. Server creates a session for the user  
8. Server starts an agent for that session  
9. User talks to the agent through the web UI  

## 4. Agent invocation flow

On session agent start, the orchestrator:

1. Reads user identity from the session (email, scopes)
2. Mints a short-lived internal JWT, e.g.
   `{ "sub": "user@example.com", "scopes": ["read_memory", "write_memory"], "exp": <15m> }`
3. Places the JWT in a ContextVar (invisible to the LLM)
4. Filters tools by scopes
5. Starts the agent with the filtered tool set

## 5. Tool execution flow

1. Agent calls a tool  
2. Decorator / wrapper reads JWT from ContextVar  
3. Validates signature, expiry (refresh if policy allows), and required scope  
4. If the tool needs an external service, the server fetches the user’s OAuth
   token from the vault and injects it  
5. Tool runs; agent receives only the result  

## 6. Components

### 6.1 `jwt_handler.py` (experimental, present)

- `create_access_token(data)` — signed JWT  
- `decode_access_token(token)` — verify + decode  

### 6.2 `context.py` (TODO / partial)

- ContextVar for current JWT  
- `set_agent_token` / `get_agent_token`  

### 6.3 `decorators.py` (TODO)

- `@require_scope(scope)`  
- Optional token regeneration on expiry  

### 6.4 Tool registry (dynamic MCP)

Load-time: discover MCP tools → map `tool_name` → required scope from DB →
deny by default if unmapped or scope missing → cache filtered list.

Runtime wrapper double-checks JWT scopes before calling the MCP tool.

### 6.5 ADK session integration (TODO)

- How ADK owns sessions  
- Binding user → session  
- Passing context into the agent  

## 7. Non-functional requirements

| Requirement | Approach |
| --- | --- |
| Speed | JWT verified locally |
| Security | Short-lived tokens; regenerate as needed |
| Isolation | Session-per-agent |
| Scale | Stateless tokens + session store |
| UX | Transparent after login |

## 8. Open questions

1. ADK session lifecycle details  
2. Concrete token-vault API for the chosen IdP  
3. User session timeout handling  

## 9. Note for local digital-brain-buddy

The local Compose MCP server is **unauthenticated**. Multi-user JWT/OAuth is a
future cloud path, not the current local threat model.
