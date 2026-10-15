"""5.6.43 (cahier v9, § 10 à 14) : conserver un courriel dans CORRESPONDANCES et ses pièces jointes dans NOS PIECES.

Deux actions INDÉPENDANTES, deux travaux déterministes (aucun appel au modèle) :
- ``archive_email`` (travail ``archive_correspondence5643``) : octets IMAP complets lus sans modification ni marque de lecture
  (``Mailbox.fetch_raw``), empreinte SHA-256, dépôt exclusif de ``original.eml`` puis relecture et comparaison ; dérivés déterministes
  ``lecture.md`` (extraction reproductible, jamais un résumé), ``images/`` (images intégrées ``cid:``) et ``metadata.json``.
- ``file_attachments`` (travail ``file_mail_attachments5643``) : octets décodés MIME de chaque pièce choisie, déposés dans
  ``NOS PIECES/PJ courriels/AAAA-MM-JJ--<archive>/`` (création exclusive, relecture, bilan par fichier). Ne crée aucun ``.eml``.

Identité : archive = portée + compte + SHA-256 des octets (jamais le seul Message-ID) ; source = compte/boîte/UIDVALIDITY/UID ;
classement = (archive, dossier). Une confirmation porte une clé de requête : un double clic ou deux onglets rejoignent le même travail.
Rien ne dépend du LLM : le dossier est choisi par l'utilisateur (ou, en automatique, par un rattachement CONFIRMÉ unique).
Politique par défaut : assistée ; les deux autorisations automatiques sont désactivées à l'installation.
"""
from datetime import datetime, timezone
from email import policy as email_policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
import hashlib
import html as html_lib
import json
from pathlib import PurePosixPath
import re
import secrets

from .common import Stop, clean_path, load_matters, matter_display, under

EXTRACTION_VERSION = 'mime5643-1'
ACTIONS = ('archive_email', 'file_attachments')
JOB_FOR = {'archive_email': 'archive_correspondence5643', 'file_attachments': 'file_mail_attachments5643'}
DEFAULT_LIMITS = {'archive_max_message_bytes': 50 * 1024 * 1024, 'archive_max_part_bytes': 25 * 1024 * 1024,
                  'archive_max_total_extracted_bytes': 100 * 1024 * 1024, 'archive_max_parts': 60}
WINDOWS_RESERVED = {'con', 'prn', 'aux', 'nul'} | {'com%d' % i for i in range(1, 10)} | {'lpt%d' % i for i in range(1, 10)}
STATE_LABELS = {'not_requested': 'Non demandé', 'proposed': 'Proposé', 'queued': 'En attente', 'saving': 'Enregistrement en cours',
                'verifying': 'Vérification', 'verified': 'Original vérifié', 'partial': 'Partiel', 'error': 'Échec expliqué',
                'uncertain': 'Dépôt incertain', 'cancel_requested': 'Arrêt demandé', 'cancelled': 'Annulé', 'withdrawn': 'Retiré'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def limits(cfg):
    mail = (cfg or {}).get('mail') or {}
    return {k: int(mail.get(k) or v) for k, v in DEFAULT_LIMITS.items()}


def account_id(cfg):
    mail = cfg['mail']
    return _sha(('%s@%s' % (mail.get('username', ''), mail.get('host', ''))).encode())[:32]


def safe_name(name, maxlen=80, default='piece'):
    """Nom de fichier sûr partout (Nextcloud, Windows, Linux) : caractères interdits, noms réservés, « .. » et longueur bornés."""
    name = str(name or '').replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', '', name).strip().strip('.')
    name = re.sub(r'\s+', ' ', name)
    if not name or name in ('.', '..'):
        name = default
    stem, dot, ext = name.rpartition('.')
    if not dot:
        stem, ext = name, ''
    if stem.lower() in WINDOWS_RESERVED:
        stem = '_' + stem
    ext = re.sub(r'[^A-Za-z0-9]', '', ext)[:10]
    stem = stem[:max(8, maxlen - len(ext) - 1)].rstrip(' .') or default
    return stem + ('.' + ext if ext else '')


def _slug(text, n=40):
    from .common import fold
    s = re.sub(r'[^a-z0-9]+', '-', fold(str(text or ''))).strip('-')
    return s[:n].rstrip('-') or 'sans-objet'


# ------------------------------------------------------------------------------------------------ analyse MIME (copie analysée)
def parse(raw):
    return BytesParser(policy=email_policy.default).parsebytes(raw)


def mime_parts(msg, cfg=None):
    """Feuilles MIME numérotées dans l'ordre du message (clé stable pour les mêmes octets) avec octets décodés et empreinte."""
    lim = limits(cfg or {})
    out, total, index = [], 0, 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        index += 1
        if index > lim['archive_max_parts']:
            out.append({'key': str(index), 'state': 'non_extraite', 'reason': 'trop_de_parties'})
            continue
        ctype = part.get_content_type()
        filename = part.get_filename() or ''
        cid = str(part.get('Content-ID') or '').strip().strip('<>')
        disposition = (part.get_content_disposition() or '').lower()
        try:
            data = part.get_payload(decode=True) or b''
        except Exception:   # noqa: BLE001 — partie illisible : elle reste dans l'original
            data = b''
        item = {'key': str(index), 'type': ctype, 'name': filename, 'cid': cid, 'disposition': disposition, 'size': len(data)}
        if len(data) > lim['archive_max_part_bytes'] or total + len(data) > lim['archive_max_total_extracted_bytes']:
            item.update(state='non_extraite', reason='taille')
            out.append(item)
            continue
        total += len(data)
        body = ctype in ('text/plain', 'text/html') and not filename and disposition != 'attachment'
        inline_image = ctype.startswith('image/') and bool(cid) and disposition != 'attachment'
        signature_like = ctype.startswith('image/') and (inline_image or (len(data) < 30_000 and re.match(r'(?i)image\d*\.(png|jpe?g|gif)$', filename or '')))
        item.update(data=data, sha256=_sha(data), body=body, inline_image=inline_image,
                    attachment=bool(not body and (filename or disposition == 'attachment') and not inline_image),
                    signature_like=bool(signature_like), state='extraite')
        out.append(item)
    return out


def _headers(msg):
    def addr(name):
        return ', '.join(a for _, a in getaddresses([str(msg.get(name, ''))]) if a)[:1000]
    declared = ''
    try:
        if msg.get('Date'):
            declared = parsedate_to_datetime(str(msg['Date'])).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        declared = ''
    return {'from': str(msg.get('From', ''))[:300], 'from_address': addr('From'), 'to': addr('To'), 'cc': addr('Cc'),
            'subject': str(msg.get('Subject', ''))[:500], 'message_id': str(msg.get('Message-ID', '')).strip()[:300], 'declared_date': declared}


def _html_text(raw):
    """HTML reçu → texte : balises retirées, scripts et styles ignorés, entités décodées ; aucune ressource chargée."""
    raw = re.sub(r'(?is)<(script|style|head)[^>]*>.*?</\1>', ' ', raw)
    raw = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>', '\n', raw)
    text = html_lib.unescape(re.sub(r'(?s)<[^>]+>', ' ', raw))
    return re.sub(r'[ \t\r\f\v]+', ' ', re.sub(r'\n\s*\n\s*\n+', '\n\n', text)).strip()


def body_text(msg, parts):
    """Corps lisible : partie texte, sinon conversion de la partie HTML ; ``partial`` si aucune partie lisible."""
    for p in parts:
        if p.get('body') and p['type'] == 'text/plain' and p.get('data') is not None:
            return _decode(p), False
    for p in parts:
        if p.get('body') and p['type'] == 'text/html' and p.get('data') is not None:
            return _html_text(_decode(p)), False
    return '', True


def _decode(part):
    for enc in ('utf-8', 'latin-1'):
        try:
            return part['data'].decode(enc)
        except UnicodeDecodeError:
            continue
    return part['data'].decode('utf-8', 'replace')


def remote_images(msg):
    count = 0
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            try:
                count += len(re.findall(r'(?i)<img[^>]+src=["\']https?://', part.get_content()))
            except Exception:   # noqa: BLE001
                pass
    return count


def reading_md(meta, msg, parts, piece_paths=None):
    """Extraction déterministe (jamais une réécriture) : en-têtes utiles, corps, liens vers les pièces et images intégrées."""
    h = meta['headers']
    sens = {'received': 'reçu', 'sent': 'envoyé par le cabinet', 'internal': 'interne au cabinet'}.get(meta.get('direction'), 'à vérifier')
    text, partial = body_text(msg, parts)
    lines = ['# ' + (h['subject'] or '(sans objet)'), '',
             '- **De** : ' + (h['from'] or '—'), '- **À** : ' + (h['to'] or '—')]
    if h['cc']:
        lines.append('- **Cc** : ' + h['cc'])
    lines += ['- **Date déclarée** : ' + (h['declared_date'] or 'absente de l’en-tête'),
              '- **Reçu par le serveur** : ' + (meta.get('internaldate') or 'inconnu'),
              '- **Sens** : ' + sens, '- **Identifiant du message** : ' + (h['message_id'] or 'absent'), '', '---', '',
              text or '_Aucune partie texte lisible : consulter l’original._', '', '---', '']
    attachments = [p for p in parts if p.get('attachment')]
    if attachments:
        lines.append('## Pièces jointes')
        for p in attachments:
            where = (piece_paths or {}).get(p['key'])
            lines.append('- %s (%s, %d octets) — %s' % (p['name'] or 'sans nom', p['type'], p['size'],
                                                       ('enregistrée dans NOS PIECES : ' + where) if where else 'conservée dans l’original'))
        lines.append('')
    images = [p for p in parts if p.get('inline_image') and p.get('state') == 'extraite']
    if images:
        lines.append('## Images intégrées')
        for p in images:
            lines.append('- ![%s](images/%s)' % (p['name'] or 'image', image_name(p)))
        lines.append('')
    distant = remote_images(msg)
    if distant:
        lines += ['_%d image(s) distante(s) non conservée(s) : leur chargement est bloqué._' % distant, '']
    skipped = [p for p in parts if p.get('state') == 'non_extraite']
    if skipped:
        lines += ['_%d partie(s) non extraite(s) (taille ou nombre) : elles restent dans original.eml._' % len(skipped), '']
    lines.append('_Extraction déterministe %s depuis original.eml (SHA-256 %s). Ce texte n’est pas un résumé ; l’original fait foi._'
                 % (EXTRACTION_VERSION, meta['raw_sha256']))
    return '\n'.join(lines) + '\n', partial or bool(skipped)


def image_name(part):
    ext = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}.get(part['type'], 'img')
    return 'image-%s.%s' % (part['key'], ext)


