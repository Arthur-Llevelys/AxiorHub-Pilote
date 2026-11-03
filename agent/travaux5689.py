"""5.6.89 (chapitre 20) : une seule lecture des travaux, pour l'en-tête d'« Aujourd'hui », l'activité et la file de travail.

L'état métier se déduit du travail enregistré (statut, résultat, preuve), jamais d'un texte du modèle, et il n'y en a qu'un : en attente de calcul, en cours, attente d'information,
attente de décision, suspendu, annulé, échec, terminé avec preuve. Un travail fini sans livrable reste « terminé », sans se réclamer d'une preuve. « En cours » ne désigne jamais un
mélange de travaux lancés et de travaux encore en file : chaque compteur est séparé et son périmètre est annoncé.

Pour un travail qui attend : depuis quand, la dernière étape utile, ce qui le bloque et l'action pertinente. Lecture bornée, sans appel au modèle ni au réseau.

Relances : une erreur qu'un nouvel essai identique ne corrige pas (requête refusée, autorisation absente, paramètre invalide) n'est pas relancée par un automatisme avec les mêmes
arguments. Une demande de l'avocat reste possible, et un succès lève la retenue.

Diagnostic d'une erreur de modèle : connecteur, modèle, étape et erreur utile, regroupés quand ils sont identiques. Seuls des champs structurés et courts sont retenus : ni secret,
ni texte du fournisseur, qui peut recopier le contenu envoyé.
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .common import Stop
from .etat5657 import _age, _result, _since, business_state

ETATS = {'attente_calcul': 'En attente de calcul', 'en_cours': 'En cours', 'attente_information': 'Attente d’information', 'attente_decision': 'Attente de décision',
         'suspendu': 'Suspendu', 'annule': 'Annulé', 'echec': 'Échec', 'termine_preuve': 'Terminé avec preuve', 'termine': 'Terminé'}
PERIMETRE = 'Tous les travaux du cabinet, automatismes compris.'
# 5.6.89 : erreurs qu'un nouvel essai identique ne corrige pas ; le correctif vient d'un réglage ou d'une autorisation
DEFINITIVES = frozenset(('http_400', 'http_401', 'http_403', 'http_405', 'http_413', 'http_422', 'authentification_fournisseur_refusee', 'acces_fournisseur_refuse',
                         'modele_ou_route_fournisseur_absent', 'fournisseur_externe_non_autorise', 'modele_distant_refuse', 'type_fournisseur_invalide', 'modele_ollama_absent',
                         'chatgpt_option_non_prise_en_charge', 'chatgpt_acces_refuse', 'chatgpt_compte_non_eligible', 'chatgpt_reconnexion_requise'))
STATUTS_DEFINITIFS = (400, 401, 403, 404, 405, 413, 422)
RETENUE_HEURES = 6               # délai de reprise : au-delà, un automatisme a droit à un seul nouvel essai
TAILLE_DIAGNOSTIC = 100
JOURS_DIAGNOSTIC = 30
CONNECTEURS = {'ollama': 'Moteur local Ollama', 'chatgpt': 'Abonnement ChatGPT', 'openai': 'OpenAI', 'mistral': 'Mistral', 'anthropic': 'Anthropic (API Claude)', 'openrouter': 'OpenRouter'}
JETON = re.compile(r'[A-Za-z0-9_.\[\]-]{1,60}')
SCHEMA = '''CREATE TABLE IF NOT EXISTS erreurs_modele5689(cle TEXT PRIMARY KEY, connecteur TEXT NOT NULL, type_connecteur TEXT NOT NULL DEFAULT '', modele TEXT NOT NULL DEFAULT '',
  etape TEXT NOT NULL DEFAULT '', code TEXT NOT NULL, statut INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '{}', occurrences INTEGER NOT NULL DEFAULT 1, job TEXT NOT NULL DEFAULT '',
  premier TEXT NOT NULL, dernier TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS erreurs_modele5689_dernier ON erreurs_modele5689(dernier);'''


# ------------------------------------------------------------------------------------------------ état métier
def _preuve(result, verified=False):
    if verified or result.get('brouillon_imap') == 'verifie' or result.get('draft_verified') or result.get('rangees'):
        return True
    return bool(result.get('sha256')) and result.get('readback_sha256') == result.get('sha256')


def etat(kind, status, result=None, priority=None, verified=False):
    """(code, libellé) unique d'un travail. ``verified`` : le registre de production confirme un livrable vérifié."""
    res = result if isinstance(result, dict) else _result(result)
    code, label = business_state(kind, status, res, priority)
    if code == 'en_file':
        return 'attente_calcul', ETATS['attente_calcul']
    if code == 'en_cours':
        return 'en_cours', ETATS['en_cours']
    if code == 'arret_demande':
        return 'en_cours', 'En cours, arrêt demandé'
    if code == 'suspendu':
        return 'suspendu', label
    if code in ('echec', 'echec_reprise'):
        return 'echec', label
    if status == 'cancelled':
        return 'annule', ETATS['annule']
    if status != 'done':
        return 'termine', ETATS['termine']
    if res.get('recuperation_partielle') or res.get('missing') or res.get('information_requise') or res.get('dossier_a_preciser'):
        return 'attente_information', ETATS['attente_information']
    if code == 'question':
        return 'attente_decision', ETATS['attente_decision']
    if _preuve(res, verified):
        return 'termine_preuve', ETATS['termine_preuve']
    return 'termine', 'Terminé, dépôt non vérifié' if code == 'projet_pret' else 'Terminé, sans livrable à vérifier'


