from google.adk.agents.llm_agent import LlmAgent
from ..tools.neo4j_toolkit import full_access_toolset
from ..callbacks.combined_before_tool_callbacks import combined_before_tool_callback
from ..callbacks.combined_tool_callbacks import combined_after_tool_callback


async def _executor_after_tool_callback(tool, args, tool_context, tool_response):
    """The MCP server is the sole owner of journal-chain state."""
    return combined_after_tool_callback(tool, args, tool_context, tool_response)


executor_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name="executor_agent",
    include_contents='none',
    before_tool_callback=combined_before_tool_callback,
    after_tool_callback=_executor_after_tool_callback,
    instruction="""
    You are an execution agent for the Digital Brain.
    
    STRUCTURED WRITE PLAN:
    {queries_output}

    Stable append key for this write flow: {journal_append_key}

    **Tools available:**
    - get_journal_chain_head()
    - append_journal_entry(append_key, content, timestamp, mood, expected_version, properties)
    - get_journal_append_receipt(append_key)
    - write_neo4j_cypher(query, params) for post-append graph links only

    **Rules:**
    1. Fetch the chain head immediately before appending.
    2. Call append_journal_entry exactly once using the structured `journal`
       payload, the stable append key, and the returned head version.
    3. If the tool transport fails or times out, call
       get_journal_append_receipt with the same append key. Receipt outcomes
       are `found` (success; use its journal_id) or `not_found` (safe to
       retry the same append payload/key once). Never mint a new key or issue
       a raw JournalEntry CREATE.
    4. Only after append `created`/`replayed` (or receipt `found`), execute
       each `post_append_mutations` item. Add the returned `journal_id` and
       the stable append key to its params. These mutations must be
       idempotent MERGE/MATCH operations; do not create JournalEntry or
       FOLLOWS, and never DELETE/DETACH/REMOVE.
    5. On append `conflict`:
       - `stale_version` / `chain_changed`: re-read head; retry **same**
         append_key + same payload with the new expected_version. Do not use
         conflict.journal_id (it is null); current head is
         current_head_journal_id.
       - `append_key_reused`: stop; mint a new key only for a truly new entry.
       - Never run post-append links after a conflict.
    6. Do not retry the entire write flow blindly. Report a partial
       post-append failure explicitly so it can be reconciled with the same
       identifiers.

    **Output format:**
    {
      "success": true,
      "journal": {"outcome": "created|replayed|conflict|found", "id": "..."},
      "post_append_mutations_completed": 0,
      "error": null
    }
    """,
    tools=[full_access_toolset()],
)
