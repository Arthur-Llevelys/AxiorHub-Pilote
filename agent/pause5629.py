"""5.6.29 : maîtrise du travail automatique de l'agent, dossier par dossier.

- **Pause de l'IA sur un dossier** : plus aucun travail automatique (diligences des courriels entrants, orchestrateur, surveillance
  des fichiers, avis de procédure, mémoire du dossier, projets et créations de fichiers automatiques, brouillons de réponse aux
  courriels entrants) n'est mis en file ni exécuté pour ce dossier ; les travaux automatiques déjà en file sont annulés, celui en
  cours reçoit une demande d'arrêt. Les demandes de l'avocat (priorité « avocat ») restent possibles.
- **Consignes permanentes** du dossier : transmises à l'agent avec chaque travail sur ce dossier (instruction des projets, réponses
  aux courriels, fiche de travail).
- **Coupe-circuit** : un travail automatique qui a échoué trois fois de suite pour le même dossier n'est plus relancé
  automatiquement ; il reste relançable à la main, et « Réautoriser » efface le compteur.

5.6.108 (lot P) : **suspension des envois programmés**, distincte de la pause de l'IA. La pause de l'IA (``missions567:pause``) arrête les
appels au modèle et les automatismes ; elle ne retient plus les envois que l'avocat a déjà validés et programmés. La suspension
(``SUSPENSION_ENVOIS``) retient les départs programmés de la file d'envoi (``envoi56106``) ; l'envoi immédiat explicite de l'avocat reste
possible avec confirmation. ``checkpoint(desk)`` garde son API et sa sémantique ; ``checkpoint_envois(desk)`` est le contrôle des départs.
Migration (une fois, au démarrage) : une installation dont la pause de l'IA était posée par l'avocat au moment de la mise à jour garde le
même effet (envois programmés suspendus) jusqu'à un changement explicite ; une pause posée par la mise à jour elle-même n'est pas reprise.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import time

from .common import Stop

PAUSE_KEY = 'pause5629:matters'
RESET_KEY = 'pause5629:reset'
FAILURES = 3
CONSIGNES_MAX = 4000
AUTOMATIC_PRIORITY = 30
SUSPENSION_ENVOIS = 'envois56108:suspension'      # 5.6.108 (lot P) : {'actif', 'depuis', 'par', 'origine'} ; absent = envois actifs
MIGRATION_ENVOIS = 'envois56108:migration'        # repère de la migration du réglage (une seule fois) et son compte rendu
PAUSE_IA = 'missions567:pause'
PAUSE_MISE_A_JOUR = 'maj5681:pause'               # repère posé par ``maj5661.pause_before_update`` : pause de la mise à jour, pas de l'avocat


def paused(desk):
    value = desk.settings(PAUSE_KEY, {}) or {}
    return value if isinstance(value, dict) else {}


def is_paused(desk, mid):
    return str(mid or '') in paused(desk)


def blocked_for_config(config, mid=''):
    """Lecture fraîche sans créer un Desk ni garder son cache pendant le passage IMAP."""
    path = Path(config['state_dir']) / 'desk.sqlite3'
    if not path.is_file():
        return ''
    from . import horaires_ia5689
    from .arret5643 import current_job, job_stop_code   # 5.6.43 (§ 16) : l'arrêt du travail en cours est aussi relu avant chaque effet
    job = current_job()
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        values = {key: json.loads(value) for key, value in db.execute(
            'SELECT key,value FROM settings WHERE key IN (?,?)', ('missions567:pause', PAUSE_KEY))}
        if values.get('missions567:pause', False):
            return 'automatismes_en_pause'
        if str(mid or '') in (values.get(PAUSE_KEY, {}) or {}):
            return 'dossier_en_pause'
        # 5.6.89 : politique fraîche, avant calcul et effet. Hors d'un travail, seule une dérogation couvrant les automatismes laisse passer.
        schedule = horaires_ia5689.code_horaire(db, matter=str(mid or (job or {}).get('matter') or ''), automatic=bool(job.get('automatic')) if job else True)
    except (sqlite3.Error, ValueError, TypeError):
        raise Stop('controle_pause_indisponible') from None
    finally:
        db.close()
    if job and job.get('job_id'):
        code = job_stop_code(job.get('state_dir') or config['state_dir'], job['job_id'])   # 5.6.44 (F09) : pause ≠ annulation
        if code:
            return code   # une annulation déjà posée n'est jamais remplacée par le motif horaire
    return schedule


def is_automatic(args, priority):
    return str((args or {}).get('automatic') or '') == 'yes' or int(priority if priority is not None else 0) >= AUTOMATIC_PRIORITY


def _resets(desk):
    value = desk.settings(RESET_KEY, {}) or {}
    return value if isinstance(value, dict) else {}


def failures(desk, kind, mid):
    """Échecs consécutifs les plus récents d'un type de travail sur un dossier (depuis la dernière réautorisation)."""
    since = _resets(desk).get('%s|%s' % (kind, mid), '')
    rows = desk.db.execute("SELECT status,finished,result FROM jobs WHERE kind=? AND json_extract(args,'$.matter')=? AND status IN ('done','error') "
                           "AND COALESCE(finished,'')>? ORDER BY id DESC LIMIT ?", (kind, mid, since, FAILURES)).fetchall()
    count = 0
    for r in rows:
        if r['status'] != 'error':
            break
        count += 1
    last = ''
    if rows and rows[0]['status'] == 'error':
        try:
            last = (json.loads(rows[0]['result'] or '{}') or {}).get('erreur', '')
        except ValueError:
            last = ''
    return count, last