def piece_name(part):
    """Nom de stockage stable dans NOS PIECES (le nom reçu reste dans les métadonnées) : nom nettoyé + numéro de partie."""
    base = safe_name(part.get('display_name') or part.get('name') or 'piece-%s' % part['key'], 70)
    stem, dot, ext = base.rpartition('.')
    if not dot:
        stem, ext = base, ''
    return '%s--partie-%s%s' % (stem, part['key'], ('.' + ext) if ext else '')


# ------------------------------------------------------------------------------------------------ dossiers et destinations
def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if x['id'] == str(mid or '')), None)
    if not m or not m.get('path'):
        raise Stop('dossier_absent')
    return m


def correspondances_dir(desk, m):
    from .docrequest520 import folders
    return clean_path(m['path'] + '/' + folders(desk)['correspondance'])


def nos_pieces_dir(desk, m):
    from .dossiers5632 import resolve
    for item in resolve(desk, m):
        if item['usage'] == 'nos_pieces' and item['path']:
            return item['path']
    return clean_path(m['path'] + '/NOS PIECES')


def message_dir(meta, archive_id):
    stamp = (meta['headers'].get('declared_date') or meta.get('internaldate') or _now())
    try:
        when = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        when = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        when = when.astimezone(ZoneInfo('Europe/Paris'))
    except Exception:   # noqa: BLE001
        pass
    sens = {'received': 'recu', 'sent': 'envoye', 'internal': 'interne'}.get(meta.get('direction'), 'courriel')
    return '%s/%s-%s-%s--%s' % (when.strftime('%Y-%m'), when.strftime('%Y-%m-%d_%H%M%S'), sens, _slug(meta['headers']['subject'], 40), archive_id[:8])


def confirmed_matter(desk, address):
    """Rattachement CONFIRMÉ : l'adresse figure parmi les correspondants validés d'UN seul dossier (jamais une déduction du modèle)."""
    address = str(address or '').strip().lower()
    if not address:
        return ''
    hits = [m['id'] for m in load_matters(desk.c) if any(str(c.get('email', '')).lower() == address for c in m.get('correspondents', []))]
    return hits[0] if len(hits) == 1 else ''


def _client(desk):
    from .dav import DAV
    return DAV(desk.c['nextcloud'])


def _box(desk):
    from .mailbox import Mailbox
    return Mailbox(desk.c['mail'])


# ------------------------------------------------------------------------------------------------ préparation et confirmation
def _source(data):
    src = data.get('source') if isinstance(data.get('source'), dict) else data
    folder = str(src.get('folder') or 'INBOX')[:200]
    uid = str(src.get('uid') or '')
    if not uid.isdigit():
        raise Stop('courriel_invalide')
    return {'folder': folder, 'uid': uid, 'uidvalidity': str(src.get('uidvalidity') or '')}


def capture(desk, source, box=None):
    """Lecture des octets complets (lecture seule) et analyse d'une COPIE ; l'original n'est jamais reconstruit depuis l'analyse."""
    own = box is None
    box = box or _box(desk)
    try:
        raw = box.fetch_raw(source['folder'], source['uid'], source.get('uidvalidity') or None, limits(desk.c)['archive_max_message_bytes'])
    finally:
        if own:
            box.close()
    if not raw['complete']:
        raise Stop('octets_imap_incomplets')
    msg = parse(raw['raw'])
    from .sens5643 import direction
    meta = {'raw_sha256': _sha(raw['raw']), 'raw_size': len(raw['raw']), 'internaldate': raw['internaldate'], 'headers': _headers(msg),
            'direction': direction(_MailView(msg), desk.c['mail']), 'source': {**source, 'uidvalidity': raw['uidvalidity']}}
    return raw, msg, meta


class _MailView:
    """Vue minimale pour la garde des identités (sens5643.direction)."""
    def __init__(self, msg):
        self.msg = msg
        self.sender = (getaddresses([str(msg.get('From', ''))]) or [('', '')])[0][1]


def prepare(desk, owner, data, box=None, mode='assisted', origin='user'):
    """Proposition versionnée : destinations lisibles, fichiers attendus par action. Aucun fichier n'est écrit."""
    actions = [a for a in (data.get('actions') or []) if a in ACTIONS]
    if not actions:
        raise Stop('action_de_classement_a_choisir')
    m = _matter(desk, data.get('matter_id') or data.get('matter'))
    source = _source(data)
    raw, msg, meta = capture(desk, source, box)
    archive_id = _sha(('cabinet|%s|%s' % (account_id(desk.c), meta['raw_sha256'])).encode())
    parts = mime_parts(msg, desk.c)
    by_key = {p['key']: p for p in parts if p.get('state') == 'extraite'}
    chosen, notices = [], []
    if 'file_attachments' in actions:
        selection = data.get('attachments_selection') or {'mode': 'all'}
        if selection.get('mode') == 'selected':
            for item in selection.get('parts') or []:
                p = by_key.get(str(item.get('part_key') or item.get('key') or ''))
                if not p or not (p.get('attachment') or p.get('inline_image')):
                    raise Stop('piece_jointe_inconnue')   # aucune partie qui ne provient pas du message capturé
                chosen.append({**p, 'display_name': safe_name(item.get('display_name') or p['name'] or '', 80)})
        else:
            chosen = [dict(p) for p in parts if p.get('attachment') and not p.get('signature_like') and p.get('state') == 'extraite']
        if not chosen:
            if 'archive_email' not in actions:
                raise Stop('aucune_piece_jointe_a_enregistrer')
            # 5.6.44 (audit F02) : l'absence de pièce n'empêche jamais la conservation du courriel demandée en même temps
            actions = [a for a in actions if a != 'file_attachments']
            notices.append('Aucune pièce jointe à enregistrer pour ce courriel : seul le courriel est conservé.')
    title = re.sub(r'\s+', ' ', str(data.get('title') or meta['headers']['subject'] or 'Courriel')).strip()[:200]
    corr, pieces = correspondances_dir(desk, m), nos_pieces_dir(desk, m)
    mdir = message_dir(meta, archive_id)
    day = mdir.split('/')[1][:10]
    pdir = clean_path(pieces + '/PJ courriels/%s--%s' % (day, archive_id[:8]))
    files = []
    if 'archive_email' in actions:
        files += [{'name': 'original.eml', 'kind': 'original', 'destination_usage': 'correspondances'},
                  {'name': 'lecture.md', 'kind': 'reading', 'destination_usage': 'correspondances'},
                  {'name': 'metadata.json', 'kind': 'manifest', 'destination_usage': 'correspondances'}]
    for p in chosen:
        files.append({'name': piece_name(p), 'kind': 'attachment', 'destination_usage': 'nos_pieces', 'part_key': p['key']})
    pid = secrets.token_hex(16)
    proposal = {'proposal_id': pid, 'version': 1, 'source': meta['source'], 'account_id': account_id(desk.c), 'archive_id': archive_id,
                'raw_sha256': meta['raw_sha256'], 'raw_size': meta['raw_size'], 'internaldate': meta['internaldate'], 'headers': meta['headers'],
                'direction': meta['direction'], 'matter_id': m['id'], 'title': title, 'actions': actions, 'mode': mode, 'origin': origin,
                'matter_resolution': {'state': 'confirmed', 'reason': 'Dossier choisi par l’utilisateur.' if origin == 'user' else
                                      'Rattachement confirmé unique (correspondant validé de ce seul dossier).'},
                'paths': {'correspondances': corr, 'message_dir': clean_path(corr + '/' + mdir), 'pieces_dir': pdir, 'matter_root': clean_path(m['path'])},
                'destinations_display': {**({'archive_email': '%s / %s' % (matter_display(m), PurePosixPath(corr).name)} if 'archive_email' in actions else {}),
                                         **({'file_attachments': '%s / %s / PJ courriels' % (matter_display(m), PurePosixPath(pieces).name)} if chosen else {})},
                'files_expected': files,
                'parts': [{'key': p['key'], 'name': p['name'], 'display_name': p.get('display_name') or p['name'], 'type': p['type'],
                           'size': p['size'], 'sha256': p['sha256'], 'stored_name': piece_name(p)} for p in chosen],
                'warnings': ([] if meta['direction'] in ('received', 'sent') else ['Sens du message à vérifier (en-têtes)']), 'notices': notices}
    desk.db.execute('INSERT INTO mail_archive_proposals(id,owner,version,data_json,created_at) VALUES(?,?,?,?,?)',
                    (pid, owner, 1, json.dumps(proposal, ensure_ascii=False), _now()))
    desk.db.commit()
    return proposal


