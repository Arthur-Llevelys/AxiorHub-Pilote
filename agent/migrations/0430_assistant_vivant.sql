CREATE TABLE IF NOT EXISTS live_events_v430(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
 message TEXT NOT NULL, job_id INTEGER, matter TEXT NOT NULL DEFAULT '',
 source_key TEXT NOT NULL DEFAULT '', dedupe TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS live_events_job_v430 ON live_events_v430(job_id,id);
CREATE TABLE IF NOT EXISTS live_services_v430(
 name TEXT PRIMARY KEY, heartbeat REAL NOT NULL, status TEXT NOT NULL,
 message TEXT NOT NULL, next_check REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS live_observations_v430(
 source TEXT NOT NULL, object_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 updated TEXT NOT NULL, PRIMARY KEY(source,object_key));
CREATE TABLE IF NOT EXISTS live_dirty_v430(
 source TEXT NOT NULL, object_key TEXT NOT NULL,
 PRIMARY KEY(source,object_key));
CREATE TABLE IF NOT EXISTS live_requests_v430(
 token TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
 job_id INTEGER, target TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS live_requests_fp_v430 ON live_requests_v430(fingerprint,created);
ALTER TABLE jobs ADD COLUMN started TEXT;
ALTER TABLE jobs ADD COLUMN worker TEXT;
ALTER TABLE jobs ADD COLUMN progress TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