def check_enqueue(desk, kind, args, priority, origin=None):
    """Refuse la mise en file d'un travail AUTOMATIQUE pendant la pause de l'IA, sur un dossier en pause ou en échec répété.
    5.6.43 : l'origine enregistrée (« automatic ») prime sur la priorité ; une demande de l'avocat est acceptée et attend la reprise."""
    if origin in ('user', 'automatic', 'control'):
        automatic = origin == 'automatic'
    else:
        automatic = is_automatic(args, priority) or kind == 'live_mail430'
    if desk.settings('missions567:pause', False) and automatic:
        raise Stop('automatismes_en_pause')
    mid = str((args or {}).get('matter') or (args or {}).get('matter_id') or '')
    if not automatic:
        return
    if mid:
        if is_paused(desk, mid):
            raise Stop('agent_en_pause_sur_ce_dossier')
        count, _ = failures(desk, kind, mid)
        if count >= FAILURES:
            raise Stop('relances_suspendues_apres_trois_echecs')
    from .arret5643 import SANS_MODELE
    if kind not in SANS_MODELE:   # 5.6.89 : en dernier ; Desk.enqueue enregistre alors le travail une fois, suspendu : un événement de la nuit n'est pas perdu
        from .horaires_ia5689 import code_horaire
        code = code_horaire(desk, matter=mid, automatic=True)
        if code:
            raise Stop(code)


def check_running(desk, row, args):
    """Juste avant l'exécution : un travail automatique d'un dossier mis en pause entre-temps n'est pas exécuté.
    5.6.43 (§ 16) : pendant la pause de l'IA, tout travail qui appelle le modèle attend la reprise (état « suspendu ») ; seuls les travaux
    déterministes (sans IA) s'exécutent."""
    from .arret5643 import DETERMINISTIC, SANS_MODELE, is_automatic_row
    mid = str((args or {}).get('matter') or (args or {}).get('matter_id') or '')
    automatic = is_automatic_row(row, args)
    if desk.settings('missions567:pause', False):
        if automatic:
            raise Stop('automatismes_en_pause')
        if row['kind'] not in DETERMINISTIC:
            raise Stop('ia_en_pause')
    if mid and automatic and is_paused(desk, mid):
        raise Stop('agent_en_pause_sur_ce_dossier')
    if (row['kind'] if 'kind' in row.keys() else '') not in SANS_MODELE:   # 5.6.89 : plage programmée, après la pause manuelle et celle du dossier ; demandes de l'avocat comprises
        from .horaires_ia5689 import code_horaire
        code = code_horaire(desk, matter=mid, automatic=automatic)
        if code:
            raise Stop(code)


def check_suspended(desk, row, args):
    """5.6.94 (F02) : au démarrage, un travail AUTOMATIQUE dont les relances sont suspendues pour ce dossier (trois échecs consécutifs) ne
    tourne pas : il se termine « ignoré » avec son motif (avant : un échec de plus, compté dans « demandes en erreur »). Une demande de
    l'avocat n'est jamais concernée."""
    from .arret5643 import is_automatic_row
    mid = str((args or {}).get('matter') or (args or {}).get('matter_id') or '')
    if not mid or not is_automatic_row(row, args):
        return
    count, _ = failures(desk, row['kind'], mid)
    if count >= FAILURES:
        raise Stop('relances_suspendues_apres_trois_echecs')


