"""5.6.83 : la discussion lit réellement les courriels et l'agenda du cabinet, et choisit mieux ses documents.

Avant : sans dossier sélectionné, le modèle ne recevait que des extraits de documents, sans la date du jour. « Résume mes mails d'aujourd'hui »
partait donc en recherche plein texte sur les mots de la question et ramenait des pièces sans rapport. Ici, l'intention est reconnue sans appel au modèle ;
les courriels sont lus en lecture seule (``BODY.PEEK`` : rien n'est marqué comme lu) et l'agenda vient de l'instantané local. Le contenu reste une donnée
non fiable : le bouclier 5.6.59 s'applique comme aux pièces. Un courriel ou un événement d'un dossier réservé à d'autres membres n'est jamais transmis.
Si la messagerie ne répond pas, la réponse le dit : aucune absence de courriel n'est déduite d'une lecture échouée.

5.6.96 (M01-M04) : une demande de lecture devient une requête structurée (``structurer`` : intention, boîte, bornes en heure locale de la
personne, filtres nommés seulement sur demande) ; « liste ma boîte de réception » n'est pas une recherche textuelle. Une liste simple est
rendue depuis le connecteur, sans modèle (``liste_answer``), avec un contrat de résultat (statuts ``complet``, ``partiel``, ``vide_verifie``,
``indisponible``, ``refuse`` et compteurs). Les bornes sont revérifiées sur l'INTERNALDATE de chaque en-tête : SINCE/BEFORE ne connaissent que
le jour du serveur. Un en-tête illisible est compté et rend la lecture partielle, jamais « vide vérifié »."""
from datetime import date, datetime, time as dtime, timedelta, timezone
import math
import re
import time
from zoneinfo import ZoneInfo

from .common import Stop, fold, load_matters, matter_display

MAIL = re.compile(r'\b(mails?|e mails?|emails?|courriels?|messagerie|boite de reception|boite mail|inbox|messages? recus?)\b')
AGENDA = re.compile(r'\b(agendas?|calendriers?|rendez vous|rdv|audiences?|evenements?|planning|reunions?|ma journee|ma semaine|mes taches|echeances?)\b')
BRIEF = re.compile(r'\b(quoi de neuf|point du jour|ma journee|briefing)\b')
FREE_DAY = re.compile(r"\b(j ai quoi|qu est ce que j ai|ai je quelque chose|suis je libre|prevu)\b")
DRAFTING = re.compile(r'\b(redige|rediger|ecris|ecrire|prepare|preparer|envoie|envoyer|reponds|repondre)\b')
READING = re.compile(r'\b(recus?|recues?|arrives?|arrivees?|lire|lis|resum\w*|synthe\w*|liste\w*|cherche\w*|trouve\w*|retrouve\w*|derniers?|dernieres?|nouveaux?|nouvelles?|non lus?|'
                     r'non traites?|aujourd hui|hier|ce matin|cette semaine)\b')
SENT = re.compile(r'\b(envoyes?|envoyees?|expedies?|que j ai envoye|boite d envoi)\b')
DOCUMENT = re.compile(r'\b(pieces?|documents?|contrats?|conclusions?|assignations?|actes?|statuts|jugements?|arrets?|fichiers?)\b')
RECENT = re.compile(r'\b(recents?|recentes?|derniers?|dernieres?|nouveaux?|nouvelles?|aujourd hui|cette semaine|ce mois)\b')
FOLLOW_UP = re.compile(r'^\W*(et\b|ceux|celles?|plutot|aussi|idem|pareil)|\b(hier|avant hier|demain|ce matin|cette semaine|semaine derniere|semaine prochaine)\b')
GENERIC = frozenset(('resume resumer resumes resumez synthese synthetise fais faire moi peux pouvez pourrais pourriez donne donner montre montrer liste lister trouve trouver retrouve retrouver cherche '
                     'chercher recherche rechercher analyse analyser explique expliquer dis dire voir lire lis mes mon ma nos notre votre vos tes ton ta aujourd hui hier demain ce cette ces matin soir semaine '
                     'mois jour jours dernier derniers derniere dernieres recent recents recente recentes nouveau nouveaux nouvelle nouvelles recu recus recue recues arrive arrives prevu prevus besoin '
                     'veux voudrais aimerais souhaite merci stp svp bonjour bonsoir tu vous je il elle on les des une pour dans que qui sur par avec est son ses aux pas plus quel quelle quels quelles '
                     'concernant propos sujet '
                     # 5.6.96 (M01) : verbes conjugués et pluriels courants d'une demande : jamais des termes de recherche
                     'souhaiterais souhaiterait souhaites souhaitez souhaiterions voudrait voudrions voulez veut aimerait aimeriez pourrait pourrions listes listez listent affiche affiches '
                     'affichez afficher montres montrez montrons donnes donnez indique indiques indiquez indiquer sors sortez sortir presente presentes presentez presenter envoye envoyes envoyee '
                     'envoyees expedie expedies lesquels lesquelles combien tous toutes tout toute ceux celles celui celle quoi afin bien').split()) | frozenset(('mail mails email emails courriel courriels messagerie boite reception message messages agenda agendas calendrier calendriers rendez vous rdv '
                                                                     'evenement evenements document documents piece pieces fichier fichiers dossier dossiers').split())
