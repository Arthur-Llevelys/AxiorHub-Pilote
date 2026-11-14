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
# 5.6.95 (D03) : réponse rédigée en entier ; une position de l'avocat se présente en 2 ou 3 variantes rédigées, un marqueur ne vise qu'un fait absent.
INSTRUCTION = ('Réponse préparée sans dossier confirmé : répondre en entier à partir du seul courriel et de ses pièces jointes, sans rien affirmer '
               'sur un dossier ni prendre d’engagement ; là où la position de l’avocat est nécessaire, proposer 2 ou 3 variantes rédigées ; '
               '« [À COMPLÉTER : …] » seulement pour un fait absent, au plus 2.')


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


MAX_ATTEMPTS = 3


def _attempted(desk, key):
    """Déjà tenté ? 5.6.72 : un échec dû à l'IA injoignable, en pause ou surchargée ne compte pas (3 essais au plus)."""
    if desk.db.execute('SELECT 1 FROM manual_drafts WHERE mail_key=?', (key,)).fetchone():
        return True
    marker = '%"key": "' + key + '"%'
    rows = desk.db.execute("SELECT status,json_extract(result,'$.erreur') FROM jobs WHERE kind='prepare_reply' AND args LIKE ?", (marker,)).fetchall()
    if not rows:
        return False
    from .ia5672 import TRANSIENT
    if all(r[0] == 'error' and str(r[1] or '').split(':')[0] in TRANSIENT for r in rows) and len(rows) < MAX_ATTEMPTS:
        return False
    return True


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
    from .ia5672 import local_down
    if local_down(desk, now)['down']:   # 5.6.72 : pas de tentative pendant une coupure du modèle local
        return {'mis_en_preparation': 0, 'ia_injoignable': True}
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
    try:   # 5.6.100 (lot B) : rattrapage borné de la file commune « À relire » à chaque passage (écritures hors de toute page)
        from .relecture56100 import rattraper
        rattraper(desk, 300)
    except Exception:   # noqa: BLE001 — une projection en retard ne bloque jamais les automatismes
        pass
    last = float(desk.settings('auto:reponses5671', 0) or 0)
    if now_ts - last < INTERVAL_MINUTES * 60:
        return None
    desk.setting('auto:reponses5671', now_ts)
    return sweep(desk)


# ------------------------------------------------------------------------------------------- point 4 : réponses en attente
def _hidden():
    from .membres5650 import viewer_hidden
    return viewer_hidden() or frozenset()


def _source_visible(desk, key, hidden):
    # 5.6.100 (R04) : rattachements du message ET dossier du projet (garde commune ``acces_courriel56100`` ; panne : 503, jamais visible)
    from .acces5697 import visible
    from .acces_courriel56100 import dossiers
    return not hidden or visible(dossiers(desk, [key]).get(key, ()), hidden)


def pending(desk):
    """Réponses locales en attente, filtrées sur le rattachement actuel avant la limite."""
    from .acces5697 import masques_requete, visible
    from .messages5697 import dossiers_par_cle
    hidden = masques_requete(desk)
    out, after = [], None
    sql = """SELECT mail_key,data,COALESCE(updated,'') AS at FROM manual_drafts
             WHERE CASE WHEN json_valid(data) THEN json_extract(data,'$.result.requires_decision')=1 ELSE 0 END"""
    while len(out) < 200:
        pred = " AND (COALESCE(updated,''),mail_key)<(?,?)" if after is not None else ''
        rows = desk.db.execute(sql+pred+' ORDER BY at DESC,mail_key DESC LIMIT 128', after or ()).fetchall()
        if not rows:
            break
        assoc = dossiers_par_cle(desk, [r['mail_key'] for r in rows]) if hidden else {}
        for row in rows:
            data = json.loads(row['data'])
            if not isinstance(data, dict) or not isinstance(data.get('result'), dict):
                continue
            value = data['result']
            key = row['mail_key']
            if not value.get('body') or str(data.get('matter') or '') in hidden or not visible(assoc.get(key, ()), hidden):
                continue
            out.append((key, data, row['at']))
            if len(out) == 200:
                break
        after = (rows[-1]['at'], rows[-1]['mail_key'])
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
    try:   # 5.6.89 (UX14) : brouillon déjà déposé pour cette réponse : sa carte de relecture porte la décision, celle-ci ferait double emploi
        from .livrables5689 import reponse_deposee, synchroniser
        synchroniser(desk)
        deposited = reponse_deposee(desk)
    except Exception:   # noqa: BLE001 — registre indisponible : on préfère deux cartes à une réponse perdue
        deposited = set()
    for key, data, updated in pending(desk):
        if key in deposited:
            continue
        try:
            report = report_for(desk.c, key)
        except Stop:
            report = {}
        subject = str(report.get('subject') or 'Courriel')[:160]
        mid = str(data.get('matter') or '')
        from .relecture56100 import id_proposition, revision_proposition   # 5.6.100 (lot B) : identifiant stable du lecteur commun
        out.append({'class': 'courriel', 'object': 'reponse5671:' + key, 'revision': str(updated), 'when': str(updated), 'matter': mid,
                    'relecture': id_proposition(key, data),
                    'matter_name': labels.get(mid) or ('Sans dossier' if not mid else mid),
                    'title': 'Réponse préparée, à décider : ' + subject,
                    'consequence': 'Non déposée dans Brouillons : ' + _why(data.get('result') or {}),
                    'href': prefix + '/mail?key=' + quote(key, safe=''),
                    'choices': ('ouvrir', {'api': 'm5671/reponse/ecarter', 'label': 'Écarter', 'ghost': True,
                                           'fields': {'key': key, 'revision': revision_proposition(data)},
                                           'confirm': 'Écarter cette réponse préparée ? Le courriel reste dans la messagerie.'})})
    return out