def confirm(desk, owner, data):
    """Crée ou rejoint le classement (archive, dossier) et ses travaux. Idempotent par clé de requête et par proposition."""
    pid = str(data.get('proposal_id') or '')
    key = str(data.get('request_key') or '')[:80]
    if not re.fullmatch(r'[0-9a-f]{32}', pid) or not re.fullmatch(r'[\w.:-]{8,80}', key):
        raise Stop('confirmation_invalide')
    row = desk.db.execute('SELECT * FROM mail_archive_proposals WHERE request_key=?', (key,)).fetchone()
    if row and row['link_id']:
        return status(desk, row['link_id'])
    row = desk.db.execute('SELECT * FROM mail_archive_proposals WHERE id=?', (pid,)).fetchone()
    if not row:
        raise Stop('proposition_absente')
    if row['owner'] != owner and owner != 'cabinet':
        raise Stop('proposition_autre_utilisateur')
    if int(data.get('expected_version') or 0) != row['version']:
        raise Stop('proposition_obsolete')
    if row['link_id']:
        return status(desk, row['link_id'])
    p = json.loads(row['data_json'])
    _matter(desk, p['matter_id'])   # le dossier existe encore
    from .pause5629 import is_paused
    if p['origin'] == 'automatic' and is_paused(desk, p['matter_id']):
        raise Stop('agent_en_pause_sur_ce_dossier')
    link_id = _sha(('link|%s|%s' % (p['archive_id'], p['matter_id'])).encode())
    stamp = _now()
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    try:
        desk.db.execute('INSERT OR IGNORE INTO mail_archives(id,scope_id,account_id,raw_sha256,raw_size,message_id,direction,headers_json,declared_date,'
                        'internaldate,capture_state,extraction_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (p['archive_id'], 'cabinet', p['account_id'], p['raw_sha256'], p['raw_size'], p['headers']['message_id'], p['direction'],
                         json.dumps(p['headers'], ensure_ascii=False), p['headers']['declared_date'] or None, p['internaldate'] or stamp, 'captured',
                         EXTRACTION_VERSION, stamp))
        s = p['source']
        desk.db.execute('INSERT OR REPLACE INTO mail_archive_sources VALUES(?,?,?,?,?,?,?)',
                        (p['account_id'], s['folder'], s['uidvalidity'], s['uid'], p['archive_id'], stamp, 'present'))
        desk.db.execute('INSERT OR IGNORE INTO mail_archive_links(id,archive_id,matter_id,owner,title,mode,resolution_json,confirmed_by,confirmed_at,'
                        'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (link_id, p['archive_id'], p['matter_id'], owner, p['title'],
                         'automatic_confirmed' if p['origin'] == 'automatic' else ('manual' if p['mode'] == 'manual' else 'assisted'),
                         json.dumps({**p['matter_resolution'], 'paths': p['paths'], 'parts': p['parts']}, ensure_ascii=False), owner, stamp, stamp, stamp))
        link = desk.db.execute('SELECT * FROM mail_archive_links WHERE id=?', (link_id,)).fetchone()
        res = json.loads(link['resolution_json'])
        if 'file_attachments' in p['actions']:   # nouvelles pièces ajoutées à la sélection, chemins stables du premier classement
            known = {x['key'] for x in res.get('parts', [])}
            res['parts'] = res.get('parts', []) + [x for x in p['parts'] if x['key'] not in known]
        jobs = []
        for action in p['actions']:
            column, state_col = ('email_requested', 'conservation_state') if action == 'archive_email' else ('attachments_requested', 'attachments_state')
            current = _reconcile(desk, link, action)   # 5.6.44 (audit F07) : l'état du classement suit celui de son travail
            desk.db.execute('UPDATE mail_archive_links SET %s=1, resolution_json=?, version=version+1, updated_at=? WHERE id=?' % column,
                            (json.dumps(res, ensure_ascii=False), stamp, link_id))
            if current in ACTIVE_LINK_STATES:
                continue   # travail réellement actif (en file, en cours, suspendu ou arrêt en cours de confirmation) : on le rejoint
            if action == 'archive_email' and current == 'verified' and link['extraction_state'] == 'complete':
                continue   # original vérifié et lecture complète : rien à refaire (une lecture partielle est régénérée, audit F08)
            # 5.6.44 (audit F03) : le dossier confirmé est porté par le travail (pause du dossier, contrôles pendant l'exécution)
            job = desk.enqueue(JOB_FOR[action], {'link_id': link_id, 'proposal_id': pid, 'matter': p['matter_id']}, priority=0, transactional=True,
                               origin='automatic' if p['origin'] == 'automatic' else 'user')
            desk.db.execute('UPDATE mail_archive_links SET %s=?, %s=? WHERE id=?' % (state_col, 'job_id' if action == 'archive_email' else 'attachments_job_id'),
                            ('queued', job, link_id))
            jobs.append({'job_id': job, 'action': action, 'state': 'queued'})
        desk.db.execute('UPDATE mail_archive_proposals SET request_key=?, link_id=? WHERE id=?', (key, link_id, pid))
        desk.db.execute('INSERT INTO mail_archive_events(link_id,actor,action,from_version,to_version,detail_json,at) VALUES(?,?,?,?,?,?,?)',
                        (link_id, owner, 'confirm', link['version'], link['version'] + 1, json.dumps({'actions': p['actions'], 'origin': p['origin']}), stamp))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    for j in jobs:
        desk.notify_queued(j['job_id'], {'matter': p['matter_id']})
    out = status(desk, link_id)
    out['jobs'] = jobs
    return out


# ------------------------------------------------------------------------------------------------ dépôt vérifié (commun aux deux actions)
def _asset(desk, link_id, kind, part_key, version=1):
    return desk.db.execute('SELECT * FROM mail_archive_assets WHERE link_id=? AND kind=? AND part_key=? AND version=?',
                           (link_id, kind, part_key, version)).fetchone()


# 5.6.44 (audit F07) : états d'un classement dont le travail est réellement actif ; un état « en file » dont le travail est terminé
# ou absent est réconcilié (annulé, erreur) pour permettre une nouvelle commande.
ACTIVE_LINK_STATES = ('queued', 'saving', 'verifying', 'cancel_requested')
ACTIVE_JOB_STATES = ('pending', 'running', 'suspended', 'cancel_requested')


def _projected(desk, link, action):
    """État affichable d'une action du classement, cohérent avec la file (sans écriture)."""
    state_col, job_col = ('conservation_state', 'job_id') if action == 'archive_email' else ('attachments_state', 'attachments_job_id')
    current = link[state_col]
    if current not in ACTIVE_LINK_STATES:
        return current
    job = desk.db.execute('SELECT status FROM jobs WHERE id=?', (link[job_col],)).fetchone() if link[job_col] else None
    if job and job['status'] in ACTIVE_JOB_STATES:
        return 'cancel_requested' if job['status'] == 'cancel_requested' else current
    if job and job['status'] == 'cancelled':
        return 'cancelled'
    return 'error'


def _reconcile(desk, link, action):
    """Écrit l'état projeté quand il diffère (appelé dans la transaction de l'appelant) ; renvoie l'état réconcilié."""
    state_col = 'conservation_state' if action == 'archive_email' else 'attachments_state'
    state = _projected(desk, link, action)
    if state != link[state_col] and state in ('cancelled', 'error'):
        desk.db.execute('UPDATE mail_archive_links SET %s=?, updated_at=? WHERE id=?' % state_col, (state, _now(), link['id']))
    return state


def reconcile(desk, link_id):
    link = _link(desk, link_id)
    for action in ACTIONS:
        _reconcile(desk, link, action)
    desk.db.commit()


def _guard(desk, link, kind):
    """5.6.44 (audit F04 et F03) : avant chaque nouvel effet d'un travail AUTOMATIQUE (frontière d'engagement), la décision actuelle
    est relue : pause du dossier porté par le classement, autorisation effective de l'action exacte (règle générale du compte ou du
    cabinet, exception de ce message), dossier du mandat. Une commande de l'utilisateur garde son propre mandat."""
    jid = getattr(desk, 'active_job_id', None)
    if not jid:
        return
    row = desk.db.execute('SELECT origin FROM jobs WHERE id=?', (jid,)).fetchone()
    if not row or row['origin'] != 'automatic':
        return
    from .pause5629 import is_paused
    if is_paused(desk, link['matter_id']):
        raise Stop('agent_en_pause_sur_ce_dossier')
    src = desk.db.execute('SELECT folder,uidvalidity,uid FROM mail_archive_sources WHERE archive_id=? AND account_id=? ORDER BY observed_at DESC',
                          (link['archive_id'], account_id(desk.c))).fetchone()
    if not src:
        raise Stop('source_du_courriel_absente')
    action = 'file_attachments' if kind == 'attachment' else 'archive_email'
    eff = effective(desk, {'folder': src['folder'], 'uidvalidity': src['uidvalidity'], 'uid': src['uid']}, link['matter_id'])
    if not eff[action]['allowed']:
        raise Stop('autorisation_retiree')
    if eff['matter_id'] and eff['matter_id'] != link['matter_id']:
        raise Stop('dossier_du_mandat_modifie')


