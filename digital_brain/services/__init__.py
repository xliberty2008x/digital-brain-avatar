# File: digital_brain/services/__init__.py
from .entity_resolver import resolve_entities
from .consistency_checker import run_consistency_check as check_consistency
from .core_entity_service import get_potential_core_entities
from .recent_entries_service import get_recent_journal_entries, get_latest_journal_entry_id

__all__ = [
    "resolve_entities",
    "check_consistency",
    "get_potential_core_entities",
    "get_recent_journal_entries",
    "get_latest_journal_entry_id",
]