# 5.6.96 (M01, M03) : intention d'une demande de lecture : synthèse (modèle), recherche nommée, ou simple liste (connecteur seul)
RESUME = re.compile(r'\b(resum\w*|synthe\w*|analys\w*|expliqu\w*|prioris\w*|urgent\w*|important\w*|contenu|dit|disent|point|repond\w*|redig\w*|prepar\w*|tradui\w*)\b')
SEARCHING = re.compile(r'\b(cherch\w*|trouv\w*|retrouv\w*)\b')
LISTING = re.compile(r'\b(list\w*|quels|quelles|lesquels|lesquelles|affich\w*|montr\w*|derniers?|dernieres?|nouveaux?|nouvelles?|recus?|recues?|arrives?|arrivees?|non lus?|'
                     r'combien|y a t il|ai je|qu est ce que j ai|boite de reception|inbox|envoyes?|envoyees?)\b')
# mots qui suivent « de » sans désigner un expéditeur (« mails de ma boîte », « courriels d'hier »)
NOT_A_NAME = frozenset(('ma mon mes la le les l ce cette ces cet hier aujourd avant demain reception boite semaine jour journee mois matin soir dernier derniere derniers dernieres '
                        'cabinet client clients tous toutes tout toute nouveau nouveaux ta ton tes votre vos notre nos un une des du de part').split())
STATUTS = ('complet', 'partiel', 'vide_verifie', 'indisponible', 'refuse')
MONTHS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8, 'septembre': 9, 'octobre': 10, 'novembre': 11, 'decembre': 12}
WEEKDAYS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
IMAP_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
MAX_LISTED = 40
LIMITS = {'assistant': {'bodies': 12, 'chars': 1800, 'seconds': 15}, 'voice': {'bodies': 5, 'chars': 600, 'seconds': 7}}
PORTFOLIO_WEIGHT = {'active': 1.25, 'to_confirm': 1.1, 'dormant': 0.85, 'archived': 0.65, 'closed': 0.65}


def _text(question):
    return re.sub(r'[^0-9a-z]+', ' ', fold(str(question or ''))).strip()


def wants_mail(question):
    text = _text(question)
    if BRIEF.search(text):
        return True
    if not MAIL.search(text):
        return False
    return not DRAFTING.search(text) or bool(READING.search(text))     # « rédige un mail à… » n'est pas une demande de lecture


def wants_agenda(question):
    text = _text(question)
    return bool(AGENDA.search(text) or FREE_DAY.search(text) or BRIEF.search(text))


def wants_documents(question):
    return bool(DOCUMENT.search(_text(question)))


def follow_up(question, previous):
    """« Et ceux d'hier ? » : la question précédente portait sur les courriels ou l'agenda, celle-ci se contente de préciser."""
    text = _text(question)
    if len(text.split()) > 10 or not FOLLOW_UP.search(text):
        return False, False
    return wants_mail(previous), wants_agenda(previous)


def focus_terms(question):
    """Mots qui portent l'objet de la demande : sans eux, la recherche classerait des pièces sur « résume » ou « aujourd'hui »."""
    return [w for w in _text(question).split() if len(w) >= 3 and w not in GENERIC and not w.isdigit()]


def mail_filter(question):
    """5.6.96 (M01) : (champ, valeur) d'un filtre demandé en clair ; ('', '') sinon. Un nom ou un objet ne filtre que s'il est demandé :
    « de DURAND » (expéditeur ou objet), « au sujet de … » / « objet … » / « dont l'objet contient « … » » (objet)."""
    raw, text = str(question or ''), _text(question)
    quoted = re.search(r'(objet|de la part de|de|expediteur|expéditeur)\s*(?:contient|mentionne|est|:)?\s*["«]\s*([^"»]{2,100}?)\s*["»]', raw, re.I)
    if quoted:
        return ('objet' if fold(quoted[1]) == 'objet' else 'expediteur', _text(quoted[2]))
    subject = re.search(r'\b(?:au sujet de|a propos de|dont l objet (?:contient|mentionne|est|parle de)|objet)\s+([a-z0-9][a-z0-9 ]{1,80})$', text)
    if subject:
        value = re.split(r'\b(?:aujourd hui|hier|avant hier|ce matin|ce soir|cette semaine|ce mois|semaine derniere|merci|stp|svp)\b', subject[1], maxsplit=1)[0].strip()
        if value and value.split()[0] not in NOT_A_NAME:
            return 'objet', value
    named = re.search(r'\b(?:mails?|e mails?|emails?|courriels?|messages?)\s+(?:(?:recus?|envoyes?|arrives?)\s+)?(?:de la part de|de|du|d)\s+([a-z0-9]+(?: [a-z0-9]+)?)', text)
    if named:
        words = [w for w in named[1].split() if w not in NOT_A_NAME and w not in GENERIC]
        if words and named[1].split()[0] == words[0]:
            return 'expediteur_ou_objet', ' '.join(words)
    return '', ''