def section_html(desk, prefix):
    """5.6.86 : page Courriels. Une réponse non déposée n'est pas dans Brouillons : elle doit se trouver à l'endroit où l'avocat la cherche, avec ou sans dossier."""
    from .cockpit530 import _local
    rows = ''
    cards = queue_items(desk, prefix)
    for it in cards:
        rows += '<li><a href="%s"><strong>%s</strong><span>%s · %s</span></a></li>' % (
            e(it['href'], quote=True), e(it['title'].removeprefix('Réponse préparée, à décider : ')), e(it['matter_name']), e(_local(desk, str(it['when']), '%d/%m %H:%M')))
    if not rows:
        return ''
    return ('<section class="r86-pending" aria-labelledby="r86-t"><h2 id="r86-t">Réponses à décider <span class="q51-n">%d</span></h2><p class="ax-muted">Préparées par l’agent, pas encore déposées dans '
            'Brouillons. Ouvrez-en une pour la relire, la modifier et la déposer.</p><ul>%s</ul></section>') % (len(cards), rows)


PRESENTATION = 'reponses5687:presentation'
TYPES = {'ollama': 'Ollama (local)', 'mistral': 'Mistral', 'anthropic': 'Anthropic', 'openrouter': 'OpenRouter', 'openai': 'OpenAI', 'chatgpt': 'ChatGPT'}


def first_contact(desk):
    """5.6.87 : ce que le modèle peut dire du cabinet à un nouveau contact : le texte de Paramètres, et rien d'autre."""
    text = str(desk.settings(PRESENTATION, '') or '').strip()
    return {'cabinet': text or ('Présentation du cabinet non renseignée : ne rien affirmer sur la compétence, les modalités ou les honoraires ; '
                                'proposer de les préciser lors d’un premier échange.')}   # 5.6.95 : pas de marqueur pour une présentation absente


def connections(desk):
    """Connexions à proposer pour une relance : actives, et autorisées à recevoir des données si elles sont externes."""
    from .ai_gateway import provider_registry
    rows = []
    for pid, p in sorted(provider_registry(desk.c).items()):
        kind = p.get('type', 'ollama')
        if p.get('enabled', True) and (kind == 'ollama' or p.get('external_data_allowed')) and p.get('model'):
            rows.append((pid, '%s — %s' % (TYPES.get(kind, kind), p['model'])))
    return rows


