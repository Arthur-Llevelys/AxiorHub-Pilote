"""5.6.94 (lot fiabilité, F01 à F05) : constats du serveur du cabinet en 5.6.93.

- **F01 Indisponibilité passagère de Nextcloud** (503, 502, 504, 429, connexion coupée, délai) : la surveillance suspendue après trois
  échecs repart seule, un essai à la fois, espacé de 30 min, 1 h, 2 h puis 4 h au plus (``Retry-After`` respecté) ; elle se lève au premier
  succès. Une erreur d'autorisation, un 404 ou une erreur de configuration restent suspendus jusqu'à décision humaine. Pendant la panne :
  une seule ligne « Nextcloud indisponible depuis HH:MM, nouvel essai à HH:MM », et les synchronisations Deck et Tâches attendent l'essai.
- **F02** : un travail automatique suspendu pour un dossier (coupe-circuit de ``pause5629``) se termine « ignoré », jamais en erreur.
- **F03** : fiche vivante trop longue pour le modèle : une génération par rubrique ; une fiche partielle le dit et nomme ce qui manque.
- **F04** : nettoyage de la boîte À traiter incrémental (UIDVALIDITY, UIDNEXT, HIGHESTMODSEQ ; relecture complète une fois par jour).
- **F05** : dossiers « illisibles » : une erreur passagère ne signale rien et le dossier est relu au lot suivant ; un 404 est réessayé une
  fois par jour au plus et présenté comme « dossier introuvable », avec « Retrouver son nouvel emplacement ».

Aucun travail de fond nouveau : tout se décide dans les passages existants (planificateur de la surveillance, nettoyage de la boîte).
"""
from datetime import datetime, timezone
import imaplib
import json
import re
import time

from .common import Stop, clean_path

# ------------------------------------------------------------------------------------------------ F01 erreurs passagères
PASSAGERES = frozenset({'http_429', 'http_502', 'http_503', 'http_504', 'connexion_http_indisponible', 'delai_http_depasse'})
PALIERS = (1800, 3600, 7200, 14400)          # 30 min, 1 h, 2 h, puis 4 h au plus entre deux nouveaux essais automatiques
RETRY_AFTER_MAX = 86400                        # un Retry-After démesuré est borné à 24 h (il reste respecté en deçà)
SURVEILLANCES = ('live_calendar430', 'live_documents430')
MARQUE = 'reprise5694'                         # argument des nouveaux essais automatiques (compte des paliers)
# travaux qui ne font que parler à Nextcloud : leurs erreurs passagères rejoignent la ligne unique de la panne
NEXTCLOUD = frozenset({'live_calendar430', 'live_documents430', 'deck530_sync', 'sync_caldav_tasks', 'index', 'index_all', 'monitor_matter',
                       'discover', 'sync'})
ATTENTE_PANNE = frozenset({'deck530_sync', 'sync_caldav_tasks'})   # mis en file seulement au moment du nouvel essai pendant une panne


def passagere(code):
    return str(code or '') in PASSAGERES


def _instant(iso):
    try:
        value = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()


def _resultat(row):
    try:
        result = json.loads(row['result'] or '{}')
    except (TypeError, ValueError):
        return '', 0
    result = result if isinstance(result, dict) else {}
    detail = result.get('detail') if isinstance(result.get('detail'), dict) else {}
    try:
        attente = max(0, int(detail.get('retry_after') or 0))
    except (TypeError, ValueError):
        attente = 0
    return str(result.get('erreur') or ''), attente


def _marque(row):
    try:
        return MARQUE in (json.loads(row['args'] or '{}') or {})
    except (TypeError, ValueError, AttributeError):
        return False