def checkpoint(desk):
    """Revalide l'état réel du job avant de poursuivre ou de produire un effet."""
    jid = getattr(desk, 'active_job_id', None)
    if not jid:
        if getattr(desk, 'origin_context', None) == 'automatic':   # 5.6.89 : effet d'un automatisme hors travail, commencé avant le début de la plage
            from .horaires_ia5689 import code_horaire
            code = code_horaire(desk, automatic=True)
            if code:
                raise Stop(code)
        return
    from .arret5643 import stop_code
    row = desk.db.execute('SELECT kind,status,priority,args,stop_reason FROM jobs WHERE id=?', (jid,)).fetchone()
    if not row:
        raise Stop('operation_annulee')
    code = stop_code(row['status'], row['stop_reason'])   # 5.6.44 (F09) : une demande d'arrêt de la pause suspend, elle n'annule pas
    if code:
        raise Stop(code)
    full = desk.db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    check_running(desk, full, json.loads(row['args'] or '{}'))


# ------------------------------------------------------------------- 5.6.108 (lot P) : suspension des envois programmés
def suspension_envois(desk):
    """État de la suspension des envois programmés, relu dans la base (aucun cache) : dict si elle est active, ``None`` sinon.
    Valeur illisible ou de forme inconnue : ``Stop('controle_suspension_indisponible')`` ; l'appelant retient le départ (jamais d'envoi
    parce que le contrôle a échoué)."""
    try:
        value = desk.settings(SUSPENSION_ENVOIS, None)
    except (ValueError, TypeError, sqlite3.Error):
        raise Stop('controle_suspension_indisponible') from None
    if value is None or value is False:
        return None
    if value is True:
        return {'actif': True}
    if isinstance(value, dict) and isinstance(value.get('actif'), bool):
        return value if value['actif'] else None
    raise Stop('controle_suspension_indisponible')


def checkpoint_envois(desk):
    """Contrôle d'un départ programmé, juste avant l'effet (et avant la réservation) : suspension des envois programmés.
    Distinct de ``checkpoint`` : la pause de l'IA n'y entre pas."""
    if suspension_envois(desk):
        raise Stop('envois_suspendus')


def ecrire_suspension_envois(desk, actif, owner='cabinet', origine='manuel', now=None):
    """Écrit le réglage dans la transaction en cours (l'appelant valide avec ses propres changements). Rend la valeur écrite."""
    now = time.time() if now is None else now
    value = {'actif': bool(actif), 'depuis' if actif else 'leve': now, 'par': str(owner or 'cabinet')[:120], 'origine': origine}
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (SUSPENSION_ENVOIS, json.dumps(value)))
    return value


def migrer_suspension_envois(desk, now=None):
    """Une seule fois, au démarrage (``envoi56106.ensure_schema``) : avant la 5.6.108, la pause de l'IA retenait aussi les départs.
    Pause posée par l'avocat au moment de la mise à jour : les envois programmés restent suspendus (même effet) jusqu'à un changement
    explicite. Pause posée par la mise à jour elle-même (repère ``maj5681:pause``) : pas de suspension. Rend ``None`` si la migration est
    déjà faite, sinon le compte rendu (écrit dans la transaction en cours ; l'appelant valide et journalise)."""
    if desk.settings(MIGRATION_ENVOIS, None):
        return None
    try:
        pause_ia = bool(desk.settings(PAUSE_IA, False))
        repere = desk.settings(PAUSE_MISE_A_JOUR, {})
    except (ValueError, TypeError):   # réglage illisible : par prudence, mêmes effets qu'une pause de l'avocat (rien ne part sans geste)
        pause_ia, repere = True, {}
    par_mise_a_jour = isinstance(repere, dict) and bool(repere.get('version'))
    deja = desk.db.execute('SELECT 1 FROM settings WHERE key=?', (SUSPENSION_ENVOIS,)).fetchone() is not None
    suspendre = pause_ia and not par_mise_a_jour and not deja
    if suspendre:
        ecrire_suspension_envois(desk, True, 'migration-5.6.108', 'migration', now)
    rapport = {'le': time.time() if now is None else now, 'pause_ia': pause_ia, 'pause_posee_par_la_mise_a_jour': par_mise_a_jour,
               'envois_suspendus': bool(suspendre or (deja and suspension_actif_brut(desk)))}
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (MIGRATION_ENVOIS, json.dumps(rapport)))
    return rapport