def liste_simple(question, previous=''):
    """5.6.96 (M02) : vrai pour une demande de liste de courriels sans synthèse ni recherche nommée : le connecteur répond seul, sans modèle.
    « Et ceux d'hier ? » après une liste reste une liste."""
    if wants_agenda(question) or wants_documents(question):
        return False
    text = _text(question)
    if wants_mail(question):
        return not RESUME.search(text) and not SEARCHING.search(text) and bool(LISTING.search(text))
    return bool(previous) and follow_up(question, previous) == (True, False) and liste_simple(previous)


def structurer(question, today, zone, matter='', dossier=''):
    """5.6.96 (M01) : requête structurée d'une lecture de courriels : intention (lister, resumer, chercher), boîte IMAP (inbox, sent),
    intervalle [debut, fin[ en heure locale du compte (« aujourd'hui » : minuit local → minuit suivant ; défaut : sept jours calendaires,
    aujourd'hui compris), fuseau, filtres nommés explicites. Aucun terme libre."""
    text = _text(question)
    start, end, label, explicit = period(question, today)
    champ, valeur = mail_filter(question)
    intention = 'resumer' if RESUME.search(text) else ('chercher' if valeur or SEARCHING.search(text) else 'lister')
    if intention == 'chercher' and valeur and not explicit:        # un nom sans date : l'année écoulée, annoncée comme telle
        start, label = today - timedelta(days=365), 'les douze derniers mois'
    tz = ZoneInfo(zone)
    return {'intention': intention, 'dossier_imap': dossier or ('sent' if SENT.search(text) else 'inbox'), 'fuseau': zone, 'periode': label, 'precisee': explicit,
            'du': start.isoformat(), 'au': end.isoformat(), 'debut': datetime.combine(start, dtime.min, tz), 'fin': datetime.combine(end + timedelta(days=1), dtime.min, tz),
            'filtres': {'expediteur': valeur if champ in ('expediteur', 'expediteur_ou_objet') else '', 'objet': valeur if champ in ('objet', 'expediteur_ou_objet') else '',
                        'champ': champ, 'dossier': matter}}


def dans_bornes(stamp, debut, fin):
    """5.6.96 (M02) : l'INTERNALDATE réelle, convertie dans le fuseau du compte, est dans [debut, fin[ ; SINCE/BEFORE ne garantissent que le jour UTC."""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return debut <= stamp < fin


def _correspond(filtres, sender, subject):
    champ = filtres.get('champ')
    if not champ:
        return True
    valeur = filtres.get('expediteur') or filtres.get('objet')
    compared = {'expediteur': sender, 'objet': subject}.get(champ, sender + ' ' + subject)
    return valeur in _text(compared)


def compte_refuse(desk, owner, cfg):
    """5.6.96 (M04) : la personne lit le compte déjà autorisé du cabinet ; un profil actif qui revendique une adresse hors de ce compte
    n'ouvre rien (même règle que ``settings568.check_owner``)."""
    if not owner or owner == 'cabinet':
        return ''
    try:
        from .settings568 import profile
        from .mailbox import own_addresses
        p = profile(desk, owner)
        claimed = {str(a).strip().lower() for a in [p.get('primary_address', '')] + list(p.get('aliases') or []) if str(a).strip()}
        if p.get('enabled') and claimed - {str(a).lower() for a in own_addresses(cfg)}:
            return 'compte de messagerie non autorisé pour cette personne'
    except Exception:   # noqa: BLE001 — profil illisible : lecture du compte du cabinet, comme avant
        return ''
    return ''


def today_of(desk, owner='cabinet'):
    from zoneinfo import ZoneInfo
    from .settings568 import profile
    try:
        name = profile(desk, owner)['timezone']
        return datetime.now(ZoneInfo(name)).date(), name
    except Exception:   # noqa: BLE001 — fuseau illisible : date du serveur
        return datetime.now(timezone.utc).date(), 'UTC'


