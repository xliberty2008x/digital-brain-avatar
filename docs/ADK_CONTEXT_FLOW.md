# ADK Context Flow: Як передається контекст між агентами

## Основні концепції

В ADK є **дві** системи для передачі інформації між агентами:

| Система | Що це | Хто бачить | Коли використовувати |
|---------|-------|------------|---------------------|
| **Session State** | Key-value сховище | Всі агенти в сесії | Для передачі структурованих даних |
| **Conversation History** | Історія повідомлень | За замовчуванням всі, можна вимкнути | Для контексту розмови |

---

## 1. Session State (`ctx.session.state`)

Це **словник**, доступний всім агентам. Агенти можуть писати і читати.

### Як агент пише в state:

```python
# Варіант 1: Через output_key (автоматично)
router_agent = LlmAgent(
    output_key="routing_decision"  # ← Відповідь агента автоматично збережеться
)

# Варіант 2: Вручну в Orchestrator
ctx.session.state["clarify_missing"] = ["event", "who"]
```

### Як агент читає з state:

```python
# В Orchestrator (Python код)
decision = ctx.session.state.get("routing_decision")
missing = ctx.session.state.get("clarify_missing")

# В LlmAgent (через placeholder в промпті)
instruction="Current missing info: {clarify_missing}"
```

### Приклад: State після CLARIFY

```python
ctx.session.state = {
    "routing_decision": {
        "route": "CLARIFY",
        "reason": "Missing event and who",
        "missing": ["event", "who"]
    },
    "clarify_missing": ["event", "who"]
}
```

---

## 2. Conversation History

Кожен `LlmAgent` за замовчуванням бачить **всю історію розмови**.

### Контроль через `include_contents`:

```python
# Бачить ВСЮ історію (default)
response_agent = LlmAgent(
    include_contents='default'
)

# Бачить ТІЛЬКИ поточний input
router_agent = LlmAgent(
    include_contents='none'
)
```

---

## 3. Повний приклад розмови

### Turn 1: "мені погано"

```
┌─────────────────────────────────────────────────────────┐
│ User Input: "мені погано"                               │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ ROUTER AGENT                                            │
│ include_contents='none' → бачить тільки "мені погано"   │
│                                                         │
│ Output: {"route": "CLARIFY", "missing": ["event","who"]}│
│ ↓ Записує в state через output_key="routing_decision"  │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ ORCHESTRATOR (Python код)                               │
│                                                         │
│ decision = ctx.session.state.get("routing_decision")    │
│ if decision["route"] == "CLARIFY":                      │
│     ctx.session.state["clarify_missing"] = ["event"]    │
│     → запускає response_agent                           │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ RESPONSE AGENT                                          │
│ include_contents='default' → бачить всю історію        │
│                                                         │
│ Бачить в history:                                       │
│   - User: "мені погано"                                 │
│   - Router: {"route": "CLARIFY"...}                     │
│                                                         │
│ Читає state["clarify_missing"] = ["event", "who"]      │
│                                                         │
│ Output: "Чую тебе. А що саме сталось?"                  │
└─────────────────────────────────────────────────────────┘

STATE після Turn 1:
{
    "routing_decision": {"route": "CLARIFY", "missing": ["event", "who"]},
    "clarify_missing": ["event", "who"]
}
```

### Turn 2: "батько знову почав про роботу"

```
┌─────────────────────────────────────────────────────────┐
│ User Input: "батько знову почав про роботу"             │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ ROUTER AGENT                                            │
│ include_contents='none' → бачить тільки нове            │
│                                                         │
│ Аналізує: event=почав, who=батько, when=? (implied now) │
│ Output: {"route": "WRITE", "missing": null}             │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ ORCHESTRATOR                                            │
│                                                         │
│ decision["route"] == "WRITE"                            │
│ → запускає WRITE flow (Entity Extractor, Writer...)     │
└─────────────────────────────────────────────────────────┘

CONVERSATION HISTORY (що бачать агенти з include_contents='default'):
[
    {"role": "user",      "content": "мені погано"},
    {"role": "assistant", "content": "Чую тебе. А що саме сталось?"},
    {"role": "user",      "content": "батько знову почав про роботу"}
]

STATE після Turn 2:
{
    "routing_decision": {"route": "WRITE", "missing": null},  # ← оновлено!
    "clarify_missing": ["event", "who"]  # ← ще тут (якщо не очистили)
}
```

---

## 4. Підсумок: Хто що бачить

| Агент | State | History | Чому |
|-------|-------|---------|------|
| **Router** | ✅ Читає/пише | ❌ Тільки поточний input | Класифікує лише поточне повідомлення |
| **Orchestrator** | ✅ Читає/пише | N/A (Python код) | Керує логікою, передає дані |
| **Response Agent** | ✅ Читає | ✅ Вся історія | Потрібен контекст для відповіді |
| **Entity Extractor** | ✅ Читає | ✅ Вся історія | Синтезує інформацію з CLARIFY |

---

## 5. Важливі нюанси

### State зберігається між turns
Якщо не очистити, старі значення залишаються. Наприклад, `clarify_missing` може існувати навіть після WRITE.

### Явна передача vs Implict
- **State + output_key** — явна, надійна
- **History** — неявна, агент "бачить" але може не використати

### Placeholder-и в промптах
Можна використовувати `{key_name}` в instruction для явного доступу до state:
```python
instruction="Missing info: {clarify_missing}"
```
