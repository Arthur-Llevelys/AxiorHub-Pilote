-- 5.6.43 (cahier v9, § 13.2) : conservation des courriels dans CORRESPONDANCES et des pièces dans NOS PIECES.
-- Adapté de la migration de départ jointe au cahier ; la transaction est ouverte par le registre de migrations.
-- Aucune autorisation automatique n’est activée : politiques absentes = désactivées ; exceptions par message = « inherit ».
CREATE TABLE IF NOT EXISTS mail_archives (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL CHECK(length(raw_sha256)=64 AND raw_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_size INTEGER NOT NULL CHECK(raw_size>0),
    message_id TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT 'unknown' CHECK(direction IN ('received','sent','internal','unknown')),
    headers_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(headers_json)),
    declared_date TEXT,
    internaldate TEXT NOT NULL,
    -- captured = octets lus ; seule la présence d'un actif original vérifié
    -- prouve une conservation EML. Les pièces seules n'en créent pas.
    capture_state TEXT NOT NULL DEFAULT 'captured' CHECK(capture_state IN ('captured','unavailable','error')),
    extraction_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(scope_id,account_id,raw_sha256)
);

CREATE TABLE IF NOT EXISTS mail_archive_sources (
    account_id TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity TEXT NOT NULL,
    uid TEXT NOT NULL,
    archive_id TEXT NOT NULL REFERENCES mail_archives(id) ON DELETE RESTRICT,
    observed_at TEXT NOT NULL,
    availability TEXT NOT NULL DEFAULT 'present' CHECK(availability IN ('present','moved','missing','unknown')),
    PRIMARY KEY(account_id,folder,uidvalidity,uid)
);

CREATE TABLE IF NOT EXISTS mail_archive_links (
    id TEXT PRIMARY KEY,
    archive_id TEXT NOT NULL REFERENCES mail_archives(id) ON DELETE RESTRICT,
    matter_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'courriel',
    tags_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(tags_json) AND json_type(tags_json)='array'),
    mode TEXT NOT NULL CHECK(mode IN ('manual','assisted','automatic_confirmed')),
    resolution_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(resolution_json)),
    confirmed_by TEXT,
    confirmed_at TEXT,
    email_requested INTEGER NOT NULL DEFAULT 0 CHECK(email_requested IN (0,1)),
    attachments_requested INTEGER NOT NULL DEFAULT 0 CHECK(attachments_requested IN (0,1)),
    conservation_state TEXT NOT NULL DEFAULT 'not_requested'
        CHECK(conservation_state IN ('not_requested','proposed','queued','saving','verifying','verified','error','uncertain','cancel_requested','cancelled','withdrawn')),
    attachments_state TEXT NOT NULL DEFAULT 'not_requested'
        CHECK(attachments_state IN ('not_requested','proposed','queued','saving','verifying','verified','partial','error','uncertain','cancel_requested','cancelled','withdrawn')),
    extraction_state TEXT NOT NULL DEFAULT 'pending' CHECK(extraction_state IN ('pending','complete','partial','error')),
    indexing_state TEXT NOT NULL DEFAULT 'pending' CHECK(indexing_state IN ('pending','indexed','error')),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
    job_id INTEGER,
    attachments_job_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(archive_id,matter_id)
);

CREATE TABLE IF NOT EXISTS mail_archive_assets (
    id TEXT PRIMARY KEY,
    link_id TEXT NOT NULL REFERENCES mail_archive_links(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('original','reading','attachment','inline_image','annotation','render_pdf','manifest')),
    destination_usage TEXT NOT NULL CHECK(destination_usage IN ('correspondances','nos_pieces','legacy')),
    part_key TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    content_id TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
    etag TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
    state TEXT NOT NULL DEFAULT 'staged' CHECK(state IN ('staged','writing','verified','uncertain','error','superseded')),
    provenance TEXT NOT NULL CHECK(provenance IN ('mail_original','mail_extraction','mail_summary_ai','human_annotation','attachment')),
    detail_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(detail_json)),
    verified_at TEXT,
    UNIQUE(link_id,kind,part_key,version),
    CHECK(kind NOT IN ('original','reading','inline_image','annotation','render_pdf') OR destination_usage='correspondances'),
    CHECK(kind<>'attachment' OR destination_usage IN ('nos_pieces','legacy'))
);

