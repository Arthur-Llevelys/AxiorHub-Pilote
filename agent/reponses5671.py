"""5.6.71 : réponses « sans dossier » automatiques et réponses préparées en attente de décision (retour du cabinet du 22 octobre 2026).

Point 3 — réponses « sans dossier ». Diagnostic : les courriels qui appellent une réponse mais dont le dossier n'est pas confirmé
(« correspondant ou dossier à confirmer », expéditeur inconnu — un prospect par exemple — ou rattachement incertain bloqué par
l'analyse) n'étaient jamais rédigés : la préparation automatique ne parcourt que les courriels rattachés à un dossier. Désormais, au
plus 5 par passage (toutes les 10 minutes), une réponse est préparée à partir du seul courriel et de ses pièces jointes, comme
« Traiter avec l'IA » : jamais un dossier deviné (le dossier supposé par l'agent est ignoré), contrôle indépendant, dépôt « à
compléter » dans Brouillons seulement si le contrôle de sûreté passe, jamais envoyée. Une seule tentative automatique par courriel.
Réglage : Paramètres › Automatismes (``reponses5671:sans_dossier``), et les réglages existants (brouillons automatiques, mode
observation, autonomie « proposer seulement ») s'appliquent.

Point 4 — réponses en attente de décision. Une réponse préparée que le contrôle n'autorise pas à déposer restait invisible (seulement
sur la fiche technique du courriel, sans aucun bouton). Elle devient une carte de « À relire » (rubrique Courriels) avec le motif, et
la page du courriel propose de relire et modifier le texte puis de le déposer dans Brouillons, de le déposer tel quel « à compléter »
(si le contrôle de sûreté l'autorise) ou de l'écarter. Rien n'est jamais envoyé.
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import json
import re

from .common import Stop

SETTING = 'reponses5671:sans_dossier'
REASONS = ('correspondant_ou_dossier_a_confirmer', 'expediteur_inconnu_sans_dossier')
ASSOCIATION = 'Association de dossier à confirmer.'
WINDOW_DAYS = 3
PER_PASS = 5
INTERVAL_MINUTES = 10
MAX_BODY = 20000
INSTRUCTION = ('Réponse préparée sans dossier confirmé : répondre à partir du seul courriel et de ses pièces jointes, sans rien affirmer sur '
               'un dossier ni prendre d’engagement ; indiquer « [À COMPLÉTER : …] » là où la position de l’avocat est nécessaire.')


def enabled(desk):
    if not desk.settings(SETTING, True):
        return False
    if desk.c.get('mode') == 'observe':
        return False
    if not desk.settings('automation:automatic_mail_drafts_enabled', desk.c.get('orchestrator', {}).get('automatic_mail_drafts_enabled', True)):
        return False
    try:
        from . import autonomy480
        if autonomy480.level(desk, 'courriel_reponse') == 'propose':
            return False
    except Exception:   # noqa: BLE001 — réglage d'autonomie absent : autorisé comme les autres brouillons automatiques
        pass
    return True


def _attempted(desk, key):
    if desk.db.execute('SELECT 1 FROM manual_drafts WHERE mail_key=?', (key,)).fetchone():
        return True
    marker = '%"key": "' + key + '"%'
    return bool(desk.db.execute("SELECT 1 FROM jobs WHERE kind='prepare_reply' AND args LIKE ?", (marker,)).fetchone())


def candidates(desk, limit=PER_PASS, now=None):
    """Courriels récents qui appellent une réponse et dont le dossier n'est pas confirmé, pas encore tentés."""
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=WINDOW_DAYS)).isoformat()
    keys = []
    from .state import State
    st = State(desk.c['state_dir'])
    for row in st.db.execute('SELECT key FROM messages WHERE status=? AND reason IN (%s) AND updated>=? ORDER BY updated DESC LIMIT 200'
                             % ','.join('?' * len(REASONS)), ('review',) + REASONS + (since,)):
        keys.append(str(row[0]))
    try:
        from .orchestrator import ensure_schema
        ensure_schema(desk)
        for row in desk.db.execute("SELECT mail_key FROM mail_orchestrations_v260 WHERE status='blocked' AND updated>=? "
                                   "AND json_extract(classification,'$.needs_reply') AND json_extract(differential,'$.blocking_reasons[0]')=? "
                                   "ORDER BY updated DESC LIMIT 100", (since, ASSOCIATION)):
            keys.append(str(row[0]))
    except Exception:   # noqa: BLE001 — orchestration absente : seulement les courriels « à confirmer »
        pass
    from .desk import report_for
    from .mailbox import own_addresses
    own = {a.lower() for a in own_addresses(desk.c.get('mail', {}))}
    out = []
    for key in dict.fromkeys(keys):
        if not re.fullmatch(r'[a-f0-9]{64}', key) or _attempted(desk, key):
            continue
        state = desk.db.execute('SELECT state FROM work_items WHERE mail_key=?', (key,)).fetchone()
        if state and state['state'] == 'draft_ready':
            continue
        try:
            report = report_for(desk.c, key)
        except Stop:
            continue
        if report.get('manual_request') or str(report.get('sender') or '').strip().lower() in own:
            continue
        out.append(key)
        if len(out) >= limit:
            break
    return out


