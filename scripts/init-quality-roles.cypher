// Deterministic Neo4j Enterprise role bootstrap for the Operational boundary.
//
// GENERATED — do not hand-edit DENY lists. Regenerate with:
//   uv run --group dev python scripts/init_quality_roles.py --write-cypher
// Source of truth: digital_brain_mcp_cypher.quality.PROTECTED_QUALITY_LABELS
//
// Apply via the reviewed host helper. It primes every protected label token in
// the target database before installing the label-scoped DENYs:
//
//   python scripts/init_quality_roles.py --apply
//
// Parameter substitution is performed by init_quality_roles.py.
//
// Roles:
//   digital_brain_runtime  — life-graph MATCH/WRITE; DENY quality/control labels
//   digital_brain_quality  — typed quality/control transactions (Operational OK)
//
// Operator/admin (neo4j) retains full privileges for bootstrap/migration only.
// Operator activation credentials must not be mounted into model-facing MCP.

// --- runtime role ---
CREATE ROLE digital_brain_runtime IF NOT EXISTS;
CREATE USER $runtime_user IF NOT EXISTS
  SET PASSWORD $runtime_password CHANGE NOT REQUIRED
  SET STATUS ACTIVE;
// Password rotate when user already exists (idempotent re-apply):
ALTER USER $runtime_user SET PASSWORD $runtime_password CHANGE NOT REQUIRED;
GRANT ROLE digital_brain_runtime TO $runtime_user;

GRANT ACCESS ON DATABASE $database TO digital_brain_runtime;
GRANT MATCH {*} ON GRAPH $database TO digital_brain_runtime;
GRANT WRITE ON GRAPH $database TO digital_brain_runtime;
GRANT NAME MANAGEMENT ON DATABASE $database TO digital_brain_runtime;
GRANT CREATE CONSTRAINT ON DATABASE $database TO digital_brain_runtime;
GRANT CREATE INDEX ON DATABASE $database TO digital_brain_runtime;
GRANT SHOW INDEX ON DATABASE $database TO digital_brain_runtime;
GRANT SHOW CONSTRAINT ON DATABASE $database TO digital_brain_runtime;

// Deny CREATE / DELETE / SET PROPERTY / SET LABEL on every protected control
// label (PROTECTED_QUALITY_LABELS). Partial labels (e.g. Feedback without
// Operational) are also denied so a missing Operational tag cannot bypass.
DENY CREATE ON GRAPH $database NODE ActivationAuthority TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE AgentPolicyRevision TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE Alias TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE ChangeIntent TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE Decision TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE Deployment TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE DreamRun TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE DreamStageReceipt TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE EffectReceipt TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE EntityProtection TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE EvaluationReceipt TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE EvidenceRef TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE EvidenceSnapshot TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE ExposureWindow TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE Feedback TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE FeedbackLifecycleEvent TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE Finding TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE HarnessGeneration TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE LearningLog TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE MaintenanceLease TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE Operational TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE PatchArtifact TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE PolicySlot TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE Proposal TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE QualityPayload TO digital_brain_runtime;
DENY CREATE ON GRAPH $database NODE RunEvent TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE ActivationAuthority TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE AgentPolicyRevision TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE Alias TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE ChangeIntent TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE Decision TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE Deployment TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE DreamRun TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE DreamStageReceipt TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE EffectReceipt TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE EntityProtection TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE EvaluationReceipt TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE EvidenceRef TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE EvidenceSnapshot TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE ExposureWindow TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE Feedback TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE FeedbackLifecycleEvent TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE Finding TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE HarnessGeneration TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE LearningLog TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE MaintenanceLease TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE Operational TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE PatchArtifact TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE PolicySlot TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE Proposal TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE QualityPayload TO digital_brain_runtime;
DENY DELETE ON GRAPH $database NODE RunEvent TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE ActivationAuthority TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE AgentPolicyRevision TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE Alias TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE ChangeIntent TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE Decision TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE Deployment TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE DreamRun TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE DreamStageReceipt TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE EffectReceipt TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE EntityProtection TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE EvaluationReceipt TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE EvidenceRef TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE EvidenceSnapshot TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE ExposureWindow TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE Feedback TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE FeedbackLifecycleEvent TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE Finding TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE HarnessGeneration TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE LearningLog TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE MaintenanceLease TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE Operational TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE PatchArtifact TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE PolicySlot TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE Proposal TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE QualityPayload TO digital_brain_runtime;
DENY SET PROPERTY {*} ON GRAPH $database NODE RunEvent TO digital_brain_runtime;
DENY SET LABEL ActivationAuthority ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL AgentPolicyRevision ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL Alias ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL ChangeIntent ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL Decision ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL Deployment ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL DreamRun ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL DreamStageReceipt ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL EffectReceipt ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL EntityProtection ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL EvaluationReceipt ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL EvidenceRef ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL EvidenceSnapshot ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL ExposureWindow ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL Feedback ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL FeedbackLifecycleEvent ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL Finding ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL HarnessGeneration ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL LearningLog ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL MaintenanceLease ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL Operational ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL PatchArtifact ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL PolicySlot ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL Proposal ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL QualityPayload ON GRAPH $database TO digital_brain_runtime;
DENY SET LABEL RunEvent ON GRAPH $database TO digital_brain_runtime;

// --- quality role ---
CREATE ROLE digital_brain_quality IF NOT EXISTS;
CREATE USER $quality_user IF NOT EXISTS
  SET PASSWORD $quality_password CHANGE NOT REQUIRED
  SET STATUS ACTIVE;
ALTER USER $quality_user SET PASSWORD $quality_password CHANGE NOT REQUIRED;
GRANT ROLE digital_brain_quality TO $quality_user;

GRANT ACCESS ON DATABASE $database TO digital_brain_quality;
GRANT MATCH {*} ON GRAPH $database TO digital_brain_quality;
GRANT WRITE ON GRAPH $database TO digital_brain_quality;
GRANT NAME MANAGEMENT ON DATABASE $database TO digital_brain_quality;
GRANT CREATE CONSTRAINT ON DATABASE $database TO digital_brain_quality;
GRANT CREATE INDEX ON DATABASE $database TO digital_brain_quality;
GRANT SHOW INDEX ON DATABASE $database TO digital_brain_quality;
GRANT SHOW CONSTRAINT ON DATABASE $database TO digital_brain_quality;