def reprise(desk, kind, stamp=None):
    """Surveillance suspendue sur une erreur PASSAGÈRE : {'kind', 'code', 'depuis' (ISO), 'essai' (secondes), 'essais', 'due'} ; sinon None
    (pas suspendue, ou suspendue sur une erreur qui attend l'avocat)."""
    stamp = time.time() if stamp is None else stamp
    rows = desk.db.execute('SELECT id,status,attempts,args,result,created,finished FROM jobs WHERE kind=? ORDER BY id DESC LIMIT 20', (kind,)).fetchall()
    if not rows or rows[0]['status'] != 'error' or int(rows[0]['attempts'] or 0) < 3:
        return None
    code, attente = _resultat(rows[0])
    if not passagere(code):
        return None
    essais, depuis = 0, rows[0]['created'] or ''
    for row in rows:
        if row['status'] != 'error' or not passagere(_resultat(row)[0]):
            break
        depuis = row['created'] or depuis
        essais += 1 if _marque(row) else 0
    delai = PALIERS[min(essais, len(PALIERS) - 1)]
    if attente:
        delai = max(delai, min(attente, RETRY_AFTER_MAX))
    fin = _instant(rows[0]['finished']) or _instant(rows[0]['created']) or stamp
    return {'kind': kind, 'code': code, 'depuis': depuis, 'essai': fin + delai, 'essais': essais, 'due': stamp >= fin + delai}


def relancer(desk, kind):
    """Un seul nouvel essai : le travail est inscrit avec deux essais déjà consommés, de sorte qu'un échec le suspend aussitôt (la reprise
    automatique de ``activity431.maintenance`` ne le rejoue pas trois fois) ; un succès lève la suspension."""
    args = {MARQUE: 'oui'}
    try:
        jid = desk.enqueue(kind, args, priority=55, transactional=True)
        desk.db.execute("UPDATE jobs SET attempts=MAX(attempts,2) WHERE id=? AND status='pending'", (jid,))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.notify_queued(jid, args)
    desk.audit('fiabilite5694_nouvel_essai', {'kind': kind, 'job_id': jid})
    return jid


def indisponibilite(desk, stamp=None):
    """Panne passagère de Nextcloud vue par la surveillance (agenda, puis documents) ; None sans panne connue."""
    for kind in SURVEILLANCES:
        found = reprise(desk, kind, stamp)
        if found:
            return found
    return None


def en_attente(desk, now_ts=None):
    """Vrai pendant une panne passagère tant que le prochain essai n'est pas venu : rien d'autre ne va frapper à la porte de Nextcloud."""
    try:
        panne = indisponibilite(desk, now_ts)
    except Exception:   # noqa: BLE001 — jamais bloquant : sans lecture possible, le planificateur fait comme avant
        return False
    return bool(panne and not panne['due'])


def _heure(desk, seconds):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        zone = ZoneInfo(desk.c.get('calendar', {}).get('timezone', 'Europe/Paris'))
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    return datetime.fromtimestamp(seconds, zone).strftime('%H:%M')


def ligne(desk, panne):
    debut = _instant(panne['depuis']) or time.time()
    return 'Nextcloud indisponible depuis %s, nouvel essai à %s.' % (_heure(desk, debut), _heure(desk, max(panne['essai'], time.time())))


def replie(desk, panne, kind, result, finished):
    """Erreur d'un travail emportée par la panne en cours : comptée dans la ligne unique, pas une par travail."""
    if not panne:
        return False
    result = result if isinstance(result, dict) else {}
    if not passagere(result.get('erreur')):
        return False
    debut = (_instant(panne['depuis']) or 0) - 900
    if (_instant(finished) or 0) < debut:
        return False
    if kind in NEXTCLOUD:
        return True
    service = str((result.get('detail') or {}).get('service') or '') if isinstance(result.get('detail'), dict) else ''
    from urllib.parse import urlsplit
    hote = urlsplit(str((desk.c.get('nextcloud') or {}).get('url') or '')).hostname or ''
    return bool(service and hote and service == hote)


# ------------------------------------------------------------------------------------------------ F02 travail suspendu
IGNORE = 'relances_suspendues_apres_trois_echecs'
SUSPENSIONS = frozenset({IGNORE, 'agent_en_pause_sur_ce_dossier', 'relance_identique_refusee'})
MESSAGE_IGNORE = ('Ignoré : les relances automatiques de ce travail sont suspendues pour ce dossier après trois échecs. '
                  'Corrigez la cause puis « Réautoriser » ; une demande de votre part reste toujours possible.')


