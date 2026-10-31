"""5.6.83 : la discussion lit réellement les courriels et l'agenda du cabinet, et choisit mieux ses documents.

Avant : sans dossier sélectionné, le modèle ne recevait que des extraits de documents, sans la date du jour. « Résume mes mails d'aujourd'hui »
partait donc en recherche plein texte sur les mots de la question et ramenait des pièces sans rapport. Ici, l'intention est reconnue sans appel au modèle ;
les courriels sont lus en lecture seule (``BODY.PEEK`` : rien n'est marqué comme lu) et l'agenda vient de l'instantané local. Le contenu reste une donnée
non fiable : le bouclier 5.6.59 s'applique comme aux pièces. Un courriel ou un événement d'un dossier réservé à d'autres membres n'est jamais transmis.
Si la messagerie ne répond pas, la réponse le dit : aucune absence de courriel n'est déduite d'une lecture échouée."""
from datetime import date, datetime, timedelta, timezone
import math
import re
import time

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
                     'concernant propos sujet').split()) | frozenset(('mail mails email emails courriel courriels messagerie boite reception message messages agenda agendas calendrier calendriers rendez vous rdv '
                                                                     'evenement evenements document documents piece pieces fichier fichiers dossier dossiers').split())
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
        return today - timedelta(days=min(int(match.group(1)), 90)), today, match.group(0), True
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
    return today - timedelta(days=7), today, 'les sept derniers jours', False


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


def read_mail(desk, question, matter='', hidden=(), voice=False, box=None, today=None, owner='cabinet'):
    """Courriels de la période demandée, du plus récent au plus ancien : liste complète bornée et texte des plus récents.
    Retourne (sources, état). Une panne de messagerie est rendue dans l'état, jamais une liste vide présentée comme une absence de courriel."""
    from . import mailbox as mbx
    from .boite5625 import _account
    cfg = desk.c.get('mail') or {}
    today = today or today_of(desk, owner)[0]
    start, end, label, explicit = period(question, today)
    terms = focus_terms(question)
    state = {'consultes': False, 'periode': label, 'du': start.isoformat(), 'au': end.isoformat(), 'trouves': 0, 'lus': 0}
    if not cfg.get('host'):
        return [], {**state, 'erreur': 'messagerie non configurée'}
    limits = LIMITS['voice' if voice else 'assistant']
    deadline = time.monotonic() + limits['seconds']
    folder_key = 'sent' if SENT.search(_text(question)) else 'inbox'
    own = box is None
    try:
        box = box or mbx.Mailbox(cfg)
        folder = cfg.get(folder_key) or cfg.get('inbox') or 'INBOX'
        since = start if explicit or not terms else today - timedelta(days=365)     # un nom sans date : toute l'année écoulée
        state['periode'] = label if since == start else 'les douze derniers mois'
        criteria = ['UNDELETED', 'SINCE', _imap_day(since)]
        if explicit:
            criteria += ['BEFORE', _imap_day(end + timedelta(days=1))]
        uids = sorted(box.search(folder, *criteria), key=int, reverse=True)
        account = _account(cfg)
        work, labels = _work_rows(desk), _labels(desk)
        rows, skipped_time = [], 0
        for uid in uids[:400]:
            if time.monotonic() > deadline or len(rows) >= MAX_LISTED:
                skipped_time += 1
                continue
            try:
                mail = box.fetch(folder, uid, headers_only=True)
            except Stop:
                continue
            item = work.get(mail.key(account)) or {}
            mid = item.get('matter') or ''
            if mid in hidden or (matter and mid != matter):
                continue
            sender = str(mail.msg.get('From', ''))[:160]
            subject = (mail.subject or '(sans objet)')[:180]
            haystack = fold(sender + ' ' + subject)
            if terms and not explicit and not any(t in haystack for t in terms):
                continue
            rows.append({'uid': uid, 'de': sender, 'objet': subject, 'date': mail.timestamp.isoformat(), 'dossier': labels.get(mid, ''), 'matter': mid, 'etat': item.get('state') or '',
                         'score': sum(1 for t in terms if t in haystack)})
        if terms and explicit and any(r['score'] for r in rows):       # période ET nom : ceux qui correspondent d'abord
            rows.sort(key=lambda r: -r['score'])
        sources = []
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
                            'etat_traitement': row['etat'], 'path': 'Courriel : ' + row['objet'], 'modified': row['date'], 'excerpt': ' '.join(body.split())[:limits['chars']],
                            'partial': len(body) > limits['chars']})
        state.update(consultes=True, trouves=len(rows), total_periode=len(uids), complet=not skipped_time and len(uids) <= 400 and len(rows) < MAX_LISTED)
        if rows:
            listing = '\n'.join('%s | %s | %s%s' % (r['date'][:16].replace('T', ' '), r['de'], r['objet'], ' | dossier : ' + r['dossier'] if r['dossier'] else '') for r in rows)
            sources.insert(0, {'id': 'courriels-liste', 'kind': 'liste_courriels', 'path': 'Liste des courriels (%s)' % label, 'excerpt': listing, 'partial': not state['complet'], 'matter': matter})
        return sources, state
    except Exception as ex:   # noqa: BLE001 — messagerie injoignable : dit dans la réponse, sans empêcher le reste
        return [], {**state, 'erreur': 'messagerie injoignable (%s)' % str(ex)[:60]}
    finally:
        if own and box is not None:
            try:
                box.close()
            except Exception:   # noqa: BLE001
                pass


def read_agenda(desk, question, matter='', hidden=(), today=None, owner='cabinet'):
    """Agenda du cabinet (instantané Nextcloud et agenda local) et tâches à échéance sur la période. Lecture seule, sans appel réseau."""
    today = today or today_of(desk, owner)[0]
    start, end, label, _ = period(question, today, forward=True)
    low, high = start.isoformat(), (end + timedelta(days=1)).isoformat()
    labels = _labels(desk)
    rows, state = [], {'consulte': False, 'periode': label, 'du': low, 'au': end.isoformat(), 'trouves': 0}
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


def gather(desk, question, matter='', hidden=(), previous='', voice=False, box=None, owner='cabinet'):
    """Sources du cabinet demandées par la question, et état de ce qui a été consulté, à transmettre au modèle."""
    today, zone = today_of(desk, owner)
    mail, agenda = wants_mail(question), wants_agenda(question)
    if not mail and not agenda:
        mail, agenda = follow_up(question, previous)
    days = WEEKDAYS[today.weekday()]
    access = {'date_du_jour': today.isoformat(), 'jour': days, 'fuseau': zone}
    sources = []
    if mail:
        found, state = read_mail(desk, question, matter, hidden, voice, box, today, owner)
        sources += found
        access['courriels'] = state
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
            out.append('Courriels consultés (%s) : %d trouvé(s), %d lu(s)%s.' % (mail['periode'], mail.get('trouves', 0), mail.get('lus', 0), '' if mail.get('complet', True) else ', liste partielle'))
    agenda = access.get('agenda')
    if agenda and not agenda.get('consulte'):
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