def compteurs(desk):
    """Compteurs de l'en-tête et de la file : une requête bornée, sans appel extérieur. Un arrêt demandé compte en cours tant que le travailleur ne l'a pas confirmé."""
    counts = dict(desk.db.execute("SELECT status,COUNT(*) FROM jobs WHERE status IN ('running','cancel_requested','pending','suspended') GROUP BY status").fetchall())
    return {'en_cours': counts.get('running', 0) + counts.get('cancel_requested', 0), 'attente_calcul': counts.get('pending', 0), 'suspendu': counts.get('suspended', 0), 'perimetre': PERIMETRE}


def libelle_compteurs(data):
    parts = ['%d en cours' % data['en_cours'], '%d en attente de calcul' % data['attente_calcul']]
    if data['suspendu']:
        parts.append('%d suspendu%s' % (data['suspendu'], 's' if data['suspendu'] > 1 else ''))
    return ' · '.join(parts)


def _dependance(row, now):
    """Ce qui retient un travail, et ce qu'on peut y faire."""
    status, keys = row['status'], row.keys()
    reason = row['stop_reason'] if 'stop_reason' in keys else None
    if status == 'suspended':
        return ('Pause de l’IA sur ce dossier', 'Reprendre l’IA sur le dossier.') if reason == 'pause_dossier' else ('Pause de l’IA', 'Reprendre l’IA depuis Aujourd’hui ou Paramètres.')
    if status == 'cancel_requested':
        return ('Confirmation d’arrêt du travailleur', '')
    if status != 'pending':
        return ('', '')
    blocked = str(row['blocked_by'] or '') if 'blocked_by' in keys else ''
    later = _since(row['not_before'], now) if 'not_before' in keys and row['not_before'] else None
    if blocked:
        from .chatgpt5682 import HELD_CODES
        if blocked in HELD_CODES:
            return ('Limite d’usage ou indisponibilité du service d’IA', 'Rien à faire : le travail reprendra seul.')
        return ('Verrou tenu par : ' + blocked[:80], 'Attendre, ou arrêter le travail qui le tient.')
    if later is not None and later < 0:
        return ('Nouvel essai prévu dans %s' % _age(-later), '')
    return ('Place dans la file de travail', '')