def _versioned(path, version):
    """Chemin de la version ``version`` d'un fichier dérivé : la version 1 garde le nom d'origine (``lecture.md``), les suivantes
    reçoivent un suffixe (``lecture.v2.md``) ; aucune version vérifiée n'est jamais remplacée."""
    if version <= 1:
        return path
    p = PurePosixPath(path)
    return str(p.with_name('%s.v%d%s' % (p.stem, version, p.suffix)))


def _derived(desk, client, link, kind, base_path, data, mime, provenance, part_key='', source_name='', content_id='', boundary=''):
    """5.6.44 (audit F08) : dépôt d'un dérivé (lecture, manifeste, image) versionné. Contenu identique à la version courante vérifiée :
    rien n'est réécrit. Contenu nouveau : nouvelle version à un nouveau chemin, déposée puis vérifiée ; la version précédente passe à
    « remplacée » seulement après cette vérification (elle reste dans le dossier)."""
    sha = _sha(data)
    rows = desk.db.execute('SELECT * FROM mail_archive_assets WHERE link_id=? AND kind=? AND part_key=? ORDER BY version DESC',
                           (link['id'], kind, part_key)).fetchall()
    current = next((r for r in rows if r['state'] == 'verified'), None)
    if current and current['sha256'] == sha:
        return {'path': current['path'], 'state': 'verified', 'sha256': sha, 'rejoined': True, 'version': current['version']}
    retry = next((r for r in rows if r['sha256'] == sha and r['state'] in ('staged', 'writing', 'uncertain')), None)
    if retry:
        version = retry['version']
    else:
        version = (rows[0]['version'] + 1) if rows else 1
    out = _deposit(desk, client, link, kind, 'correspondances', _versioned(base_path, version), data, mime, provenance, part_key=part_key,
                   source_name=source_name, content_id=content_id, boundary=boundary, version=version)
    desk.db.execute("UPDATE mail_archive_assets SET state='superseded' WHERE link_id=? AND kind=? AND part_key=? AND version<>? AND state='verified'",
                    (link['id'], kind, part_key, version))
    desk.db.commit()
    return {**out, 'version': version}


