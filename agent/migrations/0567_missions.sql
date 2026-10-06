CREATE TABLE IF NOT EXISTS missions_v567(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, instruction TEXT NOT NULL, matter TEXT NOT NULL,
 kind TEXT NOT NULL, state TEXT NOT NULL, context TEXT NOT NULL, plan TEXT NOT NULL,
 exceptions TEXT NOT NULL, job_id INTEGER, ref TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(owner,request_key));
 CREATE INDEX IF NOT EXISTS missions567_owner ON missions_v567(owner,updated);
 CREATE TABLE IF NOT EXISTS mission_events_v567(
 id INTEGER PRIMARY KEY, mission TEXT NOT NULL, at TEXT NOT NULL,
 kind TEXT NOT NULL, detail TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS reception_v567(
 id TEXT PRIMARY KEY, channel TEXT NOT NULL, at TEXT NOT NULL, caller TEXT NOT NULL,
 text TEXT NOT NULL, task_id TEXT NOT NULL, provider_reference TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ai_reservations_v567(
 id TEXT PRIMARY KEY, at TEXT NOT NULL, provider TEXT NOT NULL, amount REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','uncertain','closed')));
 CREATE INDEX IF NOT EXISTS reservations567_month ON ai_reservations_v567(provider,at,state);