def period(question, today, forward=False):
    """(début, fin incluse, libellé, précisée) : une expression non reconnue donne la période par défaut, annoncée comme telle."""
    text = _text(question)
    monday = today - timedelta(days=today.weekday())
    if 'avant hier' in text:
        return today - timedelta(days=2), today - timedelta(days=2), 'avant-hier', True
    if re.search(r'\bhier\b', text):
        return today - timedelta(days=1), today - timedelta(days=1), 'hier', True
    if re.search(r'\bapres demain\b', text):
        return today + timedelta(days=2), today + timedelta(days=2), 'après-demain', True
    if re.search(r'\bdemain\b', text):
        return today + timedelta(days=1), today + timedelta(days=1), 'demain', True
    if re.search(r'\b(aujourd hui|ce jour|ce matin|cet apres midi|ce soir|ma journee|du jour|quoi de neuf|briefing)\b', text):
        return today, today, 'aujourd’hui', True
    if 'semaine derniere' in text or 'semaine passee' in text:
        return monday - timedelta(days=7), monday - timedelta(days=1), 'la semaine dernière', True
    if 'semaine prochaine' in text:
        return monday + timedelta(days=7), monday + timedelta(days=13), 'la semaine prochaine', True
    if re.search(r'\b(cette semaine|ma semaine)\b', text):
        return monday, monday + timedelta(days=6) if forward else today, 'cette semaine', True
    if 'ce mois' in text:
        return today.replace(day=1), (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1) if forward else today, 'ce mois-ci', True
    match = re.search(r'\b(\d+) derniers? jours?\b', text)
    if match:
        n = max(1, min(int(match.group(1)), 90))            # 5.6.96 (M01) : n jours calendaires, aujourd'hui compris
        return today - timedelta(days=n - 1), today, match.group(0), True
    match = re.search(r'\b(\d{1,2}) (\d{1,2})(?: (\d{2,4}))?\b', text) if re.search(r'\d{1,2}/\d{1,2}', str(question)) else None
    named = re.search(r'\b(\d{1,2}|1er) (%s)(?: (\d{4}))?\b' % '|'.join(MONTHS), text)
    try:
        if match:
            year = int(match.group(3) or today.year)
            day = date(year + 2000 if year < 100 else year, int(match.group(2)), int(match.group(1)))
            return day, day, day.strftime('%d/%m/%Y'), True
        if named:
            day = date(int(named.group(3) or today.year), MONTHS[named.group(2)], 1 if named.group(1) == '1er' else int(named.group(1)))
            return day, day, day.strftime('%d/%m/%Y'), True
    except ValueError:
        pass
    if forward:
        return today, today + timedelta(days=14), 'les quinze prochains jours', False
    return today - timedelta(days=6), today, 'les sept derniers jours', False       # 5.6.96 (M01) : sept jours calendaires, aujourd'hui compris


def _imap_day(day):
    return '%02d-%s-%04d' % (day.day, IMAP_MONTHS[day.month - 1], day.year)


def named_matters(desk, question, hidden=()):
    """Dossiers cités par leur nom dans la question : la recherche s'y limite au lieu de parcourir tout le cabinet."""
    text = ' %s ' % _text(question)
    found = []
    for m in load_matters(desk.c):
        if m.get('id') in hidden:
            continue
        names = [str(m.get('client_name') or ''), str(m.get('id') or '')] + [str(x) for x in (m.get('aliases') or [])] + [str(x) for x in (m.get('references') or [])]
        best = max((len(n) for n in (_text(x) for x in names) if len(n) >= 4 and ' %s ' % n in text), default=0)
        if best:
            found.append((best, m))
    found.sort(key=lambda x: -x[0])
    return [m for _, m in found[:2]]


def _work_rows(desk):
    try:
        return {r['mail_key']: dict(r) for r in desk.db.execute('SELECT mail_key, state, matter, subject, sender, received FROM work_items')}
    except Exception:   # noqa: BLE001 — table absente sur une base neuve
        return {}


def _labels(desk):
    return {m['id']: matter_display(m) for m in load_matters(desk.c)}


MAX_SCANNED = 400


def _etat_initial(req, zone):
    """Contrat de résultat (M03) : statut et compteurs toujours présents, même avant toute connexion."""
    return {'consultes': False, 'statut': 'indisponible', 'periode': req['periode'], 'du': req['du'], 'au': req['au'], 'fuseau': zone, 'dossier_imap': req['dossier_imap'],
            'intention': req['intention'], 'filtres_demandes': {k: v for k, v in req['filtres'].items() if v and k != 'champ'},
            'trouves': 0, 'lus': 0, 'entetes_lus': 0, 'en_echec': 0, 'filtres': 0, 'affiches': 0, 'non_examines': 0, 'limite': MAX_LISTED, 'limite_atteinte': False,
            'delai_depasse': False, 'complet': False, 'heure_lecture': '', 'compte': '', 'boite': '', 'uidvalidity': '', 'lignes': []}


