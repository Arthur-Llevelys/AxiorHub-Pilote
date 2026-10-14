-- 5.6.44 (audit 5.6.43, F06) : mandat durable « Confier ce classement à l'IA » pour UN SEUL message (localisateur complet), avec le
-- dossier, les actions cochées et la sélection exacte des pièces. Il attend la reprise de l'IA ou le choix du dossier sans dépendre
-- de la borne des nouveaux messages ; il n'est exécuté qu'une fois et n'étend jamais l'autorisation à un fil ou à un expéditeur.
CREATE TABLE IF NOT EXISTS mail_filing_mandates (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity TEXT NOT NULL,
    uid TEXT NOT NULL,
    matter_id TEXT NOT NULL DEFAULT '',
    actions_json TEXT NOT NULL CHECK(json_valid(actions_json) AND json_type(actions_json)='array'),
    selection_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(selection_json)),
    owner TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','waiting_matter','done','cancelled','error')),
    error_code TEXT NOT NULL DEFAULT '',
    link_id TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_id,account_id,folder,uidvalidity,uid)
);

CREATE INDEX IF NOT EXISTS mail_filing_mandates_state
    ON mail_filing_mandates(state,updated_at);