def suspension_actif_brut(desk):
    try:
        return suspension_envois(desk) is not None
    except Stop:
        return True


def set_pause(desk, mid, on, owner='cabinet'):
    from .common import load_matters
    if not any(m['id'] == mid for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    value = paused(desk)
    cancelled = stopping = 0
    if on:
        value[mid] = {'at': desk.now(), 'by': owner}
        desk.setting(PAUSE_KEY, value)
        from .arret5643 import suspend_matter   # 5.6.43 (§ 16) : suspendus (reprenables), plus annulés ; matter et matter_id
        suspended, stopping = suspend_matter(desk, mid)
        resumed = 0
        message = 'IA en pause sur ce dossier : plus aucun travail automatique.'
        if suspended or stopping:
            message += ' %d travail(aux) en file suspendu(s)%s.' % (suspended, (', %d arrêt demandé' % stopping) if stopping else '')
    else:
        value.pop(mid, None)
        desk.setting(PAUSE_KEY, value)
        from .arret5643 import resume
        resumed = resume(desk, mid)
        suspended = stopping = 0
        message = 'L’IA peut de nouveau travailler automatiquement sur ce dossier.' + (
            ' %d travail(aux) suspendu(s) remis en file.' % resumed if resumed else '')
    desk.audit('pause5629_dossier', {'matter': mid, 'pause': bool(on), 'suspendus': suspended, 'arrets': stopping, 'repris': resumed,
                                     'owner': owner})
    return {'paused': bool(on), 'suspended': suspended, 'cancelled': 0, 'stopping': stopping, 'resumed': resumed, 'message': message}


def consignes(desk, mid):
    return str(desk.settings('consignes5629:' + str(mid), '') or '')


def set_consignes(desk, mid, text, owner='cabinet'):
    from .common import load_matters
    if not any(m['id'] == mid for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    text = str(text or '').replace('\r\n', '\n').strip()
    if len(text) > CONSIGNES_MAX:
        raise Stop('consignes_trop_longues_4000_maximum')
    desk.setting('consignes5629:' + mid, text)
    desk.audit('consignes5629_dossier', {'matter': mid, 'longueur': len(text), 'owner': owner})
    return {'message': 'Consignes enregistrées : l’agent les suivra pour chaque travail sur ce dossier.' if text else 'Consignes effacées.'}


def with_consignes(desk, args):
    """Ajoute les consignes permanentes du dossier à l'instruction d'un travail (copie des arguments)."""
    mid = str((args or {}).get('matter') or '')
    text = consignes(desk, mid) if mid else ''
    if not text or 'instruction' not in (args or {}):
        return args
    return {**args, 'instruction': (str(args.get('instruction') or '').rstrip() + '\n\nConsignes permanentes de l’avocat pour ce dossier (prioritaires) :\n' + text).strip()}


def blocked(desk, mid, days=14):
    """Travaux automatiques de ce dossier suspendus après des échecs répétés (pour affichage et réautorisation)."""
    # 5.6.94 (F06) : borne calculée en Python au format des horodatages enregistrés (ISO 8601, « T », UTC). Avant : la borne SQLite
    # « AAAA-MM-JJ HH:MM:SS », comparée en texte : tout le jour du bord entrait dans la fenêtre (« T » > « espace »).
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = desk.db.execute("SELECT DISTINCT kind FROM jobs WHERE json_extract(args,'$.matter')=? AND status='error' AND finished>=?",
                           (mid, since)).fetchall()
    out = []
    for r in rows:
        count, last = failures(desk, r['kind'], mid)
        if count >= FAILURES:
            out.append({'kind': r['kind'], 'failures': count, 'error': last})
    return out


def reset(desk, kind, mid, owner='cabinet'):
    value = _resets(desk)
    value['%s|%s' % (kind, mid)] = desk.now()
    desk.setting(RESET_KEY, value)
    desk.audit('pause5629_reautorisation', {'matter': mid, 'kind': kind, 'owner': owner})
    return {'message': 'Relances automatiques réautorisées pour ce travail.'}
