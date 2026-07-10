# Module 3: OAuth and OIDC flows

## Why OAuth exists

You want an app to call Google/GitHub **on behalf of a user** without ever
seeing the user’s password. OAuth issues **scoped, revocable tokens** after
user consent.

OIDC (OpenID Connect) sits on top of OAuth and adds an **ID token** (JWT) that
tells your app *who* logged in.

---

## 1. Client credentials (machine-to-machine)

The app authenticates as itself with `client_id` + `client_secret` and gets a
token for system APIs. No human user.

- **Use when:** backend jobs, service accounts.  
- **Not for:** “act as Alice on GitHub.”

---

## 2. Authorization code flow (user login) — preferred for web apps

1. User clicks “Sign in with Google”.  
2. Browser redirects to Google (front channel).  
3. User consents.  
4. Google redirects back with a short-lived **authorization code** (not the
   final secret).  
5. Your **server** exchanges `code` + `client_id` + `client_secret` on the
   **back channel** for tokens.  
6. Server creates a session and may mint an **internal** JWT for the agent
   runtime (agent still should not see Google/GitHub secrets).

### Why a code instead of putting tokens in the URL?

- Front channel (browser) is leaky (history, logs, extensions).  
- Back channel (server ↔ IdP) is trusted.  
- Stealing a code without `client_secret` is not enough.

### ID token vs access token

| Token | Purpose |
| --- | --- |
| `id_token` (JWT) | Who logged in (`sub`, `email`, …). Verify signature via IdP JWKS. |
| `access_token` | Call Google/GitHub APIs. Opaque to your app’s logic often. |

Example verification idea: libraries like `google-auth` fetch Google’s public
keys and check the ID token signature so you know Google attested
`alice@example.com`.

---

## 3. Where secrets live

| Secret | Who holds it |
| --- | --- |
| IdP `client_secret` | Your server only (env / secret manager) |
| User’s GitHub access token | Your server vault / DB, keyed by user id |
| Internal JWT signing key | Your server only |
| Agent / LLM context | **No** external OAuth secrets |

Illustrative (not production) storage:

```cypher
MERGE (u:User {email: "alice@example.com"})
SET u.github_access_token = "gho_example_not_a_real_token"
```

Never commit real tokens. Prefer a dedicated secret store over graph properties
in production.

---

## 4. Tool call with user-auth (safe pattern)

1. Agent requests “create GitHub repo” with **internal** JWT only.  
2. Tool wrapper validates internal JWT → `sub=alice@example.com`.  
3. Server loads Alice’s GitHub token from the vault.  
4. Server calls GitHub.  
5. Agent receives “repo created” — not the GitHub token.

If prompt injection makes the model dump its context, it still should not hold
the GitHub secret.

---

## 5. Local Digital Brain reality check

- Local MCP/Neo4j for buddy sessions: **no OAuth** today; localhost trust.  
- Docs here describe the multi-user target, not the current Compose stack.  
- See root `SECURITY.md` and `docs/architecture/auth_architecture.md`.

---

## 6. Checklist

- [ ] Prefer authorization code (+ PKCE for public clients) over implicit  
- [ ] Keep `client_secret` server-side  
- [ ] Store external tokens outside the agent context  
- [ ] Short-lived internal JWTs with explicit scopes  
- [ ] Never put real tokens in git or in mentorship examples  