def _deposit(desk, client, link, kind, usage, path, data, mime, provenance, part_key='', source_name='', content_id='', boundary='', version=1):
    """Réserve l'opération (transaction courte), contrôle l'arrêt, dépose exclusivement HORS transaction, relit et compare l'empreinte.
    Un fichier déjà présent n'est réutilisé que si ses octets sont identiques ; sinon conflit expliqué (jamais d'écrasement)."""
    from .pause5629 import checkpoint
    sha, lim = _sha(data), limits(desk.c)
    done = _asset(desk, link['id'], kind, part_key, version)
    if done and done['state'] == 'verified' and done['sha256'] == sha:
        return {'path': done['path'], 'state': 'verified', 'sha256': sha, 'rejoined': True}
    aid = _sha(('asset|%s|%s|%s' % (link['id'], kind, part_key) + ('|v%d' % version if version > 1 else '')).encode())[:32]
    stamp = _now()
    desk.db.execute('INSERT OR IGNORE INTO mail_archive_assets(id,link_id,kind,destination_usage,part_key,mime_type,source_name,content_id,path,sha256,'
                    'size_bytes,version,state,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (aid, link['id'], kind, usage, part_key, mime, source_name[:300], content_id[:300], path, sha, len(data), version, 'staged', provenance))
    op = _sha(('op|%s|%s|%s|%s' % (link['id'], kind, path, sha)).encode())[:32]   # 5.6.44 : version (chemin) et empreinte dans l'identifiant
    desk.db.execute('INSERT OR IGNORE INTO mail_archive_operations(id,request_key,link_id,phase,destination,expected_sha256,state,created_at,updated_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?)', (op, link['id'], link['id'], kind + ':' + part_key, path, sha, 'prepared', stamp, stamp))
    desk.db.commit()
    checkpoint(desk)   # frontière d'engagement : un arrêt demandé avant ce point empêche l'écriture
    _guard(desk, link, kind)   # 5.6.44 : autorisation et pause du dossier relues avant la création des dossiers et le dépôt
    desk.db.execute("UPDATE mail_archive_operations SET state='started',updated_at=? WHERE id=?", (_now(), op))
    desk.db.execute("UPDATE mail_archive_assets SET state='writing' WHERE id=?", (aid,))
    desk.db.commit()
    parent = str(PurePosixPath(path).parent)
    client.ensure_folder(parent, boundary)
    limit = lim['archive_max_message_bytes'] if kind == 'original' else max(lim['archive_max_part_bytes'], len(data))
    rejoined = False
    try:
        client.put_archive(path, data, limit, mime)
    except Stop as ex:
        if str(ex) not in ('http_412', 'http_409'):
            desk.db.execute("UPDATE mail_archive_operations SET state='uncertain',error_code=?,updated_at=? WHERE id=?", (str(ex)[:80], _now(), op))
            desk.db.execute("UPDATE mail_archive_assets SET state='uncertain' WHERE id=?", (aid,))
            desk.db.commit()
            raise
        rejoined = True   # fichier déjà présent (reprise après coupure) : vérifié ci-dessous, jamais remplacé
    try:
        back = client.read_archive(path, limit + 1)
    except Stop as ex:
        desk.db.execute("UPDATE mail_archive_operations SET state='uncertain',error_code=?,updated_at=? WHERE id=?", ('relecture:' + str(ex)[:60], _now(), op))
        desk.db.execute("UPDATE mail_archive_assets SET state='uncertain' WHERE id=?", (aid,))
        desk.db.commit()
        raise Stop('relecture_depot_impossible') from None
    if _sha(back) != sha:
        desk.db.execute("UPDATE mail_archive_operations SET state='error',error_code=?,updated_at=? WHERE id=?",
                        ('conflit_fichier_existant' if rejoined else 'empreinte_differente', _now(), op))
        desk.db.execute("UPDATE mail_archive_assets SET state='error' WHERE id=?", (aid,))
        desk.db.commit()
        raise Stop('conflit_fichier_existant' if rejoined else 'empreinte_depot_differente')
    desk.db.execute("UPDATE mail_archive_operations SET state='confirmed',updated_at=? WHERE id=?", (_now(), op))
    desk.db.execute("UPDATE mail_archive_assets SET state='verified',verified_at=? WHERE id=?", (_now(), aid))
    desk.db.commit()
    return {'path': path, 'state': 'verified', 'sha256': sha, 'rejoined': rejoined}


def _link(desk, link_id):
    row = desk.db.execute('SELECT * FROM mail_archive_links WHERE id=?', (str(link_id or ''),)).fetchone()
    if not row:
        raise Stop('classement_absent')
    from .membres5650 import viewer_hidden   # 5.6.51 (audit B01) : le dossier de l'objet est contrôlé avant toute lecture
    if row['matter_id'] in viewer_hidden():
        raise Stop('dossier_reserve')
    return row


def _set(desk, link_id, **values):
    cols = ','.join('%s=?' % k for k in values)
    desk.db.execute('UPDATE mail_archive_links SET %s, updated_at=? WHERE id=?' % cols, tuple(values.values()) + (_now(), link_id))
    desk.db.commit()


def _recapture(desk, link, box=None):
    """Relit le message source et vérifie que ses octets sont ceux de la préparation (source déplacée ou modifiée : explication)."""
    archive = desk.db.execute('SELECT * FROM mail_archives WHERE id=?', (link['archive_id'],)).fetchone()
    src = desk.db.execute('SELECT * FROM mail_archive_sources WHERE archive_id=? ORDER BY observed_at DESC', (link['archive_id'],)).fetchone()
    if not archive or not src:
        raise Stop('source_du_courriel_absente')
    try:
        raw, msg, meta = capture(desk, {'folder': src['folder'], 'uid': src['uid'], 'uidvalidity': src['uidvalidity']}, box)
    except Stop as ex:
        if str(ex) in ('message_imap_disparu', 'uidvalidity_modifiee'):
            desk.db.execute("UPDATE mail_archive_sources SET availability='missing' WHERE account_id=? AND folder=? AND uidvalidity=? AND uid=?",
                            (src['account_id'], src['folder'], src['uidvalidity'], src['uid']))
            desk.db.commit()
            raise Stop('courriel_source_deplace_ou_absent') from None
        raise
    if meta['raw_sha256'] != archive['raw_sha256']:
        raise Stop('courriel_source_modifie_depuis_la_preparation')
    return raw, msg, meta


# 5.6.44 : interruptions qui arrêtent un travail de conservation sans être des échecs du dépôt
INTERRUPTIONS = frozenset({'operation_annulee', 'ia_en_pause', 'automatismes_en_pause', 'agent_en_pause_sur_ce_dossier', 'dossier_en_pause',
                           'controle_pause_indisponible', 'autorisation_retiree', 'dossier_du_mandat_modifie'})


def _failed_state(error):
    code = str(error)
    if code == 'operation_annulee' or code in ('autorisation_retiree', 'dossier_du_mandat_modifie'):
        return 'cancelled'
    if code in INTERRUPTIONS:
        return 'queued'   # pause : le travail est suspendu puis repris ; le classement reste en file
    if code == 'relecture_depot_impossible':
        return 'uncertain'
    return 'error'


def run_archive_email(desk, args, box=None, client=None):
    """Travail déterministe : original vérifié, puis dérivés ; l'échec d'un dérivé laisse visible l'état réel de l'original."""
    link = _link(desk, args.get('link_id'))
    res = json.loads(link['resolution_json'])
    paths = res['paths']
    _set(desk, link['id'], conservation_state='saving')
    try:
        raw, msg, meta = _recapture(desk, link, box)
        client = client or _client(desk)
        mdir = paths['message_dir']
        if not under(mdir, paths['matter_root']):
            raise Stop('destination_hors_dossier')
        _deposit(desk, client, link, 'original', 'correspondances', mdir + '/original.eml', raw['raw'], 'message/rfc822', 'mail_original',
                 boundary=paths['matter_root'])
    except Stop as ex:
        _set(desk, link['id'], conservation_state=_failed_state(ex))
        raise
    _set(desk, link['id'], conservation_state='verified')
    extraction = 'complete'
    try:   # dérivés : reproductibles ; un échec ne remet jamais en cause l'original vérifié
        parts = mime_parts(msg, desk.c)
        pieces = {a['part_key']: a['path'] for a in desk.db.execute(
            "SELECT part_key,path FROM mail_archive_assets WHERE link_id=? AND kind='attachment' AND state='verified'", (link['id'],))}
        text, partial = reading_md(meta, msg, parts, pieces)
        _derived(desk, client, link, 'reading', mdir + '/lecture.md', text.encode('utf-8'), 'text/markdown; charset=utf-8',
                 'mail_extraction', boundary=paths['matter_root'])
        for p in parts:
            if p.get('inline_image') and p.get('state') == 'extraite' and p['data']:
                _derived(desk, client, link, 'inline_image', mdir + '/images/' + image_name(p), p['data'], p['type'],
                         'mail_extraction', part_key=p['key'], source_name=p['name'], content_id=p['cid'], boundary=paths['matter_root'])
        created = desk.db.execute('SELECT created_at FROM mail_archives WHERE id=?', (link['archive_id'],)).fetchone()
        manifest = {'format': 'axiorhub-correspondance/1', 'extraction_version': EXTRACTION_VERSION, 'archive_id': link['archive_id'],
                    'matter_id': link['matter_id'], 'raw_sha256': meta['raw_sha256'], 'raw_size': meta['raw_size'], 'headers': meta['headers'],
                    'internaldate': meta['internaldate'], 'direction': meta['direction'],
                    'source': {k: meta['source'][k] for k in ('folder', 'uidvalidity', 'uid')},
                    'parts': [{k: p.get(k) for k in ('key', 'type', 'name', 'cid', 'size', 'sha256', 'state', 'reason')} for p in parts],
                    'nos_pieces': pieces, 'reading': {'partial': partial},
                    'created_at': created['created_at'] if created else link['created_at']}   # date stable : contenu identique = fichier identique
        _derived(desk, client, link, 'manifest', mdir + '/metadata.json',
                 json.dumps(manifest, ensure_ascii=False, indent=1).encode('utf-8'), 'application/json', 'mail_extraction', boundary=paths['matter_root'])
        extraction = 'partial' if partial else 'complete'
    except Stop as ex:
        if str(ex) in INTERRUPTIONS:
            _set(desk, link['id'], extraction_state='partial')
            raise
        extraction = 'partial'
    _set(desk, link['id'], extraction_state=extraction)
    return {'message': 'Courriel conservé dans %s : original vérifié%s.' % (PurePosixPath(paths['correspondances']).name,
                                                                          ', lecture partielle' if extraction == 'partial' else ', lecture prête'),
            'link_id': link['id'], 'conservation_state': 'verified', 'extraction_state': extraction}


def run_file_attachments(desk, args, box=None, client=None):
    """Travail déterministe : pièces choisies déposées dans NOS PIECES ; bilan par fichier ; aucune copie EML."""
    link = _link(desk, args.get('link_id'))
    res = json.loads(link['resolution_json'])
    paths = res['paths']
    _set(desk, link['id'], attachments_state='saving')
    try:
        raw, msg, meta = _recapture(desk, link, box)   # lecture temporaire : elle ne vaut pas conservation du courriel
    except Stop as ex:
        _set(desk, link['id'], attachments_state=_failed_state(ex))
        raise
    parts = {p['key']: p for p in mime_parts(msg, desk.c) if p.get('state') == 'extraite'}
    client = client or _client(desk)
    if not under(paths['pieces_dir'], paths['matter_root']):
        raise Stop('destination_hors_dossier')
    files, ok, failed = [], 0, 0
    for item in res.get('parts', []):
        p = parts.get(item['key'])
        if not p or p['sha256'] != item['sha256']:
            files.append({'part_key': item['key'], 'name': item['stored_name'], 'state': 'error', 'error': 'piece_modifiee_ou_absente'})
            failed += 1
            continue
        try:
            out = _deposit(desk, client, link, 'attachment', 'nos_pieces', paths['pieces_dir'] + '/' + item['stored_name'], p['data'],
                           p['type'] or 'application/octet-stream', 'attachment', part_key=item['key'], source_name=item['name'], boundary=paths['matter_root'])
            files.append({'part_key': item['key'], 'name': item['stored_name'], 'state': 'verified', 'sha256': out['sha256'], 'path': out['path']})
            ok += 1
        except Stop as ex:
            if str(ex) in INTERRUPTIONS:   # arrêt, pause ou autorisation retirée : rien d'autre n'est déposé
                _set(desk, link['id'], attachments_state=('partial' if ok and _failed_state(ex) != 'queued' else _failed_state(ex)))
                raise
            files.append({'part_key': item['key'], 'name': item['stored_name'], 'state': 'error', 'error': str(ex)[:80]})
            failed += 1
    state = 'verified' if ok and not failed else ('partial' if ok else 'error')
    _set(desk, link['id'], attachments_state=state)
    message = ('%d pièce(s) enregistrée(s) dans NOS PIECES' % ok + ((', %d en échec' % failed) if failed else '') +
               ('.' if link['email_requested'] else '. Courriel complet non conservé dans CORRESPONDANCES.'))
    if not ok:
        raise Stop('aucune_piece_enregistree')
    return {'message': message, 'link_id': link['id'], 'attachments_state': state, 'files': files}


def perform(desk, kind, args):
    if kind == 'archive_correspondence5643':
        return run_archive_email(desk, args)
    if kind == 'file_mail_attachments5643':
        return run_file_attachments(desk, args)
    raise Stop('action_inconnue')


# ------------------------------------------------------------------------------------------------ lecture : état, liste, détail
def status(desk, link_id):
    link = _link(desk, link_id)
    assets = desk.db.execute('SELECT kind,part_key,source_name,path,sha256,size_bytes,state,verified_at FROM mail_archive_assets WHERE link_id=? '
                             'ORDER BY kind,part_key', (link['id'],)).fetchall()
    original = next((dict(a) for a in assets if a['kind'] == 'original' and a['state'] == 'verified'), None)
    pieces = [dict(a) for a in assets if a['kind'] == 'attachment']
    selected = len(json.loads(link['resolution_json']).get('parts', []))
    link = {**dict(link), 'conservation_state': _projected(desk, link, 'archive_email'), 'attachments_state': _projected(desk, link, 'file_attachments')}
    return {'link_id': link['id'], 'archive_id': link['archive_id'], 'matter_id': link['matter_id'], 'title': link['title'],
            'email_requested': bool(link['email_requested']), 'attachments_requested': bool(link['attachments_requested']),
            'conservation_state': link['conservation_state'], 'attachments_state': link['attachments_state'],
            'conservation_label': STATE_LABELS.get(link['conservation_state'], link['conservation_state']),
            'attachments_label': STATE_LABELS.get(link['attachments_state'], link['attachments_state']).replace('Original vérifié', 'Pièces vérifiées'),
            'extraction_state': link['extraction_state'], 'original': original, 'version': link['version'],
            'attachments': {'selected': selected, 'verified': sum(1 for a in pieces if a['state'] == 'verified'),
                            'failed': sum(1 for a in pieces if a['state'] in ('error', 'uncertain')), 'files': pieces},
            'goto_correspondances': '/dossier?id=%s&vue=correspondances&archive=%s' % (link['matter_id'], link['archive_id']),
            'goto_pieces': '/dossier?id=%s&vue=pieces' % link['matter_id']}


def links_for_source(desk, folder, uid, uidvalidity=''):
    sql = ('SELECT l.id FROM mail_archive_links l JOIN mail_archive_sources s ON s.archive_id=l.archive_id WHERE s.account_id=? AND s.folder=? AND s.uid=?'
           + (' AND s.uidvalidity=?' if uidvalidity else ''))
    params = (account_id(desk.c), folder, str(uid)) + ((str(uidvalidity),) if uidvalidity else ())
    return [status(desk, r[0]) for r in desk.db.execute(sql, params)]


def listing(desk, matter, query='', limit=200):
    """Une ligne par correspondance CONSERVÉE (original vérifié) — ni les pièces seules, ni chaque fichier dérivé."""
    rows = desk.db.execute('SELECT l.*, a.headers_json, a.direction, a.declared_date, a.internaldate FROM mail_archive_links l '
                           "JOIN mail_archives a ON a.id=l.archive_id WHERE l.matter_id=? AND l.email_requested=1 AND l.conservation_state<>'withdrawn' "
                           'ORDER BY COALESCE(a.declared_date,a.internaldate) DESC LIMIT ?', (matter, int(limit))).fetchall()
    out = []
    from .common import fold
    for r in rows:
        h = json.loads(r['headers_json'] or '{}')
        item = {'link_id': r['id'], 'archive_id': r['archive_id'], 'title': r['title'], 'subject': h.get('subject', ''), 'from': h.get('from', ''),
                'to': h.get('to', ''), 'date': r['declared_date'] or r['internaldate'], 'direction': r['direction'], 'category': r['category'],
                'tags': json.loads(r['tags_json'] or '[]'), 'state': r['conservation_state'], 'state_label': STATE_LABELS.get(r['conservation_state'], ''),
                'extraction': r['extraction_state'],
                'pieces': desk.db.execute("SELECT COUNT(*) FROM mail_archive_assets WHERE link_id=? AND kind='attachment' AND state='verified'",
                                          (r['id'],)).fetchone()[0]}
        if query and fold(query) not in fold(' '.join(str(item[k]) for k in ('title', 'subject', 'from', 'to'))):
            continue
        out.append(item)
    return out


def detail(desk, link_id, client=None):
    """Lecture SANS IMAP : la version de lecture archivée et les métadonnées ; les notes du cabinet restent séparées du message."""
    st = status(desk, link_id)
    client = client or _client(desk)
    reading = desk.db.execute("SELECT path FROM mail_archive_assets WHERE link_id=? AND kind='reading' AND state='verified' ORDER BY version DESC",
                              (st['link_id'],)).fetchone()
    text = ''
    if reading:
        try:
            text = client.read_archive(reading['path'], 5_000_000).decode('utf-8', 'replace')
        except Stop:
            text = ''
    notes = [dict(r) for r in desk.db.execute("SELECT path,version,verified_at FROM mail_archive_assets WHERE link_id=? AND kind='annotation' ORDER BY version",
                                              (st['link_id'],))]
    return {**st, 'reading': text, 'reading_available': bool(text), 'annotations': notes}


def original(desk, link_id, client=None):
    """Octets de l'original, relus depuis la destination autorisée et comparés à l'empreinte enregistrée."""
    st = status(desk, link_id)
    if not st['original']:
        raise Stop('original_non_conserve')
    data = (client or _client(desk)).read_archive(st['original']['path'], limits(desk.c)['archive_max_message_bytes'] + 1)
    if _sha(data) != st['original']['sha256']:
        raise Stop('original_altere')
    return data


def update(desk, owner, data):
    link = _link(desk, data.get('link_id'))
    if int(data.get('expected_version') or 0) != link['version']:
        raise Stop('classement_modifie_entre_temps')
    title = re.sub(r'\s+', ' ', str(data.get('title') or link['title'])).strip()[:200]
    tags = [re.sub(r'\s+', ' ', str(t)).strip()[:40] for t in (data.get('tags') or json.loads(link['tags_json'])) if str(t).strip()][:20]
    category = str(data.get('category') or link['category'])[:40]
    desk.db.execute('UPDATE mail_archive_links SET title=?, tags_json=?, category=?, version=version+1, updated_at=? WHERE id=? AND version=?',
                    (title, json.dumps(tags, ensure_ascii=False), category, _now(), link['id'], link['version']))
    desk.db.execute('INSERT INTO mail_archive_events(link_id,actor,action,from_version,to_version,detail_json,at) VALUES(?,?,?,?,?,?,?)',
                    (link['id'], owner, 'update', link['version'], link['version'] + 1, json.dumps({'fields': ['title', 'tags', 'category']}), _now()))
    desk.db.commit()
    return status(desk, link['id'])


def annotate(desk, owner, data, client=None):
    """Note du cabinet : nouvelle version ``annotations-vN.md`` à côté du message ; l'original n'est jamais modifié."""
    link = _link(desk, data.get('link_id'))
    text = str(data.get('text') or '').replace('\r\n', '\n').strip()
    if not 1 <= len(text) <= 20000:
        raise Stop('annotation_invalide')
    res = json.loads(link['resolution_json'])
    version = (desk.db.execute("SELECT MAX(version) FROM mail_archive_assets WHERE link_id=? AND kind='annotation'", (link['id'],)).fetchone()[0] or 0) + 1
    body = '# Note du cabinet (version %d)\n\n_Par %s, le %s. Cette note n’est pas le courriel reçu._\n\n%s\n' % (version, owner, _now()[:16].replace('T', ' '), text)
    path = res['paths']['message_dir'] + '/annotations-v%d.md' % version
    data_b = body.encode('utf-8')
    client = client or _client(desk)
    aid = _sha(('annotation|%s|%d' % (link['id'], version)).encode())[:32]
    desk.db.execute('INSERT INTO mail_archive_assets(id,link_id,kind,destination_usage,part_key,mime_type,path,sha256,size_bytes,version,state,provenance) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (aid, link['id'], 'annotation', 'correspondances', '', 'text/markdown', path, _sha(data_b), len(data_b),
                                                        version, 'writing', 'human_annotation'))
    desk.db.commit()
    client.ensure_folder(res['paths']['message_dir'], res['paths']['matter_root'])
    client.put_archive(path, data_b, 1_000_000, 'text/markdown; charset=utf-8')
    if _sha(client.read_archive(path, 1_000_001)) != _sha(data_b):
        desk.db.execute("UPDATE mail_archive_assets SET state='error' WHERE id=?", (aid,))
        desk.db.commit()
        raise Stop('empreinte_depot_differente')
    desk.db.execute("UPDATE mail_archive_assets SET state='verified',verified_at=? WHERE id=?", (_now(), aid))
    desk.db.execute('INSERT INTO mail_archive_events(link_id,actor,action,from_version,to_version,detail_json,at) VALUES(?,?,?,?,?,?,?)',
                    (link['id'], owner, 'annotate', None, version, '{}', _now()))
    desk.db.commit()
    return {'version': version, 'path': path, 'message': 'Note enregistrée (version %d) ; le courriel d’origine est inchangé.' % version}


