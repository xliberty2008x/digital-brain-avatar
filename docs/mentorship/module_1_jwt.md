# Module 1: JWT basics (JSON Web Token)

## 1.1 Main surprise: JWT is not encryption by default

> [!WARNING]
> A typical JWT (`id_token` style) is **not encrypted**. It is **signed** and
> Base64-encoded. Anyone who has the token can decode the payload on
> [jwt.io](https://jwt.io) and read claims such as email or name.

### Then what is the point? (Digital signature)

Think of a **transparent envelope** with a wax seal:

1. **Transparency:** anyone can read the contents (“this is Alice”).  
2. **Seal (signature):** if someone changes “Alice” to “Attacker”, the seal breaks.

JWT:

- **Payload:** open JSON claims (e.g. email, subject).  
- **Signature:** proves integrity — change one character and verification fails.

**Conclusion:** do not put secrets (passwords, API keys) in a JWT payload. Put
identifiers and scopes the server already knows.

### 1.2 How the signature works (simplified)

1. Take header + payload.  
2. Combine with a server-side `SECRET_KEY` (HMAC) or private key (asymmetric).  
3. Hash → signature (one-way; you cannot recover the secret from the signature).  
4. Receiver recomputes the signature with the real secret/public key and compares.

Whoever holds the signing secret (or private key) can mint valid tokens.

---

## 0. Why authorization at all?

If you are the only user on a local machine talking to local tools, you may not
need JWT. Multi-user servers need:

- proof of **who** is calling  
- **scopes** limiting tools  
- short lifetime so stolen tokens expire  

Local digital-brain-buddy MCP is currently **unauthenticated** (loopback trust).
JWT is for a future multi-user path — see `docs/architecture/auth_architecture.md`.

---

## 1.3 Structure of a JWT

Three Base64url parts separated by dots:

```text
header.payload.signature
```

Example claims (illustrative):

```json
{
  "sub": "alice@example.com",
  "scopes": ["read_memory", "write_memory"],
  "exp": 1735689600
}
```

---

## 1.4 Create and verify (project helpers)

Experimental code: `digital_brain/security/jwt_handler.py`

- `create_access_token(data, expires_delta=None)`  
- `decode_access_token(token)` → payload or `None`  

Set `JWT_SECRET_KEY` in the environment for anything beyond local learning tests.
Do not rely on the built-in development default on a shared machine.

Run the simple script:

```bash
PYTHONPATH=. python test_jwt.py
```

---

## 1.5 Common mistakes

| Mistake | Why it hurts |
| --- | --- |
| Putting passwords in claims | Payload is readable |
| Long-lived tokens without rotation | Stolen tokens stay useful |
| Sharing the signing secret with the agent | Agent becomes a token mint |
| Treating “decode on jwt.io” as a bug | Encoding ≠ encryption |

---

## Next modules

- Module 2: how ADK sessions relate to auth context  
- Module 3: OAuth / OIDC flows and external tokens  