def relancer(desk, owner, key, provider, instruction=''):
    """Nouvelle rédaction du même courriel avec la connexion choisie. Chaque demande garde son propre brouillon ; rien n'est déposé sans relecture, rien n'est envoyé."""
    from .desk import report_for
    from .state import State
    if not re.fullmatch(r'[a-f0-9]{64}', str(key or '')):
        raise Stop('courriel_introuvable')
    from .acces5697 import masques_requete
    if not _source_visible(desk, key, masques_requete(desk)):
        raise Stop('introuvable')
    if str(provider) not in dict(connections(desk)):
        raise Stop('fournisseur_ia_inactif')
    report = report_for(desk.c, key)
    mid = str(report.get('matter') or '')
    if mid in _hidden():
        raise Stop('introuvable')   # 5.6.100 (R04) : même réponse qu'un courriel absent
    instruction = re.sub(r'[ \t]+', ' ', str(instruction or '')).strip()[:4000]
    State(desk.c['state_dir']).report(key, {**report, 'manual_request': {'by': str(owner), 'at': desk.now(), 'instruction': instruction[:500], 'attachments': True}})
    job = desk.enqueue('prepare_reply', {'key': key, 'matter': mid, 'instruction': instruction, 'pieces_jointes': 'oui', 'sans_dossier': 'oui' if not mid else '', 'fournisseur': str(provider), 'origine': 'relance5687'}, priority=0)
    desk.audit('reponses5687_relance', {'key': key[:16], 'fournisseur': str(provider), 'job': job, 'par': str(owner)[:80]})
    return {'job_id': job, 'message': 'Relance demandée (travail n° %d). La nouvelle réponse arrivera dans Courriels, rubrique « Réponses à décider ».' % job}


def mail_of_draft(desk, draft_key):
    """Courriel d'origine d'un brouillon déposé par l'agent ; vide pour un brouillon plus ancien que la 5.6.87."""
    try:
        row = desk.db.execute('SELECT mail_key FROM reply_drafts5687 WHERE draft_key=?', (str(draft_key or ''),)).fetchone()
    except Exception:   # noqa: BLE001 — table créée au premier dépôt
        return ''
    return row[0] if row else ''


def retry_html(desk, key):
    """Page du courriel : relancer la rédaction avec une autre IA, en précisant au besoin ce qui manquait."""
    rows = connections(desk)
    if not rows:
        return ''
    return ('<section class="r5671" id="relance5687" aria-labelledby="r5687-t"><h2 id="r5687-t">Relancer avec une autre IA</h2><form class="r5671-form" data-c5671="m5671/reponse/relancer"><input type="hidden" name="key" value="%s">'
            '<label>Intelligence artificielle<select name="fournisseur">%s</select></label><label>Ce qu’il faut améliorer (facultatif)<textarea name="instruction" rows="3"></textarea></label>'
            '<div class="r5671-actions"><button class="ax-btn ghost" type="submit">Relancer la rédaction</button></div><p role="status" class="m5-status"></p></form></section>') % (
        e(key, quote=True), ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in rows))


def _store(desk, key, data, projeter=True):
    desk.db.execute('UPDATE manual_drafts SET data=?,updated=? WHERE mail_key=?', (json.dumps(data, ensure_ascii=False), desk.now(), key))
    desk.db.commit()
    if projeter:   # 5.6.100 (lot B) : la file commune « À relire » suit le producteur
        from .relecture56100 import projeter_apres_ecriture
        projeter_apres_ecriture(desk, key)


def _verifier_revision(data, revision):
    """5.6.100 (R09) : anciennes actions (déposer, écarter, garder) soumises au même contrôle de révision que le lecteur commun : la
    version affichée doit être la version actuelle du projet, sinon 409 sans aucun effet."""
    if revision is None:
        return
    from .relecture56100 import revision_proposition
    if str(revision) != revision_proposition(data):
        raise Stop('version_perimee')


def _load(desk, key):
    if not re.fullmatch(r'[a-f0-9]{64}', str(key or '')):
        raise Stop('courriel_introuvable')
    from .acces5697 import masques_requete
    hidden = masques_requete(desk)
    if not _source_visible(desk, key, hidden):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (key,)).fetchone()
    if not row:
        raise Stop('introuvable' if hidden else 'projet_prudent_absent')   # 5.6.100 : compte cloisonné : absent = interdit (même 404)
    data = json.loads(row[0])
    if str(data.get('matter') or '') in _hidden():
        raise Stop('introuvable')   # 5.6.100 (R04) : même réponse qu'un projet absent
    return data