def sweep(desk, now=None):
    if not enabled(desk):
        return {'mis_en_preparation': 0, 'desactive': True}
    queued = []
    for key in candidates(desk, PER_PASS, now):
        try:
            queued.append(desk.enqueue('prepare_reply', {'key': key, 'matter': '', 'sans_dossier': 'oui', 'ignorer_dossier_suppose': 'oui',
                                                         'pieces_jointes': 'oui', 'automatic': 'yes', 'origine': 'sans_dossier5671',
                                                         'instruction': INSTRUCTION}, priority=25))
        except Stop:
            break
    if queued:
        desk.audit('reponses5671_sans_dossier', {'travaux': queued})
    return {'mis_en_preparation': len(queued), 'travaux': queued}


def schedule(desk, now_ts):
    """Appelé par la boucle des automatismes : un passage toutes les 10 minutes au plus."""
    last = float(desk.settings('auto:reponses5671', 0) or 0)
    if now_ts - last < INTERVAL_MINUTES * 60:
        return None
    desk.setting('auto:reponses5671', now_ts)
    return sweep(desk)


# ------------------------------------------------------------------------------------------- point 4 : réponses en attente
def _hidden():
    from .membres5650 import viewer_hidden
    return viewer_hidden() or frozenset()


def pending(desk):
    """Réponses préparées que le contrôle n'a pas autorisé à déposer : [(clé, données, mise à jour)]."""
    hidden = _hidden()
    out = []
    for row in desk.db.execute('SELECT mail_key,data,updated FROM manual_drafts ORDER BY updated DESC LIMIT 100'):
        try:
            data = json.loads(row['data'])
        except (TypeError, ValueError):
            continue
        value = data.get('result') or {}
        if not value.get('body') or not value.get('requires_decision'):
            continue
        if str(data.get('matter') or '') in hidden:
            continue
        out.append((row['mail_key'], data, row['updated']))
    return out


def _why(value):
    limits = [str(x) for x in (value.get('limits') or []) if str(x).strip()]
    return ' · '.join(limits)[:400] or 'Le contrôle demande votre décision avant tout dépôt.'


def queue_items(desk, prefix='', actor='cabinet', role='administrateur'):
    from .desk import report_for
    from urllib.parse import quote
    out = []
    labels = {}
    try:
        from .common import load_matters, matter_display
        labels = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}
    except Exception:   # noqa: BLE001
        pass
    for key, data, updated in pending(desk):
        try:
            report = report_for(desk.c, key)
        except Stop:
            report = {}
        subject = str(report.get('subject') or 'Courriel')[:160]
        mid = str(data.get('matter') or '')
        out.append({'class': 'courriel', 'object': 'reponse5671:' + key, 'revision': str(updated), 'when': str(updated), 'matter': mid,
                    'matter_name': labels.get(mid) or ('Sans dossier' if not mid else mid),
                    'title': 'Réponse préparée, à décider : ' + subject,
                    'consequence': 'Non déposée dans Brouillons : ' + _why(data.get('result') or {}),
                    'href': prefix + '/mail?key=' + quote(key, safe=''),
                    'choices': ('ouvrir', {'api': 'm5671/reponse/ecarter', 'label': 'Écarter', 'ghost': True, 'fields': {'key': key},
                                           'confirm': 'Écarter cette réponse préparée ? Le courriel reste dans la messagerie.'})})
    return out


def _store(desk, key, data):
    desk.db.execute('UPDATE manual_drafts SET data=?,updated=? WHERE mail_key=?', (json.dumps(data, ensure_ascii=False), desk.now(), key))
    desk.db.commit()


