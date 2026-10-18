-- 5.6.60 : un travail qui attend le verrou général n'est plus repris toutes les 3 secondes en tête de file ; il attend un délai croissant
-- (not_before), le nombre d'attentes et le détenteur du verrou sont notés pour le diagnostic.
ALTER TABLE jobs ADD COLUMN not_before TEXT;
ALTER TABLE jobs ADD COLUMN lock_waits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN blocked_by TEXT NOT NULL DEFAULT '';
