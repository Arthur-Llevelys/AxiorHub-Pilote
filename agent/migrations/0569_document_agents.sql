CREATE TABLE IF NOT EXISTS document_rules568(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, template TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
 instruction TEXT NOT NULL, recipe TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
 created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(owner,template));
CREATE TABLE IF NOT EXISTS document_runs568(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, rule_id TEXT NOT NULL, revision INTEGER NOT NULL,
 matter TEXT NOT NULL, path TEXT NOT NULL, etag TEXT NOT NULL, metadata TEXT NOT NULL,
 state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', explanation TEXT NOT NULL DEFAULT '',
 job_id INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL,
 UNIQUE(owner,rule_id,revision,path,etag));
CREATE INDEX IF NOT EXISTS document_runs568_owner ON document_runs568(owner,state,updated);
CREATE TABLE IF NOT EXISTS document_effects568(
 run_id TEXT NOT NULL, step TEXT NOT NULL, state TEXT NOT NULL, proof TEXT NOT NULL DEFAULT '{}',
 updated TEXT NOT NULL, PRIMARY KEY(run_id,step));
CREATE TABLE IF NOT EXISTS document_content568(
 owner TEXT NOT NULL, rule_id TEXT NOT NULL, revision INTEGER NOT NULL, matter TEXT NOT NULL,
 sha256 TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(owner,rule_id,revision,matter,sha256));
CREATE TABLE IF NOT EXISTS calendar_targets568(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, label TEXT NOT NULL, provider TEXT NOT NULL,
 config TEXT NOT NULL, enabled INTEGER NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_deposits568(
 owner TEXT NOT NULL, target_id TEXT NOT NULL, event_key TEXT NOT NULL, event_id TEXT NOT NULL,
 state TEXT NOT NULL, proof TEXT NOT NULL, updated TEXT NOT NULL,
 PRIMARY KEY(owner,target_id,event_key));
CREATE TABLE IF NOT EXISTS google_oauth568(
 state_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, redirect TEXT NOT NULL,
 verifier TEXT NOT NULL, config_hash TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
