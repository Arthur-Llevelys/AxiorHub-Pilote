CREATE TABLE IF NOT EXISTS production_deliverables_v420(
  id TEXT PRIMARY KEY, job_id INTEGER NOT NULL, job_kind TEXT NOT NULL,
  event_kind TEXT NOT NULL, source_id TEXT NOT NULL, matter TEXT NOT NULL,
  deliverable_kind TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL,
  stage TEXT NOT NULL, stages TEXT NOT NULL, trigger_text TEXT NOT NULL,
  source_ids TEXT NOT NULL, target TEXT NOT NULL, verification TEXT NOT NULL,
  business_message TEXT NOT NULL, error_code TEXT NOT NULL,
  retryable INTEGER NOT NULL, informed INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS production_deliverables_state_v420 ON production_deliverables_v420(status,updated);
CREATE INDEX IF NOT EXISTS production_deliverables_matter_v420 ON production_deliverables_v420(matter,updated);
CREATE TABLE IF NOT EXISTS production_studio_v420(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, deliverable_kind TEXT NOT NULL,
  instruction_sha256 TEXT NOT NULL, source_paths TEXT NOT NULL,
  template_id TEXT NOT NULL, requested_model TEXT NOT NULL,
  confidentiality TEXT NOT NULL, estimate TEXT NOT NULL, status TEXT NOT NULL,
  job_id INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS production_metric_snapshots_v420(
  day TEXT PRIMARY KEY, metrics TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS production_rule_influence_v420(
  deliverable_id TEXT NOT NULL, rule_id INTEGER NOT NULL,
  decision TEXT NOT NULL DEFAULT '', changed_characters INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL, updated TEXT NOT NULL,
  PRIMARY KEY(deliverable_id,rule_id)
);