def projection(desk, row, now=None):
    """Lecture commune d'une ligne de ``jobs`` : état, depuis quand, dernière étape utile, dépendance et action."""
    now = now or datetime.now(timezone.utc)
    keys = row.keys()
    result = row['result'] if 'result' in keys else None
    priority = row['priority'] if 'priority' in keys else None
    verified = False
    if row['status'] == 'done':
        try:
            verified = bool(desk.db.execute("SELECT 1 FROM production_deliverables_v420 WHERE job_id=? AND status='verified' LIMIT 1", (row['id'],)).fetchone())
        except sqlite3.Error:     # table de production jamais créée
            verified = False
    code, label = etat(row['kind'], row['status'], result, priority, verified)
    since = (row['started'] if row['status'] in ('running', 'cancel_requested') and 'started' in keys else None) or (row['created'] if 'created' in keys else None)
    last = None
    try:
        last = desk.db.execute('SELECT message FROM live_events_v430 WHERE job_id=? ORDER BY id DESC LIMIT 1', (row['id'],)).fetchone()
    except sqlite3.Error:
        pass
    dependance, action = _dependance(row, now)
    seconds = _since(since, now)
    return {'etat': code, 'label': label, 'depuis': since or '', 'duree': _age(seconds) if seconds is not None else '', 'etape': last[0] if last else '', 'dependance': dependance, 'action': action}


def phrase_attente(view):
    """Ligne affichée sous un travail qui attend ; rien pour un travail qui avance normalement."""
    if view['etat'] not in ('attente_calcul', 'suspendu') or not view['duree']:
        return ''
    return ' · '.join(x for x in ('Attend depuis ' + view['duree'], view['dependance'], view['action']) if x)


# ------------------------------------------------------------------------------------------------ relances
def definitif(code, detail=None):
    return str(code or '') in DEFINITIVES or int((detail or {}).get('remote_status') or 0) in STATUTS_DEFINITIFS


def check_relance(desk, kind, args, origin):
    """Refuse la remise en file AUTOMATIQUE d'un travail identique dont le dernier essai a échoué sur une erreur définitive, pendant la retenue. Un succès postérieur lève la retenue."""
    if origin != 'automatic':
        return
    raw = json.dumps(args or {}, sort_keys=True)
    row = desk.db.execute("SELECT status,finished,result FROM jobs WHERE kind=? AND args=? AND status IN ('done','error') ORDER BY id DESC LIMIT 1", (kind, raw)).fetchone()
    if not row or row['status'] != 'error':
        return
    result = _result(row['result'])
    if not definitif(result.get('erreur'), result.get('detail')):
        return
    age = _since(row['finished'], datetime.now(timezone.utc))
    if age is not None and age < RETENUE_HEURES * 3600:
        raise Stop('relance_identique_refusee')


# ------------------------------------------------------------------------------------------------ regroupement
def regrouper(items, key):
    """Alertes identiques réunies sur une ligne ; le détail de chacune reste dans ``autres``. L'ordre du premier rencontré est conservé."""
    seen, out = {}, []
    for item in items:
        k = key(item)
        if k in seen:
            seen[k]['nombre'] = seen[k].get('nombre', 1) + 1
            seen[k].setdefault('autres', []).append(item)
        else:
            seen[k] = item
            out.append(item)
    return out


# ------------------------------------------------------------------------------------------------ diagnostic d'une erreur de modèle
def _jeton(value):
    value = str(value or '')
    return value if JETON.fullmatch(value) and not value.startswith('sk-') else ''


def _champs(provider):
    """Seuls des identifiants courts passent : le message libre d'un fournisseur peut recopier le texte envoyé."""
    return {k: _jeton((provider or {}).get(k)) for k in ('type', 'code', 'param') if _jeton((provider or {}).get(k))}