-- Un original logique par classement ; les dérivés peuvent avoir des versions.
CREATE UNIQUE INDEX IF NOT EXISTS mail_archive_one_original
    ON mail_archive_assets(link_id) WHERE kind='original';

CREATE TABLE IF NOT EXISTS mail_archive_operations (
    id TEXT PRIMARY KEY,
    request_key TEXT NOT NULL,
    link_id TEXT NOT NULL REFERENCES mail_archive_links(id) ON DELETE RESTRICT,
    phase TEXT NOT NULL,
    destination TEXT NOT NULL,
    expected_sha256 TEXT NOT NULL CHECK(length(expected_sha256)=64 AND expected_sha256 NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL DEFAULT 'prepared'
        CHECK(state IN ('prepared','started','confirmed','uncertain','cancel_requested','cancelled','error')),
    job_id INTEGER,
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(link_id,phase,destination,expected_sha256)
);

CREATE TABLE IF NOT EXISTS mail_archive_events (
    id INTEGER PRIMARY KEY,
    link_id TEXT NOT NULL REFERENCES mail_archive_links(id) ON DELETE RESTRICT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    from_version INTEGER,
    to_version INTEGER,
    detail_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(detail_json)),
    at TEXT NOT NULL
);

-- Pas d'activation automatique lors de la migration. La politique compte
-- est générale pour les nouveaux messages de ce compte ; le cabinet est
-- une portée distincte réservée au rôle habilité.
CREATE TABLE IF NOT EXISTS mail_filing_policies (
    scope_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('cabinet','account')),
    scope_key TEXT NOT NULL,
    email_auto INTEGER NOT NULL DEFAULT 0 CHECK(email_auto IN (0,1)),
    attachments_auto INTEGER NOT NULL DEFAULT 0 CHECK(attachments_auto IN (0,1)),
    effective_from TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(scope_id,scope_kind,scope_key)
);

-- Exception pour un seul message, jamais pour un fil/expéditeur.
-- archive_id devient disponible après la capture complète ; les anciens
-- localisateurs ne sont reportés qu'après vérification des octets/droits.
CREATE TABLE IF NOT EXISTS mail_filing_message_rules (
    scope_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity TEXT NOT NULL,
    uid TEXT NOT NULL,
    archive_id TEXT REFERENCES mail_archives(id) ON DELETE RESTRICT,
    email_override TEXT NOT NULL DEFAULT 'inherit' CHECK(email_override IN ('inherit','allow','deny')),
    attachments_override TEXT NOT NULL DEFAULT 'inherit' CHECK(attachments_override IN ('inherit','allow','deny')),
    matter_id TEXT NOT NULL DEFAULT '',
    confirmed_by TEXT,
    confirmed_at TEXT,
    attachments_selection TEXT NOT NULL DEFAULT 'all' CHECK(attachments_selection IN ('all','selected')),
    selected_parts_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(selected_parts_json) AND json_type(selected_parts_json)='array'),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(scope_id,account_id,folder,uidvalidity,uid)
);

CREATE INDEX IF NOT EXISTS mail_archive_links_matter_date
    ON mail_archive_links(matter_id,created_at DESC);
CREATE INDEX IF NOT EXISTS mail_archive_sources_archive
    ON mail_archive_sources(archive_id);
CREATE INDEX IF NOT EXISTS mail_archive_operations_state
    ON mail_archive_operations(state,updated_at);
CREATE INDEX IF NOT EXISTS mail_archive_message_id
    ON mail_archives(account_id,message_id);
CREATE INDEX IF NOT EXISTS mail_filing_message_archive
    ON mail_filing_message_rules(scope_id,archive_id);

-- Propositions versionnées (préparation → confirmation) ; la clé de requête rend la confirmation idempotente (double clic, deux onglets).
CREATE TABLE IF NOT EXISTS mail_archive_proposals (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
    data_json TEXT NOT NULL CHECK(json_valid(data_json)),
    request_key TEXT UNIQUE,
    link_id TEXT,
    created_at TEXT NOT NULL
);
