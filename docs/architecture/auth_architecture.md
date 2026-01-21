# Архітектура Аутентифікації та Авторизації Digital Brain

## 1. Огляд

Система забезпечує безпечний доступ тисяч користувачів до спільного Агента без додаткових кроків з їхнього боку. Агент не знає про існування токенів чи безпеки — вся логіка прихована на рівні інфраструктури.

---

## 2. Архітектурні рішення

| Питання | Рішення |
|---------|---------|
| Де логіниться юзер? | Веб-інтерфейс |
| Як юзер автентифікується? | OAuth через Entra/Okta |
| Де зберігаються дані юзера? | У провайдера (Entra/Okta) |
| Де зберігаються OAuth токени (GitHub)? | Token Vault провайдера |
| Скільки агентів на юзера? | Session-per-Agent (один агент на сесію) |
| Чи потрібен API Key агенту? | **Ні** |
| Як ідентифікується агент? | Через метадані Runtime (ADK) |

### 2.1. Політика зберігання токенів

| Токен | Де зберігається | Доступ Агента |
|-------|-----------------|---------------|
| Internal JWT | ContextVar (runtime memory) | **Ні** — Агент не бачить |
| OAuth tokens (GitHub тощо) | Token Vault провайдера (Entra/Okta) | **Ні** — Агент не бачить |
| User session | Server-side (Redis/Provider) | **Ні** — Агент не бачить |

**Ключовий принцип:**
- Агент **НІКОЛИ** не зберігає токени у своєму `state`, `memory` чи будь-де.
- Вся security-логіка працює на рівні **Orchestrator/Runtime**.
- OAuth flow відбувається **програмно** на сервері, Агент ізольований.

---

## 3. User Flow

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. USER → Відкриває веб-інтерфейс Digital Brain                  │
│ 2. USER → Натискає "Login with Microsoft/Google"                 │
│ 3. BROWSER → Редірект на Entra/Okta                              │
│ 4. USER → Логіниться у провайдера                                │
│ 5. PROVIDER → Редірект назад з Authorization Code                │
│ 6. SERVER → Обмінює Code на ID Token + Access Token              │
│ 7. SERVER → Створює Session для цього User                       │
│ 8. SERVER → Запускає Agent для цієї Session                      │
│ 9. USER → Спілкується з Agent через веб-інтерфейс                │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Agent Invocation Flow

```
┌──────────────────────────────────────────────────────────────────┐
│ ORCHESTRATOR (при створенні Agent для Session)                   │
├──────────────────────────────────────────────────────────────────┤
│ 1. Отримує User Identity з Session (email, scopes)               │
│ 2. Генерує Internal JWT:                                         │
│    {                                                             │
│      "sub": "user_email@example.com",                            │
│      "scopes": ["read_memory", "write_memory", "github"],        │
│      "exp": <15 хвилин>                                          │
│    }                                                             │
│ 3. Встановлює JWT у ContextVar (невидимо для Agent)              │
│ 4. Фільтрує список Tools на основі scopes                        │
│ 5. Запускає Agent з відфільтрованими Tools                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. Tool Execution Flow

```
┌──────────────────────────────────────────────────────────────────┐
│ AGENT викликає Tool (напр. github_search)                        │
├──────────────────────────────────────────────────────────────────┤
│ 1. DECORATOR (@require_scope) перехоплює виклик                  │
│ 2. Читає JWT з ContextVar                                        │
│ 3. Перевіряє:                                                    │
│    a) Чи валідний підпис?                                        │
│    b) Чи не expired? → Якщо так: регенерує новий JWT             │
│    c) Чи є потрібний scope?                                      │
│ 4. Якщо Tool потребує зовнішнього сервісу (GitHub):              │
│    a) Бере user_id з JWT                                         │
│    b) Отримує GitHub Access Token з Token Vault провайдера       │
│    c) Підставляє токен у запит до GitHub                         │
│ 5. Виконує Tool                                                  │
│ 6. Повертає результат Agent                                      │
│                                                                  │
│ AGENT нічого не знає про токени — він просто отримує результат   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 6. Компоненти системи

### 6.1. jwt_handler.py (✅ Готово)
- `create_access_token(data)` — створює підписаний JWT
- `decode_access_token(token)` — перевіряє та декодує JWT

### 6.2. context.py (⬜ TODO)
- `ContextVar` для зберігання JWT у поточному потоці
- `set_agent_token(token)` — встановити
- `get_agent_token()` — отримати

