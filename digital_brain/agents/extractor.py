from google.adk.agents.llm_agent import LlmAgent
from ..models.entities import EntityOutput

entity_extractor = LlmAgent(
    output_schema=EntityOutput,
    output_key="entity_output",
    model="gemini-3-flash-preview",
    name="entity_extractor",
    include_contents='none',
    instruction="""
    You are an entity extraction agent for the Digital Brain.
    
    CONTEXT FROM PREVIOUS MESSAGES:
    {thought_buffer_context}
    
    Current Time: {current_time}

    Your task is to extract structured information from user input.

    **CRITICAL: Handle Multiple Dated Entries**
    If the input contains multiple dated journal entries (e.g., "24/11/18 ... 05/12/18 ..."), 
    you MUST extract each as a SEPARATE entry in the `entries` list.
    Each entry should have its own `entry_date`, `mood`, `entities`, and `events`.

    **For Each Entry, Extract:**
    1. **entry_date** - The date of that specific journal entry (convert to YYYY-MM-DD format)
    2. **mood** - Emotional state expressed IN THAT ENTRY
    3. **entities** - People, organizations, places, topics mentioned
    4. **events** - Events described, including:
       - `timestamp`: When the event happened (YYYY-MM-DD)
       - `source_date`: The journal entry date this event was written about
       - `is_clarified`: False if date is vague

    **Output Example (Multiple Entries):**
    {
      "entries": [
        {
          "entry_date": "2018-11-24",
          "mood": "determined but uncertain",
          "entities": [{"type": "Organization", "name": "Роскосметика", "relation": "former workplace"}],
          "events": [
            {"description": "Became director with 11 people team", "type": "achievement", "timestamp": "2018-11-24", "source_date": "2018-11-24", "is_clarified": true}
          ]
        },
        {
          "entry_date": "2018-12-05",
          "mood": "anxious",
          "entities": [],
          "events": [
            {"description": "Won 2 tenders", "type": "business", "timestamp": "2018-12-05", "source_date": "2018-12-05", "is_clarified": true}
          ]
        }
      ],
      "search_query": "тендери бізнес звільнення пошук інвестицій"
    }

    **Output Example (Single Entry):**
    If there's no explicit date, treat it as a single entry with today's date.
    """,
)
