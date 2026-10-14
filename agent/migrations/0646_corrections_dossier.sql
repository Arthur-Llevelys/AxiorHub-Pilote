-- 5.6.44 : corrections de l'avocat sur les informations proposées par l'IA dans la page du dossier (parties, juridiction, numéro de
-- RG) : supprimer, modifier ou ajouter. Les corrections s'appliquent à l'affichage et aux analyses suivantes ; elles sont historisées
-- et annulables (active=0), jamais effacées.
CREATE TABLE IF NOT EXISTS corrections5644 (
    id INTEGER PRIMARY KEY,
    matter TEXT NOT NULL,
    section TEXT NOT NULL CHECK(section IN ('parties','jurisdiction','case_number')),
    action TEXT NOT NULL CHECK(action IN ('hide','replace','add')),
    target TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL DEFAULT '',
    record_id TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL,
    created TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    undone_by TEXT NOT NULL DEFAULT '',
    undone_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS corrections5644_matter
    ON corrections5644(matter,section,active);
