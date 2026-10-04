BEGIN;

CREATE TABLE IF NOT EXISTS ai_integration_actions (
    action_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    client_request_id VARCHAR(36) NOT NULL,
    action_type VARCHAR(80) NOT NULL,
    folder_id VARCHAR(200),
    message_key VARCHAR(64),
    status VARCHAR(32) NOT NULL,
    progress VARCHAR(500) NOT NULL DEFAULT '',
    preview JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    preview_hash VARCHAR(64),
    confirmed_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    UNIQUE (user_id, client_request_id),
    CHECK (status IN (
        'queued', 'running', 'awaiting_confirmation', 'succeeded',
        'partially_succeeded', 'failed', 'cancelled'
    ))
);

CREATE INDEX IF NOT EXISTS ai_integration_actions_user_updated_idx
    ON ai_integration_actions (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ai_integration_actions_folder_updated_idx
    ON ai_integration_actions (folder_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_audit_events (
    audit_id VARCHAR(36) PRIMARY KEY,
    audit_seq BIGSERIAL UNIQUE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id VARCHAR(128) NOT NULL,
    folder_id VARCHAR(200),
    message_key VARCHAR(64),
    correlation_id VARCHAR(36) NOT NULL,
    action VARCHAR(80) NOT NULL,
    phase VARCHAR(40) NOT NULL,
    target VARCHAR(80) NOT NULL DEFAULT 'roundcube',
    object_type VARCHAR(80),
    object_id VARCHAR(300),
    status VARCHAR(32) NOT NULL,
    payload_hash VARCHAR(64),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code VARCHAR(100),
    safe_error_message VARCHAR(1000),
    previous_hash VARCHAR(64),
    event_hash VARCHAR(64) NOT NULL
);

ALTER TABLE ai_audit_events ADD COLUMN IF NOT EXISTS audit_seq BIGSERIAL;
CREATE UNIQUE INDEX IF NOT EXISTS ai_audit_events_seq_uidx ON ai_audit_events (audit_seq);

CREATE INDEX IF NOT EXISTS ai_audit_events_user_time_idx
    ON ai_audit_events (user_id, audit_seq DESC);
CREATE INDEX IF NOT EXISTS ai_audit_events_folder_time_idx
    ON ai_audit_events (folder_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ai_audit_events_correlation_idx
    ON ai_audit_events (correlation_id, occurred_at);

CREATE TABLE IF NOT EXISTS ai_project_activities (
    activity_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    folder_id VARCHAR(200) NOT NULL,
    activity_type VARCHAR(80) NOT NULL,
    title VARCHAR(300) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status VARCHAR(24) NOT NULL DEFAULT 'done',
    due_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    source_type VARCHAR(80),
    source_id VARCHAR(300),
    source_quote TEXT,
    external_references JSONB NOT NULL DEFAULT '{}'::jsonb,
    correlation_id VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('proposed', 'in_progress', 'done', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS ai_project_activities_folder_idx
    ON ai_project_activities (folder_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_calendar_event_links (
    logical_event_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    folder_id VARCHAR(200),
    message_key VARCHAR(64),
    fingerprint VARCHAR(64) NOT NULL,
    title VARCHAR(300) NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    openwebui_calendar_id VARCHAR(200),
    openwebui_event_id VARCHAR(200),
    nextcloud_calendar_name VARCHAR(300),
    nextcloud_uid VARCHAR(300),
    status VARCHAR(32) NOT NULL,
    override_reason VARCHAR(1000),
    correlation_id VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, fingerprint),
    CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
    CHECK (status IN ('reserved', 'succeeded', 'partially_succeeded', 'failed')),
    CHECK (end_at > start_at)
);

CREATE INDEX IF NOT EXISTS ai_calendar_event_links_folder_idx
    ON ai_calendar_event_links (folder_id, start_at DESC);

CREATE TABLE IF NOT EXISTS ai_file_import_fingerprints (
    import_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    folder_id VARCHAR(200) NOT NULL,
    source_type VARCHAR(40) NOT NULL DEFAULT 'nextcloud',
    source_fingerprint VARCHAR(64) NOT NULL,
    source_etag VARCHAR(300) NOT NULL DEFAULT '',
    content_sha256 VARCHAR(64),
    openwebui_file_id VARCHAR(200),
    status VARCHAR(24) NOT NULL,
    correlation_id VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, folder_id, source_fingerprint, source_etag),
    CHECK (source_fingerprint ~ '^[a-f0-9]{64}$'),
    CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[a-f0-9]{64}$'),
    CHECK (status IN ('reserved', 'succeeded', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ai_file_import_content_uidx
    ON ai_file_import_fingerprints (user_id, folder_id, content_sha256)
    WHERE content_sha256 IS NOT NULL AND status = 'succeeded';
CREATE INDEX IF NOT EXISTS ai_file_import_folder_idx
    ON ai_file_import_fingerprints (user_id, folder_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_project_snapshots (
    snapshot_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    folder_id VARCHAR(200) NOT NULL,
    overview_chat_id VARCHAR(200),
    manifest_hash VARCHAR(64) NOT NULL,
    manifest JSONB NOT NULL,
    source_versions JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ai_project_snapshots_folder_idx
    ON ai_project_snapshots (user_id, folder_id, created_at DESC);

CREATE OR REPLACE FUNCTION ai_audit_immutable_guard()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ai_audit_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ai_audit_no_update ON ai_audit_events;
CREATE TRIGGER ai_audit_no_update
BEFORE UPDATE OR DELETE ON ai_audit_events
FOR EACH ROW EXECUTE FUNCTION ai_audit_immutable_guard();

COMMIT;
