// Reviewed migration: backfill Operational on legacy control labels.
//
// DO NOT run automatically at session startup. Operator applies explicitly:
//
//   cypher-shell -u neo4j -p "$NEO4J_ADMIN_PASSWORD" -d neo4j \
//     -f scripts/migrate_operational_labels.cypher
//
// Or:
//
//   python scripts/migrate_operational_labels.py --apply
//
// After this migration, heavy-node/BOOTSTRAP paths continue excluding both
// Operational and the temporary legacy Alias/LearningLog filters until the
// legacy filter can be retired in a later cleanup task.

// Preview counts (safe to re-run)
MATCH (a:Alias) WHERE NOT a:Operational
RETURN 'Alias_missing_Operational' AS kind, count(a) AS count;

MATCH (l:LearningLog) WHERE NOT l:Operational
RETURN 'LearningLog_missing_Operational' AS kind, count(l) AS count;

// Apply (idempotent)
MATCH (a:Alias) WHERE NOT a:Operational
SET a:Operational
RETURN count(a) AS alias_labeled;

MATCH (l:LearningLog) WHERE NOT l:Operational
SET l:Operational
RETURN count(l) AS learning_log_labeled;