### 6.3. decorators.py (⬜ TODO)
- `@require_scope(scope)` — перевіряє права перед виклком Tool
- Автоматична регенерація токена при expiration

### 6.4. Tool Registry (Dynamic MCP Integration)
**Файл:** `digital_brain/tools/registry.py`

Ми відмовились від хардкоду на користь динамічного підходу з **Zero Trust**.

**Рівень 1: Dynamic Discovery & Filtering (Load-Time)**

1. **Fetch**: При старті сесії отримуємо актуальний список тулз з MCP-серверів.
2. **Consult DB**: Отримуємо з БД мапінг дозволів: `tool_name` -> `required_scope`.
   - *Примітка: Розробник додає ці правила в БД при деплої нової тулзи.*
3. **Filter**:
   - Якщо тулзи немає в БД → **DENY** (блокуємо за замовчуванням).
   - Якщо у юзера немає потрібного скоупу → **DENY**.
4. **Cache**: Відфільтрований список кешується в сесії (Redis/Memory) з TTL (напр. 15 хв), щоб не смикати MCP та БД щоразу.

**Рівень 2: Обгортка (Runtime)**
Для кожного дозволеного інструменту ми створюємо Runtime Wrapper:

```python
def wrap_mcp_tool(original_tool, required_scope):
    """Програмний аналог декоратора"""
    def wrapper(*args, **kwargs):
        # 1. Перевірка токена з контексту (Internal JWT)
        token = get_agent_token()
        payload = decode_access_token(token)
        
        # 2. Перевірка прав (Runtime Double-Check)
        if required_scope not in payload.get("scopes", []):
            raise PermissionError(f"Missing scope: {required_scope}")
            
        # 3. Виклик оригінальної тулзи (через HTTP/mTLS до MCP)
        # Auth header додається автоматично інфраструктурним шаром
        return original_tool(*args, **kwargs)
    return wrapper
```

### 6.5. ADK Session Integration (⬜ TODO — потребує дослідження)
- Як ADK керує сесіями
- Як прив'язати User до Session
- Як передати контекст в Agent

---

## 7. Non-Functional Requirements

| Вимога | Рішення |
|--------|---------|
| Швидкість | JWT перевіряється локально (без звернення до БД) |
| Безпека | Токени живуть 15 хв; автоматична регенерація |
| Ізоляція | Кожен User має окремий Agent (Session-per-Agent) |
| Масштабованість | Stateless токени; Session Store (Redis/Provider) |
| UX | Юзер нічого не робить — все прозоро після логіну |

---

## 8. Відкриті питання (для дослідження)

1. **ADK Sessions**: Як саме ADK керує сесіями? Документація потребує вивчення.
2. **Token Vault API**: Який саме API у Entra/Okta для отримання збережених токенів?
3. **Session Timeout**: Як хендлити закінчення сесії юзера?

---

## 9. Діаграма архітектури

```
┌─────────────┐      OAuth       ┌─────────────────┐
│    User     │ ───────────────► │  Entra / Okta   │
│  (Browser)  │ ◄─────────────── │  (IDP + Vault)  │
└─────────────┘   ID + Tokens    └─────────────────┘
       │                                  │
       │ Session                          │ Token Vault
       ▼                                  ▼
┌─────────────────────────────────────────────────────┐
│              Digital Brain Server                   │
│  ┌───────────────┐    ┌─────────────────────────┐   │
│  │  Orchestrator │───►│  Agent (per Session)    │   │
│  │  - JWT Issue  │    │  - Filtered Tools       │   │
│  │  - ContextVar │    │  - No token knowledge   │   │
│  └───────────────┘    └─────────────────────────┘   │
│           │                      │                  │
│           │                      │ @require_scope   │
│           ▼                      ▼                  │
│  ┌─────────────────────────────────────────────┐    │
│  │              Tool Layer                     │    │
│  │  - Implicit JWT validation                  │    │
│  │  - Auto-refresh on expiration               │    │
│  │  - External token fetch from Vault          │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

---

## 10. Статус

- [x] jwt_handler.py — реалізовано
- [ ] context.py — запланвоано
- [ ] decorators.py — заплановано
- [ ] tool_registry.py — заплановано
- [ ] ADK Session Integration — потребує дослідження
- [ ] Entra/Okta Integration — потребує дослідження