# ------------------------------------------------------------------------------------------------ F03 fiche par rubriques
RUBRIQUES = (('synthese', 'Synthèse, parties et juridiction', ('summary', 'parties', 'jurisdiction', 'case_number', 'claims', 'key_documents', 'last_email')),
             ('chronologie', 'Chronologie', ('chronology',)),
             ('echeances', 'Échéances et montants', ('deadlines', 'amounts')),
             ('suivi', 'Instructions, positions et actions', ('latest_instructions', 'negotiation_positions', 'actions_completed', 'actions_planned', 'open_questions')))
MEMOIRE = 'Mémoire structurée du dossier'
ECHECS_RUBRIQUE = ('generation_ia_incomplete', 'contexte_ia_insuffisant')


def fiche_vide():
    return {'summary': '', 'parties': [], 'jurisdiction': '', 'case_number': '', 'claims': [], 'chronology': [], 'key_documents': [],
            'latest_instructions': [], 'negotiation_positions': [], 'amounts': [], 'deadlines': [], 'last_email': '', 'actions_completed': [],
            'actions_planned': [], 'open_questions': [], 'source_ids': [], 'limits': []}


def fiche_par_rubriques(model, payload):
    """Une génération par rubrique, chacune dans la limite de sortie du modèle (même modèle, même politique, même budget : chaque appel
    passe par ``Model.ask``). Une rubrique coupée est nommée dans les manquantes ; toute autre erreur (budget, pause, politique) remonte."""
    fiche, manquantes, produites = fiche_vide(), [], 0
    for ident, label, champs in RUBRIQUES:
        try:
            part = model.ask('case_brief_part', {**payload, 'rubriques_demandees': list(champs)})
        except Stop as error:
            if str(error) not in ECHECS_RUBRIQUE:
                raise
            manquantes.append(label)
            continue
        produites += 1
        for champ in champs:
            fiche[champ] = part.get(champ, fiche[champ])
        for champ in ('source_ids', 'limits'):
            fiche[champ] = list(dict.fromkeys(fiche[champ] + [x for x in (part.get(champ) or []) if isinstance(x, str)]))
    if not produites:
        raise Stop('generation_ia_incomplete')
    return fiche, manquantes


def marquer_partielle(fiche, manquantes):
    if manquantes:
        fiche['limits'] = list(fiche.get('limits') or []) + ['Fiche partielle : rubriques non produites (limite du modèle) : ' + ', '.join(manquantes) + '.']
    return fiche


# ------------------------------------------------------------------------------------------------ F04 boîte À traiter incrémentale
ETAT_BOITE = 'portfolio:rapprochement5694'
COMPLET_SECONDES = 86400          # relecture complète une fois par jour
NOUVEAUX_MAX = 2000               # au-delà, la relecture complète est plus sûre qu'un relevé des nouveaux messages
# refus du contrôle qui ne dépendent que du contenu des boîtes : tant qu'aucun message utile n'arrive, la relecture redonnerait le même refus
CONTROLES_STABLES = frozenset({'trop_de_brouillons_a_controler', 'historique_envois_a_verifier', 'fil_trop_long', 'historique_trop_long'})
STATUT = re.compile(rb'(MESSAGES|UIDNEXT|UIDVALIDITY|HIGHESTMODSEQ) (\d+)')


def _compter(categorie='imap', n=1):
    from .charge5693 import compter
    compter(categorie, n)


def _dossiers(cfg):
    return list(dict.fromkeys([cfg['inbox'], cfg['sent'], cfg['drafts']]))


def etat_boites(box):
    """STATUS des boîtes utiles (réception, envoyés, brouillons) : {dossier: {'uidvalidity', 'uidnext', 'messages', 'modseq'}} ; None si le
    serveur ne les donne pas (le passage reste alors complet, comme avant)."""
    from .mailbox import Mailbox, imap_quote, modified_utf7
    if not isinstance(box, Mailbox) or not hasattr(box.conn, 'status'):
        return None
    from .traces5694 import etape
    caps = {(x.decode() if isinstance(x, bytes) else str(x)).upper() for x in (getattr(box.conn, 'capabilities', ()) or ())}
    items = '(MESSAGES UIDNEXT UIDVALIDITY' + (' HIGHESTMODSEQ)' if 'CONDSTORE' in caps else ')')
    out = {}
    for folder in _dossiers(box.cfg):
        _compter()
        etape('imap', 'status')
        try:
            status, data = box.conn.status(imap_quote(modified_utf7(folder)), items)
        except (imaplib.IMAP4.error, OSError):
            return None
        raw = b' '.join(x for x in (data or []) if isinstance(x, bytes))
        values = {k.decode().lower(): int(v) for k, v in STATUT.findall(raw)}
        if status != 'OK' or 'uidnext' not in values or 'uidvalidity' not in values:
            return None
        out[folder] = {'uidvalidity': values['uidvalidity'], 'uidnext': values['uidnext'], 'messages': values.get('messages', -1),
                       'modseq': values.get('highestmodseq', 0)}
    return out


