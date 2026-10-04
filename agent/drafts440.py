"""Edit mail drafts inside AxiorHub (4.4.0).

The editor works on the configured Drafts folder only. It never sends mail.
Saving a draft is "append the edited version, read it back, then remove the
previous version of that same draft" - the same sequence a webmail uses. The
previous UID is removed only after the new copy has been read back, and only
when its UIDVALIDITY and content revision are still the ones the lawyer opened.
"""
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, formataddr
import hashlib
import imaplib
import re

from .common import Stop
from .mailbox import (Mailbox, MID, addresses, body_text, draft_key, imap_quote,
                      modified_utf7, subject_key)

MAX_BODY = 120_000
MAX_RECIPIENTS = 25
ADDRESS = re.compile(r'^[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+$')
SKIP_HEADERS = {'content-type', 'content-transfer-encoding', 'mime-version', 'content-disposition',
                'to', 'cc', 'bcc', 'subject', 'date', 'x-axiorhub-edited', 'content-length'}
HEADER_FIELDS = 'FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO X-AXIORHUB-DRAFT-KEY'


def _uid(value):
    value = str(value or '')
    if not re.fullmatch(r'\d{1,12}', value):
        raise Stop('uid_invalide')
    return value


def _clean_header(value, maximum=500):
    value = str(value or '')
    if '\r' in value or '\n' in value or '\x00' in value:
        raise Stop('entete_invalide')
    value = value.strip()
    if len(value) > maximum:
        raise Stop('entete_trop_long')
    return value


def parse_recipients(value, label='destinataires'):
    """Return a normalised comma-separated list, rejecting anything ambiguous."""
    value = _clean_header(value, 2000)
    if not value:
        return ''
    parsed = getaddresses([value.replace(';', ',')])
    out = []
    for name, address in parsed:
        if not address and not name:
            continue
        if not ADDRESS.fullmatch(address or ''):
            raise Stop(label + '_invalides')
        out.append(formataddr((name, address)) if name else address)
    if len(out) > MAX_RECIPIENTS:
        raise Stop(label + '_trop_nombreux')
    return ', '.join(out)


def _select(box, folder, readonly):
    status, _ = box.conn.select(imap_quote(modified_utf7(folder)), readonly=readonly)
    if status != 'OK':
        raise Stop('dossier_imap_inaccessible')
    _, vv = box.conn.response('UIDVALIDITY')
    if not vv or not vv[0]:
        raise Stop('uidvalidity_absent')
    box.selected, box.validity = folder, vv[0].decode()
    return box.validity


def _search_uids(box, *criteria):
    status, data = box.conn.uid('SEARCH', None, *criteria)
    if status != 'OK':
        raise Stop('recherche_imap_echouee')
    return [x.decode() for x in (data[0].split() if data and data[0] else [])]


def revision(msg):
    """Content fingerprint used to detect that the draft changed under the editor."""
    attachments = sorted(str(p.get_filename() or '') for p in msg.iter_attachments())
    material = '\x1f'.join([str(msg.get('To', '')), str(msg.get('Cc', '')), str(msg.get('Bcc', '')),
                            str(msg.get('Subject', '')), body_text(msg), *attachments])
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def _parse_fetch(rows):
    """Yield (uid, flags, internaldate, raw header bytes) from a FETCH response."""
    items = []
    for index, row in enumerate(rows):
        if not isinstance(row, tuple):
            continue
        meta = row[0] if isinstance(row[0], bytes) else b''
        tail = rows[index + 1] if index + 1 < len(rows) and isinstance(rows[index + 1], bytes) else b''
        joined = meta + b' ' + tail
        uid = re.search(rb'UID (\d+)', joined)
        if not uid:
            continue
        flags = re.search(rb'FLAGS \(([^)]*)\)', joined)
        stamp = re.search(rb'INTERNALDATE "([^"]+)"', joined)
        size = re.search(rb'RFC822\.SIZE (\d+)', joined)
        items.append((uid[1].decode(), (flags[1].decode() if flags else ''),
                      (stamp[1].decode() if stamp else ''), int(size[1]) if size else 0, row[1]))
    return items


def _internal_date(value):
    try:
        return datetime.strptime(value, '%d-%b-%Y %H:%M:%S %z').astimezone(timezone.utc).isoformat()
    except ValueError:
        return ''


def list_drafts(mail_cfg, limit=40):
    limit = max(1, min(int(limit), 100))
    box = Mailbox(mail_cfg)
    try:
        validity = _select(box, mail_cfg['drafts'], True)
        uids = _search_uids(box, 'UNDELETED')
        uids = sorted(uids, key=int)[-limit:]
        items = []
        if uids:
            status, rows = box.conn.uid('FETCH', ','.join(uids),
                '(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (' + HEADER_FIELDS + ')])')
            if status != 'OK':
                raise Stop('lecture_imap_echouee')
            for uid, flags, stamp, size, raw in _parse_fetch(rows):
                head = BytesParser(policy=policy.default).parsebytes(raw or b'', headersonly=True)
                items.append({'uid': uid, 'subject': str(head.get('Subject', '')) or '(sans objet)',
                              'to': str(head.get('To', '')), 'date': _internal_date(stamp),
                              'agent': bool(head.get('X-AxiorHub-Draft-Key')),
                              'agent_key': str(head.get('X-AxiorHub-Draft-Key', '') or '').strip()[:100],
                              'message_id': ''.join(MID.findall(str(head.get('Message-ID', '')))[:1]),
                              'in_reply_to': ''.join(MID.findall(str(head.get('In-Reply-To', '')))[:1]),
                              'size': size})
        items.sort(key=lambda x: (x['date'], int(x['uid'])), reverse=True)
        return {'folder': mail_cfg['drafts'], 'uidvalidity': validity, 'items': items}
    finally:
        box.close()