# ------------------------------------------------------------------------------------------------ autorisations générales et par message
def get_policy(desk, scope_kind='account'):
    key = account_id(desk.c) if scope_kind == 'account' else 'cabinet'
    row = desk.db.execute('SELECT * FROM mail_filing_policies WHERE scope_id=? AND scope_kind=? AND scope_key=?', ('cabinet', scope_kind, key)).fetchone()
    if not row:
        return {'scope_kind': scope_kind, 'scope_key': key, 'email_auto': False, 'attachments_auto': False, 'version': 0, 'effective_from': ''}
    return {'scope_kind': scope_kind, 'scope_key': key, 'email_auto': bool(row['email_auto']), 'attachments_auto': bool(row['attachments_auto']),
            'version': row['version'], 'effective_from': row['effective_from'], 'updated_by': row['updated_by'], 'updated_at': row['updated_at']}


def set_policy(desk, owner, role, data):
    """Deux autorisations générales distinctes, pour les NOUVEAUX messages du compte affiché (portée cabinet : administrateur seul)."""
    scope_kind = 'cabinet' if data.get('scope_kind') == 'cabinet' else 'account'
    if scope_kind == 'cabinet' and role != 'administrateur':
        raise Stop('role_insuffisant')
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    current = get_policy(desk, scope_kind)
    if int(data.get('expected_version') or 0) != current['version']:
        raise Stop('reglage_modifie_entre_temps')
    email = bool(data.get('email_auto'))
    pieces = bool(data.get('attachments_auto'))
    stamp = _now()
    effective = current['effective_from'] if (current['email_auto'] or current['attachments_auto']) and current['effective_from'] else stamp
    desk.db.execute('INSERT OR REPLACE INTO mail_filing_policies VALUES(?,?,?,?,?,?,?,?,?)',
                    ('cabinet', scope_kind, current['scope_key'], int(email), int(pieces), effective if (email or pieces) else stamp,
                     current['version'] + 1, owner, stamp))
    desk.db.commit()
    desk.audit('correspondances5643_politique', {'scope': scope_kind, 'email_auto': email, 'attachments_auto': pieces, 'owner': owner})
    return {**get_policy(desk, scope_kind), 'historical_backfill_enabled': False,
            'message': 'Règles enregistrées : %s ; %s. Elles s’appliquent aux nouveaux messages de ce compte.' % (
                'courriels enregistrés automatiquement' if email else 'courriels non enregistrés automatiquement',
                'pièces jointes enregistrées automatiquement' if pieces else 'pièces jointes non enregistrées automatiquement')}