def note_model_error(cfg, stage, error):
    """Journalise une erreur de modèle, regroupée à l'identique. Ne lève jamais d'exception : le diagnostic ne change pas l'issue de l'appel."""
    try:
        state = str((cfg or {}).get('state_dir') or '')
        from .arret5643 import STOP_CODES
        code = str(error)
        if not state or code in STOP_CODES:
            return
        detail = dict(getattr(error, 'detail', None) or {})
        status, champs = int(detail.get('remote_status') or 0), _champs(detail.get('provider_error'))
        kind = str((cfg or {}).get('provider_type') or (cfg or {}).get('type') or 'ollama')
        db = sqlite3.connect(Path(state) / 'desk.sqlite3', timeout=5)
        try:
            db.executescript(SCHEMA)
            if kind == 'chatgpt' and not status:      # le transport de l'abonnement note le motif réel à part
                from .chatgpt5662 import FAILURE_KEY
                row = db.execute('SELECT value FROM settings WHERE key=?', (FAILURE_KEY,)).fetchone()
                note = json.loads(row[0]) if row else {}
                status, champs = int(note.get('statut') or 0), _champs({'code': note.get('code'), 'param': note.get('param')})
                if status and not getattr(error, 'detail', None):
                    error.detail = {'service': 'fournisseur_ia', 'remote_status': status}
            from .trace480 import CTX
            ctx = CTX.get() or {}
            connecteur = str((cfg or {}).get('provider_id') or (cfg or {}).get('id') or kind)[:60]
            modele, etape = str((cfg or {}).get('model') or '')[:80], str(stage or (cfg or {}).get('purpose') or 'génération')[:60]
            raw = json.dumps(champs, sort_keys=True)
            cle = hashlib.sha256(json.dumps([connecteur, modele, etape, code, status, raw]).encode('utf-8')).hexdigest()
            stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            db.execute('INSERT INTO erreurs_modele5689(cle,connecteur,type_connecteur,modele,etape,code,statut,detail,job,premier,dernier) VALUES(?,?,?,?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(cle) DO UPDATE SET occurrences=occurrences+1,dernier=excluded.dernier,job=excluded.job',
                       (cle, connecteur, kind, modele, etape, code[:80], status, raw, str(ctx.get('job_id') or ''), stamp, stamp))
            db.execute('DELETE FROM erreurs_modele5689 WHERE dernier<? OR cle NOT IN (SELECT cle FROM erreurs_modele5689 ORDER BY dernier DESC LIMIT ?)',
                       ((datetime.now(timezone.utc) - timedelta(days=JOURS_DIAGNOSTIC)).isoformat(), TAILLE_DIAGNOSTIC))
            db.commit()
        finally:
            db.close()
    except Exception:   # noqa: BLE001 — jamais d'effet sur l'appel du modèle
        pass


CONSEILS = {400: 'Le fournisseur refuse cette requête : un nouvel essai identique échouerait. Vérifiez le modèle choisi et ses options dans Paramètres › Modèles d’IA.',
            401: 'L’autorisation n’est plus valable : renouvelez la clé ou reconnectez le compte.', 403: 'Ce compte n’a pas accès à ce modèle : vérifiez son abonnement ou ses droits.',
            404: 'Le modèle ou l’adresse n’existe pas chez ce fournisseur : choisissez un modèle disponible.', 413: 'La demande dépasse la taille admise par ce modèle.',
            422: 'Un paramètre de la requête est refusé : vérifiez les options de ce modèle.', 429: 'Limite d’usage atteinte : le travail reprendra plus tard.'}


def diagnostic(desk, limit=10):
    """Erreurs de modèle récentes, de la plus récente à la plus ancienne, sans secret ni contenu."""
    desk.db.executescript(SCHEMA)
    out = []
    for r in desk.db.execute('SELECT * FROM erreurs_modele5689 ORDER BY dernier DESC LIMIT ?', (int(limit),)).fetchall():
        champs = _result(r['detail'])
        utile = 'refus %d' % r['statut'] if r['statut'] else r['code']
        precision = ', '.join(x for x in (champs.get('type'), champs.get('code'), ('paramètre ' + champs['param']) if champs.get('param') else '') if x)
        out.append({'connecteur': CONNECTEURS.get(r['type_connecteur'], r['connecteur']), 'modele': r['modele'] or 'modèle non renseigné', 'etape': r['etape'], 'code': r['code'], 'statut': r['statut'],
                    'erreur': utile + ((' (%s)' % precision) if precision else ''), 'occurrences': r['occurrences'], 'dernier': r['dernier'], 'conseil': CONSEILS.get(r['statut'], '')})
    return out


def diagnostic_html(desk):
    try:
        rows = diagnostic(desk)
    except sqlite3.Error:
        return ''
    if not rows:
        return ''
    items = ''.join('<li><strong>%s</strong> · %s · étape %s · %s%s%s</li>' % (
        e(r['connecteur']), e(r['modele']), e(r['etape']), e(r['erreur']), (' · %d fois' % r['occurrences']) if r['occurrences'] > 1 else '',
        ('<div class="ax-muted">%s</div>' % e(r['conseil'])) if r['conseil'] else '') for r in rows)
    return '<details class="e5657-modele"><summary>Erreurs du modèle d’IA (%d)</summary><ul>%s</ul></details>' % (len(rows), items)
