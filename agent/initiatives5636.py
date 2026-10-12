"""5.6.36 : Initiatives — l'assistant propose un plan d'actions, l'avocat le corrige et le valide, AxiorHub l'exécute.

Exemple : une assignation arrive par courriel. AxiorHub la repère (objet, texte, pièces jointes), relève la juridiction, la date
d'audience et les parties, puis propose :
  1. créer le dossier « DEMANDEUR - DÉFENDEUR - AAAAMMJJnnn » (référence libre du jour) avec ses sous-dossiers ;
  2. ranger les pièces jointes du courriel dans le dossier ;
  3. enregistrer les intervenants (client, partie adverse, confrère) dans les contacts du dossier ;
  4. inscrire l'audience à l'agenda (agenda AxiorHub, copié vers Nextcloud et Microsoft 365 s'ils sont reliés) ;
  5. préparer un courriel au client (brouillon) ;
  6. rédiger une note interne d'analyse des arguments adverses (Word, dossier) ;
  7. préparer un projet d'acte de constitution (Word, sous-dossier PROCEDURE) ;
  8. préparer un courriel au confrère adverse sollicitant la communication des pièces (brouillon) ;
  9. préparer un courrier à la juridiction (Word, CORRESPONDANCES).

Garde-fous :
- rien n'est exécuté sans validation ; l'avocat corrige les éléments relevés et décoche les étapes inutiles ;
- les courriels restent des brouillons (dossier Brouillons de la messagerie) ; les écrits sont de NOUVEAUX fichiers Word, sans écrasement ;
- une étape en échec n'empêche pas les suivantes (sauf celles qui ont besoin du dossier) ; chaque résultat est affiché avec son motif ;
- la détection ne lit que les courriels déjà relevés par AxiorHub ; elle peut être désactivée.
"""
from datetime import date, datetime, timezone
from html import escape as e
import json
import re
import uuid

from .common import Stop, fold, load_matters, matter_display

AUTO = 'initiatives5636:detection'
STATES = {'analyse': 'Analyse en cours', 'proposee': 'À valider', 'en_cours': 'Exécution en cours', 'executee': 'Exécutée', 'ecartee': 'Écartée',
          'echec': 'Analyse impossible', 'redactions': 'Rédactions en cours'}
# 5.6.37 (audit F12) : états d'une étape ; une rédaction mise en file n'est jamais « faite » avant que son document existe
STEP_STATUS = {'fait': '✅ ', 'prepare': '📄 ', 'echec': '⚠️ ', 'attente': '⏳ ', 'en_file': '⏳ ', 'annulee': '⛔ '}
STEP_LABELS = {'en_file': 'Rédaction en cours', 'prepare': 'Document préparé — à relire', 'annulee': 'Non exécutée (plan écarté)'}
STEPS = (
    ('dossier', 'Créer le dossier', 'Répertoire « {nom_dossier} » sous {emplacement}, avec PROCEDURE, CORRESPONDANCES et PIECES.'),
    ('pj', 'Ranger les pièces jointes', 'Les pièces jointes du courriel vont dans « PJ courriels » du dossier (sans écrasement).'),
    ('contacts', 'Enregistrer les intervenants', 'Client, partie adverse et confrère dans les contacts, reliés au dossier.'),
    ('agenda', 'Inscrire l’audience à l’agenda', 'Audience du {date_audience} {heure_audience} — {juridiction}.'),
    ('courriel_client', 'Courriel au client', 'Brouillon : réception de l’assignation, date d’audience, pièces et informations à transmettre.'),
    ('note_interne', 'Note interne : arguments adverses', 'Word : prétentions adverses, moyens, points faibles, pièces à réunir, stratégie à arrêter.'),
    ('constitution', 'Projet d’acte de constitution', 'Word dans PROCEDURE : constitution d’avocat pour {client_nom} (à relire et signer).'),
    ('courriel_confrere', 'Courriel au confrère : pièces', 'Brouillon à {confrere_email} sollicitant la communication des pièces visées à l’assignation.'),
    ('lettre_juridiction', 'Courrier à la juridiction', 'Word dans CORRESPONDANCES : courrier au greffe de {juridiction}.'),
)
FIELDS = (('juridiction', 'Juridiction'), ('date_audience', 'Date d’audience (AAAA-MM-JJ)'), ('heure_audience', 'Heure (HH:MM)'),
          ('demandeur', 'Demandeur'), ('defendeur', 'Défendeur'), ('client_nom', 'Notre client'), ('client_email', 'Courriel du client'),
          ('confrere_nom', 'Avocat adverse'), ('confrere_email', 'Courriel de l’avocat adverse'), ('numero_rg', 'N° RG (si connu)'),
          ('objet', 'Objet du litige'), ('nom_dossier', 'Nom du dossier'), ('emplacement', 'Emplacement (Nextcloud)'))
NEEDS_MATTER = {'pj', 'contacts', 'agenda', 'courriel_client', 'note_interne', 'constitution', 'courriel_confrere', 'lettre_juridiction'}
WORDS = ('assignation', 'assigne', 'assigné', 'citation a comparaitre', 'acte introductif', 'requete introductive', 'signification d’une assignation',
         'signification d\'une assignation', 'placement de l’assignation', 'enrolement')