def _vus(desk):
    value = desk.settings(ETAT_BOITE, {}) or {}
    return value if isinstance(value, dict) else {}


def _court(key):
    return str(key)[:24]


def _entetes(box, folder, depuis):
    """En-têtes de liaison des seuls messages arrivés depuis ``depuis`` (UID) : un SELECT et un FETCH."""
    from .mailbox import addresses
    box.select(folder)
    _compter()
    status, data = box.conn.uid('FETCH', '%d:*' % depuis, '(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES FROM TO CC)])')
    if status != 'OK':
        raise Stop('lecture_imap_echouee')
    out = []
    for part in data or []:
        if not isinstance(part, tuple):
            continue
        uid = re.search(rb'UID (\d+)', part[0] or b'')
        if not uid or int(uid[1]) < depuis:
            continue                                       # « n:* » rend le dernier message même s'il est plus ancien
        head = (part[1] or b'').decode('utf-8', 'replace')
        field = lambda name: ' '.join(re.findall(r'(?im)^' + name + r':\s*(.*(?:\r?\n[ \t].*)*)', head))
        out.append({'refs': {x.lower() for x in re.findall(r'<[^<>\s]+>', field('In-Reply-To') + ' ' + field('References'))},
                    'from': (addresses(field('From')) or [''])[0], 'to': set(addresses(field('To')) + addresses(field('Cc')))})
    return out


def _drapeaux(box, folder, uids):
    """Drapeaux des messages ouverts dans un dossier : un SELECT et un seul FETCH pour tous. {uid: drapeaux} ; un UID absent a disparu."""
    box.select(folder)
    _compter()
    status, data = box.conn.uid('FETCH', ','.join(sorted(uids, key=int)), '(UID FLAGS)')
    if status != 'OK':
        raise Stop('lecture_imap_echouee')
    out = {}
    for part in data or []:
        meta = part[0] if isinstance(part, tuple) else part
        if not isinstance(meta, bytes):
            continue
        uid = re.search(rb'UID (\d+)', meta)
        if uid:
            out[uid[1].decode()] = {x.decode() for x in imaplib.ParseFlags(meta)}
    return out