def _load(desk, key):
    if not re.fullmatch(r'[a-f0-9]{64}', str(key or '')):
        raise Stop('courriel_introuvable')
    row = desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (key,)).fetchone()
    if not row:
        raise Stop('projet_prudent_absent')
    data = json.loads(row[0])
    if str(data.get('matter') or '') in _hidden():
        raise Stop('dossier_reserve')
    return data


def deposer(desk, owner, key, body=None, a_completer=False):
    """Dépôt dans Brouillons d'une réponse en attente : texte relu (et éventuellement modifié) par l'avocat, ou tel quel « à compléter »
    si le contrôle de sûreté l'autorise. Jamais envoyé."""
    from .intelligence import deposit_draft
    data = _load(desk, key)
    if body is not None:
        text = str(body).replace('\r\n', '\n').strip()
        if not text:
            raise Stop('texte_vide')
        if len(text) > MAX_BODY:
            raise Stop('texte_trop_long')
        result = dict(data.get('result') or {})
        result.update({'body': text, 'requires_decision': False,
                       'limits': list(result.get('limits') or []) + ['Texte relu et validé par l’avocat avant dépôt.']})
        data['result'] = result
        data['decided_by'] = str(owner)[:120]
        original = desk.db.execute('SELECT data,updated FROM manual_drafts WHERE mail_key=?', (key,)).fetchone()
        _store(desk, key, data)
        try:
            out = deposit_draft(desk, {'key': key, 'confirm': 'yes'})
        except Exception:
            # dépôt refusé (courriel ou dossier modifié, messagerie indisponible…) : la réponse reste « en attente de décision »
            desk.db.rollback()
            desk.db.execute('UPDATE manual_drafts SET data=?,updated=? WHERE mail_key=?', (original[0], original[1], key))
            desk.db.commit()
            raise
        how = 'relu'
    elif a_completer:
        out = deposit_draft(desk, {'key': key, 'confirm': 'yes', 'a_completer': 'oui'})
        how = 'a_completer'
    else:
        raise Stop('decision_requise')
    desk.audit('reponses5671_depot', {'key': key[:16], 'mode': how, 'par': str(owner)[:80]})
    return {'ok': True, **out, 'message': 'Réponse déposée dans Brouillons et relue dans la messagerie ; rien n’est envoyé.'}


def ecarter(desk, owner, key):
    _load(desk, key)
    desk.db.execute('DELETE FROM manual_drafts WHERE mail_key=?', (key,))
    desk.db.commit()
    desk.audit('reponses5671_ecartee', {'key': key[:16], 'par': str(owner)[:80]})
    return {'ok': True, 'message': 'Réponse préparée écartée. Le courriel reste dans la messagerie.'}


def editor_html(key, value, verification=None):
    """Page du courriel : relire et modifier la réponse en attente, puis la déposer ; tel quel « à compléter » si le contrôle le permet."""
    from .prudent5667 import safe_to_complete
    safe = safe_to_complete(verification)
    rows = max(8, min(30, str(value.get('body') or '').count('\n') + 4))
    out = ('<section class="r5671" aria-labelledby="r5671-t"><h2 id="r5671-t">Réponse préparée — votre décision</h2>'
           '<p class="notice">%s</p>'
           '<form class="r5671-form" data-c5671="m5671/reponse/deposer" data-reload>'
           '<input type="hidden" name="key" value="%s"><label for="r5671-body">Texte de la réponse (modifiable)</label>'
           '<textarea id="r5671-body" name="corps" rows="%d">%s</textarea>'
           '<div class="r5671-actions"><button class="ax-btn" type="submit">Déposer ce texte dans Brouillons (relu par moi)</button></div></form>') % (
        e(_why(value)), e(key, quote=True), rows, e(str(value.get('body') or '')))
    if safe:
        out += ('<form class="r5671-form r5671-inline" data-c5671="m5671/reponse/deposer" data-reload><input type="hidden" name="key" value="%s">'
                '<input type="hidden" name="a_completer" value="oui"><button class="ax-btn ghost" type="submit">Déposer tel quel « à compléter »'
                '</button></form>') % e(key, quote=True)
    out += ('<form class="r5671-form r5671-inline" data-c5671="m5671/reponse/ecarter" data-reload data-confirm="Écarter cette réponse préparée ?">'
            '<input type="hidden" name="key" value="%s"><button class="ax-btn ghost" type="submit">Écarter</button></form>'
            '<p class="ax-muted">Rien n’est envoyé : le brouillon déposé s’ouvre dans votre messagerie pour l’envoi.</p></section>') % e(key, quote=True)
    return out