def deposer(desk, owner, key, body=None, a_completer=False, revision=None, projeter=True):
    """Dépôt dans Brouillons d'une réponse en attente : texte relu (et éventuellement modifié) par l'avocat, ou tel quel « à compléter »
    si le contrôle de sûreté l'autorise. Jamais envoyé. 5.6.100 : ``revision`` (version affichée) contrôlée avant tout effet."""
    from .intelligence import deposit_draft
    data = _load(desk, key)
    _verifier_revision(data, revision)
    try:
        return _deposer(desk, owner, key, data, body, a_completer, deposit_draft)
    finally:
        if projeter:
            from .relecture56100 import projeter_apres_ecriture
            projeter_apres_ecriture(desk, key)


def _deposer(desk, owner, key, data, body, a_completer, deposit_draft):
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
        _store(desk, key, data, projeter=False)
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


def ecarter(desk, owner, key, revision=None):
    """Écarter une réponse préparée. 5.6.100 (R09) : transition conditionnelle dans une transaction (le projet écarté est celui de la
    version affichée, jamais une version plus récente), tracée dans l'historique de la file commune ; le courriel reçu n'est pas touché."""
    data = _load(desk, key)
    _verifier_revision(data, revision)
    from .relecture56100 import ecarter_cle
    ecarter_cle(desk, owner, key, data)
    desk.audit('reponses5671_ecartee', {'key': key[:16], 'par': str(owner)[:80]})
    return {'ok': True, 'message': 'Réponse préparée écartée. Le courriel reste dans la messagerie.'}


def garder_variante(desk, owner, key, rang, lettre, version_vue, revision=None):
    """5.6.95 (D05) : l'avocat garde une variante d'un choix : les autres variantes et les lignes du bloc disparaissent du texte en attente.
    Version périmée (texte modifié depuis l'affichage) : refus 409 ; même demande rejouée : sans second effet. Rien n'est déposé ni envoyé.
    5.6.100 (R09) : la révision du projet, si le formulaire la porte, est contrôlée aussi (destinataires, dossier, décision)."""
    from . import redaction5695 as rd
    data = _load(desk, key)
    corps_actuel = rd.version(str((data.get('result') or {}).get('body') or ''))
    if revision is not None and not any(h.get('avant') == str(version_vue) and h.get('apres') == corps_actuel for h in (data.get('choix5695') or [])):
        _verifier_revision(data, revision)   # une même demande rejouée (déjà appliquée) garde sa réponse idempotente
    result = dict(data.get('result') or {})
    body = str(result.get('body') or '')
    current = rd.version(body)
    try:
        rang = int(rang)
    except (TypeError, ValueError):
        raise Stop('choix_introuvable') from None
    lettre = str(lettre or '').strip().upper()[:1]
    history = list(data.get('choix5695') or [])
    done = next((h for h in history if (h.get('avant'), h.get('choix'), h.get('variante')) == (str(version_vue), rang, lettre)), None)
    if done and done.get('apres') == current:                 # clé d'idempotence : (version vue, choix, variante)
        return {'ok': True, 'deja_fait': True, 'version': current, 'choix_restants': rd.compter(body)['choix'],
                'message': 'Cette variante était déjà gardée.'}
    if str(version_vue or '') != current:
        raise Stop('version_perimee')
    text = rd.garder(body, rang, lettre)
    result['body'] = text
    data['result'] = result
    history.append({'avant': current, 'apres': rd.version(text), 'choix': rang, 'variante': lettre, 'par': str(owner)[:120], 'le': desk.now()})
    data['choix5695'] = history[-40:]
    _store(desk, key, data)
    left = rd.compter(text)['choix']
    desk.audit('redaction5695_variante_gardee', {'key': key[:16], 'choix': rang, 'variante': lettre, 'restants': left, 'par': str(owner)[:80]})
    done = 'Proposition retirée.' if lettre == rd.RETIRER else 'Variante gardée.'
    return {'ok': True, 'version': rd.version(text), 'choix_restants': left,
            'message': ('%s Il reste %d choix à faire.' % (done, left)) if left else '%s Plus aucun choix à faire : relisez puis déposez.' % done}