def get_message_rule(desk, source):
    row = desk.db.execute('SELECT * FROM mail_filing_message_rules WHERE scope_id=? AND account_id=? AND folder=? AND uidvalidity=? AND uid=?',
                          ('cabinet', account_id(desk.c), source['folder'], source['uidvalidity'], source['uid'])).fetchone()
    if not row:
        return {'email_override': 'inherit', 'attachments_override': 'inherit', 'matter_id': '', 'version': 0}
    return {'email_override': row['email_override'], 'attachments_override': row['attachments_override'], 'matter_id': row['matter_id'],
            'version': row['version'], 'updated_by': row['updated_by'], 'updated_at': row['updated_at']}


def _selection(data):
    """Sélection des pièces d'un mandat : « toutes » (pièces ordinaires) ou la liste exacte des parties choisies."""
    sel = data.get('attachments_selection') if isinstance(data.get('attachments_selection'), dict) else {}
    if sel.get('mode') == 'selected':
        parts = [{'part_key': str(x.get('part_key') or x.get('key') or '')[:80], 'display_name': str(x.get('display_name') or '')[:120]}
                 for x in (sel.get('parts') or []) if isinstance(x, dict)][:200]
        return {'mode': 'selected', 'parts': parts}
    return {'mode': 'all', 'parts': []}


def set_message_rule(desk, owner, data):
    """Exception pour CE SEUL message (localisateur complet) ; jamais étendue au fil, à l'expéditeur ni aux prochains courriels."""
    source = _source(data)
    if not source['uidvalidity']:
        raise Stop('uidvalidity_requise')
    current = get_message_rule(desk, source)
    if int(data.get('expected_version') or 0) != current['version']:
        raise Stop('reglage_modifie_entre_temps')
    values = {}
    for key in ('email_override', 'attachments_override'):
        v = str(data.get(key) or current[key])
        if v not in ('inherit', 'allow', 'deny'):
            raise Stop('autorisation_invalide')
        values[key] = v
    matter = str(data.get('matter_id') if data.get('matter_id') is not None else current['matter_id'])
    if matter:
        _matter(desk, matter)
    stamp = _now()
    sel = _selection(data)
    desk.db.execute('INSERT OR REPLACE INTO mail_filing_message_rules(scope_id,account_id,folder,uidvalidity,uid,email_override,attachments_override,'
                    'matter_id,confirmed_by,confirmed_at,attachments_selection,selected_parts_json,version,updated_by,updated_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('cabinet', account_id(desk.c), source['folder'], source['uidvalidity'], source['uid'], values['email_override'],
                     values['attachments_override'], matter, owner if matter else None, stamp if matter else None, sel['mode'],
                     json.dumps(sel['parts'], ensure_ascii=False), current['version'] + 1, owner, stamp))
    # 5.6.44 (F06) : un mandat qui attendait le dossier le reçoit ; un mandat dont une action est maintenant exclue est retiré
    mandate = _mandate(desk, source)
    if mandate and mandate['state'] in ('pending', 'waiting_matter'):
        acts = [a for a in json.loads(mandate['actions_json']) if values['email_override' if a == 'archive_email' else 'attachments_override'] != 'deny']
        state = ('cancelled' if not acts else ('pending' if matter else 'waiting_matter'))
        desk.db.execute('UPDATE mail_filing_mandates SET matter_id=?, actions_json=?, state=?, version=version+1, updated_at=? WHERE id=?',
                        (matter, json.dumps(acts), state, stamp, mandate['id']))
    desk.db.commit()
    return {**get_message_rule(desk, source), 'effective': effective(desk, source)}


def effective(desk, source, matter=''):
    """Autorisation effective de chaque action pour ce message (avant contrôles de dossier et de pause)."""
    general = get_policy(desk, 'account')
    cabinet = get_policy(desk, 'cabinet')
    rule = get_message_rule(desk, source)
    out = {}
    for action, gkey, okey in (('archive_email', 'email_auto', 'email_override'), ('file_attachments', 'attachments_auto', 'attachments_override')):
        base = general[gkey] or cabinet[gkey]
        override = rule[okey]
        out[action] = {'allowed': override == 'allow' or (override == 'inherit' and base), 'general': base, 'override': override,
                       'reason': {'allow': 'Autorisé pour ce courriel', 'deny': 'Exclu pour ce courriel'}.get(override,
                                                                                                           'Règle générale ' + ('active' if base else 'désactivée'))}
    out['matter_id'] = rule['matter_id'] or matter
    if (out['archive_email']['allowed'] or out['file_attachments']['allowed']) and not out['matter_id']:
        out['notice'] = 'Autorisé pour ce courriel — en attente du dossier.'
    return out


def message_state(desk, uid, folder, box=None):
    """État complet pour le panneau « Classement dans le dossier client » d'un message."""
    source = {'folder': str(folder or desk.c['mail'].get('inbox', 'INBOX')), 'uid': str(uid), 'uidvalidity': ''}
    raw, msg, meta = capture(desk, source, box)
    source['uidvalidity'] = meta['source']['uidvalidity']
    parts = mime_parts(msg, desk.c)
    sender = meta['headers']['from_address'].split(',')[0].strip()
    suggested = confirmed_matter(desk, sender) if meta['direction'] == 'received' else ''
    return {'source': source, 'direction': meta['direction'], 'subject': meta['headers']['subject'], 'from': meta['headers']['from'],
            'raw_size': meta['raw_size'], 'raw_sha256': meta['raw_sha256'],
            'parts': [{'part_key': p['key'], 'name': p.get('name') or '', 'type': p.get('type', ''), 'size': p.get('size', 0),
                       'default': bool(p.get('attachment') and not p.get('signature_like')), 'inline_image': bool(p.get('inline_image')),
                       'state': p.get('state')} for p in parts if p.get('attachment') or p.get('inline_image')],
            'policy': get_policy(desk, 'account'), 'rule': get_message_rule(desk, source), 'effective': effective(desk, source, suggested),
            'suggested_matter': suggested, 'links': links_for_source(desk, source['folder'], source['uid'], source['uidvalidity']),
            'matters': [{'id': m['id'], 'label': matter_display(m)} for m in load_matters(desk.c)]}


# ------------------------------------------------------------------------------------------------ automatique (nouveaux messages)
DEFERRED_MAX = 500


def _cursor_key(desk, folder):
    return 'correspondances5644:curseur:%s:%s' % (account_id(desk.c), folder)


def _mandate(desk, source):
    return desk.db.execute('SELECT * FROM mail_filing_mandates WHERE scope_id=? AND account_id=? AND folder=? AND uidvalidity=? AND uid=?',
                           ('cabinet', account_id(desk.c), source['folder'], source['uidvalidity'], source['uid'])).fetchone()


def _run_mandates(desk, box, limit):
    """5.6.44 (audit F06) : mandats explicites en attente (reprise de l'IA, dossier choisi), indépendants de la borne des nouveaux
    messages ; chacun est revérifié (autorisation actuelle, dossier en pause) puis exécuté une seule fois avec sa sélection exacte."""
    from .pause5629 import is_paused
    queued = 0
    rows = desk.db.execute("SELECT * FROM mail_filing_mandates WHERE state='pending' AND account_id=? ORDER BY created_at LIMIT ?",
                           (account_id(desk.c), int(limit))).fetchall()
    for m in rows:
        if not m['matter_id'] or is_paused(desk, m['matter_id']):
            continue
        source = {'folder': m['folder'], 'uid': m['uid'], 'uidvalidity': m['uidvalidity']}
        eff = effective(desk, source, m['matter_id'])
        actions = [a for a in json.loads(m['actions_json']) if a in ACTIONS and eff[a]['allowed']]
        if not actions:
            desk.db.execute("UPDATE mail_filing_mandates SET state='cancelled', error_code='autorisation_retiree', updated_at=? WHERE id=?", (_now(), m['id']))
            desk.db.commit()
            continue
        try:
            prop = prepare(desk, m['owner'], {'source': source, 'matter_id': m['matter_id'], 'actions': actions,
                                              'attachments_selection': json.loads(m['selection_json'] or '{}')}, box, mode='assisted', origin='automatic')
            out = confirm(desk, m['owner'], {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'mandat-%s-%d' % (m['id'][:40], m['version'])})
        except Stop as ex:
            if str(ex) in ('automatismes_en_pause', 'ia_en_pause', 'agent_en_pause_sur_ce_dossier', 'controle_pause_indisponible'):
                continue   # reste en attente
            desk.db.execute("UPDATE mail_filing_mandates SET state='error', error_code=?, updated_at=? WHERE id=?", (str(ex)[:80], _now(), m['id']))
            desk.db.commit()
            continue
        desk.db.execute("UPDATE mail_filing_mandates SET state='done', link_id=?, error_code='', updated_at=? WHERE id=?", (out['link_id'], _now(), m['id']))
        desk.db.commit()
        queued += 1
    return queued


