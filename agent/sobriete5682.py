"""5.6.82 : sobriété des appels automatiques à l'IA.

Les contrôles réguliers portent sur le NOUVEAU (courriel, fichier, événement) ; sans nouveauté, aucun appel. Ce module borne ce qu'un
élément nouveau peut déclencher :

- plafond journalier des appels automatiques : au total, et pour les modèles avancés, les plus coûteux. Atteint, les travaux attendent le
  lendemain sur leur ligne. Les demandes de l'avocat ne sont jamais comptées ni bloquées ;
- reprise bornée d'un courriel qui échoue : trois essais, sans quoi il repasserait à chaque balayage pendant la fenêtre d'examen ;
- synthèse d'un dossier regroupée : plusieurs changements rapprochés donnent une seule actualisation ;
- une seule préparation automatique d'une même audience, quel que soit le mécanisme qui la déclenche ;
- les fichiers produits par AxiorHub ne relancent pas d'analyse.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from .common import Stop

CODE = 'plafond_automatique_atteint'
OWN_FOLDER = '90_AxiorHub_'
BRIEF_HOURS = 6
HEARING_DAYS = 14
MAX_MAIL_FAILURES = 3
SCHEMA = 'CREATE TABLE IF NOT EXISTS orchestration_echecs_5682(mail_key TEXT PRIMARY KEY, essais INTEGER NOT NULL, code TEXT NOT NULL, at TEXT NOT NULL)'


def _day():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d') + '%'


def counters(state_dir):
    """(appels automatiques du jour, dont modèles avancés). Les modèles locaux ne comptent pas."""
    path = Path(str(state_dir or '')) / 'desk.sqlite3'
    if not path.is_file():
        return 0, 0
    db = sqlite3.connect(path, timeout=5)
    try:
        row = db.execute("SELECT COUNT(*),COALESCE(SUM(niveau IN ('avance','maximal')),0) FROM decisions_5682 WHERE automatique=1 AND unite<>'aucune' AND at LIKE ?", (_day(),)).fetchone()
    except sqlite3.Error:
        return 0, 0
    finally:
        db.close()
    return int(row[0] or 0), int(row[1] or 0)


def check_daily(state_dir, rule, level):
    total, advanced = counters(state_dir)
    if total >= rule['plafond_auto_jour'] or (level in ('avance', 'maximal') and advanced >= rule['plafond_auto_avance_jour']):
        raise Stop(CODE)


def hold(config):
    """Motif de ne pas lancer un balayage automatique : ne pas relire des courriels pour un appel qui sera refusé."""
    from . import chatgpt5682, politique5682
    rule = politique5682.current(config)
    if not rule or rule['mode'] == 'local':
        return ''
    if rule['mode'] == 'chatgpt':
        found = chatgpt5682.deferred(config.get('state_dir'))
        if found:
            return str(found.get('motif') or 'chatgpt_limite_usage_atteinte')
    return CODE if counters(config.get('state_dir'))[0] >= rule['plafond_auto_jour'] else ''


def tomorrow():
    return (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0).isoformat()


def own_file(path):
    return OWN_FOLDER in str(path or '')


def recent_job(desk, kind, matter, hours, states=('pending', 'running', 'done')):
    """Fin (ou création) du dernier travail de ce type sur ce dossier dans la période ; '' sinon."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    marks = ','.join('?' * len(states))
    row = desk.db.execute('SELECT COALESCE(finished,created) FROM jobs WHERE kind=? AND matter_ref=? AND status IN (%s) AND COALESCE(finished,created)>=? '
                          'ORDER BY id DESC LIMIT 1' % marks, (kind, str(matter or ''), *states, since)).fetchone()
    return str(row[0]) if row else ''


def hearing_prepared(desk, matter):
    return bool(recent_job(desk, 'prepare_hearing', matter, HEARING_DAYS * 24))


def brief_after(desk, matter):
    """Heure à partir de laquelle relancer la synthèse du dossier ; '' si elle peut partir tout de suite."""
    last = recent_job(desk, 'refresh_brief', matter, BRIEF_HOURS, ('done',))
    if not last:
        return ''
    try:
        return (datetime.fromisoformat(last) + timedelta(hours=BRIEF_HOURS)).isoformat()
    except ValueError:
        return ''


def mail_failures(desk):
    desk.db.execute(SCHEMA)
    return {r[0] for r in desk.db.execute('SELECT mail_key FROM orchestration_echecs_5682 WHERE essais>=?', (MAX_MAIL_FAILURES,))}


def note_mail_failure(desk, key, code):
    desk.db.execute(SCHEMA)
    desk.db.execute('INSERT INTO orchestration_echecs_5682(mail_key,essais,code,at) VALUES(?,1,?,?) ON CONFLICT(mail_key) DO UPDATE SET essais=essais+1,code=excluded.code,at=excluded.at',
                    (key, str(code)[:80], datetime.now(timezone.utc).isoformat()))
    desk.db.execute('DELETE FROM orchestration_echecs_5682 WHERE at<?', ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),))
    desk.db.commit()


def classification_from_triage(triage, source_ids):
    """Le courriel a déjà été trié à l'arrivée : ce résultat évite un second appel pour la même question."""
    if not isinstance(triage, dict) or 'needs_reply' not in triage or 'intent' not in triage:
        return None
    intent = str(triage.get('intent') or 'other')
    excluded = str(triage.get('exclude_category') or 'none') != 'none'
    actionable = not excluded and bool(triage.get('needs_reply') or intent in ('legal', 'strategy', 'documents'))
    return {'actionable': actionable, 'category': {'appointment': 'administrative', 'status': 'client_reply', 'documents': 'document_update', 'administrative': 'administrative',
                                                   'legal': 'legal_opinion', 'strategy': 'legal_opinion'}.get(intent, 'information'),
            'urgency': 'normal' if actionable else 'none', 'ambiguous_matter': bool(triage.get('ambiguous')), 'needs_reply': bool(triage.get('needs_reply')),
            'needs_document_project': False, 'needs_legal_analysis': intent in ('legal', 'strategy'), 'reason': str(triage.get('reason') or 'tri à l’arrivée')[:300],
            'source_ids': list(source_ids)[:1]}