def _find_source(box, mail_cfg, in_reply_to):
    if not in_reply_to:
        return None
    for folder in (mail_cfg['inbox'], mail_cfg['sent']):
        try:
            _select(box, folder, True)
            uids = _search_uids(box, 'UNDELETED', 'HEADER', 'Message-ID', imap_quote(in_reply_to))
        except Stop:
            continue
        if uids:
            found = box.fetch(folder, uids[-1])
            return found
    return None


def get_draft(mail_cfg, uid, validity=''):
    uid = _uid(uid)
    box = Mailbox(mail_cfg)
    try:
        folder = mail_cfg['drafts']
        mail = box.fetch(folder, uid)
        if validity and mail.uidvalidity != str(validity):
            raise Stop('uidvalidity_modifiee')
        msg = mail.msg
        attachments = []
        movable = True
        for part in msg.iter_attachments():
            if part.get_content_maintype() == 'message' or part.get_payload(decode=True) is None:
                movable = False
            attachments.append({'filename': str(part.get_filename() or 'pièce'),
                                'type': part.get_content_type(),
                                'size': len(part.get_payload(decode=True) or b'')})
        body = msg.get_body(preferencelist=('plain',))
        has_plain = body is not None
        html = msg.get_body(preferencelist=('html',))
        text = body_text(msg)
        if has_plain:
            try:
                text = body.get_content().replace('\r\n', '\n')
            except (LookupError, UnicodeError):
                pass
        source = None
        original = _find_source(box, mail_cfg, ''.join(MID.findall(str(msg.get('In-Reply-To', '')))[:1]))
        if original is not None:
            account = mail_cfg['username'] + '@' + mail_cfg['host']
            source = {'uid': original.uid, 'mailbox': original.mailbox, 'sender': original.sender,
                      'subject': original.subject, 'date': original.timestamp.isoformat(),
                      'text': original.text[:8000], 'key': original.key(account)}
        return {'uid': mail.uid, 'uidvalidity': mail.uidvalidity, 'folder': folder,
                'message_id': mail.mid, 'agent_key': draft_key(msg), 'in_reply_to': str(msg.get('In-Reply-To', '')),
                'from': str(msg.get('From', '')), 'to': str(msg.get('To', '')), 'cc': str(msg.get('Cc', '')),
                'bcc': str(msg.get('Bcc', '')), 'subject': str(msg.get('Subject', '')),
                'body': text, 'revision': revision(msg), 'attachments': attachments,
                'editable': movable,
                'lossy': html is not None,
                'edited_at': str(msg.get('X-AxiorHub-Edited', '')),
                'flags': sorted(mail.flags), 'source': source}
    finally:
        box.close()


def build_edited(old, fields, stamp):
    """Return the replacement message. Attachments are preserved byte for byte."""
    new = EmailMessage(policy=policy.SMTP)
    for name, value in old.items():
        if name.lower() in SKIP_HEADERS:
            continue
        new[name] = str(value)
    if not new.get('From'):
        raise Stop('expediteur_brouillon_absent')
    for name in ('To', 'Cc', 'Bcc'):
        if fields.get(name.lower()):
            new[name] = fields[name.lower()]
    new['Subject'] = fields['subject']
    new['Date'] = format_datetime(datetime.now(timezone.utc))
    new['X-AxiorHub-Edited'] = stamp
    new.set_content(fields['body'].rstrip() + '\n')
    for part in old.iter_attachments():
        data = part.get_payload(decode=True)
        if data is None or part.get_content_maintype() == 'message':
            raise Stop('piece_jointe_non_modifiable_ici')
        new.add_attachment(data, maintype=part.get_content_maintype(), subtype=part.get_content_subtype(),
                           filename=part.get_filename() or 'piece')
    return new


def validate_fields(data):
    subject = _clean_header(data.get('subject'), 500)
    body = str(data.get('body') or '').replace('\r\n', '\n').replace('\r', '\n')
    if '\x00' in body:
        raise Stop('corps_invalide')
    if len(body) > MAX_BODY:
        raise Stop('corps_trop_long')
    if not body.strip():
        raise Stop('corps_vide')
    to = parse_recipients(data.get('to'), 'destinataires')
    if not to:
        raise Stop('destinataire_requis')
    return {'to': to, 'cc': parse_recipients(data.get('cc'), 'copie'),
            'bcc': parse_recipients(data.get('bcc'), 'copie_cachee'),
            'subject': subject or '(sans objet)', 'body': body}