def read_mail(desk, question, matter='', hidden=(), voice=False, box=None, today=None, owner='cabinet', just_headers=False, dossier=''):
    """Courriels de la période demandée, du plus récent au plus ancien : liste bornée (en-têtes seulement, ``BODY.PEEK[HEADER]``) et, sauf
    ``just_headers``, texte des plus récents pour le modèle. Retourne (sources, état). L'état porte le contrat de résultat (M03) : ``statut``
    (complet, partiel, vide_verifie, indisponible, refuse) et compteurs (en-têtes lus, en échec, filtrés, affichés). Une panne est rendue dans
    l'état, jamais une liste vide présentée comme une absence de courriel ; un en-tête illisible rend la lecture ``partiel``. Aucune écriture IMAP."""
    from . import mailbox as mbx
    from .boite5625 import _account
    cfg = desk.c.get('mail') or {}
    day, zone = today_of(desk, owner)
    today = today or day
    req = structurer(question, today, zone, matter, dossier)
    state = _etat_initial(req, zone)
    from . import vueia5688   # 5.6.88 : contrôlé avant toute connexion à la boîte
    if vueia5688.blocked(desk, 'courriels'):
        return [], {**state, 'statut': 'refuse', 'erreur': vueia5688.REFUSED}
    refus = compte_refuse(desk, owner, cfg)   # 5.6.96 (M04) : compte autorisé de la personne, avant toute connexion
    if refus:
        return [], {**state, 'statut': 'refuse', 'erreur': refus}
    if not cfg.get('host'):
        return [], {**state, 'erreur': 'messagerie non configurée'}
    folder_key = req['dossier_imap']
    if folder_key == 'sent' and not cfg.get('sent'):
        return [], {**state, 'erreur': 'boîte des messages envoyés non configurée'}
    limits = LIMITS['voice' if voice else 'assistant']
    deadline = time.monotonic() + limits['seconds']
    own = box is None
    try:
        # connexion bornée au délai de la demande : une messagerie muette ne retient pas la réponse 45 s
        box = box or mbx.Mailbox({**cfg, 'connect_timeout_seconds': max(1, min(int(limits['seconds']), int(cfg.get('connect_timeout_seconds') or 45)))})
        folder = cfg.get(folder_key) or cfg.get('inbox') or 'INBOX'
        # SINCE/BEFORE comparent le jour de l'INTERNALDATE tel que le serveur le voit (souvent UTC) : fenêtre élargie d'un jour de chaque côté,
        # puis bornes exactes vérifiées en heure locale du compte sur chaque en-tête (``dans_bornes``)
        criteria = ['UNDELETED', 'SINCE', _imap_day(req['debut'].date() - timedelta(days=1)), 'BEFORE', _imap_day(req['fin'].date() + timedelta(days=1))]
        uids = sorted(set(box.search(folder, *criteria)), key=int, reverse=True)
        account, identities = _account(cfg), mbx.own_addresses(cfg)
        state.update(compte=account, boite=folder, uidvalidity=str(getattr(box, 'validity', '') or ''))
        work, labels = _work_rows(desk), _labels(desk)
        rows = []
        state['non_examines'] += max(0, len(uids) - MAX_SCANNED)
        for uid in uids[:MAX_SCANNED]:
            if len(rows) >= MAX_LISTED:
                state['non_examines'] += 1
                state['limite_atteinte'] = True
                continue
            if time.monotonic() > deadline:
                state['non_examines'] += 1
                state['delai_depasse'] = True
                continue
            try:
                mail = box.fetch(folder, uid, headers_only=True)
            except (Stop, OSError, ValueError):
                state['en_echec'] += 1          # compté : la lecture devient partielle, jamais « vide vérifié »
                continue
            state['entetes_lus'] += 1
            if not dans_bornes(mail.timestamp, req['debut'], req['fin']):
                state['filtres'] += 1
                continue
            item = work.get(mail.key(account)) or {}
            mid = item.get('matter') or ''
            if mid in hidden or (matter and mid != matter):
                state['filtres'] += 1
                continue
            sender = ' '.join(str(mail.msg.get('From', '')).split())[:160]
            subject = (' '.join(str(mail.subject or '').split()) or '(sans objet)')[:180]
            if not _correspond(req['filtres'], sender, subject):
                state['filtres'] += 1
                continue
            stamp = mail.timestamp if mail.timestamp.tzinfo else mail.timestamp.replace(tzinfo=timezone.utc)
            rows.append({'uid': str(uid), 'uidvalidity': str(getattr(mail, 'uidvalidity', '') or state['uidvalidity']), 'de': sender, 'objet': subject,
                         'date': stamp.astimezone(ZoneInfo(zone)).isoformat(timespec='minutes'), 'dossier': labels.get(mid, ''), 'matter': mid, 'etat': item.get('state') or '',
                         # 5.6.96 (M02) : envoyé depuis une identité du cabinet et présent dans la boîte de réception : copie, jamais une demande à traiter
                         'copie': folder_key == 'inbox' and bool(set(mbx.addresses(sender)) & identities)})
        rows.sort(key=lambda r: (datetime.fromisoformat(r['date']).timestamp(), int(r['uid'])), reverse=True)   # instant réel : l'heure d'été ne fausse pas l'ordre
        sources = []
        if not just_headers:
            for number, row in enumerate(rows[:limits['bodies']], 1):
                if time.monotonic() > deadline:
                    break
                try:
                    mail = box.fetch(folder, row['uid'])
                    body = str(mail.text or '')
                except Stop:
                    continue
                state['lus'] += 1
                sources.append({'id': 'courriel-%d' % number, 'kind': 'courriel', 'subject': row['objet'], 'from': row['de'], 'date': row['date'], 'matter': row['matter'], 'dossier': row['dossier'],
                                'etat_traitement': row['etat'], 'copie_cabinet': row['copie'], 'path': 'Courriel : ' + row['objet'], 'modified': row['date'],
                                'excerpt': ' '.join(body.split())[:limits['chars']], 'partial': len(body) > limits['chars']})
        partiel = bool(state['en_echec'] or state['non_examines'])
        state.update(consultes=True, trouves=len(rows), affiches=len(rows), complet=not partiel, lignes=rows,
                     statut='partiel' if partiel else ('complet' if rows else 'vide_verifie'), heure_lecture=datetime.now(ZoneInfo(zone)).isoformat(timespec='minutes'))
        if rows:
            listing = '\n'.join('%s | %s | %s%s%s' % (r['date'][:16].replace('T', ' '), r['de'], r['objet'], ' | dossier : ' + r['dossier'] if r['dossier'] else '',
                                                      ' | copie du cabinet' if r['copie'] else '') for r in rows)
            sources.insert(0, {'id': 'courriels-liste', 'kind': 'liste_courriels', 'path': 'Liste des courriels (%s)' % req['periode'], 'excerpt': listing,
                               'partial': partiel, 'matter': matter, 'lignes': rows})
        return sources, state
    except Exception as ex:   # noqa: BLE001 — messagerie injoignable : dit dans la réponse, sans empêcher le reste
        return [], {**state, 'statut': 'indisponible', 'heure_lecture': datetime.now(ZoneInfo(zone)).isoformat(timespec='minutes'), 'erreur': 'messagerie injoignable (%s)' % str(ex)[:60]}
    finally:
        if own and box is not None:
            try:
                box.close()
            except Exception:   # noqa: BLE001
                pass