def auto_tick(desk, box=None, limit=30):
    """Classement automatique sur rattachement confirmé : nouveaux messages seulement (depuis la prise d'effet), messages reçus d'un
    correspondant validé d'UN seul dossier ou dossier confirmé pour ce message ; listes, messages du cabinet et dossiers en pause exclus.
    Les travaux portent l'origine « automatic » : la pause de l'IA les bloque comme tout automatisme."""
    general = get_policy(desk, 'account')
    cabinet = get_policy(desk, 'cabinet')
    any_rule = desk.db.execute("SELECT 1 FROM mail_filing_message_rules WHERE email_override='allow' OR attachments_override='allow' LIMIT 1").fetchone()
    mandates = desk.db.execute("SELECT 1 FROM mail_filing_mandates WHERE state='pending' LIMIT 1").fetchone()
    if not (general['email_auto'] or general['attachments_auto'] or cabinet['email_auto'] or cabinet['attachments_auto'] or any_rule or mandates):
        return {'examined': 0, 'queued': 0}
    if desk.settings('missions567:pause', False):
        return {'examined': 0, 'queued': 0, 'paused': True}   # rien n'est consommé pendant la pause : la progression reprend ensuite
    since = min(x for x in (general['effective_from'], cabinet['effective_from'], _now()) if x)
    own = box is None
    box = box or _box(desk)
    examined = queued = 0
    folder = desk.c['mail'].get('inbox', 'INBOX')
    key = _cursor_key(desk, folder)
    cursor = desk.settings(key, {}) or {}
    before = json.dumps(cursor, sort_keys=True)
    try:
        queued += _run_mandates(desk, box, limit)
        day = datetime.fromisoformat(since.replace('Z', '+00:00')).strftime('%d-%b-%Y')
        found = sorted({str(u) for u in box.search(folder, 'SINCE', day, 'UNDELETED') if str(u).isdigit()}, key=int)
        deferred = {k: v for k, v in (cursor.get('deferred') or {}).items() if k in found}
        last = int(cursor.get('last_uid') or 0)
        now = _now()
        # 5.6.44 (audit F05) : progression persistante par compte et boîte (UID croissants) ; les messages différés (erreur, dossier
        # en pause ou à préciser) sont repris plus tard sans bloquer les suivants ; la limite porte sur les messages examinés.
        retry = [u for u in sorted(deferred, key=int) if deferred[u].get('next', '') <= now][:max(1, int(limit) // 3)]
        fresh = [u for u in found if int(u) > last and u not in deferred]
        for uid in (retry + fresh)[:int(limit)]:
            examined += 1
            outcome = _auto_one(desk, box, folder, uid, since, cursor)
            if uid in fresh:
                last = max(last, int(uid))
            if outcome == 'queued':
                queued += 1
            if outcome in ('queued', 'skipped'):
                deferred.pop(uid, None)
            elif outcome:
                entry = deferred.get(uid) or {'attempts': 0}
                hours = 6 if outcome == 'dossier_a_preciser' else min(24, 1 + entry['attempts'])
                deferred[uid] = {'reason': outcome, 'attempts': entry['attempts'] + 1,
                                 'next': datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + hours * 3600, timezone.utc).isoformat()}
        if len(deferred) > DEFERRED_MAX:
            deferred = dict(sorted(deferred.items(), key=lambda kv: int(kv[0]))[-DEFERRED_MAX:])
        cursor.update({'last_uid': last, 'deferred': deferred})
    finally:
        if own:
            box.close()
        if json.dumps(cursor, sort_keys=True) != before:
            desk.setting(key, cursor)
    return {'examined': examined, 'queued': queued, 'deferred': len(cursor.get('deferred') or {})}


def _auto_one(desk, box, folder, uid, since, cursor):
    """Un message de la progression : 'queued', 'skipped' (inéligible, définitif) ou un motif de report."""
    try:
        head = box.fetch(folder, uid, headers_only=True)
    except Stop:
        return 'lecture_impossible'
    if cursor.get('uidvalidity') and str(head.uidvalidity) != str(cursor['uidvalidity']):
        cursor.update({'uidvalidity': str(head.uidvalidity), 'last_uid': 0, 'deferred': {}})   # boîte renumérotée : parcours repris
        return 'boite_renumerotee'
    cursor['uidvalidity'] = str(head.uidvalidity)
    if head.timestamp.isoformat() < since or head.msg.get('List-Id') or head.msg.get('List-Unsubscribe'):
        return 'skipped'
    from .sens5643 import direction
    if direction(head, desk.c['mail']) != 'received':
        return 'skipped'   # message du cabinet ou interne : jamais classé automatiquement
    source = {'folder': folder, 'uid': str(uid), 'uidvalidity': head.uidvalidity}
    eff = effective(desk, source, confirmed_matter(desk, head.sender))
    actions = [a for a in ACTIONS if eff[a]['allowed']]
    if not actions:
        return 'skipped'
    if not eff['matter_id']:
        return 'dossier_a_preciser'
    from .pause5629 import is_paused
    if is_paused(desk, eff['matter_id']):
        return 'dossier_en_pause'
    done = links_for_source(desk, folder, uid, head.uidvalidity)
    actions = [a for a in actions if not any(l['matter_id'] == eff['matter_id'] and
                                             (l['email_requested'] if a == 'archive_email' else l['attachments_requested']) for l in done)]
    if not actions:
        return 'skipped'   # déjà demandé (y compris annulé par l'avocat : jamais recréé automatiquement)
    try:
        prop = prepare(desk, 'cabinet', {'source': source, 'matter_id': eff['matter_id'], 'actions': actions}, box,
                       mode='automatic_confirmed', origin='automatic')
        confirm(desk, 'cabinet', {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'auto-' + prop['archive_id'][:40]})
    except Stop as ex:
        if str(ex) in ('aucune_piece_jointe_a_enregistrer', 'message_trop_volumineux_pour_conservation', 'action_de_classement_a_choisir'):
            return 'skipped'
        return str(ex)[:60] or 'erreur'
    return 'queued'


def entrust(desk, owner, data, box=None):
    """« Confier ce classement à l'IA » : mandat pour CE SEUL message et les seules actions cochées (aucune règle générale activée).
    Avec un dossier choisi et l'IA disponible, les travaux partent aussitôt (origine automatique, soumise à la pause) ; sinon le mandat
    attend le dossier ou la reprise de l'IA."""
    actions = [a for a in (data.get('actions') or []) if a in ACTIONS]
    if not actions:
        raise Stop('action_de_classement_a_choisir')
    source = _source(data)
    if not source['uidvalidity']:
        raise Stop('uidvalidity_requise')
    matter = str(data.get('matter_id') or '')
    if matter:
        _matter(desk, matter)
    rule = get_message_rule(desk, source)
    sel = _selection(data)
    # 5.6.44 (audit F06) : mandat durable pour CE message, avec ses seules actions cochées et sa sélection exacte de pièces
    stamp, previous = _now(), _mandate(desk, source)
    mid = previous['id'] if previous else secrets.token_hex(16)
    desk.db.execute('INSERT OR REPLACE INTO mail_filing_mandates(id,scope_id,account_id,folder,uidvalidity,uid,matter_id,actions_json,selection_json,'
                    'owner,state,error_code,link_id,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (mid, 'cabinet', account_id(desk.c), source['folder'], source['uidvalidity'], source['uid'], matter, json.dumps(actions),
                     json.dumps(sel, ensure_ascii=False), owner, 'pending' if matter else 'waiting_matter', '', None,
                     (previous['version'] + 1) if previous else 1, previous['created_at'] if previous else stamp, stamp))
    desk.db.commit()
    set_message_rule(desk, owner, {**source, 'expected_version': rule['version'],
                                   'email_override': 'allow' if 'archive_email' in actions else rule['email_override'],
                                   'attachments_override': 'allow' if 'file_attachments' in actions else rule['attachments_override'],
                                   'matter_id': matter, 'attachments_selection': sel})
    if not matter:
        return {'state': 'en_attente_du_dossier', 'mandate_id': mid, 'message': 'Mandat enregistré pour ce courriel — en attente du choix du dossier.'}
    from .pause5629 import is_paused
    if desk.settings('missions567:pause', False) or is_paused(desk, matter):
        return {'state': 'en_attente_reprise', 'mandate_id': mid, 'message': 'Mandat enregistré pour ce courriel ; il sera exécuté à la reprise de l’IA.'}
    prop = prepare(desk, owner, {'source': source, 'matter_id': matter, 'actions': actions, 'attachments_selection': sel}, box,
                   mode='assisted', origin='automatic')
    m = _mandate(desk, source)
    out = confirm(desk, owner, {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'mandat-%s-%d' % (mid[:40], m['version'])})
    desk.db.execute("UPDATE mail_filing_mandates SET state='done', link_id=?, updated_at=? WHERE id=?", (out['link_id'], _now(), mid))
    desk.db.commit()
    out['mandate_id'] = mid
    out['message'] = 'Classement confié pour ce seul courriel ; les fichiers sont vérifiés après dépôt.'
    return out


SECONDARY = re.compile(r'/\d{4}-\d{2}/\d{4}-\d{2}-\d{2}_\d{6}-[a-z]+-[^/]*--[0-9a-f]{8}/'
                       r'(?:original\.eml|metadata(?:\.v\d+)?\.json|lecture\.v\d+\.md|images/[^/]+)$')   # 5.6.44 : versions de dérivés


def secondary_representation(path):
    """§ 13.6 : un courriel conservé n'est indexé qu'une fois, par sa lecture ``lecture.md`` ; l'original, le manifeste et les images
    restent des fichiers de conservation, sans être comptés comme des preuves indépendantes du même message."""
    return bool(SECONDARY.search(str(path or '')))