def plan_rapprochement(desk, box, rows, etat, repair=False, stamp=None):
    """Passage incrémental ou None (passage complet : premier passage, relecture du jour, UIDVALIDITY changée, réparation 3.6.1 en cours,
    serveur sans STATUS). Renvoie {'verifier': clés à relire comme avant, 'decisions': {clé: état}} : les autres cartes n'ont rien pu changer
    (aucun nouveau message dans la réception, les envois ou les brouillons qui les concerne, drapeaux inchangés)."""
    from .desk import report_for
    from .mailbox import own_addresses
    stamp = time.time() if stamp is None else stamp
    avant = _vus(desk)
    if repair or not etat or stamp - float(avant.get('complet') or 0) >= COMPLET_SECONDES:
        return None
    boites, vus = avant.get('boites') or {}, avant.get('vus') or {}
    if set(boites) != set(etat) or any(boites[f].get('uidvalidity') != etat[f]['uidvalidity'] for f in etat):
        return None
    if any(etat[f]['uidnext'] - int(boites[f].get('uidnext') or 0) > NOUVEAUX_MAX for f in etat):
        return None
    verifier, decisions, cartes = set(), {}, {}
    for row in rows:
        key = row['mail_key']
        try:
            report = report_for(desk.c, key)
        except Stop:
            verifier.add(key)              # la relecture habituelle sait traiter un rapport absent
            continue
        info = {'folder': str(report.get('source_mailbox') or ''), 'uid': str(report.get('source_uid') or ''),
                'sender': str(report.get('sender') or '').strip().lower(), 'mid': str(report.get('incoming_message_id') or '').strip().lower()}
        if vus.get(_court(key)) != info['uid'] or info['folder'] not in etat or not info['uid'].isdigit():
            verifier.add(key)              # carte nouvelle ou message changé : relecture habituelle
            continue
        cartes[key] = info
    par_dossier = {}
    for key, info in cartes.items():
        par_dossier.setdefault(info['folder'], {})[info['uid']] = key
    for folder, uids in par_dossier.items():
        old, new = boites.get(folder) or {}, etat[folder]
        if new['modseq'] and old.get('modseq') == new['modseq'] and old.get('messages') == new['messages'] and old.get('uidnext') == new['uidnext']:
            continue                       # CONDSTORE : rien n'a bougé dans ce dossier (ni drapeau, ni suppression, ni arrivée)
        flags = _drapeaux(box, folder, list(uids))
        for uid, key in uids.items():
            if uid not in flags or '\\Deleted' in flags[uid]:
                decisions[key] = 'archived'
            elif '\\Answered' in flags[uid]:
                decisions[key] = 'handled'
    own = own_addresses(box.cfg)
    for folder in etat:
        depuis = int(boites[folder].get('uidnext') or 0)
        if etat[folder]['uidnext'] <= depuis:
            continue
        for msg in _entetes(box, folder, depuis):
            for key, info in cartes.items():
                if key in decisions or key in verifier:
                    continue
                if ((info['mid'] and info['mid'] in msg['refs']) or
                        (folder != box.cfg['inbox'] and info['sender'] in msg['to']) or
                        (folder == box.cfg['inbox'] and (msg['from'] == info['sender'] or msg['from'] in own))):
                    verifier.add(key)
    return {'verifier': verifier, 'decisions': decisions}


def noter_rapprochement(desk, etat, complet, verifies, retires, stamp=None):
    """Après le passage : état des boîtes relevé AVANT les lectures (un changement pendant le passage sera vu au suivant), cartes relues."""
    if not etat:
        return
    stamp = time.time() if stamp is None else stamp
    avant = _vus(desk)
    vus = {} if complet else dict(avant.get('vus') or {})
    for key in retires:
        vus.pop(_court(key), None)
    vus.update({_court(k): v for k, v in verifies.items()})
    desk.setting(ETAT_BOITE, {'boites': etat, 'vus': dict(list(vus.items())[-2000:]),
                              'complet': stamp if complet else float(avant.get('complet') or 0)})


# ------------------------------------------------------------------------------------------------ F05 dossiers non lus
INTROUVABLE = 'http_404'
INTROUVABLE_TEXTE = 'Dossier introuvable dans Nextcloud (déplacé, renommé ou supprimé ?)'
REPOS_INTROUVABLE = 86400         # un dossier introuvable est réessayé une fois par jour au plus


def introuvables_au_repos(gone, stamp=None):
    stamp = time.time() if stamp is None else stamp
    return {mid for mid, x in (gone or {}).items()
            if isinstance(x, dict) and x.get('code') == INTROUVABLE and stamp - (_instant(x.get('at')) or 0) < REPOS_INTROUVABLE}


def a_relire(gone, matters):
    """Dossiers signalés sur une erreur passagère (avant 5.6.94, par exemple pendant une panne de Nextcloud) : relus en tête du lot suivant,
    sans attendre un changement d'ETag ; le signalement tombe au premier succès."""
    return [m for m in matters if passagere((gone.get(m['id']) or {}).get('code'))]


def _candidats(desk, matter, matters):
    old = clean_path(matter['path'])
    base = old.rsplit('/', 1)[-1]
    ref = re.search(r'(\d{8,12})$', base)

    def proche(path):
        name = path.rsplit('/', 1)[-1]
        return path != old and (name == base or bool(ref and re.search(r'(?:^|[ -])' + ref[1] + r'$', name)))
    chemins = [r[0] for r in desk.db.execute('SELECT path FROM directories ORDER BY updated DESC LIMIT 20000') if proche(r[0])]
    chemins += [clean_path(m['path']) for m in matters if m['id'] != matter['id'] and proche(clean_path(m['path']))]
    return list(dict.fromkeys(chemins))[:10]


