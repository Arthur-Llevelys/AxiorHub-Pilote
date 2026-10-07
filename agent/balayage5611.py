"""5.6.11 : sélection des courriels examinés par les balayages automatiques (orchestrateur, autonomie).

Avant : tout courriel rattaché à un dossier, quel que soit son âge, finissait par être réexaminé et donnait lieu à des propositions de
réponses, de projets ou d'actes — y compris des affaires traitées des semaines plus tôt. Désormais, seuls les courriels reçus dans la
fenêtre d'antériorité (14 jours par défaut, réglable) sont examinés, et jamais ceux envoyés par le cabinet lui-même.
"""
from datetime import datetime, timedelta, timezone

DEFAULT_LOOKBACK_DAYS = 14
TABLES = {'orchestrator': 'mail_orchestrations_v260', 'autonomy': 'autonomy_mail_observations_v230'}


def lookback_days(cfg):
    try:
        return max(1, min(int(cfg.get('lookback_days') or DEFAULT_LOOKBACK_DAYS), 365))
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK_DAYS


def since(cfg):
    return (datetime.now(timezone.utc) - timedelta(days=lookback_days(cfg))).isoformat()


def recent_work_items(desk, cfg, kind, limit):
    """Courriels rattachés à un dossier, reçus dans la fenêtre, non ignorés, non envoyés par le cabinet ; les non examinés d'abord."""
    from .mailbox import own_addresses
    table = TABLES[kind]
    own = own_addresses(desk.c.get('mail', {}))
    rows = desk.db.execute("SELECT w.* FROM work_items w LEFT JOIN " + table + " o ON o.mail_key=w.mail_key"
                           " WHERE w.matter<>'' AND w.source_status NOT IN ('ignored','appending','append_uncertain') AND w.received>=?"
                           " ORDER BY CASE WHEN o.mail_key IS NULL THEN 0 ELSE 1 END,w.received DESC LIMIT ?",
                           (since(cfg), max(1, int(limit)))).fetchall()
    return [r for r in rows if str(r['sender'] or '').strip().lower() not in own]
