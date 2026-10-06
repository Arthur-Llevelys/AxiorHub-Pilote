CREATE TABLE IF NOT EXISTS events_v568(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, source TEXT NOT NULL, object_key TEXT NOT NULL,
 version TEXT NOT NULL, kind TEXT NOT NULL, matter TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL, explanation TEXT NOT NULL DEFAULT '', plan_id TEXT NOT NULL DEFAULT '',
 detected TEXT NOT NULL, occurred TEXT NOT NULL, updated TEXT NOT NULL,
 UNIQUE(owner,source,object_key,version));
CREATE INDEX IF NOT EXISTS events568_owner ON events_v568(owner,state,detected);
CREATE TABLE IF NOT EXISTS commitments_v568(
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL, owner TEXT NOT NULL, matter TEXT NOT NULL,
 author TEXT NOT NULL, recipient TEXT NOT NULL, expected TEXT NOT NULL, quote TEXT NOT NULL,
 evidence TEXT NOT NULL, due TEXT NOT NULL, condition_text TEXT NOT NULL, state TEXT NOT NULL,
 proof TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL, UNIQUE(event_id,quote));
CREATE TABLE IF NOT EXISTS plans_v568(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 matter TEXT NOT NULL, title TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL,
 updated TEXT NOT NULL, UNIQUE(owner,request_key));
CREATE TABLE IF NOT EXISTS plan_steps_v568(
 plan_id TEXT NOT NULL, position INTEGER NOT NULL, role TEXT NOT NULL, instruction TEXT NOT NULL,
 dependencies TEXT NOT NULL, context TEXT NOT NULL, mission_id TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'waiting', updated TEXT NOT NULL, PRIMARY KEY(plan_id,position));
CREATE TABLE IF NOT EXISTS talk_rooms_v568(
 event_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL UNIQUE, token TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL, proof TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_v568(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
 published TEXT NOT NULL, summary TEXT NOT NULL, fields TEXT NOT NULL, detected TEXT NOT NULL,
 UNIQUE(owner,url,published));
CREATE TABLE IF NOT EXISTS voice_usage_v568(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, month TEXT NOT NULL, amount TEXT NOT NULL,
 state TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS voice568_month ON voice_usage_v568(month,state);
