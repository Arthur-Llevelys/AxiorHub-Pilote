"""5.6.29 : maîtrise du travail automatique de l'agent, dossier par dossier.

- **Pause de l'IA sur un dossier** : plus aucun travail automatique (diligences des courriels entrants, orchestrateur, surveillance
  des fichiers, avis de procédure, mémoire du dossier, projets et créations de fichiers automatiques, brouillons de réponse aux
  courriels entrants) n'est mis en file ni exécuté pour ce dossier ; les travaux automatiques déjà en file sont annulés, celui en
  cours reçoit une demande d'arrêt. Les demandes de l'avocat (priorité « avocat ») restent possibles.
- **Consignes permanentes** du dossier : transmises à l'agent avec chaque travail sur ce dossier (instruction des projets, réponses
  aux courriels, fiche de travail).
- **Coupe-circuit** : un travail automatique qui a échoué trois fois de suite pour le même dossier n'est plus relancé
  automatiquement ; il reste relançable à la main, et « Réautoriser » efface le compteur.
"""
import json
from pathlib import Path
import sqlite3

from .common import Stop

PAUSE_KEY = 'pause5629:matters'
RESET_KEY = 'pause5629:reset'
FAILURES = 3
CONSIGNES_MAX = 4000
AUTOMATIC_PRIORITY = 30


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
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        values = {key: json.loads(value) for key, value in db.execute(
            'SELECT key,value FROM settings WHERE key IN (?,?)', ('missions567:pause', PAUSE_KEY))}
        if values.get('missions567:pause', False):
            return 'automatismes_en_pause'
        if str(mid or '') in (values.get(PAUSE_KEY, {}) or {}):
            return 'dossier_en_pause'
    except (sqlite3.Error, ValueError, TypeError):
        raise Stop('controle_pause_indisponible') from None
    finally:
        db.close()
    from .arret5643 import current_job, job_stop_code   # 5.6.43 (§ 16) : l'arrêt du travail en cours est aussi relu avant chaque effet
    job = current_job()
    if job and job.get('job_id'):
        return job_stop_code(job.get('state_dir') or config['state_dir'], job['job_id'])   # 5.6.44 (F09) : pause ≠ annulation
    return ''


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
    if not mid or not automatic:
        return
    if is_paused(desk, mid):
        raise Stop('agent_en_pause_sur_ce_dossier')
    count, _ = failures(desk, kind, mid)
    if count >= FAILURES:
        raise Stop('relances_suspendues_apres_trois_echecs')


def check_running(desk, row, args):
    """Juste avant l'exécution : un travail automatique d'un dossier mis en pause entre-temps n'est pas exécuté.
    5.6.43 (§ 16) : pendant la pause de l'IA, tout travail qui appelle le modèle attend la reprise (état « suspendu ») ; seuls les travaux
    déterministes (sans IA) s'exécutent."""
    from .arret5643 import DETERMINISTIC, is_automatic_row
    mid = str((args or {}).get('matter') or (args or {}).get('matter_id') or '')
    automatic = is_automatic_row(row, args)
    if desk.settings('missions567:pause', False):
        if automatic:
            raise Stop('automatismes_en_pause')
        if row['kind'] not in DETERMINISTIC:
            raise Stop('ia_en_pause')
    if mid and automatic and is_paused(desk, mid):
        raise Stop('agent_en_pause_sur_ce_dossier')


def checkpoint(desk):
    """Revalide l'état réel du job avant de poursuivre ou de produire un effet."""
    jid = getattr(desk, 'active_job_id', None)
    if not jid:
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
    rows = desk.db.execute("SELECT DISTINCT kind FROM jobs WHERE json_extract(args,'$.matter')=? AND status='error' AND finished>=datetime('now',?)",
                           (mid, '-%d days' % days)).fetchall()
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