def _date_fr(value):
    try:
        return date.fromisoformat(str(value)[:10]).strftime('%d/%m/%Y')
    except ValueError:
        return str(value)


def _heure_fr(value):
    text = str(value or '')
    return (text[8:10] + '/' + text[5:7] + ' ' + text[11:16].replace(':', ' h ')) if len(text) >= 16 else text


def _date_heure_fr(value):
    text = str(value or '')
    return (text[8:10] + '/' + text[5:7] + '/' + text[:4] + ' ' + text[11:16]) if len(text) >= 16 else text


def liste_answer(sources, state, voice=False):
    """5.6.96 (M03) : liste simple rendue depuis le connecteur, sans modèle : tableau (date locale, expéditeur, objet, dossier, état) ou phrases
    pour la voix ; statut et compteurs publiés tels quels ; le total IMAP brut n'apparaît jamais. Lecture partielle : heure et « Réessayer »."""
    from .boite5625 import STATUS_LABELS
    liste = next((s for s in sources if s.get('kind') == 'liste_courriels'), None)
    rows = state.get('lignes') or (liste or {}).get('lignes') or []
    boite = 'Messages envoyés' if state.get('dossier_imap') == 'sent' else 'Boîte de réception'
    statut = state.get('statut') or 'indisponible'
    lines = ['%s, %s (du %s au %s, heure de %s).' % (boite, state.get('periode', ''), _date_fr(state.get('du')), _date_fr(state.get('au')), state.get('fuseau', ''))]
    if statut == 'refuse':
        lines.append('Lecture refusée : %s.' % state.get('erreur', ''))
    elif statut == 'indisponible':
        lines.append('La messagerie n’a pas pu être consultée (%s). Aucune conclusion à tirer sur l’absence de courriels. Réessayer dans un instant.' % state.get('erreur', 'motif inconnu'))
    else:
        if rows and voice:
            lines += ['%d. Le %s, de %s : %s%s%s.' % (n, _heure_fr(r['date']), r['de'], r['objet'], ', dossier ' + r['dossier'] if r['dossier'] else '', ', copie du cabinet' if r['copie'] else '')
                      for n, r in enumerate(rows, 1)]
        elif rows:
            lines.append('Date | Expéditeur | Objet | Dossier | État')
            lines += ['%s | %s | %s | %s | %s' % (_date_heure_fr(r['date']), r['de'], r['objet'], r['dossier'] or '—',
                                                 ' ; '.join(x for x in (STATUS_LABELS.get(r['etat'], r['etat']) if r['etat'] else '', 'copie du cabinet' if r['copie'] else '') if x) or '—') for r in rows]
        if statut == 'vide_verifie':
            lines.append('Aucun courriel sur cette période : boîte consultée en entier, %d en-tête(s) lu(s).' % state.get('entetes_lus', 0))
        if state.get('limite_atteinte'):
            lines.append('Liste limitée aux %d courriels les plus récents : d’autres messages de la période ne sont pas affichés ; précisez la période pour les voir.' % state.get('limite', MAX_LISTED))
        if statut == 'partiel':
            motifs = [x for x in ('%d en échec' % state['en_echec'] if state.get('en_echec') else '', 'délai dépassé' if state.get('delai_depasse') else '',
                                  '%d non examiné(s)' % state['non_examines'] if state.get('non_examines') and not state.get('limite_atteinte') else '') if x]
            lines.append('Lecture partielle à %s : %d en-tête(s) lu(s), %d affiché(s)%s. Une liste partielle ne prouve pas l’absence d’autres courriels : Réessayer pour compléter.'
                         % (_heure_fr(state.get('heure_lecture')), state.get('entetes_lus', 0), state.get('affiches', 0), (' ; ' + ', '.join(motifs)) if motifs else ''))
    compteurs = {k: state.get(k, 0) for k in ('entetes_lus', 'en_echec', 'filtres', 'affiches')}
    return {'answer': '\n'.join(lines), 'source_ids': [liste['id']] if liste else [], 'limits': limits_of({'courriels': state}), 'proposed_actions': [],
            'sources': [{'id': liste['id'], 'matter': liste.get('matter', ''), 'path': liste.get('path', ''), 'modified': '', 'excerpt': str(liste.get('excerpt') or '')[:1200], 'lignes': rows}] if liste else [],
            'connecteur': True, 'statut': statut, 'compteurs': compteurs, 'fuseau': state.get('fuseau', ''), 'heure_lecture': state.get('heure_lecture', ''),
            'reessayer': statut in ('partiel', 'indisponible')}


