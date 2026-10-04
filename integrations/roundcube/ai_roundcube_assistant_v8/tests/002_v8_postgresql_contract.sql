BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '20s';

INSERT INTO ai_integration_actions
    (action_id,user_id,client_request_id,action_type,status,progress)
VALUES
    ('00000000-0000-4000-8000-000000000801','__v8_contract__',
     '00000000-0000-4000-8000-000000000811','contract_test','running','test');

INSERT INTO ai_integration_actions
    (action_id,user_id,client_request_id,action_type,status,progress)
VALUES
    ('00000000-0000-4000-8000-000000000802','__v8_contract__',
     '00000000-0000-4000-8000-000000000811','contract_test','running','double')
ON CONFLICT (user_id,client_request_id) DO NOTHING;

DO $$
BEGIN
    IF (SELECT COUNT(*) FROM ai_integration_actions
        WHERE user_id='__v8_contract__'
          AND client_request_id='00000000-0000-4000-8000-000000000811') <> 1 THEN
        RAISE EXCEPTION 'action idempotency contract failed';
    END IF;
END $$;

INSERT INTO ai_calendar_event_links
    (logical_event_id,user_id,fingerprint,title,start_at,end_at,status,correlation_id)
VALUES
    ('00000000-0000-4000-8000-000000000821','__v8_contract__',repeat('a',64),
     'Test V8','2099-01-01T09:00:00Z','2099-01-01T10:00:00Z','reserved',
     '00000000-0000-4000-8000-000000000801');

INSERT INTO ai_calendar_event_links
    (logical_event_id,user_id,fingerprint,title,start_at,end_at,status,correlation_id)
VALUES
    ('00000000-0000-4000-8000-000000000822','__v8_contract__',repeat('a',64),
     'Double','2099-01-01T09:00:00Z','2099-01-01T10:00:00Z','reserved',
     '00000000-0000-4000-8000-000000000801')
ON CONFLICT (user_id,fingerprint) DO NOTHING;

DO $$
BEGIN
    IF (SELECT COUNT(*) FROM ai_calendar_event_links
        WHERE user_id='__v8_contract__' AND fingerprint=repeat('a',64)) <> 1 THEN
        RAISE EXCEPTION 'calendar idempotency contract failed';
    END IF;
END $$;

INSERT INTO ai_file_import_fingerprints
    (import_id,user_id,folder_id,source_fingerprint,source_etag,content_sha256,
     openwebui_file_id,status,correlation_id)
VALUES
    ('00000000-0000-4000-8000-000000000831','__v8_contract__','folder-test',
     repeat('b',64),'etag-1',repeat('c',64),'file-1','succeeded',
     '00000000-0000-4000-8000-000000000801');

INSERT INTO ai_file_import_fingerprints
    (import_id,user_id,folder_id,source_fingerprint,source_etag,content_sha256,
     openwebui_file_id,status,correlation_id)
VALUES
    ('00000000-0000-4000-8000-000000000832','__v8_contract__','folder-test',
     repeat('d',64),'etag-2',repeat('c',64),'file-2','succeeded',
     '00000000-0000-4000-8000-000000000801')
ON CONFLICT DO NOTHING;

DO $$
BEGIN
    IF (SELECT COUNT(*) FROM ai_file_import_fingerprints
        WHERE user_id='__v8_contract__' AND folder_id='folder-test'
          AND content_sha256=repeat('c',64) AND status='succeeded') <> 1 THEN
        RAISE EXCEPTION 'file content idempotency contract failed';
    END IF;
END $$;

INSERT INTO ai_audit_events
    (audit_id,user_id,correlation_id,action,phase,target,status,event_hash)
VALUES
    ('00000000-0000-4000-8000-000000000841','__v8_contract__',
     '00000000-0000-4000-8000-000000000801','contract_test','created',
     'postgresql','succeeded',repeat('e',64));

DO $$
BEGIN
    BEGIN
        UPDATE ai_audit_events SET status='failed'
        WHERE audit_id='00000000-0000-4000-8000-000000000841';
        RAISE EXCEPTION 'audit append-only contract failed';
    EXCEPTION
        WHEN raise_exception THEN
            IF SQLERRM <> 'ai_audit_events is append-only' THEN
                RAISE;
            END IF;
    END;
END $$;

ROLLBACK;