def variantes_html(key, value):
    """5.6.95 (D05) : chaque choix restant, ses variantes et un bouton « Garder cette variante » (formulaires v5671.js, sans script en ligne)."""
    from . import redaction5695 as rd
    from .i18n5631 import th
    body = str(value.get('body') or '')
    found = rd.blocs(body)
    faits = [f for f in rd.normaliser(value).get('a_completer') or [] if f.get('quoi')]
    if not found and not faits:
        return ''
    version = rd.version(body)
    out = '<section class="rd95" aria-labelledby="rd95-t"><h3 id="rd95-t">%s</h3>' % th('redaction5695.titre')
    if found:
        out += '<p class="ax-muted">%s</p>' % th('redaction5695.intro', n=len(found))
    form = ('<form class="r5671-form r5671-inline" data-c5671="m5671/reponse/garder" data-reload>'
            '<input type="hidden" name="key" value="%s"><input type="hidden" name="choix" value="%d"><input type="hidden" name="variante" value="%s">'
            '<input type="hidden" name="version" value="%s"><button class="ax-btn%s" type="submit" aria-label="%s">%s</button></form>')
    for n, block in enumerate(found, 1):
        if block.get('proposition'):   # v2 : une seule option : l'avocat la garde ou la retire, rien n'est versé au corps sans lui
            option = block['options'][0]
            out += ('<fieldset class="rd95-choix"><legend>%s</legend><div class="rd95-variante"><p class="rd95-texte">%s</p>' % (
                th('redaction5695.proposition', n=n, point=block['point'] or '—'), e(option['texte'])))
            if block['complet']:
                if option['texte'].strip():
                    out += form % (e(key, quote=True), n, 'A', e(version, quote=True), '', th('redaction5695.garder_proposition_aria', n=n),
                                   th('redaction5695.garder_proposition'))
                out += form % (e(key, quote=True), n, rd.RETIRER, e(version, quote=True), ' ghost', th('redaction5695.retirer_aria', n=n),
                               th('redaction5695.retirer_proposition'))
            else:
                out += '<p class="notice">%s</p>' % th('redaction5695.incomplet')
            out += '</div></fieldset>'
            continue
        out += '<fieldset class="rd95-choix"><legend>%s</legend>' % th('redaction5695.choix', n=n, point=block['point'] or '—')
        if not block['complet']:
            out += '<p class="notice">%s</p>' % th('redaction5695.incomplet')
        for option in block['options']:
            out += ('<div class="rd95-variante"><p class="rd95-libelle"><strong>%s</strong></p><p class="rd95-texte">%s</p>' % (
                th('redaction5695.variante', lettre=option['lettre'], libelle=option['libelle'] or '—'), e(option['texte'])))
            if block['complet'] and option['texte'].strip():
                out += ('<form class="r5671-form r5671-inline" data-c5671="m5671/reponse/garder" data-reload>'
                        '<input type="hidden" name="key" value="%s"><input type="hidden" name="choix" value="%d"><input type="hidden" name="variante" value="%s">'
                        '<input type="hidden" name="version" value="%s"><button class="ax-btn" type="submit" aria-label="%s">%s</button></form>') % (
                    e(key, quote=True), n, e(option['lettre'], quote=True), e(version, quote=True),
                    th('redaction5695.garder_aria', lettre=option['lettre'], n=n), th('redaction5695.garder'))
            out += '</div>'
        out += '</fieldset>'
    if faits:
        out += '<h4>%s</h4><ul class="rd95-faits">%s</ul>' % (th('redaction5695.faits'), ''.join(
            '<li><strong>%s</strong>%s</li>' % (e(f['quoi']), (' — ' + e(f['pourquoi'])) if f.get('pourquoi') else '') for f in faits[:rd.MAX_MARQUEURS + 4]))
    out += '<p class="ax-muted">%s</p></section>' % th('redaction5695.libre')
    return out


def editor_html(key, value, verification=None, revision=''):
    """Page du courriel : relire et modifier la réponse en attente, puis la déposer ; tel quel « à compléter » si le contrôle le permet.
    5.6.95 : les choix à faire (variantes) s'affichent au-dessus du texte, avec « Garder cette variante »."""
    from .prudent5667 import safe_to_complete
    safe = safe_to_complete(verification)
    rows = max(8, min(30, str(value.get('body') or '').count('\n') + 4))
    out = ('<section class="r5671" aria-labelledby="r5671-t"><h2 id="r5671-t">Réponse préparée — votre décision</h2>'
           '<p class="notice">%s</p>' + variantes_html(key, value).replace('%', '%%') +
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
    if revision:   # 5.6.100 (R09) : chaque formulaire porte la version affichée ; le serveur refuse (409) une version remplacée
        champ = '<input type="hidden" name="key" value="%s">' % e(key, quote=True)
        out = out.replace(champ, champ + '<input type="hidden" name="revision" value="%s">' % e(revision, quote=True))
    return out
