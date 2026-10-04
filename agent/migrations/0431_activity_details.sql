CREATE TABLE IF NOT EXISTS live_outputs_v431(
 job_id INTEGER NOT NULL, source_key TEXT NOT NULL, matter TEXT NOT NULL,
 status TEXT NOT NULL, message TEXT NOT NULL, details TEXT NOT NULL,
 updated TEXT NOT NULL, PRIMARY KEY(job_id,source_key));
CREATE TABLE IF NOT EXISTS live_maintenance_v431(
 job_id INTEGER PRIMARY KEY, last_retry REAL NOT NULL DEFAULT 0,
 incident TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS live_event_archive_v431(
 id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
 message TEXT NOT NULL, job_id INTEGER, matter TEXT NOT NULL,
 source_key TEXT NOT NULL, dedupe TEXT UNIQUE);
