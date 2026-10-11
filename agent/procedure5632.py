"""5.6.32 (cahier des charges additionnel, R15 / DP01–DP05) : documents de procédure → propositions d'agenda → brouillons par destinataire.

Mission ponctuelle intégrée à la page du dossier (aucune rubrique nouvelle, aucune règle permanente à créer) :
1. lecture complète des documents choisis (pages, empreinte) et extraction des dates SOURCÉES par ``procedure568.source_events`` :
   année explicite exigée, dates annulées ou anciennes conservées pour contrôle sans inscription, date passée jamais inscrite d'office,
   durée de rappel signalée comme telle (jamais une durée d'audience prouvée) ;
2. une proposition par événement : Ajouter, Ignorer ou Préciser, avec les agendas cibles choisis parmi ceux autorisés ;
3. après le choix de l'avocat seulement, écriture par ``calendar568.deposit`` (relecture, idempotence, aucun participant) — un effet par
   cible : Nextcloud réussi et Google en échec donne une inscription partielle, reprise sur la seule cible en échec ;
4. un brouillon par destinataire (client, confrère) : contenu adapté au rôle, aucune information interne vers l'avocat adverse ; le texte
   n'affirme une inscription que si sa preuve existe ; dépôt IMAP relu, ou conservation locale signalée si la messagerie est indisponible.
Aucun courriel ne part, aucune pièce n'est déplacée.
"""
from datetime import datetime, timezone
import base64
import hashlib
import json
import re

from .common import Stop, load_matters, matter_display

ACTIONS = ('add', 'ignore', 'clarify')
ROLES = ('client', 'confrere')