def read_agenda(desk, question, matter='', hidden=(), today=None, owner='cabinet'):
    """Agenda du cabinet (instantané Nextcloud et agenda local) et tâches à échéance sur la période. Lecture seule, sans appel réseau."""
    today = today or today_of(desk, owner)[0]
    start, end, label, _ = period(question, today, forward=True)
    low, high = start.isoformat(), (end + timedelta(days=1)).isoformat()
    labels = _labels(desk)
    rows, state = [], {'consulte': False, 'periode': label, 'du': low, 'au': end.isoformat(), 'trouves': 0}
    from . import vueia5688   # 5.6.88 : aucune ligne d'agenda lue si la catégorie est fermée
    if vueia5688.blocked(desk, 'agenda'):
        return [], {**state, 'refuse': vueia5688.REFUSED}
    fetched = None
    try:
        for r in desk.db.execute('SELECT title, location, starts, ends, matter FROM calendar_cache WHERE starts>=? AND starts<? ORDER BY starts LIMIT 200', (low, high)):
            rows.append((r['starts'], r['ends'], 'Événement', r['title'], r['location'], r['matter']))
        fetched = desk.db.execute('SELECT MAX(fetched) FROM calendar_cache').fetchone()[0]
        state['consulte'] = True
    except Exception:   # noqa: BLE001 — instantané absent : agenda local seul
        pass
    try:
        for r in desk.db.execute("SELECT title, location, starts, ends, matter FROM agenda5634 WHERE status='actif' AND starts>=? AND starts<? ORDER BY starts LIMIT 200", (low, high)):
            rows.append((r['starts'], r['ends'], 'Événement', r['title'], r['location'], r['matter']))
        state['consulte'] = True
    except Exception:   # noqa: BLE001 — table créée à la première ouverture de l'agenda
        pass
    try:
        for r in desk.db.execute("SELECT title, due, matter FROM tasks WHERE status IN ('open','todo') AND due IS NOT NULL AND due>=? AND due<? ORDER BY due LIMIT 100", (low, high)):
            rows.append((r['due'], '', 'Tâche', r['title'], '', r['matter']))
        state['consulte'] = True
    except Exception:   # noqa: BLE001
        pass
    seen, lines = set(), []
    for starts, ends, kind, title, location, mid in sorted(rows, key=lambda x: str(x[0])):
        if mid in hidden or (matter and mid != matter) or (starts, title) in seen:
            continue
        seen.add((starts, title))
        lines.append('%s%s | %s | %s%s%s' % (str(starts)[:16].replace('T', ' '), ' → ' + str(ends)[11:16] if ends else '', kind, title, ' | lieu : ' + location if location else '',
                                             ' | dossier : ' + labels[mid] if mid in labels else ''))
    state.update(trouves=len(lines), instantane=fetched or '')
    if fetched:
        try:
            state['instantane_ancien'] = datetime.fromisoformat(fetched.replace('Z', '+00:00')) < datetime.now(timezone.utc) - timedelta(minutes=30)
        except ValueError:
            state['instantane_ancien'] = True
    if not lines:
        return [], state
    return [{'id': 'agenda-liste', 'kind': 'agenda', 'path': 'Agenda (%s)' % label, 'excerpt': '\n'.join(lines[:120]), 'partial': len(lines) > 120, 'matter': matter}], state


