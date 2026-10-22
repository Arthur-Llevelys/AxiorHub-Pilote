"""5.6.71 : surveillance des dossiers allégée (diagnostic du cabinet du 22 octobre 2026).

Constat en lecture seule : environ 1 500 « Surveillances immédiates du dossier » par jour, chacune relisant un répertoire dans Nextcloud,
suivies de près de 200 indexations et 150 actualisations. Deux causes :

1. tout événement d'agenda nouveau ou modifié (synchronisation, récurrences…) relançait le tour COMPLET de tous les dossiers ;
2. le tour périodique (toutes les 30 minutes) relisait dans Nextcloud chaque répertoire, alors que la veille des documents
   (« live_documents430 ») le fait déjà et lance elle-même la surveillance du dossier dont un fichier a changé.

Désormais :
- un événement d'agenda modifié ne relance que la surveillance du ou des dossiers qu'il désigne (au plus 5), ou rien ;
- quand la veille des documents a terminé un passage depuis moins de 2 heures, le tour périodique ne relit plus Nextcloud
  (les signaux — échéances, rendez-vous, courriels sans réponse, pièces illisibles — sont recalculés depuis la base) et il est espacé
  à 6 heures au moins. Sans veille des documents récente, rien ne change.
"""
from datetime import datetime, timedelta, timezone

WATCH_HOURS = 2
ROUND_MINUTES = 360
MAX_CALENDAR_MATTERS = 5


def documents_watch_recent(desk, hours=WATCH_HOURS):
    """Vrai si la veille des documents a terminé un passage récemment (elle repère seule les fichiers nouveaux ou modifiés)."""
    try:
        row = desk.db.execute("SELECT MAX(finished) FROM jobs WHERE kind='live_documents430' AND status='done'").fetchone()
    except Exception:   # noqa: BLE001 — base ancienne : comportement d'avant
        return False
    if not row or not row[0]:
        return False
    try:
        when = datetime.fromisoformat(str(row[0]).replace('Z', '+00:00'))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when <= timedelta(hours=hours)


def round_minutes(desk, minutes):
    """Intervalle du tour périodique des dossiers : au moins 6 heures quand la veille des documents tourne."""
    return max(int(minutes), ROUND_MINUTES) if documents_watch_recent(desk) else int(minutes)


def skip_live_inventory(desk, args):
    """Le tour périodique (``tour``) n'a pas à relire Nextcloud si la veille des documents vient de le faire."""
    return bool(args.get('tour')) and documents_watch_recent(desk)


def after_calendar(desk, events):
    """Surveillance des seuls dossiers désignés par les événements d'agenda modifiés (titre, description) ; jamais le tour complet."""
    from .proactive568 import match_matter
    found = []
    for ev in events or []:
        try:
            matter, _candidates = match_matter(desk, str(ev.get('summary') or ev.get('title') or ''), str(ev.get('description') or ''))
        except Exception:   # noqa: BLE001 — événement illisible : ignoré
            continue
        mid = str((matter or {}).get('id') if isinstance(matter, dict) else (matter or ''))
        if mid and mid not in found:
            found.append(mid)
        if len(found) >= MAX_CALENDAR_MATTERS:
            break
    queued = []
    from .common import Stop
    for mid in found:
        try:
            queued.append(desk.enqueue('monitor_matter', {'matter': mid}, priority=60))
        except Stop:
            continue
    return {'dossiers': found, 'travaux': queued}