def retrouver(desk, mid, dav=None, owner='cabinet'):
    """« Retrouver son nouvel emplacement » : dossiers déjà connus de la découverte qui portent le même nom ou la même référence, vérifiés dans
    Nextcloud (au plus 5 lectures). Sans candidat, la découverte existante est lancée (demande de l'avocat) ; rien n'est déplacé d'office."""
    from .common import load_matters
    from .watch430 import UNREADABLE, unreadable
    matters = load_matters(desk.c)
    matter = next((m for m in matters if m['id'] == str(mid or '')), None)
    if not matter:
        raise Stop('dossier_absent')
    if dav is None:
        from .dav import DAV
        dav = DAV(desk.c['nextcloud'])
    suivis = {clean_path(m['path']): m['id'] for m in matters if m['id'] != matter['id']}
    trouves = []
    for path in _candidats(desk, matter, matters)[:5]:
        try:
            dav.folder_etag(path)
        except Stop:
            continue
        trouves.append({'path': path, 'suivi_par': suivis.get(path, '')})
    gone = unreadable(desk)
    entry = dict(gone.get(matter['id']) or {})
    entry['candidats'] = [x['path'] for x in trouves if not x['suivi_par']]
    if matter['id'] in gone:
        gone[matter['id']] = entry
        desk.setting(UNREADABLE, gone)
    desk.audit('fiabilite5694_retrouver', {'matter': matter['id'], 'candidats': len(trouves), 'owner': owner})
    libres = [x for x in trouves if not x['suivi_par']]
    if libres:
        return {'candidats': [x['path'] for x in libres],
                'message': '%d emplacement(s) possible(s) trouvé(s) : choisissez « Utiliser cet emplacement » dans la liste.' % len(libres)}
    if trouves:
        return {'candidats': [], 'message': 'Le dossier retrouvé est déjà suivi comme un autre dossier AxiorHub : « Ne plus surveiller » '
                                            'l’ancien, ou corrigez le dossier dans Paramètres.'}
    jid = desk.enqueue('discover', {}, priority=20, origin='user')
    return {'candidats': [], 'job_id': jid, 'message': 'Aucun emplacement connu : parcours des dossiers Nextcloud lancé. Revenez dans '
                                                       'quelques minutes et cliquez de nouveau sur « Retrouver son nouvel emplacement ».'}


def deplacer(desk, mid, path, owner='cabinet'):
    """« Utiliser cet emplacement » : seul un emplacement proposé par « Retrouver » est accepté ; le dossier est relu au prochain lot."""
    from .common import load_matters
    from .desk import save_matter
    from .watch430 import UNREADABLE, unreadable
    from .workspace import allowed
    gone = unreadable(desk)
    entry = gone.get(str(mid or '')) or {}
    path = allowed(desk.c, str(path or ''))
    if path not in (entry.get('candidats') or []):
        raise Stop('emplacement_non_propose')
    matters = load_matters(desk.c)
    matter = next((m for m in matters if m['id'] == str(mid)), None)
    if not matter:
        raise Stop('dossier_absent')
    if any(clean_path(m['path']) == path for m in matters if m['id'] != matter['id']):
        raise Stop('emplacement_suivi_par_un_autre_dossier')
    old = matter['path']
    save_matter(desk.c, {**matter, 'path': path})
    gone.pop(matter['id'], None)
    desk.setting(UNREADABLE, gone)
    desk.setting('live430:scan:' + matter['id'], None)
    from . import charge5693
    charge5693.oublier(desk, matter['id'])
    desk.audit('fiabilite5694_emplacement', {'matter': matter['id'], 'ancien_sha': __import__('hashlib').sha256(str(old).encode()).hexdigest()[:12],
                                             'owner': owner})
    return {'message': 'Emplacement du dossier mis à jour ; il sera relu au prochain contrôle (dans 5 minutes au plus).'}
