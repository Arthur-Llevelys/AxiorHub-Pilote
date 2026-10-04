"""Procedural notices (avis de renvoi, convocation, calendrier de procédure...) - AxiorHub 4.4.0.

When a new document whose name looks like a procedural notice reaches a matter
folder, the agent reads it, extracts the dates that matter with deterministic
rules (every date is checked against the text), records them in the Nextcloud
calendar and prepares an information e-mail in the Drafts folder. Nothing is
sent. Dates that cannot be grounded in the document are never invented.
"""
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata
from zoneinfo import ZoneInfo

from .common import Stop, digest, load_matters

NOTICE_KINDS = (
    ('renvoi', r'renvoi'),
    ('convocation', r'convocation|avis\s+d.?audience|avis\s+de\s+fixation|fixation|date\s+de\s+plaidoirie|appel\s+des\s+causes'),
    ('calendrier', r'calendrier\s+de\s+proc[eé]dure|avis\s+de\s+calendrier|mise\s+en\s+[eé]tat|avis\s+de\s+cl[oô]ture|ordonnance\s+de\s+cl[oô]ture|cl[oô]ture'),
    ('delibere', r'd[eé]lib[eé]r[eé]|prorogation'),
)
NOTICE_EXTENSIONS = {'.pdf', '.docx', '.odt', '.txt', '.png', '.jpg', '.jpeg', '.tif', '.tiff'}
MONTHS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8,
          'septembre': 9, 'octobre': 10, 'novembre': 11, 'decembre': 12}
MONTH_NAMES = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre',
               'novembre', 'décembre']
DAY_NAMES = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
DATE_TEXT = re.compile(r'(?<![\d/])(\d{1,2})(?:er)?\s+(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|'
                       r'octobre|novembre|decembre)(?:\s+(\d{4}))?')
DATE_NUM = re.compile(r'(?<![\d/.])(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})(?![\d/])')
TIME = re.compile(r'(?:\ba\s+|\bvers\s+)?(\d{1,2})\s*(?:h|:)\s*(\d{2})?')
ROLES = (
    ('cloture', r'cloture'),
    ('plaidoirie', r'plaid(?:oiries?|ee?|er|era)\b'),
    ('delibere', r'delibere|mise a disposition|jugement sera rendu|decision sera rendue|prorog'),
    ('conclusions', r'conclusions?|conclure|ecritures|communication de pieces|bordereau'),
    ('renvoi', r'renvoi|renvoye|prochaine audience|audience de renvoi'),
    ('audience', r'audience|comparution|convoqu|comparaitre|mise en etat'),
)
SPECIFIC = {'cloture', 'plaidoirie', 'delibere', 'conclusions', 'renvoi'}
TITLES = {'cloture': 'Clôture de la procédure', 'plaidoirie': 'Audience de plaidoirie',
          'delibere': 'Délibéré', 'conclusions': 'Dépôt des conclusions',
          'renvoi': 'Audience de renvoi', 'audience': 'Audience'}
ACT_BY = re.compile(r"(?:conclusions?|[ée]critures|conclure)\s+(?:de|du|des|pour|par|à)?\s*(?:la\s+partie\s+)?"
                    r"(?:(?:Ma[iî]tre|Me|M\.|Mme|Madame|Monsieur)\s+)([A-ZÀ-Ý][\w'’\-]+(?:\s+[A-ZÀ-Ý][\w'’\-]+){0,3})")
ACT_BY_2 = re.compile(r"(?:Ma[iî]tre|Me)\s+([A-ZÀ-Ý][\w'’\-]+(?:\s+[A-ZÀ-Ý][\w'’\-]+){0,3})\s*(?:,|\s)(?:est\s+invit[ée]e?\s+[àa]|devra|doit)\s+conclure")
RG = re.compile(r'\bR\.?\s?G\.?\s*(?:n[°o]\s*)?:?\s*(\d{2,4}[/.]\d{3,6})', re.I)
CASE_ROLES = ('postulant', 'plaidant', 'defendeur', 'demandeur', 'conseil')
ROLE_LABELS = {'postulant': 'Postulant (confrère plaidant ou dominus litis à informer)',
               'plaidant': 'Plaidant', 'defendeur': 'Défendeur (constitution à préparer)',
               'demandeur': 'Demandeur', 'conseil': 'Conseil (sans plaidoirie)'}