def _now():
    return datetime.now(timezone.utc).isoformat()


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS dp_missions5632(id TEXT PRIMARY KEY, owner TEXT NOT NULL, matter TEXT NOT NULL, instruction TEXT NOT NULL,
      sources TEXT NOT NULL, warnings TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS dp_events5632(mission TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, decision TEXT NOT NULL DEFAULT 'pending',
      targets TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '', PRIMARY KEY(mission, key));
    CREATE TABLE IF NOT EXISTS dp_effects5632(mission TEXT NOT NULL, key TEXT NOT NULL, target TEXT NOT NULL, state TEXT NOT NULL,
      proof TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL, PRIMARY KEY(mission, key, target));
    CREATE TABLE IF NOT EXISTS dp_drafts5632(mission TEXT NOT NULL, role TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
      body TEXT NOT NULL, state TEXT NOT NULL, proof TEXT NOT NULL DEFAULT '{}', updated TEXT NOT NULL, PRIMARY KEY(mission, role, recipient));
    ''')


def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if x['id'] == str(mid or '')), None)
    if not m:
        raise Stop('dossier_introuvable')
    return m


def _read(desk, path):
    """Lecture d'une pièce du dossier (racines autorisées) : pages avec libellé et mode d'extraction, empreinte du contenu."""
    from .dav import DAV
    from .documents import extract_pages
    from .web440 import allowed_path
    path = allowed_path(desk, path)
    client = DAV(desk.c['nextcloud'])
    meta = client.stat(path)
    raw = client.download(meta)
    pages = extract_pages(raw, path.rsplit('/', 1)[-1], desk.c)
    return pages, hashlib.sha256(raw).hexdigest(), meta.get('etag', '')


def start(desk, owner, matter_id, paths, instruction='', reader=None):
    """Mission ponctuelle : lecture de tous les documents, dates sourcées, avertissements visibles ; rien n'est écrit dans l'agenda."""
    schema(desk)
    m = _matter(desk, matter_id)
    paths = [str(p) for p in (paths or []) if str(p).strip()][:20]
    if not paths:
        raise Stop('documents_a_lire_requis')
    from .procedure568 import source_events
    desk.document_owner568 = owner
    events, warnings, sources = [], [], []
    for path in paths:
        pages, sha, etag = (reader or _read)(desk, path)
        sources.append({'path': path, 'sha256': sha, 'pages': len(pages), 'ocr': any(p.get('extraction') == 'ocr' for p in pages)})
        found = source_events(desk, pages, m, path, sha, persist=False)
        events += found.get('events', []) if isinstance(found, dict) else []
        warnings += found.get('warnings', []) if isinstance(found, dict) else []
        if any(p.get('extraction') == 'ocr' for p in pages):
            warnings.append('Lecture par reconnaissance de caractères (OCR) pour ' + path.rsplit('/', 1)[-1] + ' : contrôler les dates sur la pièce.')
    mid = 'dp-' + hashlib.sha256(json.dumps([owner, m['id'], sorted(s['sha256'] for s in sources), instruction], ensure_ascii=False).encode()).hexdigest()[:20]
    if desk.db.execute('SELECT 1 FROM dp_missions5632 WHERE id=?', (mid,)).fetchone():
        return view(desk, owner, mid)   # même demande sur les mêmes versions : la mission existante est reprise, sans doublon
    now = _now()
    desk.db.execute('INSERT INTO dp_missions5632 VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (mid, owner, m['id'], str(instruction or '')[:4000], json.dumps(sources, ensure_ascii=False), json.dumps(list(dict.fromkeys(warnings)), ensure_ascii=False),
                     'proposed', 1, now, now))
    for ev in events:
        ev = {k: v for k, v in ev.items() if k in ('key', 'kind', 'date', 'title', 'start', 'end', 'all_day', 'citation', 'quote', 'source_path',
                                                   'source_sha256', 'description', 'safe', 'derived', 'duration_note', 'act_by', 'basis')}
        ev['requires_clarification'] = not ev.get('safe', False) or bool(ev.get('derived'))
        desk.db.execute('INSERT OR IGNORE INTO dp_events5632(mission,key,data) VALUES(?,?,?)', (mid, ev['key'], json.dumps(ev, ensure_ascii=False)))
    desk.db.commit()
    desk.audit('dp5632_mission', {'mission': mid, 'matter': m['id'], 'documents': len(sources), 'events': len(events), 'warnings': len(warnings)})
    return view(desk, owner, mid)


def _mission(desk, owner, mid):
    schema(desk)
    row = desk.db.execute('SELECT * FROM dp_missions5632 WHERE id=?', (str(mid or ''),)).fetchone()
    if not row:
        raise Stop('mission_absente')
    if row['owner'] != owner:
        raise Stop('mission_autre_utilisateur')
    return row


def targets(desk, owner):
    """Agendas autorisés (Nextcloud, Google) — jamais choisis par l'IA."""
    from .calendar568 import effective_targets
    try:
        return [{'id': t['id'], 'label': t['label'], 'provider': t['provider']} for t in effective_targets(desk, owner)]
    except Exception:   # noqa: BLE001 — aucun agenda configuré
        return []


def view(desk, owner, mid):
    row = _mission(desk, owner, mid)
    events = []
    for r in desk.db.execute('SELECT * FROM dp_events5632 WHERE mission=? ORDER BY key', (mid,)):
        ev = json.loads(r['data'])
        effects = {e['target']: {'state': e['state'], 'error': e['error'], 'proof': json.loads(e['proof'] or '{}')}
                   for e in desk.db.execute('SELECT * FROM dp_effects5632 WHERE mission=? AND key=?', (mid, r['key']))}
        events.append({**ev, 'decision': r['decision'], 'targets': json.loads(r['targets']), 'note': r['note'], 'effects': effects})
    drafts = [dict(d) for d in desk.db.execute('SELECT role,recipient,subject,state,updated FROM dp_drafts5632 WHERE mission=?', (mid,))]
    events.sort(key=lambda e: e.get('start', ''))
    return {'id': mid, 'matter': row['matter'], 'status': row['status'], 'revision': row['revision'], 'sources': json.loads(row['sources']),
            'warnings': json.loads(row['warnings']), 'events': events, 'drafts': drafts, 'targets': targets(desk, owner)}


def decide(desk, owner, mid, key, action, target_ids=(), note='', revision=None):
    row = _mission(desk, owner, mid)
    if revision is not None and int(revision) != row['revision']:
        raise Stop('mission_modifiee_recharger')
    if action not in ACTIONS:
        raise Stop('action_inconnue')
    known = {t['id'] for t in targets(desk, owner)}
    chosen = [t for t in target_ids if t in known]
    if action == 'add' and not chosen:
        raise Stop('agenda_cible_requis')
    cur = desk.db.execute('UPDATE dp_events5632 SET decision=?, targets=?, note=? WHERE mission=? AND key=?',
                          (action, json.dumps(chosen), str(note or '')[:500], mid, key))
    if not cur.rowcount:
        raise Stop('evenement_absent')
    desk.db.execute('UPDATE dp_missions5632 SET revision=revision+1, updated=? WHERE id=?', (_now(), mid))
    desk.db.commit()
    return view(desk, owner, mid)


def apply(desk, owner, mid, deposit=None):
    """Écrit les seuls événements choisis, cible par cible ; une cible déjà prouvée n'est jamais réécrite (pas de doublon)."""
    row = _mission(desk, owner, mid)
    from .calendar568 import effective_targets, deposit as calendar_deposit
    by_id = {t['id']: t for t in effective_targets(desk, owner)}
    write = deposit or calendar_deposit
    done = failed = 0
    for r in desk.db.execute("SELECT * FROM dp_events5632 WHERE mission=? AND decision='add'", (mid,)).fetchall():
        ev = json.loads(r['data'])
        for tid in json.loads(r['targets']):
            state = desk.db.execute('SELECT state FROM dp_effects5632 WHERE mission=? AND key=? AND target=?', (mid, r['key'], tid)).fetchone()
            if state and state['state'] == 'done':
                continue
            target = by_id.get(tid)
            if not target:
                desk.db.execute('INSERT OR REPLACE INTO dp_effects5632 VALUES(?,?,?,?,?,?,?)', (mid, r['key'], tid, 'failed', '{}', 'agenda_non_autorise', _now()))
                failed += 1
                continue
            desk.db.execute('INSERT OR REPLACE INTO dp_effects5632 VALUES(?,?,?,?,?,?,?)', (mid, r['key'], tid, 'reserved', '{}', '', _now()))   # journal avant l'effet
            desk.db.commit()
            try:
                proof = write(desk, owner, row['matter'], ev, target)
                desk.db.execute('UPDATE dp_effects5632 SET state=?, proof=?, error=?, updated=? WHERE mission=? AND key=? AND target=?',
                                ('done', json.dumps(proof, ensure_ascii=False, default=str), '', _now(), mid, r['key'], tid))
                done += 1
            except Stop as ex:
                desk.db.execute('UPDATE dp_effects5632 SET state=?, error=?, updated=? WHERE mission=? AND key=? AND target=?',
                                ('failed', str(ex)[:200], _now(), mid, r['key'], tid))
                failed += 1
            desk.db.commit()
    status = 'partial' if failed and done else ('failed' if failed else 'calendar_done')
    desk.db.execute('UPDATE dp_missions5632 SET status=?, updated=? WHERE id=?', (status, _now(), mid))
    desk.db.commit()
    desk.audit('dp5632_agenda', {'mission': mid, 'done': done, 'failed': failed})
    return {**view(desk, owner, mid), 'written': done, 'failed': failed}


def _fr_date(ev):
    from .i18n5631 import format_date
    from datetime import date
    try:
        d = date.fromisoformat(ev['date'])
        text = format_date(d, 'fr')
    except (KeyError, ValueError):
        text = ev.get('date', '')
    if not ev.get('all_day') and len(ev.get('start', '')) >= 16:
        text += ' à ' + ev['start'][11:16].replace(':', ' h ')
    return text


def compose(desk, owner, mid, role, recipient):
    """Texte déterministe par rôle : le client reçoit dates et suites à prévoir ; le confrère, les seules dates de procédure.
    Une inscription n'est mentionnée que si l'agenda l'a prouvée ; aucune stratégie ni note interne n'est reprise."""
    data = view(desk, owner, mid)
    m = _matter(desk, data['matter'])
    lines = []
    for ev in data['events']:
        if ev['decision'] == 'ignore':
            continue
        when = _fr_date(ev)
        proven = [e for e in ev['effects'].values() if e['state'] == 'done']
        line = '- ' + ev.get('title', '').split(' – ')[0] + ' : ' + when
        if role == 'client' and proven:
            line += ' (inscrit à notre agenda)'
        lines.append(line)
    if not lines:
        raise Stop('aucun_evenement_a_communiquer')
    subject = 'Dossier ' + matter_display(m) + ' — dates de procédure'
    if role == 'client':
        body = ('Madame, Monsieur,\n\nNous avons reçu les documents de procédure de votre dossier. Les dates à retenir sont les suivantes :\n\n' +
                '\n'.join(lines) + '\n\nNous reviendrons vers vous pour préparer ces échéances. N’hésitez pas à nous contacter pour toute question.\n')
    else:
        body = ('Mon cher Confrère,\n\nPour votre information, les dates de procédure du dossier en référence sont les suivantes :\n\n' +
                '\n'.join(lines) + '\n\nJe vous prie de croire, Mon cher Confrère, à l’assurance de mes sentiments confraternels.\n')
    return subject, body


def drafts(desk, owner, mid, recipients, box=None):
    """Un brouillon par destinataire choisi ; dépôt dans « Brouillons » relu, sinon conservation locale signalée. Aucun envoi."""
    _mission(desk, owner, mid)
    out = []
    for r in recipients or []:
        role, email = str(r.get('role') or ''), str(r.get('email') or '').strip()
        if role not in ROLES:
            raise Stop('role_destinataire_invalide')
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email):
            raise Stop('adresse_destinataire_a_choisir')
        existing = desk.db.execute('SELECT * FROM dp_drafts5632 WHERE mission=? AND role=? AND recipient=?', (mid, role, email)).fetchone()
        if existing and existing['state'] in ('imap_verified', 'local'):
            out.append({'role': role, 'recipient': email, 'state': existing['state'], 'subject': existing['subject'], 'reused': True})
            continue
        subject, body = compose(desk, owner, mid, role, email)
        state, proof = 'local', {}
        if (desk.c.get('mail') or {}).get('enabled', True) is not False:
            try:
                state, proof = _deposit_imap(desk, mid, role, email, subject, body, box)
            except Stop as ex:
                state, proof = 'local', {'imap_error': str(ex)}
        desk.db.execute('INSERT OR REPLACE INTO dp_drafts5632 VALUES(?,?,?,?,?,?,?,?)',
                        (mid, role, email, subject, body, state, json.dumps(proof, ensure_ascii=False, default=str), _now()))
        desk.db.commit()
        out.append({'role': role, 'recipient': email, 'state': state, 'subject': subject, 'body': body, 'proof': proof})
    desk.db.execute("UPDATE dp_missions5632 SET status='drafts_ready', updated=? WHERE id=?", (_now(), mid))
    desk.db.commit()
    return {'mission': mid, 'drafts': out, 'sent': 0}


def _deposit_imap(desk, mid, role, email, subject, body, box=None):
    from email.message import EmailMessage
    from email import policy
    from email.utils import formataddr, format_datetime
    from .maildraft5613 import find_existing, verify
    cfg = desk.c['mail']
    ident = hashlib.sha256((mid + '|' + role + '|' + email).encode()).hexdigest()[:32]
    msg = EmailMessage(policy=policy.SMTP)
    msg['From'] = formataddr((cfg.get('from_name', 'Cabinet'), cfg['from_address']))
    msg['To'] = email
    msg['Subject'] = subject
    msg['Date'] = format_datetime(datetime.now(timezone.utc))
    msg['Message-ID'] = '<axiorhub-dp-' + ident + '@mail-agent.local>'
    msg['X-AxiorHub-Draft-Key'] = 'dp5632:' + ident
    msg.set_content(body.strip() + '\n\n' + str(cfg.get('signature', '')).strip() + '\n')
    if box is None:
        from .mailbox import Mailbox
        box = Mailbox(cfg)
        own = True
    else:
        own = False
    try:
        folder = cfg.get('drafts', 'Drafts')
        if not find_existing(box, folder, str(msg['Message-ID'])):
            box.append_draft(msg)
        proof = verify(box, folder, str(msg['Message-ID']), subject, expected=msg)
    finally:
        if own:
            try:
                box.close()
            except Exception:   # noqa: BLE001
                pass
    return 'imap_verified', {'folder': cfg.get('drafts', 'Drafts'), 'message_id': str(msg['Message-ID']), 'verification': proof}
