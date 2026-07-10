# Graph Schema Contract: Digital Brain

This document defines the strict structure and rules for node creation and relationship mapping in the Neo4j graph. All agents (Extractor, Writer, Critic) must adhere to this contract.

---

## 1. Node Labels & Property Contracts

### 🏛️ Core Entities

| Label | Required Properties | Optional Properties | Lookup Strategy (Phase 1) | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Person** | `id`, `name` | `relation`, `description` | `name` OR `relation` (fuzzy) | A human entity with a specific role in the user's life (family, friend, colleague). |
| **Topic**| `id`, `name` | `importance` | `name` (case-insensitive) | A specific subject, concept, or project the user is thinking about. |
| **State**| `id`, `name` | `intensity` (0-1) | `name` (case-insensitive) | A psychological or emotional state, mood, or mental condition (e.g., Anxiety, Flow). |
| **Event**| `id`, `type` | `timestamp`, `description`| `type` + `timestamp` | A specific occurrence in time described by the user. |
| **Organization**| `id`, `name` | `industry`, `description` | `name` (case-insensitive) | A company, institution, or group (e.g., EPAM, Google). |
| **Location**| `id`, `name` | `type` (City, Country), `coords` | `name` (case-insensitive) | A physical place, city, or country (e.g., Kyiv, Home). |
| **Pet**| `id`, `name` | `species`, `breed` | `name` (fuzzy) | A non-human living being with significance (e.g., Barsik). |
| **Object**| `id`, `name` | `type`, `description` | `name` (case-insensitive) | A physical object or item (e.g., Car, Laptop, Phone). |

### 📝 Operational Nodes

| Label | Required Properties | Description |
| :--- | :--- | :--- |
| **JournalEntry** | `id`, `append_key`, `content`, `timestamp`, `embedding`, `request_fingerprint`, `chain_version` | The raw thought captured from the user. `append_key` is the idempotency key for new writes. Optional: `mood`, `previous_journal_id`, `previous_element_id`. |
| **JournalChain** | `key`, `version` | Server-owned primary append cursor; never inferred from timestamp order. |
| **Alias** | `from_name`, `to_name`, `canonical_id` | Metadata for "learned" entity resolution. |
| **LearningLog** | `type`, `entity`, `timestamp` | Audit log of merges and self-corrections. |

---

## 2. Relationship Schema

Relationships define the "web of thought". Direction matters.

| Relationship | From Node | To Node | Description |
| :--- | :--- | :--- | :--- |
| **MENTIONS** | `JournalEntry` | `Person`, `Topic`, `Organization`, `Pet`, `Location` | Indicates that an entity was explicitly referred to in a specific thought. |
| **DESCRIBES** | `JournalEntry` | `Event` | Links a raw thought or narrative to a structured event. |
| **EXPERIENCED** | `Person` | `State` | Attributes an emotional or psychological state to a specific individual. |
| **PARTICIPATED** | `Person` | `Event` | Record of a person's involvement in a specific occurrence. |
| **RELATED_TO** | `Topic` | `Topic` | Defines semantic or hierarchical connections between concepts. |
| **LIVES_IN** | `Person` | `Location` | Indicates residence or primary location. |
| **LOCATED_AT** | `Event` | `Location` | Specs where an event took place. |
| **OWNS** | `Person` | `Pet` | Relationship between a person and their pet. |
| **WORKS_AT** | `Person` | `Organization` | Professional affiliation. |
| **ALIAS_OF** | `Person` | `Person` | A hard-link indicating two nodes represent the same entity (used for resolution).|
| **HEAD** | `JournalChain` | `JournalEntry` | The one current head of the server-owned primary journal chain. |
| **FOLLOWS** | `JournalEntry` | `JournalEntry` | Created only by the JournalEntry append protocol. |

---

## 3. General Rules (The "Laws")

### Rule 1: Deterministic IDs
- Nodes must have a unique `id` (usually a UUID or a deterministic string).
- **Writer Agent** must never use `CREATE` for an entity present in `{existing_entities}`.
- A new `JournalEntry` must be created only through `append_journal_entry`.
  The server derives its stable id from `append_key`, stores the request
  fingerprint, and atomically advances `JournalChain`.

### Rule 2: Naming Conventions
- **Labels**: PascalCase (e.g., `JournalEntry`).
- **Properties**: snake_case (e.g., `source_date`).
- **Relationship Types**: UPPER_SNAKE_CASE (e.g., `MENTIONS`).

### Rule 3: Psychological Integrity
- A `JournalEntry` should capture `mood` when clearly supported by the content
  (mood is optional on the append API and may be omitted).
- If the user describes another person's mood, it must be linked via `(Person)-[:EXPERIENCED]->(State)`.
- Post-append mutations must `MATCH (j:JournalEntry {id: $journal_id})` and
  never set protected receipt/chain fields or create FOLLOWS/HEAD.

### Rule 4: Self-Hexing (Prevention of Duplicates)
- Every write operation is subject to **Phase 3 Reflex Loop**.
- If two nodes have a Levenshtein similarity > 0.8 AND share a relationship to the same `JournalEntry`, they are automatically merged.

---

> [!IMPORTANT]
> This contract is the "Constitution" of the Digital Brain's memory. Any agent generating Cypher that violates these rules is subject to immediate rejection by the **Critic Agent**.