MONTHS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8, 'septembre': 9, 'octobre': 10,
          'novembre': 11, 'decembre': 12}
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'required': ['est_assignation', 'juridiction', 'date_audience', 'heure_audience', 'demandeur', 'defendeur', 'objet'],
          'properties': {'est_assignation': {'type': 'boolean'}, 'juridiction': {'type': 'string', 'maxLength': 200},
                         'date_audience': {'type': 'string', 'maxLength': 10}, 'heure_audience': {'type': 'string', 'maxLength': 5},
                         'demandeur': {'type': 'string', 'maxLength': 200}, 'defendeur': {'type': 'string', 'maxLength': 200},
                         'confrere_nom': {'type': 'string', 'maxLength': 200}, 'confrere_email': {'type': 'string', 'maxLength': 200},
                         'numero_rg': {'type': 'string', 'maxLength': 60}, 'objet': {'type': 'string', 'maxLength': 400}}}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS initiatives5636(id TEXT PRIMARY KEY, kind TEXT NOT NULL, mail_key TEXT NOT NULL DEFAULT '', mail_uid TEXT NOT NULL DEFAULT '',
      mail_folder TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, source TEXT NOT NULL DEFAULT '{}',
      fields TEXT NOT NULL DEFAULT '{}', steps TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL, job INTEGER, owner TEXT NOT NULL DEFAULT '',
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS initiatives5636_mail ON initiatives5636(kind, mail_key) WHERE mail_key<>'';''')
    if 'version' not in {r[1] for r in desk.db.execute('PRAGMA table_info(initiatives5636)')}:   # 5.6.37 (audit F11) : version de validation
        desk.db.execute('ALTER TABLE initiatives5636 ADD COLUMN version INTEGER NOT NULL DEFAULT 0')
        desk.db.commit()


def enabled(desk):
    return bool(desk.settings(AUTO, True))


def set_enabled(desk, value, owner='cabinet'):
    desk.setting(AUTO, bool(value))
    desk.audit('initiatives5636_detection', {'enabled': bool(value), 'owner': owner})
    return {'enabled': bool(value), 'message': 'Détection des initiatives ' + ('activée.' if value else 'désactivée.')}


def looks_like_summons(text):
    f = fold(text or '')
    return any(fold(w) in f for w in WORDS)


def _row(desk, ident):
    schema(desk)
    row = desk.db.execute('SELECT * FROM initiatives5636 WHERE id=?', (ident,)).fetchone()
    if not row:
        raise Stop('initiative_absente')
    out = dict(row)
    for k in ('source', 'fields', 'steps'):
        out[k] = json.loads(out[k] or ('[]' if k == 'steps' else '{}'))
    return out


def _save(desk, ident, **values):
    for k in ('source', 'fields', 'steps'):
        if k in values:
            values[k] = json.dumps(values[k], ensure_ascii=False)
    values['updated'] = _now()
    desk.db.execute('UPDATE initiatives5636 SET %s WHERE id=?' % ','.join('%s=?' % k for k in values), list(values.values()) + [ident])
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ détection
def observe(desk, key, report):
    """Courriel nouvellement relevé : s'il ressemble à une assignation, une analyse est mise en file (rien d'autre)."""
    if not enabled(desk):
        return None
    hay = ' '.join(str(report.get(k) or '') for k in ('subject', 'summary', 'question'))
    hay += ' ' + ' '.join(str(a.get('filename') or a.get('name') or '') for a in (report.get('attachments') or []) if isinstance(a, dict))
    if not looks_like_summons(hay):
        return None
    return propose_for_mail(desk, key=key, origin='detection')


def propose_for_mail(desk, key='', uid='', folder='', origin='manuel', owner='cabinet'):
    """Crée l'initiative (état « analyse ») et met l'analyse en file ; une seule initiative par courriel."""
    schema(desk)
    if key and not re.fullmatch(r'[0-9a-f]{64}', key):
        raise Stop('cle_invalide')
    if not key and not str(uid).isdigit():
        raise Stop('courriel_absent')
    ident_key = key or ('uid:%s:%s' % (folder or 'INBOX', uid))
    old = desk.db.execute("SELECT id, state FROM initiatives5636 WHERE kind='assignation' AND mail_key=?", (ident_key,)).fetchone()
    if old:
        if old['state'] in ('echec', 'ecartee'):
            _save(desk, old['id'], state='analyse')
            job = desk.enqueue('initiative5636', {'id': old['id'], 'action': 'analyse'}, priority=30)
            return {'id': old['id'], 'job': job, 'message': 'Nouvelle analyse demandée.'}
        return {'id': old['id'], 'message': 'Une initiative existe déjà pour ce courriel.'}
    ident = uuid.uuid4().hex
    now = _now()
    desk.db.execute('INSERT INTO initiatives5636(id,kind,mail_key,mail_uid,mail_folder,title,source,state,owner,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, 'assignation', ident_key, str(uid or ''), str(folder or ''), 'Assignation reçue', json.dumps({'origin': origin}), 'analyse',
                     owner, now, now))
    desk.db.commit()
    job = desk.enqueue('initiative5636', {'id': ident, 'action': 'analyse'}, priority=30)
    desk.db.execute('UPDATE initiatives5636 SET job=? WHERE id=?', (job, ident))
    desk.db.commit()
    desk.audit('initiatives5636_proposee', {'id': ident, 'origin': origin, 'owner': owner})
    return {'id': ident, 'job': job, 'message': 'Analyse lancée : le plan d’actions apparaîtra dans « Initiatives » dans quelques instants.'}


def _fetch_mail(desk, row, mailbox=None):
    from . import mailbox as mbx
    box = mailbox or mbx.Mailbox(desk.c['mail'])
    try:
        if row['mail_uid']:
            return box.fetch(row['mail_folder'] or desk.c['mail'].get('inbox', 'INBOX'), row['mail_uid'])
        from .desk import report_for
        report = report_for(desk.c, row['mail_key'])
        return box.fetch(report.get('source_mailbox') or desk.c['mail'].get('inbox', 'INBOX'), str(report.get('source_uid') or ''))
    finally:
        if mailbox is None:
            try:
                box.close()
            except Exception:   # noqa: BLE001
                pass


def _attachment_text(desk, msg, limit=40000):
    from .documents import extract
    out = []
    for part in msg.iter_attachments():
        name = str(part.get_filename() or '')
        if not name.lower().endswith(('.pdf', '.docx', '.odt', '.txt', '.rtf')):
            continue
        raw = part.get_payload(decode=True) or b''
        try:
            out.append('[' + name + ']\n' + extract(raw, name, {**desk.c.get('documents', {}), 'max_document_chars': limit}))
        except (Stop, OSError, ValueError):
            continue
        if sum(len(x) for x in out) > limit:
            break
    return '\n\n'.join(out)[:limit]


def find_hearing(text):
    """Première date située après « audience » (ou « comparaître ») : (AAAA-MM-JJ, HH:MM) ou ('', '')."""
    f = fold(text or '')
    for anchor in re.finditer(r'audience|comparaitre|comparution|se presenter', f):
        window = f[anchor.start():anchor.start() + 260]
        m = re.search(r'(\d{1,2})(?:er)?\s+(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\s+(\d{4})', window)
        n = re.search(r'(\d{1,2})[/.](\d{1,2})[/.](\d{4})', window)
        found = None
        if m and (not n or m.start() < n.start()):
            found, end = (int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))), m.end()
        elif n:
            found, end = (int(n.group(3)), int(n.group(2)), int(n.group(1))), n.end()
        if not found:
            continue
        try:
            day = date(*found).isoformat()
        except ValueError:
            continue
        h = re.search(r'^\D{0,25}?(\d{1,2})\s*(?:h|heures?|:)\s*(\d{2})?', window[end:end + 40])
        hour = '%02d:%02d' % (int(h.group(1)), int(h.group(2) or 0)) if h and int(h.group(1)) < 24 else ''
        return day, hour
    return '', ''


def _model(desk, matter=''):
    from .docrequest520 import _model as model
    return model(desk, matter)


def extract_fields(desk, text, sender=''):
    """Éléments relevés : modèle d'IA du cabinet (schéma strict), puis contrôle ; repli par règles si le modèle est indisponible."""
    day, hour = find_hearing(text)
    fields = {'date_audience': day, 'heure_audience': hour, 'juridiction': '', 'demandeur': '', 'defendeur': '', 'objet': '', 'numero_rg': '',
              'confrere_nom': '', 'confrere_email': ''}
    j = re.search(r"(tribunal (?:judiciaire|de commerce|de proximit[ée]|des activit[ée]s [ée]conomiques|administratif)|conseil de prud[’']hommes|cour d[’']appel)\s+d[e’']\s*([A-Z][\w\- ]{2,40})", text or '', re.I)
    if j:
        fields['juridiction'] = (j.group(1) + ' de ' + j.group(2)).strip()[:200]
    try:
        raw = _model(desk).complete([
            {'role': 'system', 'content': 'Tu relèves les éléments d’un acte de procédure français reçu par un cabinet d’avocats. Réponds en JSON '
                                          'conforme au schéma. Laisse vide ("") ce qui ne figure pas dans le texte : n’invente rien. Dates au format '
                                          'AAAA-MM-JJ, heure HH:MM.'},
            {'role': 'user', 'content': (text or '')[:24000]}], temperature=0, max_tokens=900, json_schema=SCHEMA)
        found = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (Stop, ValueError, TypeError, KeyError, OSError):
        found = {}
    folded = fold(text or '')
    for k in ('juridiction', 'demandeur', 'defendeur', 'objet', 'numero_rg', 'confrere_nom', 'confrere_email', 'date_audience', 'heure_audience'):
        v = str(found.get(k) or '').strip()
        if not v or (k in ('date_audience', 'heure_audience') and fields[k]):
            continue   # la date et l'heure lues dans le texte priment sur la proposition du modèle
        if k == 'date_audience' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
            continue
        if k == 'heure_audience' and not re.fullmatch(r'\d{2}:\d{2}', v):
            continue
        if k in ('juridiction', 'demandeur', 'defendeur', 'numero_rg', 'confrere_nom', 'confrere_email') and fold(v) not in folded:
            continue   # contrôle : un nom, une juridiction ou une adresse absents du texte ne sont pas retenus
        fields[k] = v[:200]
    fields['est_assignation'] = bool(found.get('est_assignation', looks_like_summons(text)))
    return fields


def analyse(desk, ident, mailbox=None):
    """Travail de fond : lit le courriel et ses pièces jointes, relève les éléments, construit le plan proposé."""
    row = _row(desk, ident)
    mail = _fetch_mail(desk, row, mailbox)
    text = (mail.text or '') + '\n\n' + _attachment_text(desk, mail.msg)
    sender = str(mail.msg.get('From', ''))
    email = (re.search(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+', sender) or [''])[0].lower()
    f = extract_fields(desk, text, sender)
    matter = ''
    if row['mail_key'] and not row['mail_key'].startswith('uid:'):
        r = desk.db.execute('SELECT matter FROM work_items WHERE mail_key=?', (row['mail_key'],)).fetchone()
        matter = (r['matter'] if r else '') or ''
    from .boite5625 import matter_proposal
    prop = matter_proposal(desk)
    from .mailbox import own_addresses
    own = {a.lower() for a in own_addresses(desk.c['mail'])}
    sender_is_lawyer = bool(re.search(r'avocat|cabinet|selarl|aarpi|scp|barreau', fold(sender)))
    defendeur = f.get('defendeur') or ''
    client = defendeur or ''
    fields = {'juridiction': f['juridiction'], 'date_audience': f['date_audience'], 'heure_audience': f['heure_audience'] or '09:00',
              'demandeur': f['demandeur'], 'defendeur': defendeur, 'client_nom': client,
              'client_email': '' if (sender_is_lawyer or email in own) else email,
              'confrere_nom': f['confrere_nom'], 'confrere_email': f['confrere_email'] or (email if sender_is_lawyer else ''),
              'numero_rg': f['numero_rg'], 'objet': f['objet'],
              'nom_dossier': ' - '.join(x for x in ((f['demandeur'] or 'DEMANDEUR').upper()[:60], (defendeur or 'DÉFENDEUR').upper()[:60], prop['reference'])),
              'emplacement': prop['parent'], 'reference': prop['reference']}
    steps = [{'id': sid, 'label': label, 'checked': not (sid == 'dossier' and matter), 'status': '', 'result': ''} for sid, label, _ in STEPS]
    if not fields['confrere_email']:
        for s in steps:
            if s['id'] == 'courriel_confrere':
                s['checked'] = False
    title = 'Assignation reçue' + (' — ' + f['juridiction'] if f['juridiction'] else '') + (' — audience du ' + _fr(f['date_audience']) if f['date_audience'] else '')
    source = {'subject': str(mail.subject or '')[:300], 'from': sender[:300], 'date': mail.timestamp.isoformat() if getattr(mail, 'timestamp', None) else '',
              'excerpt': (mail.text or '')[:600], 'est_assignation': f['est_assignation'], 'origin': (row['source'] or {}).get('origin', '')}
    state = 'proposee'
    if source['origin'] == 'detection' and not f['est_assignation'] and not f['date_audience']:
        state = 'ecartee'   # repérage automatique non confirmé par la lecture : rien n'est proposé (aucun encombrement)
    _save(desk, ident, state=state, title=title[:300], fields=fields, steps=steps, source=source, matter=matter)
    desk.audit('initiatives5636_analysee', {'id': ident, 'date': bool(f['date_audience']), 'juridiction': bool(f['juridiction'])})
    return {'id': ident, 'state': 'proposee'}


def _fr(iso):
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return '%d %s %d' % (d.day, [k for k, v in MONTHS.items() if v == d.month][0].replace('fevrier', 'février').replace('aout', 'août')
                         .replace('decembre', 'décembre'), d.year)


# ------------------------------------------------------------------------------------------------ validation et exécution
def validate(desk, ident, data, owner='cabinet'):
    """Enregistre les corrections et les étapes cochées, puis met l'exécution en file."""
    row = _row(desk, ident)
    if row['state'] not in ('proposee', 'executee', 'redactions'):
        raise Stop('initiative_absente')
    fields = dict(row['fields'])
    for k, _ in FIELDS:
        if k in data:
            v = re.sub(r'[\x00-\x1f]+', ' ', str(data.get(k) or '')).strip()[:300]
            fields[k] = v
    if fields.get('date_audience') and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fields['date_audience']):
        raise Stop('date_audience_invalide')
    if fields.get('heure_audience') and not re.fullmatch(r'\d{1,2}:\d{2}', fields['heure_audience']):
        raise Stop('date_audience_invalide')
    if data.get('matter') is not None:
        row['matter'] = str(data.get('matter') or '')
    steps = row['steps']
    for s in steps:
        if s['status'] in ('fait', 'prepare', 'en_file'):   # 5.6.37 (F12) : une étape faite ou en cours n'est jamais relancée en double
            s['checked'] = False
            continue
        s['checked'] = bool(data.get('step_' + s['id']))
    if not any(s['checked'] for s in steps):
        raise Stop('aucune_etape_choisie')
    if row['matter'] and row['matter'] not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    version = int(row.get('version') or 0) + 1
    _save(desk, ident, fields=fields, steps=steps, state='en_cours', matter=row['matter'], owner=owner, version=version)
    job = desk.enqueue('initiative5636', {'id': ident, 'action': 'executer', 'version': version, **({'matter': row['matter']} if row['matter'] else {})},
                       priority=20)
    _save(desk, ident, job=job)
    desk.audit('initiatives5636_validee', {'id': ident, 'steps': [s['id'] for s in steps if s['checked']], 'owner': owner})
    return {'id': ident, 'job': job, 'message': 'Plan validé : les étapes cochées sont en cours d’exécution (brouillons et projets seulement).'}


def dismiss(desk, ident, owner='cabinet'):
    """5.6.37 (audit F11) : écarter invalide la version validée et annule le travail encore en attente ; l'exécution vérifie l'état et la
    version avant chaque étape. Les étapes déjà accomplies restent tracées, rien n'est effacé."""
    row = _row(desk, ident)
    _save(desk, ident, state='ecartee', version=int(row.get('version') or 0) + 1)
    if row.get('job'):
        try:
            desk.cancel_job(row['job'])
        except Stop:
            pass
    desk.audit('initiatives5636_ecartee', {'id': ident, 'owner': owner})
    return {'id': ident, 'message': 'Initiative écartée : aucune étape ne sera plus exécutée.'}


def _authorized(desk, ident, version):
    """L'initiative est toujours validée dans CETTE version (ni écartée, ni revalidée entre-temps)."""
    row = desk.db.execute('SELECT state, version FROM initiatives5636 WHERE id=?', (ident,)).fetchone()
    return bool(row) and row['state'] == 'en_cours' and (version is None or int(row['version'] or 0) == int(version))


def _mail_text(desk, purpose, fields, matter_label):
    """Brouillon de courriel : rédigé par le modèle du cabinet (sans invention), sinon trame sobre avec [À COMPLÉTER]."""
    facts = {k: fields.get(k, '') for k, _ in FIELDS if k not in ('emplacement',)}
    fallback = {
        'client': ('Assignation reçue — audience du %s' % (_fr(fields.get('date_audience', '')) or '[À COMPLÉTER]'),
                   'Madame, Monsieur,\n\nNous avons bien reçu l’assignation délivrée à la requête de %s devant le %s.\n\n'
                   'L’affaire sera appelée à l’audience du %s à %s. Nous nous constituons dans vos intérêts.\n\n'
                   'Afin de préparer votre défense, nous vous remercions de nous transmettre dans les meilleurs délais :\n'
                   '- tout document et échange en lien avec ce litige ;\n- votre version des faits ;\n- [À COMPLÉTER].\n\n'
                   'Nous restons à votre disposition pour en échanger.\n\nBien cordialement,' % (
                       fields.get('demandeur') or '[À COMPLÉTER]', fields.get('juridiction') or '[À COMPLÉTER]',
                       _fr(fields.get('date_audience', '')) or '[À COMPLÉTER]', fields.get('heure_audience') or '[À COMPLÉTER]')),
        'confrere': ('%s c/ %s — communication de pièces' % (fields.get('demandeur') or '[À COMPLÉTER]', fields.get('defendeur') or '[À COMPLÉTER]'),
                     'Mon Cher Confrère,\n\nJe vous informe intervenir dans les intérêts de %s dans l’affaire qui l’oppose à %s, '
                     'appelée à l’audience du %s devant le %s.\n\nJe vous remercie de bien vouloir me communiquer les pièces visées à votre '
                     'assignation.\n\nVotre bien dévoué,' % (fields.get('client_nom') or '[À COMPLÉTER]', fields.get('demandeur') or '[À COMPLÉTER]',
                                                           _fr(fields.get('date_audience', '')) or '[À COMPLÉTER]', fields.get('juridiction') or '[À COMPLÉTER]')),
    }[purpose]
    try:
        raw = _model(desk).complete([
            {'role': 'system', 'content': 'Tu rédiges un projet de courriel pour un avocat français. Utilise uniquement les éléments fournis ; écris '
                                          '[À COMPLÉTER] pour toute information manquante. Ton professionnel, sobre. JSON {"objet", "corps"}.'},
            {'role': 'user', 'content': json.dumps({'destinataire': 'client' if purpose == 'client' else 'avocat adverse', 'dossier': matter_label,
                                                    'elements': facts, 'trame': fallback[1]}, ensure_ascii=False)}],
            temperature=0.2, max_tokens=1200,
            json_schema={'type': 'object', 'additionalProperties': False, 'required': ['objet', 'corps'],
                         'properties': {'objet': {'type': 'string', 'maxLength': 200}, 'corps': {'type': 'string', 'maxLength': 6000}}})
        out = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if out.get('corps') and out.get('objet'):
            return str(out['objet'])[:200], str(out['corps'])[:6000], 'ia'
    except (Stop, ValueError, TypeError, KeyError, OSError):
        pass
    return fallback[0], fallback[1], 'trame'


def _draft(desk, to, subject, body, owner):
    from html import escape
    from .webmail5628 import save
    html = ''.join('<p>%s</p>' % escape(p).replace('\n', '<br>') for p in body.split('\n\n'))
    return save(desk, {'mode': 'nouveau', 'to': to or '', 'subject': subject, 'html': html}, owner)


def _step(desk, row, sid, fields, owner, dav=None):
    """Exécute une étape ; renvoie le texte du résultat (ou lève Stop)."""
    from .common import load_matters as lm
    matter = row['matter']
    label = next((matter_display(m) for m in lm(desk.c) if m['id'] == matter), matter)
    if sid == 'dossier':
        from .boite5625 import create_matter, _dav
        name = fields.get('nom_dossier') or ''
        parts = [p.strip() for p in name.split(' - ')]
        ref = parts[-1] if len(parts) >= 2 and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', parts[-1]) else fields.get('reference', '')
        client = parts[0] if parts else ''
        title = ' - '.join(parts[1:-1]) if len(parts) >= 3 else (parts[1] if len(parts) == 2 and parts[1] != ref else '')
        out = create_matter(desk, {'client_name': client, 'title': title, 'reference': ref, 'parent': fields.get('emplacement') or '',
                                   'correspondent': fields.get('client_email') or '', 'role': 'client'}, owner)
        row['matter'] = out['id']
        client_dav = dav or _dav(desk)
        for sub in ('PROCEDURE', 'CORRESPONDANCES', 'PIECES'):
            try:
                client_dav.ensure_folder(out['path'] + '/' + sub, out['path'])
            except Stop:
                pass
        return out['message']
    if not matter:
        raise Stop('dossier_absent')
    if sid == 'pj':
        from .ged5636 import file_attachments
        key = row['mail_key'] if not row['mail_key'].startswith('uid:') else ''
        if not key:
            raise Stop('dossier_du_courriel_a_preciser')
        return file_attachments(desk, key, matter, owner, dav=dav)['message']
    if sid == 'contacts':
        from . import crm5636
        done = []
        for name, email, cat, role in ((fields.get('client_nom'), fields.get('client_email'), 'client', 'client'),
                                       (fields.get('demandeur') if fields.get('client_nom') != fields.get('demandeur') else fields.get('defendeur'), '',
                                        'partie_adverse', 'partie_adverse'),
                                       (fields.get('confrere_nom'), fields.get('confrere_email'), 'confrere', 'avocat_adverse')):
            if name or email:
                crm5636.find_or_create(desk, {'name': name or email.split('@')[0], 'email': email or '', 'category': cat, 'matter': matter, 'role': role,
                                              'kind': 'personne' if cat == 'confrere' else ''})
                done.append(name or email)
        return 'Intervenants enregistrés : ' + (', '.join(done) or 'aucun élément à enregistrer') + '.'
    if sid == 'agenda':
        if not fields.get('date_audience'):
            raise Stop('date_audience_invalide')
        from .agenda5634 import create
        out = create(desk, {'title': 'Audience — %s' % label, 'start': fields['date_audience'] + 'T' + (fields.get('heure_audience') or '09:00').zfill(5),
                            'duration_minutes': 60, 'matter': matter, 'kind': 'audience', 'location': fields.get('juridiction', ''),
                            'description': 'RG %s — %s' % (fields.get('numero_rg') or 'à venir', fields.get('objet') or '')}, owner)
        try:
            from .microsoft5636 import push_event
            push_event(desk, out['event'])
        except Stop:
            pass
        except ImportError:
            pass
        return out['message']
    if sid in ('courriel_client', 'courriel_confrere'):
        purpose = 'client' if sid == 'courriel_client' else 'confrere'
        to = fields.get('client_email' if purpose == 'client' else 'confrere_email') or ''
        subject, body, how = _mail_text(desk, purpose, fields, label)
        out = _draft(desk, to, subject, body, owner)
        return 'Brouillon déposé dans « Brouillons »%s%s.' % ((' pour ' + to) if to else ' (destinataire à compléter)', ' — trame à compléter' if how == 'trame' else '')
    from .docrequest520 import submit
    ctx = '; '.join('%s : %s' % (lab, fields.get(k)) for k, lab in FIELDS if fields.get(k) and k not in ('emplacement', 'nom_dossier'))
    if sid == 'note_interne':
        out = submit(desk, 'Note interne d’analyse de l’assignation reçue (%s) : résumé des prétentions et moyens adverses, analyse critique de chaque '
                           'argument (points faibles, éléments de preuve manquants, prescription, compétence, recevabilité), pièces à réunir auprès du '
                           'client, stratégie de défense proposée et questions à trancher par l’avocat. Éléments relevés : %s' % (label, ctx),
                     matter, 'note')
    elif sid == 'constitution':
        out = submit(desk, 'Projet d’acte de constitution d’avocat pour %s dans l’instance engagée par %s devant le %s (audience du %s, RG %s), à '
                           'notifier au confrère adverse et à remettre au greffe. Éléments relevés : %s' % (
                               fields.get('client_nom') or '[client]', fields.get('demandeur') or '[demandeur]', fields.get('juridiction') or '[juridiction]',
                               _fr(fields.get('date_audience', '')), fields.get('numero_rg') or 'à venir', ctx), matter, 'acte_procedure')
    elif sid == 'lettre_juridiction':
        out = submit(desk, 'Courrier au greffe du %s : constitution dans les intérêts de %s, affaire %s c/ %s appelée à l’audience du %s (RG %s) ; '
                           'demande de communication du calendrier de procédure. Éléments relevés : %s' % (
                               fields.get('juridiction') or '[juridiction]', fields.get('client_nom') or '[client]', fields.get('demandeur') or '[demandeur]',
                               fields.get('defendeur') or '[défendeur]', _fr(fields.get('date_audience', '')), fields.get('numero_rg') or 'à venir', ctx),
                     matter, 'courrier')
    else:
        raise Stop('operation_inconnue')
    return {'status': 'en_file', 'job': out['job_id'], 'request': out['request'],
            'result': 'Rédaction en cours (travail n° %s) : le document Word sera déposé dans le dossier.' % out['job_id']}


def refresh_children(desk, ident):
    """5.6.37 (audit F12) : état des rédactions d'après leur travail : document préparé (fichier déposé), échec (motif), annulée ; le plan
    n'est « exécuté » que lorsque plus aucune rédaction n'est en cours."""
    row = _row(desk, ident)
    steps, changed = row['steps'], False
    for s in steps:
        if s.get('status') != 'en_file' or not s.get('request'):
            continue
        req = desk.db.execute('SELECT status, path, result FROM docreq520 WHERE id=?', (s['request'],)).fetchone()
        job = desk.db.execute('SELECT status FROM jobs WHERE id=?', (s.get('job'),)).fetchone() if s.get('job') else None
        if req and req['status'] == 'cree' and req['path']:
            s.update(status='prepare', result='Document préparé, à relire : %s' % req['path'].rsplit('/', 1)[-1], path=req['path'])
            changed = True
        elif (req and req['status'] in ('echec', 'dossier_a_choisir')) or (job and job['status'] == 'error'):
            detail = json.loads(req['result'] or '{}') if req else {}
            s.update(status='echec', result='Rédaction en échec%s : relancez cette étape seule.' % (
                (' (%s)' % detail.get('erreur')) if detail.get('erreur') else ''))
            changed = True
        elif job and job['status'] in ('cancelled', 'cancel_requested'):
            s.update(status='annulee', result='Rédaction annulée.')
            changed = True
    state = row['state']
    if state == 'redactions' and not any(s.get('status') == 'en_file' for s in steps):
        state = 'executee'
    if changed or state != row['state']:
        _save(desk, ident, steps=steps, state=state)
    return steps


def execute(desk, ident, dav=None, version=None):
    """Travail de fond : exécute les étapes cochées dans l'ordre ; une étape en échec n'arrête pas les autres.
    5.6.37 (audit F11) : l'initiative doit être encore validée dans la même version au démarrage puis avant CHAQUE étape avec effet."""
    from .erreurs5634 import explain
    row = _row(desk, ident)
    fields, steps = row['fields'], row['steps']
    if not _authorized(desk, ident, version):
        desk.audit('initiatives5636_non_executee', {'id': ident, 'motif': 'ecartee_ou_revalidee'})
        return {'id': ident, 'steps': [{k: s[k] for k in ('id', 'status')} for s in steps], 'interrompu': True}
    interrupted = False
    for s in steps:
        if not s.get('checked') or s.get('status') in ('fait', 'prepare', 'en_file'):
            continue
        if not _authorized(desk, ident, version):   # écartée pendant l'exécution : les étapes suivantes ne démarrent pas
            interrupted = True
            break
        if s['id'] in NEEDS_MATTER and not row['matter']:
            s.update(status='attente', result='En attente du dossier : cochez « Créer le dossier » ou choisissez un dossier existant.')
            continue
        try:
            out = _step(desk, row, s['id'], fields, row['owner'] or 'cabinet', dav)
            if isinstance(out, dict):   # rédaction mise en file : suivie jusqu'à son document (F12)
                s.update(status=out['status'], result=out['result'], job=out['job'], request=out['request'])
            else:
                s['result'] = out
                s['status'] = 'fait'
        except Stop as exc:
            detail = getattr(exc, 'detail', None)
            message = explain(str(exc), detail if isinstance(detail, dict) else None)
            precision = getattr(exc, 'precision', '')
            s.update(status='echec', result=message + ((' (' + str(precision) + ')') if precision else ''))
        except Exception as exc:   # noqa: BLE001 — une étape ne doit jamais interrompre les suivantes
            s.update(status='echec', result='Étape interrompue (%s).' % type(exc).__name__)
        s['checked'] = False
        _save(desk, ident, steps=steps, matter=row['matter'])
    if interrupted:
        for s in steps:
            if s.get('checked'):
                s.update(checked=False, status='annulee', result='Non exécutée : l’initiative a été écartée.')
    final = 'redactions' if any(s.get('status') == 'en_file' for s in steps) else 'executee'
    desk.db.execute('UPDATE initiatives5636 SET steps=?, matter=?, updated=? WHERE id=?', (json.dumps(steps, ensure_ascii=False), row['matter'], _now(), ident))
    # transition conditionnelle : une annulation concurrente (état écarté, version changée) n'est jamais écrasée
    desk.db.execute("UPDATE initiatives5636 SET state=? WHERE id=? AND state='en_cours'" + ('' if version is None else ' AND version=?'),
                    (final, ident) + (() if version is None else (int(version),)))
    desk.db.commit()
    desk.audit('initiatives5636_executee', {'id': ident, 'done': [s['id'] for s in steps if s.get('status') == 'fait'],
                                            'failed': [s['id'] for s in steps if s.get('status') == 'echec']})
    return {'id': ident, 'steps': [{k: s[k] for k in ('id', 'status')} for s in steps]}


def job(desk, args):
    ident = str(args.get('id') or '')
    if args.get('action') == 'executer':
        return execute(desk, ident, version=args.get('version'))
    try:
        return analyse(desk, ident)
    except Stop as exc:
        _save(desk, ident, state='echec', source={'erreur': str(exc)})
        raise


# ------------------------------------------------------------------------------------------------ page
def listing(desk, states=('analyse', 'proposee', 'en_cours', 'redactions'), limit=50):
    schema(desk)
    for r in desk.db.execute("SELECT id FROM initiatives5636 WHERE state='redactions'").fetchall():   # 5.6.37 (F12)
        try:
            refresh_children(desk, r['id'])
        except Exception:   # noqa: BLE001 — l'affichage ne dépend pas du suivi
            pass
    rows = desk.db.execute('SELECT id FROM initiatives5636 WHERE state IN (%s) ORDER BY created DESC LIMIT ?' % ','.join('?' for _ in states),
                           list(states) + [limit]).fetchall()
    return [_row(desk, r['id']) for r in rows]


def count_pending(desk):
    schema(desk)
    return desk.db.execute("SELECT COUNT(*) FROM initiatives5636 WHERE state='proposee'").fetchone()[0]


def _card(desk, prefix, r, matters):
    src = r['source'] or {}
    head = ('<header><h2>%s</h2><p class="ax-muted">%s · %s</p></header>') % (e(r['title']), e(STATES.get(r['state'], r['state'])),
                                                                          e(' — '.join(x for x in (src.get('subject', ''), src.get('from', '')) if x)))
    if r['state'] == 'analyse':
        return '<article class="ax-card i5636">%s<p>Lecture du courriel et de ses pièces jointes…</p></article>' % head
    if r['state'] == 'echec':
        return '<article class="ax-card i5636">%s<p>Analyse impossible : %s</p></article>' % (head, e(src.get('erreur', '')))
    f = r['fields']
    fields = ''.join('<label>%s<input name="%s" value="%s"%s></label>' % (
        e(lab), k, e(f.get(k, ''), quote=True), ' type="date"' if k == 'date_audience' else (' type="time"' if k == 'heure_audience' else ''))
        for k, lab in FIELDS)
    options = '<option value="">Nouveau dossier (étape « Créer le dossier »)</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (e(m['id'], quote=True), ' selected' if m['id'] == r['matter'] else '', e(matter_display(m))) for m in matters)
    steps = ''
    for s in r['steps']:
        desc = next((d for i, _, d in STEPS if i == s['id']), '')
        try:
            desc = desc.format(**{k: (f.get(k) or '…') for k, _ in FIELDS})
        except (KeyError, IndexError):
            pass
        status = STEP_STATUS.get(s.get('status'), '') + ((STEP_LABELS[s['status']] + ' — ') if s.get('status') in STEP_LABELS else '')
        steps += ('<li class="i5636-step i5636-%s"><label class="e5636-switch"><input type="checkbox" name="step_%s"%s%s> <strong>%s%s</strong></label>'
                  '<small>%s</small>%s</li>') % (e(s.get('status') or 'todo'), e(s['id']), ' checked' if s.get('checked') else '',
                                                ' disabled' if s.get('status') in ('fait', 'prepare', 'en_file') else '', status, e(s['label']), e(desc),
                                                ('<p class="i5636-result">%s</p>' % e(s['result'])) if s.get('result') else '')
    running = r['state'] == 'en_cours'
    return ('<article class="ax-card i5636" id="i-%s">%s<form class="m5-form" data-api="m5636/initiatives/valider" data-reload="1">'
            '<input type="hidden" name="id" value="%s"><details%s><summary>Éléments relevés (à vérifier et corriger)</summary><div class="i5636-fields">'
            '<label>Dossier<select name="matter">%s</select></label>%s</div></details><ol class="i5636-steps">%s</ol>'
            '<div class="ax-actions"><button class="ax-btn" type="submit"%s>%s</button>'
            '<button class="ax-btn ghost" type="submit" formnovalidate data-extra="ecarter=1">Écarter</button></div>'
            '<p class="ax-muted">Rien n’est envoyé : les courriels restent dans Brouillons, les écrits sont de nouveaux fichiers Word à relire.</p>'
            '</form></article>') % (e(r['id'], quote=True), head, e(r['id'], quote=True), ' open' if r['state'] == 'proposee' else '', options, fields, steps,
                                    ' disabled' if running else '', 'Exécution en cours…' if running else
                                    ('Valider et exécuter les étapes cochées' if r['state'] == 'proposee' else 'Exécuter les étapes cochées'))


def page_html(desk, prefix, args, env=None):
    schema(desk)
    try:
        matters = sorted(load_matters(desk.c), key=lambda m: matter_display(m).casefold())
    except Stop:
        matters = []
    pending = listing(desk)
    done = listing(desk, ('executee',), 10)
    auto = enabled(desk)
    html = ('<h1>Initiatives</h1><p class="ax-muted">AxiorHub repère les événements qui appellent plusieurs actions (une assignation reçue, des '
            'factures impayées) et vous propose un plan. Vous corrigez les éléments relevés, décochez ce qui est inutile, puis validez : '
            'les étapes s’exécutent (brouillons et projets seulement).</p>'
            '<form class="m5-form m5-inline" data-api="m5636/initiatives/detection" data-reload="1"><label class="e5636-switch"><input type="checkbox" '
            'name="enabled"%s> Repérer automatiquement les assignations dans les courriels relevés</label> <button class="ax-btn ghost" type="submit">'
            'Enregistrer</button></form>') % (' checked' if auto else '')
    try:
        from .relances5636 import pending_count
        n = pending_count(desk)
        if n:
            html += ('<section class="ax-card i5636-unpaid"><h2>Factures impayées</h2><p>%d relance(s) préparée(s) en brouillon attendent votre '
                     'validation.</p><a class="ax-btn" href="%s">Voir les relances</a></section>') % (n, e(prefix + '/relances', quote=True))
    except (ImportError, Stop):
        pass
    html += ''.join(_card(desk, prefix, r, matters) for r in pending) or ('<p class="ax-card ax-muted">Aucune initiative à valider. Depuis un courriel, '
                                                                           '« 🧭 Proposer un plan d’actions » en crée une à la demande.</p>')
    if done:
        html += '<h2>Exécutées récemment</h2>' + ''.join(_card(desk, prefix, r, matters) for r in done)
    return html


def api_post(desk, route, data, owner):
    if route == 'initiatives/valider':
        if data.get('ecarter'):
            return dismiss(desk, str(data.get('id') or ''), owner)
        return validate(desk, str(data.get('id') or ''), data, owner)
    if route == 'initiatives/ecarter':
        return dismiss(desk, str(data.get('id') or ''), owner)
    if route == 'initiatives/proposer':
        return propose_for_mail(desk, str(data.get('key') or ''), str(data.get('uid') or ''), str(data.get('folder') or ''), 'manuel', owner)
    if route == 'initiatives/detection':
        return set_enabled(desk, bool(data.get('enabled')), owner)
    return None


def api_get(desk, route, args):
    if route == 'initiatives':
        return {'initiatives': listing(desk), 'pending': count_pending(desk)}
    return None