def _append_uid(response):
    for chunk in response or []:
        if isinstance(chunk, bytes):
            found = re.search(rb'APPENDUID \d+ (\d+)', chunk)
            if found:
                return found[1].decode()
    return ''


def save_draft(mail_cfg, data):
    """Persist the lawyer's edit. Returns the new UID after a successful read-back."""
    uid = _uid(data.get('uid'))
    fields = validate_fields(data)
    expected_validity, expected_revision = str(data.get('uidvalidity') or ''), str(data.get('revision') or '')
    if not expected_validity or not expected_revision:
        raise Stop('version_brouillon_inconnue')
    box = Mailbox(mail_cfg)
    try:
        folder = mail_cfg['drafts']
        current = box.fetch(folder, uid)
        if current.uidvalidity != expected_validity:
            raise Stop('uidvalidity_modifiee')
        if revision(current.msg) != expected_revision:
            raise Stop('brouillon_modifie_depuis_ouverture')
        if '\\Draft' not in current.flags:
            raise Stop('message_non_brouillon')
        stamp = datetime.now(timezone.utc).isoformat()
        new = build_edited(current.msg, fields, stamp)
        _select(box, folder, False)
        if box.validity != expected_validity:
            raise Stop('uidvalidity_modifiee')
        status, response = box.conn.append(imap_quote(modified_utf7(folder)), '(\\Draft)',
                                           imaplib.Time2Internaldate(datetime.now(timezone.utc)),
                                           new.as_bytes(policy=policy.SMTP))
        if status != 'OK':
            raise Stop('append_imap_incertain')
        new_uid = _append_uid(response)
        if not new_uid:
            _select(box, folder, False)
            candidates = [u for u in _search_uids(box, 'UNDELETED', 'HEADER', 'X-AxiorHub-Edited', imap_quote(stamp))
                          if u != uid]
            new_uid = candidates[-1] if candidates else ''
        if not new_uid:
            raise Stop('brouillon_enregistre_non_retrouve')
        saved = box.fetch(folder, new_uid)
        if (saved.uidvalidity != expected_validity or '\\Draft' not in saved.flags or
                str(saved.msg.get('X-AxiorHub-Edited', '')).strip() != stamp or
                saved.msg.get('Subject') != fields['subject'] or
                addresses(saved.msg.get('To', '')) != addresses(fields['to']) or
                addresses(saved.msg.get('Cc', '')) != addresses(fields['cc']) or
                saved.text.replace('\r\n', '\n').strip() != fields['body'].strip()):
            raise Stop('brouillon_enregistre_non_conforme')
        removed = False
        try:
            _select(box, folder, False)
            box.conn.uid('STORE', uid, '+FLAGS.SILENT', '(\\Deleted)')
            caps = {c.decode() if isinstance(c, bytes) else str(c) for c in getattr(box.conn, 'capabilities', ())}
            if 'UIDPLUS' in {c.upper() for c in caps}:
                box.conn.uid('EXPUNGE', uid)
            removed = True
        except (imaplib.IMAP4.error, OSError):
            removed = False
        return {'uid': new_uid, 'uidvalidity': saved.uidvalidity, 'revision': revision(saved.msg),
                'previous_removed': removed, 'saved_at': stamp,
                'original_body': body_text(current.msg), 'message_id': saved.mid,
                'agent_key': draft_key(saved.msg)}
    finally:
        box.close()


def find_trash(box):
    status, rows = box.conn.list()
    if status != 'OK':
        return ''
    for row in rows or []:
        if isinstance(row, bytes) and b'\\Trash' in row:
            quoted = re.search(rb'"((?:[^"\\]|\\.)*)"\s*$', row)
            name = quoted[1] if quoted else row.rstrip().rsplit(b' ', 1)[-1]
            if name:
                return name.decode()
    return ''


def discard_draft(mail_cfg, uid, validity, confirm):
    uid = _uid(uid)
    if confirm != 'yes':
        raise Stop('confirmation_requise')
    box = Mailbox(mail_cfg)
    try:
        folder = mail_cfg['drafts']
        current = box.fetch(folder, uid)
        if current.uidvalidity != str(validity) or '\\Draft' not in current.flags:
            raise Stop('uidvalidity_modifiee')
        trash = find_trash(box)
        _select(box, folder, False)
        copied = False
        if trash:
            status, _ = box.conn.uid('COPY', uid, imap_quote(trash))
            copied = status == 'OK'
        if trash and not copied:
            raise Stop('corbeille_inaccessible')
        box.conn.uid('STORE', uid, '+FLAGS.SILENT', '(\\Deleted)')
        caps = {(c.decode() if isinstance(c, bytes) else str(c)).upper() for c in getattr(box.conn, 'capabilities', ())}
        if 'UIDPLUS' in caps:
            box.conn.uid('EXPUNGE', uid)
        return {'discarded': True, 'moved_to_trash': copied}
    finally:
        box.close()


def same_conversation(a, b):
    return subject_key(a) == subject_key(b)