def fold_chars(text):
    """Accent-insensitive lower-casing that keeps string positions."""
    return ''.join(unicodedata.normalize('NFD', c)[0].lower() for c in text)


def classify_name(name):
    base = fold_chars(PurePosixPath(name).stem)
    for kind, pattern in NOTICE_KINDS:
        if re.search(fold_chars(pattern), base):
            return kind
    return ''


def looks_like_notice(path):
    p = PurePosixPath(path)
    return p.suffix.lower() in NOTICE_EXTENSIONS and bool(classify_name(p.name))


def fr_date(d):
    return '%s %d %s %d' % (DAY_NAMES[d.weekday()], d.day, MONTH_NAMES[d.month - 1], d.year)


def filename_info(name, matter_label=''):
    """Parse 'XXX - YYY - 20261002 - Avis de renvoi.pdf' into a label and document date."""
    stem = PurePosixPath(name).stem
    parts = [p.strip() for p in stem.split(' - ') if p.strip()]
    stamp = None
    kept = []
    for part in parts:
        match = re.fullmatch(r'(\d{4})(\d{2})(\d{2})', part)
        if match:
            try:
                stamp = date(int(match[1]), int(match[2]), int(match[3]))
            except ValueError:
                pass
            continue
        kept.append(part)
    title = kept[-1] if kept else stem
    label = ' / '.join(kept[:-1]) if len(kept) > 1 else matter_label
    return {'label': label or matter_label, 'title': title, 'date': stamp}


def _sentence_window(folded, start, end):
    before = folded[max(0, start - 160):start]
    after = folded[end:end + 90]
    for cut in ('. ', '\n\n', ';'):
        if cut in before:
            before = before.rsplit(cut, 1)[1]
    for cut in ('. ', '\n\n', ';'):
        if cut in after:
            after = after.split(cut, 1)[0]
    return before + ' ' + after


def _roles_in(window):
    return [role for role, pattern in ROLES if re.search(pattern, window)]


def extract_notice(text, doc_date=None, today=None, kind_hint=''):
    """Return the events found in ``text``. Every returned date occurs literally in the text."""
    today = today or date.today()
    reference = doc_date or today
    folded = fold_chars(text)
    found = {}
    for match in list(DATE_TEXT.finditer(folded)) + list(DATE_NUM.finditer(folded)):
        explicit_year = True
        try:
            if match.re is DATE_TEXT:
                day, month = int(match[1]), MONTHS[match[2]]
                if match[3]:
                    year = int(match[3])
                else:
                    explicit_year = False
                    year = reference.year
                    candidate = date(year, month, day)
                    if candidate < reference - timedelta(days=1):
                        year += 1
            else:
                day, month, year = int(match[1]), int(match[2]), int(match[3])
                if year < 100:
                    year += 2000
            when = date(year, month, day)
        except (ValueError, KeyError):
            continue
        if when < reference - timedelta(days=1) or when > reference + timedelta(days=900):
            continue
        window = _sentence_window(folded, match.start(), match.end())
        roles = _roles_in(window)
        tail = folded[match.end():match.end() + 28]
        clock = TIME.search(tail)
        when_time = ''
        if clock and clock.start() <= 14:
            hour, minute = int(clock[1]), int(clock[2] or 0)
            if 6 <= hour <= 20 and minute < 60:
                when_time = '%02d:%02d' % (hour, minute)
        entry = found.setdefault(when, {'date': when, 'time': '', 'roles': [], 'explicit_year': explicit_year,
                                        'context': '', 'position': match.start()})
        entry['explicit_year'] = entry['explicit_year'] and explicit_year
        for role in roles:
            if role not in entry['roles']:
                entry['roles'].append(role)
        if when_time and not entry['time']:
            entry['time'] = when_time
        if not entry['context']:
            entry['context'] = re.sub(r'\s+', ' ', text[max(0, match.start() - 120):match.end() + 80]).strip()
    events = []
    for entry in sorted(found.values(), key=lambda x: x['date']):
        roles = [r for r in entry['roles']]
        specific = [r for r in roles if r in SPECIFIC]
        if not roles and kind_hint not in ('renvoi', 'convocation', 'calendrier', 'delibere'):
            continue
        entry['role'] = specific[0] if specific else (roles[0] if roles else 'audience')
        entry['specific'] = bool(specific)
        events.append(entry)
    # In a simple "renvoi" notice only dates tied to the renvoi or the stated action matter.
    if kind_hint in ('renvoi', 'convocation'):
        strong = [e for e in events if e['specific'] or 'audience' in e['roles']]
        events = strong or events
    events = events[:8]
    act = ACT_BY.search(text) or ACT_BY_2.search(text)
    rg = RG.search(text)
    jurisdiction = ''
    for line in text.splitlines()[:40]:
        if re.search(r'(?i)tribunal|cour d.?appel|conseil de prud|chambre', line) and 6 < len(line.strip()) < 120:
            jurisdiction = line.strip()
            break
    return {'events': events, 'act_by': act[1].strip() if act else '', 'rg': rg[1] if rg else '',
            'jurisdiction': jurisdiction}