def gather(desk, question, matter='', hidden=(), previous='', voice=False, box=None, owner='cabinet', just_headers=False):
    """Sources du cabinet demandées par la question, et état de ce qui a été consulté, à transmettre au modèle.
    5.6.96 (M04) : ``owner`` est la personne qui demande (fuseau, compte) ; « Et ceux d'hier ? » reprend la boîte de la question précédente."""
    today, zone = today_of(desk, owner)
    mail, agenda = wants_mail(question), wants_agenda(question)
    dossier = ''
    if not mail and not agenda:
        mail, agenda = follow_up(question, previous)
        if mail and SENT.search(_text(previous)) and not SENT.search(_text(question)):
            dossier = 'sent'
    days = WEEKDAYS[today.weekday()]
    access = {'date_du_jour': today.isoformat(), 'jour': days, 'fuseau': zone}
    sources = []
    if mail:
        found, state = read_mail(desk, question, matter, hidden, voice, box, today, owner, just_headers=just_headers, dossier=dossier)
        sources += found
        # les lignes restent dans la source « liste_courriels » ; l'état transmis au modèle ne les répète pas
        access['courriels'] = state if just_headers else {k: v for k, v in state.items() if k != 'lignes'}
    if agenda:
        found, state = read_agenda(desk, question, matter, hidden, today, owner)
        sources += found
        access['agenda'] = state
    return sources, access, mail or agenda


def limits_of(access):
    """Ce qui a été consulté, en clair, sous la réponse : l'avocat sait si la liste est complète."""
    out = []
    mail = access.get('courriels')
    if mail:
        if mail.get('erreur'):
            out.append('Courriels non consultés : %s. Aucune conclusion à tirer sur leur absence.' % mail['erreur'])
        else:
            # 5.6.96 (M03) : seuls les compteurs autorisés sont publiés (jamais le total IMAP brut) ; un échec d'en-tête rend la liste partielle
            detail = ''.join(x for x in (', %d en-tête(s) en échec' % mail['en_echec'] if mail.get('en_echec') else '', ', liste limitée à %d' % mail.get('limite', MAX_LISTED) if mail.get('limite_atteinte') else '',
                                         ', délai dépassé' if mail.get('delai_depasse') else ''))
            out.append('Courriels consultés (%s, heure de %s) : %d trouvé(s), %d lu(s)%s%s.' % (mail['periode'], mail.get('fuseau', ''), mail.get('trouves', 0), mail.get('lus', 0), detail,
                                                                                        '' if mail.get('complet', True) else ' ; liste partielle'))
    agenda = access.get('agenda')
    if agenda and agenda.get('refuse'):
        out.append('Agenda non consulté : %s.' % agenda['refuse'])
    elif agenda and not agenda.get('consulte'):
        out.append('Agenda non consulté : instantané indisponible.')
    elif agenda:
        out.append('Agenda consulté (%s) : %d élément(s)%s.' % (agenda['periode'], agenda.get('trouves', 0), ' ; dernier relevé ancien, à vérifier' if agenda.get('instantane_ancien') else ''))
    return out


def portfolio_weights(desk):
    """Un dossier actif passe devant un dossier dormant ou archivé, à pertinence comparable ; aucun dossier n'est exclu."""
    try:
        return {r[0]: PORTFOLIO_WEIGHT.get(r[1], 1.0) for r in desk.db.execute('SELECT matter, state FROM matter_portfolio')}
    except Exception:   # noqa: BLE001 — portefeuille pas encore calculé
        return {}


def freshness(modified, recent=False, now=None):
    """Facteur de fraîcheur : complément du classement, jamais un filtre. Plus marqué quand la question porte sur du récent."""
    try:
        stamp = datetime.fromisoformat(str(modified).replace('Z', '+00:00'))
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        age = max(0.0, ((now or datetime.now(timezone.utc)) - stamp).total_seconds() / 86400)
    except (TypeError, ValueError):
        return 1.0
    return 1 + (1.0 * math.exp(-age / 90) if recent else 0.35 * math.exp(-age / 365))


def wants_recent(question):
    return bool(RECENT.search(_text(question)))


def fallback_answer(sources, access):
    """Modèle occupé : la liste lue est rendue telle quelle, sans rédaction, annoncée comme telle."""
    body = '\n\n'.join(str(s.get('excerpt') or '') for s in sources if s['kind'] in ('liste_courriels', 'agenda'))
    return {'answer': 'Le modèle d’IA est occupé ou trop lent : je n’ai pas pu rédiger de synthèse. Voici la liste relevée, sans analyse :\n\n' + body,
            'source_ids': [], 'limits': limits_of(access), 'proposed_actions': [], 'repli': True,
            'sources': [{'id': s['id'], 'matter': s.get('matter', ''), 'path': s.get('path', ''), 'modified': s.get('modified', ''), 'excerpt': str(s.get('excerpt') or '')[:1200]} for s in sources]}
