-- 5.6.43 (cahier v9, § 16) : origine explicite, parenté et dossier normalisé des travaux ; motif d'arrêt (pause reprenable / annulation).
-- Les travaux antérieurs gardent l'origine « legacy » : ils ne sont pas requalifiés en demandes de l'avocat d'après leur priorité.
ALTER TABLE jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE jobs ADD COLUMN parent_job_id INTEGER;
ALTER TABLE jobs ADD COLUMN matter_ref TEXT;
ALTER TABLE jobs ADD COLUMN stop_reason TEXT;
-- Le dossier normalisé des travaux existants est renseigné au démarrage (desk.py), une seule fois.
CREATE INDEX IF NOT EXISTS jobs_matter_ref ON jobs(matter_ref);
CREATE INDEX IF NOT EXISTS jobs_parent ON jobs(parent_job_id);
