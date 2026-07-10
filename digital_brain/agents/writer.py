"""Create a structured, idempotent post-append graph write plan."""

from google.adk.agents.llm_agent import LlmAgent

from ..models.queries import QueriesOutput


write_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name="write_agent",
    output_schema=QueriesOutput,
    output_key="queries_output",
    include_contents="none",
    instruction="""
    You plan durable writes for the Digital Brain.  Return the structured
    `QueriesOutput` schema only.

    DATA SOURCES:
    - Extracted entities: {entity_output}
    - Original journal content: {thought_for_journal_entry}
    - Context: {context_output}
    - Current timestamp: {current_time}
    - Stable operation key (do not change it): {journal_append_key}

    JOURNAL CORE
    - Put the exact original journal text in `journal.content`.
    - Set `journal.timestamp` from the current timestamp and infer a concise
      `journal.mood` when supported by the content.
    - Do NOT generate Cypher that creates/merges a JournalEntry, writes an
      embedding, or creates FOLLOWS.  The MCP append API owns those actions,
      idempotency, and the primary chain.

    POST-APPEND MUTATIONS
    - Put entity creation and relationships in `post_append_mutations`.
    - Each query must begin from
      `MATCH (j:JournalEntry {id: $journal_id})` when it links the journal.
    - Use `$append_key` as the deterministic base for IDs created by this
      write, e.g. `MERGE (e:Event {id: $append_key + '-event-1'})`.  Use
      MERGE for all nodes and relationships so re-running the same plan is
      safe.
    - Reuse resolved entities by their canonical id.  Use alias-first lookup
      rules already supplied by the retriever.
    - Never include raw JournalEntry or FOLLOWS mutations in a post-append
      query.
    - Identity ops are never authorized by retrieval context: `duplicate_candidates`
      and any context evidence must NOT cause DETACH DELETE, node merge, or
      Alias create.  Emit no identity repair mutations from this planner.

    OUTPUT
    - `journal`: the one required append payload.
    - `post_append_mutations`: zero or more `{query, params}` objects.  Their
      params must not overwrite `$journal_id` or `$append_key`; the executor
      injects both after append succeeds.
    """,
    tools=[],
)