def confidence(events, kind_hint):
    if not events:
        return 'none'
    best = max(events, key=lambda e: (e['specific'], e['explicit_year']))
    if best['specific'] and best['explicit_year']:
        return 'high'
    if best['specific'] or best['explicit_year']:
        return 'medium'
    return 'low'


def describe(event, act_by=''):
    title = TITLES.get(event['role'], 'Échéance')
    roles = event['roles']
    if event['role'] == 'renvoi' and 'conclusions' in roles:
        title = 'Renvoi – dépôt des conclusions'
    elif event['role'] == 'conclusions' and 'renvoi' in roles:
        title = 'Renvoi – dépôt des conclusions'
    if 'conclusions' in roles and act_by:
        title += ' de Me ' + act_by
    return title


# ----------------------------------------------------------------- settings
def matter_profile(desk, matter_id):
    value = desk.settings('matter440:' + str(matter_id), {}) or {}
    role = value.get('role', '')
    return {'role': role if role in CASE_ROLES else '', 'partner_email': value.get('partner_email', ''),
            'partner_name': value.get('partner_name', '')}


def save_matter_profile(desk, matter_id, role, partner_email='', partner_name=''):
    if not any(m['id'] == matter_id for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    if role and role not in CASE_ROLES:
        raise Stop('role_dossier_invalide')
    partner_email = str(partner_email or '').strip()
    if partner_email and not re.fullmatch(r'[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+', partner_email):
        raise Stop('adresse_confrere_invalide')
    partner_name = re.sub(r'[\r\n<>"]', ' ', str(partner_name or '')).strip()[:120]
    desk.setting('matter440:' + matter_id, {'role': role, 'partner_email': partner_email, 'partner_name': partner_name})
    desk.audit('profil_dossier_440_enregistre', {'matter': matter_id, 'role': role,
                                                 'partner_configured': bool(partner_email)})
    return {'saved': True}


# ------------------------------------------------------------------- storage
def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS notices440(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, path TEXT NOT NULL, etag TEXT NOT NULL,
      status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS notices440_matter ON notices440(matter,updated);
    ''')
    desk.db.commit()


def enabled(desk):
    return bool(desk.settings('automation:notices440_enabled', True))


def observe_inventory(desk, matter_id, items, today=None, limit=10):
    """Queue analysis for new procedural notices found in an inventory scan."""
    if not enabled(desk):
        return 0
    ensure_schema(desk)
    today = today or datetime.now(timezone.utc)
    lookback = int(desk.c.get('notices', {}).get('lookback_days', 21)) if isinstance(desk.c.get('notices'), dict) else 21
    queued = 0
    for path, info in sorted(items.items()):
        if not looks_like_notice(path):
            continue
        etag = str(info.get('etag', ''))
        ident = digest('notice440|' + path + '|' + etag)
        if desk.db.execute('SELECT 1 FROM notices440 WHERE id=?', (ident,)).fetchone():
            continue
        stale = False
        try:
            from email.utils import parsedate_to_datetime
            stamp = parsedate_to_datetime(str(info.get('modified', '')))
            stale = stamp < today - timedelta(days=lookback)
        except (TypeError, ValueError):
            stale = False
        now = desk.now()
        desk.db.execute('INSERT INTO notices440 VALUES(?,?,?,?,?,?,?,?)',
                        (ident, matter_id, path, etag, 'ignored_old' if stale else 'queued', '{}', now, now))
        desk.db.commit()
        if stale:
            continue
        desk.enqueue('analyze_notice440', {'matter': matter_id, 'path': path, 'etag': etag,
                                          'size': int(info.get('size', 0) or 0)}, priority=20)
        queued += 1
        if queued >= limit:
            break
    return queued


# ----------------------------------------------------------------- analysis
def _calendar_has(events, when, tokens):
    for event in events:
        start = str(event.get('start', ''))[:10]
        summary = fold_chars(str(event.get('summary') or event.get('title') or ''))
        if start == when.isoformat() and any(t and fold_chars(t) in summary for t in tokens):
            return True
    return False


def _write_calendar(desk, dav, path, etag, matter, labels, extraction, name):
    cfg = desk.c.get('calendar', {})
    urls = cfg.get('urls', [])
    if not urls or not desk.settings('automation:notice_calendar440', True):
        return [{'date': e['date'].isoformat(), 'title': describe(e, extraction['act_by']),
                 'calendar': 'non_configure'} for e in extraction['events']]
    tz = ZoneInfo(cfg.get('timezone', 'Europe/Paris'))
    first = min(e['date'] for e in extraction['events'])
    last = max(e['date'] for e in extraction['events'])
    try:
        existing = dav.events(urls, datetime.combine(first - timedelta(days=1), datetime.min.time(), tz),
                              datetime.combine(last + timedelta(days=2), datetime.min.time(), tz), cfg.get('timezone', 'Europe/Paris'))
    except Stop:
        existing = []
    tokens = [extraction['rg'], matter.get('client_name', '')] + list(matter.get('aliases', [])) + \
        list(matter.get('references', [])) + [p.strip() for p in labels.split('/')]
    tokens = [x for x in tokens if len(str(x or '')) >= 3]
    out = []
    for event in extraction['events']:
        title = describe(event, extraction['act_by'])
        full_title = (title + ' – ' + labels) if labels else title
        entry = {'date': event['date'].isoformat(), 'time': event['time'], 'title': full_title,
                 'confidence': 'sûre' if event['specific'] and event['explicit_year'] else 'à vérifier'}
        if not (event['specific'] or event['explicit_year']):
            entry['calendar'] = 'ignore_peu_fiable'
            out.append(entry)
            continue
        if _calendar_has(existing, event['date'], tokens):
            entry['calendar'] = 'deja_present'
            out.append(entry)
            continue
        hour, minute = (9, 0)
        if event['time']:
            hour, minute = int(event['time'][:2]), int(event['time'][3:])
        start = datetime(event['date'].year, event['date'].month, event['date'].day, hour, minute, tzinfo=tz)
        description = ('Créé automatiquement par AxiorHub depuis « %s » (%s).%s Contexte : %s' % (
            name, path, '' if event['time'] else ' Heure non précisée : 09:00 par défaut.', event['context'][:300]))
        proposal = digest('notice440|' + path + '|' + etag + '|' + event['date'].isoformat() + '|' + event['role'])
        from . import autonomy480
        if not autonomy480.allows(desk, 'agenda_avis', 'agir'):
            autonomy480.pending_add(desk, 'agenda_avis', matter.get('id', ''), full_title[:200],
                {'uid': proposal, 'title': full_title[:200], 'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat(),
                 'description': description[:1900]})
            entry['calendar'] = 'a_valider'
            out.append(entry)
            continue
        try:
            dav.put_event(urls[0], proposal, full_title[:200], start, start + timedelta(hours=1), description[:1900])
            entry['calendar'] = 'cree'
        except Stop as ex:
            entry['calendar'] = 'deja_present' if str(ex) == 'http_412' else 'erreur:' + str(ex)
        out.append(entry)
    return out


def _choose_recipient(desk, matter, profile):
    own = desk.c['mail']['from_address']
    role = profile['role']
    if role == 'postulant' and profile['partner_email']:
        return profile['partner_email'], profile['partner_name'] or 'Cher Confrère', 'confrere'
    if role == 'postulant':
        partners = [c for c in matter.get('correspondents', []) if c.get('role') == 'tiers']
        if len(partners) == 1:
            return partners[0]['email'], 'Cher Confrère', 'confrere'
    if role in ('plaidant', 'defendeur', 'demandeur', 'conseil'):
        clients = [c for c in matter.get('correspondents', []) if c.get('role') == 'client']
        if len(clients) == 1:
            return clients[0]['email'], 'Madame, Monsieur', 'client'
    return own, 'Maître', 'interne'


def compose(desk, matter, extraction, events, name, labels, audience, greeting, profile):
    main = events[0]
    signature = desk.c['mail'].get('signature', '').strip()
    rg = (' (RG ' + extraction['rg'] + ')') if extraction['rg'] else ''
    juris = ('\nJuridiction : ' + extraction['jurisdiction']) if extraction['jurisdiction'] else ''
    lines = []
    for e in events:
        hour = (' à ' + e['time'].replace(':', 'h')) if e['time'] else ''
        lines.append('– %s : %s%s' % (describe(e, extraction['act_by']), fr_date(e['date']), hour))
    verify = ''
    if any(not (e['specific'] and e['explicit_year']) for e in events):
        verify = '\nCertaines dates sont à vérifier sur l’original.'
    if audience == 'confrere':
        intro = '%s,\n\nJe vous informe que le document « %s » a été déposé au dossier %s%s.' % (
            greeting, name, labels or matter.get('client_name', ''), rg)
        outro = ('Je vous remercie de me confirmer la suite que vous souhaitez donner, notamment pour la '
                 'préparation des écritures et des pièces.\n\nConfraternellement,')
    elif audience == 'client':
        intro = '%s,\n\nJe vous informe que le tribunal a adressé un document relatif à votre dossier %s%s (« %s »).' % (
            greeting, labels or matter.get('client_name', ''), rg, name)
        outro = ('Je reviens vers vous pour préciser les démarches à prévoir. N’hésitez pas à me contacter pour '
                 'toute question.\n\nBien cordialement,')
    else:
        intro = ('Note interne – aucun destinataire n’est paramétré pour ce dossier. Le document « %s » a été '
                 'détecté dans le dossier %s%s.' % (name, labels or matter.get('client_name', ''), rg))
        outro = ('Choisissez le rôle du cabinet et le confrère à informer dans Paramètres → Rôle par dossier, '
                 'puis adaptez ce message avant de l’envoyer.')
    body = intro + juris + '\n\nDates retenues :\n' + '\n'.join(lines) + verify + '\n\n' + outro
    if audience != 'interne' and signature:
        body += '\n' + signature
    subject = '%s – %s – %s' % ('Avis de renvoi' if main['role'] in ('renvoi', 'conclusions') else 'Avis de procédure',
                                labels or matter.get('client_name', matter.get('id', '')), fr_date(main['date']))
    if audience == 'interne':
        subject = '[À traiter] ' + subject
    return subject, body


def build_draft(cfg_mail, to_addr, subject, body, key):
    msg = EmailMessage(policy=policy.SMTP)
    msg['From'] = formataddr((cfg_mail['from_name'], cfg_mail['from_address']))
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg['Date'] = format_datetime(datetime.now(timezone.utc))
    msg['Message-ID'] = '<axiorhub-' + key + '@mail-agent.local>'
    msg['X-AxiorHub-Draft-Key'] = key
    msg.set_content(body.strip() + '\n')
    return msg


def analyze(desk, args, dav=None, mailbox_factory=None, extractor=None):
    """Job body. Idempotent per (path, ETag)."""
    ensure_schema(desk)
    path, etag, mid = str(args.get('path', '')), str(args.get('etag', '')), str(args.get('matter', ''))
    matter = next((m for m in load_matters(desk.c) if m['id'] == mid), None)
    if not matter or not looks_like_notice(path):
        raise Stop('avis_ou_dossier_invalide')
    ident = digest('notice440|' + path + '|' + etag)
    row = desk.db.execute('SELECT * FROM notices440 WHERE id=?', (ident,)).fetchone()
    if row and row['status'] == 'done':
        return json.loads(row['result'])
    was_uncertain = bool(row and row['status'] == 'draft_uncertain')
    if not row:
        now = desk.now()
        desk.db.execute('INSERT INTO notices440 VALUES(?,?,?,?,?,?,?,?)', (ident, mid, path, etag, 'running', '{}', now, now))
    else:
        desk.db.execute("UPDATE notices440 SET status='running',updated=? WHERE id=?", (desk.now(), ident))
    desk.db.commit()
    from .dav import DAV
    client = dav or DAV(desk.c['nextcloud'])
    name = PurePosixPath(path).name
    stat = client.stat(path) if hasattr(client, 'stat') else {'path': path, 'etag': etag, 'size': int(args.get('size', 0))}
    raw = client.download(stat)
    if extractor is None:
        from .documents import extract
        extractor = lambda data, filename: extract(data, filename, desk.c.get('documents', {}))
    text = extractor(raw, name)
    info = filename_info(name, matter.get('client_name', ''))
    kind = classify_name(name)
    extraction = extract_notice(text, info['date'], kind_hint=kind)
    level = confidence(extraction['events'], kind)
    if level == 'none':
        result = {'status': 'blocked', 'notice': kind, 'matter': mid, 'path': path,
                  'message': 'Aucune date exploitable n’a été trouvée dans « %s ». Rien n’a été inscrit à l’agenda.' % name}
        desk.db.execute("UPDATE notices440 SET status='done',result=?,updated=? WHERE id=?",
                        (json.dumps(result, ensure_ascii=False), desk.now(), ident))
        desk.db.commit()
        return result
    labels = info['label'] or matter.get('client_name', '')
    calendar = _write_calendar(desk, client, path, etag, matter, labels, extraction, name)
    profile = matter_profile(desk, mid)
    recipient, greeting, audience = _choose_recipient(desk, matter, profile)
    usable = [e for e in extraction['events'] if e['specific'] or e['explicit_year']] or extraction['events']
    subject, body = compose(desk, matter, extraction, usable, name, labels, audience, greeting, profile)
    key = digest('notice-draft440|' + ident)
    draft = build_draft(desk.c['mail'], recipient, subject, body, key)
    result = {'notice': kind, 'matter': mid, 'path': path, 'confidence': level, 'calendar': calendar,
              'recipient_kind': audience, 'subject': subject, 'act_by': extraction['act_by'], 'rg': extraction['rg']}
    allowed = (desk.c.get('mode') == 'drafts' and
               desk.settings('automation:automatic_mail_drafts_enabled',
                             desk.c.get('orchestrator', {}).get('automatic_mail_drafts_enabled', True)))
    if not allowed:
        result.update({'status': 'prepared', 'projet_prepare': True, 'proposed_body': body,
                       'message': 'Dates traitées ; le dépôt automatique du brouillon est désactivé dans vos réglages.'})
    else:
        factory = mailbox_factory
        if factory is None:
            from .mailbox import Mailbox
            factory = Mailbox
        box = factory(desk.c['mail'])
        try:
            if box.find_own_draft(draft['Message-ID']):
                verified = box.verify_draft(draft)
            else:
                if was_uncertain:
                    raise Stop('depot_incertain_verifier_brouillons')
                desk.db.execute("UPDATE notices440 SET status='draft_uncertain',updated=? WHERE id=?", (desk.now(), ident))
                desk.db.commit()
                box.append_draft(draft)
                verified = box.verify_draft(draft)
        finally:
            box.close()
        created = [c for c in calendar if c.get('calendar') == 'cree']
        result.update({'brouillon_imap': 'verifie', 'draft_verified': verified,
                       'message': 'Avis analysé : %d date(s) retenue(s), %d inscrite(s) à l’agenda, brouillon %s déposé dans %s.' % (
                           len(usable), len(created), 'interne' if audience == 'interne' else 'd’information',
                           desk.c['mail']['drafts'])})
    desk.db.execute("UPDATE notices440 SET status='done',result=?,updated=? WHERE id=?",
                    (json.dumps(result, ensure_ascii=False, default=str), desk.now(), ident))
    desk.db.commit()
    desk.audit('avis_procedure_440_traite', {'matter': mid, 'path': path, 'events': len(usable),
                                              'draft': bool(result.get('brouillon_imap')), 'recipient': audience})
    return result


def maybe_constitution(desk, matter_id, extraction_kind, path):
    """For a defendant, a convocation triggers a prudent constitution project job."""
    profile = matter_profile(desk, matter_id)
    if profile['role'] != 'defendeur' or extraction_kind not in ('convocation',):
        return 0
    return desk.enqueue('prepare_document_project', {
        'matter': matter_id, 'document_type': 'courrier', 'automatic': 'yes',
        'trigger_kind': 'audience_entrante440', 'trigger_reason': 'Convocation reçue : ' + PurePosixPath(path).name,
        'instruction': 'Préparer un projet d’acte de constitution d’avocat pour le défendeur, à partir des '
                       'références de la juridiction, des parties et de l’audience indiquées dans le document. '
                       'Aucune mention non sourcée ; laisser entre crochets toute information manquante.'}, priority=20)
