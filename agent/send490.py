"""Envoi depuis l'interface 4.9.0 : fonction FACULTATIVE, désactivée par défaut.

Garde-fous (tous vérifiés côté serveur, jamais seulement dans le navigateur) :
  - interrupteur général ET activation dossier par dossier, chacun avec confirmation explicite ;
  - l'agent n'envoie jamais : seul un clic de l'avocat sur « Envoyer », suivi d'un SECOND clic de confirmation
    affichant destinataires et objet, déclenche l'envoi (la commande vocale « envoie » est refusée) ;
  - le message envoyé est le brouillon relu au moment de la confirmation ; s'il a changé depuis l'aperçu
    (empreinte différente), rien ne part ;
  - brouillon « À VÉRIFIER » ou sans dossier identifié : refus ;
  - un même contenu n'est jamais envoyé deux fois (journal écrit AVANT l'envoi) ; plafond horaire ;
  - copie dans le dossier Envoyés ; si la copie échoue, l'avocat en est averti explicitement ;
  - le journal des envois ne contient ni texte ni objet : dossier, empreinte, nombre de destinataires, issue.
"""
from .common import matter_display
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr
import hashlib
import imaplib
import json
import re
import secrets
import smtplib
import ssl
import time

from .common import Stop, load_matters, read_secret
from .mailbox import Mailbox, addresses, imap_quote, modified_utf7, body_text, draft_key

TOKEN_TTL = 300
MIN_DELAY = 1.0          # secondes entre l'aperçu et la confirmation : un double-clic ne confirme rien
MAX_RECIPIENTS = 20
MAX_PER_HOUR = 20
DUPLICATE_WINDOW_HOURS = 24
UNCERTAIN_WINDOW_HOURS = 1
VERIFY_MARK = re.compile(r'À\s*VÉRIFIER|A\s*VERIFIER', re.IGNORECASE)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS send_pending490(
  token TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, uid TEXT NOT NULL, validity TEXT NOT NULL, matter TEXT NOT NULL,
  created REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS send_log490(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, matter TEXT NOT NULL, fingerprint TEXT NOT NULL,
  recipients INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS send_log490_fp ON send_log490(fingerprint,at);
'''

smtp_factory = None        # remplaçable dans les tests


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------------------------------ réglages
def smtp_config(desk):
    mail = desk.c['mail']
    smtp = dict(mail.get('smtp') or desk.c.get('smtp') or {})
    if not smtp.get('host'):
        return None
    smtp.setdefault('port', 587 if smtp.get('security', 'starttls') == 'starttls' else 465)
    smtp.setdefault('security', 'starttls')
    smtp.setdefault('username', mail.get('username', ''))
    smtp.setdefault('password_file', mail.get('password_file', ''))
    if smtp['security'] not in ('starttls', 'ssl'):
        raise Stop('smtp_securite_invalide')
    return smtp


def status(desk):
    ensure_schema(desk)
    configured = smtp_config(desk) is not None
    matters = [m for m in load_matters(desk.c)]
    enabled_ids = set(desk.settings('send490:matters', []))
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = [dict(r) for r in desk.db.execute(
        'SELECT at,matter,recipients,status,detail FROM send_log490 WHERE at>=? ORDER BY id DESC LIMIT 30', (since,))]
    return {'smtp_configured': configured, 'enabled': bool(desk.settings('send490:enabled', False)) and configured,
            'requested': bool(desk.settings('send490:enabled', False)),
            'matters': [{'id': m['id'], 'label': matter_display(m),
                         'enabled': m['id'] in enabled_ids} for m in matters],
            'recent': recent, 'max_per_hour': MAX_PER_HOUR, 'max_recipients': MAX_RECIPIENTS,
            'alternative': True}


def set_enabled(desk, enabled, confirm=''):
    ensure_schema(desk)
    if enabled:
        if confirm != 'yes':
            raise Stop('confirmation_envoi_requise')
        if smtp_config(desk) is None:
            raise Stop('smtp_non_configure')
    desk.setting('send490:enabled', bool(enabled))
    desk.audit('envoi_490_interrupteur', {'enabled': bool(enabled)})
    return status(desk)


def set_matter(desk, matter, enabled, confirm=''):
    ensure_schema(desk)
    ids = {m['id'] for m in load_matters(desk.c)}
    if matter not in ids:
        raise Stop('dossier_absent')
    current = list(desk.settings('send490:matters', []))
    if enabled:
        if confirm != 'yes':
            raise Stop('confirmation_envoi_requise')
        if matter not in current:
            current.append(matter)
    else:
        current = [m for m in current if m != matter]
    desk.setting('send490:matters', sorted(set(current)))
    desk.audit('envoi_490_dossier', {'matter': matter, 'enabled': bool(enabled)})
    return status(desk)


# ------------------------------------------------------------------------------------------- brouillon
def _matter_of(desk, agent_key, source_key):
    for key in (agent_key, source_key):
        if key:
            row = desk.db.execute('SELECT matter FROM work_items WHERE mail_key=?', (key,)).fetchone()
            if row and row[0]:
                return row[0]
    return ''


def fingerprint(msg):
    parts = [str(msg.get('Message-ID', '')), ','.join(sorted(addresses(msg.get('To', '')))),
             ','.join(sorted(addresses(msg.get('Cc', '')))), ','.join(sorted(addresses(msg.get('Bcc', '')))),
             str(msg.get('Subject', '')), body_text(msg).replace('\r\n', '\n').strip()]
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True) or b''
        parts.append('%s:%d:%s' % (part.get_filename() or '', len(data), hashlib.sha256(data).hexdigest()))
    return hashlib.sha256('\x1f'.join(parts).encode('utf-8', 'replace')).hexdigest()


def _load(desk, uid, validity):
    box = Mailbox(desk.c['mail'])
    try:
        mail = box.fetch(desk.c['mail']['drafts'], str(uid))
        if validity and mail.uidvalidity != str(validity):
            raise Stop('uidvalidity_modifiee')
        if '\\Draft' not in mail.flags:
            raise Stop('message_non_brouillon')
        return mail
    finally:
        box.close()


def _recipients(msg):
    found = []
    for name in ('To', 'Cc', 'Bcc'):
        found += addresses(msg.get(name, ''))
    unique = list(dict.fromkeys(found))
    if not unique:
        raise Stop('destinataire_requis')
    if len(unique) > MAX_RECIPIENTS:
        raise Stop('trop_de_destinataires')
    for address in unique:
        if not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', address):
            raise Stop('adresse_invalide')
    return unique


def _check_sendable(desk, mail):
    if not desk.settings('send490:enabled', False):
        raise Stop('envoi_desactive')
    if smtp_config(desk) is None:
        raise Stop('smtp_non_configure')
    msg = mail.msg
    matter = _matter_of(desk, draft_key(msg), '')
    if not matter:
        raise Stop('envoi_dossier_non_identifie')
    if matter not in set(desk.settings('send490:matters', [])):
        raise Stop('envoi_non_active_pour_ce_dossier')
    subject = str(msg.get('Subject', ''))
    if VERIFY_MARK.search(subject) or VERIFY_MARK.search(body_text(msg)[:400]):
        raise Stop('brouillon_a_verifier')
    recipients = _recipients(msg)
    for part in msg.iter_attachments():
        if part.get_content_maintype() == 'message' or part.get_payload(decode=True) is None:
            raise Stop('piece_jointe_non_envoyable_ici')
    return matter, recipients


def _hourly_count(desk):
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return desk.db.execute("SELECT COUNT(*) FROM send_log490 WHERE at>=? AND status IN ('envoye','envoye_copie_manquante','en_cours')",
                           (since,)).fetchone()[0]


def _summary(msg, recipients, matter):
    body = body_text(msg).replace('\r\n', '\n').strip()
    return {'to': str(msg.get('To', '')), 'cc': str(msg.get('Cc', '')), 'bcc': str(msg.get('Bcc', '')),
            'subject': str(msg.get('Subject', '')), 'recipient_count': len(recipients), 'matter': matter,
            'attachments': [{'filename': str(p.get_filename() or 'pièce'), 'size': len(p.get_payload(decode=True) or b'')}
                            for p in msg.iter_attachments()],
            'words': len(body.split()), 'preview': body[:600]}


def prepare(desk, uid, validity):
    """Premier clic : contrôle le brouillon et rend l'aperçu à confirmer. N'envoie rien."""
    ensure_schema(desk)
    mail = _load(desk, uid, validity)
    matter, recipients = _check_sendable(desk, mail)
    if _hourly_count(desk) >= MAX_PER_HOUR:
        raise Stop('plafond_horaire_envois')
    fp = fingerprint(mail.msg)
    token = secrets.token_hex(24)
    desk.db.execute('DELETE FROM send_pending490 WHERE created<?', (time.time() - 3600,))
    desk.db.execute('INSERT INTO send_pending490(token,fingerprint,uid,validity,matter,created) VALUES(?,?,?,?,?,?)',
                    (token, fp, str(mail.uid), mail.uidvalidity, matter, time.time()))
    desk.db.commit()
    desk.audit('envoi_490_apercu', {'matter': matter, 'recipients': len(recipients)})
    return {'token': token, 'expires_in': TOKEN_TTL, 'min_delay': MIN_DELAY,
            'summary': _summary(mail.msg, recipients, matter), 'fingerprint': fp[:12]}


# ------------------------------------------------------------------------------------------- envoi
def _outgoing(msg, from_cfg):
    out = EmailMessage(policy=policy.SMTP)
    for name, value in msg.items():
        lower = name.lower()
        if lower in ('bcc', 'message-id', 'date', 'content-type', 'content-transfer-encoding', 'mime-version',
                     'content-disposition') or lower.startswith('x-axiorhub'):
            continue
        out[name] = str(value)
    domain = from_cfg.get('from_address', 'localhost').rsplit('@', 1)[-1] or 'localhost'
    out['Message-ID'] = make_msgid(domain=domain)
    out['Date'] = format_datetime(datetime.now(timezone.utc))
    out.set_content(body_text(msg).replace('\r\n', '\n').rstrip() + '\n')
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True)
        out.add_attachment(data, maintype=part.get_content_maintype(), subtype=part.get_content_subtype(),
                           filename=part.get_filename() or 'piece')
    return out


def _smtp_send(smtp_cfg, message, envelope_from, recipients):
    password = read_secret(smtp_cfg['password_file'])
    factory = smtp_factory
    try:
        if factory is not None:
            client = factory(smtp_cfg)
        elif smtp_cfg['security'] == 'ssl':
            client = smtplib.SMTP_SSL(smtp_cfg['host'], int(smtp_cfg['port']), timeout=45, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(smtp_cfg['host'], int(smtp_cfg['port']), timeout=45)
        try:
            if factory is None and smtp_cfg['security'] == 'starttls':
                client.starttls(context=ssl.create_default_context())
            client.login(smtp_cfg['username'], password)
            refused = client.send_message(message, from_addr=envelope_from, to_addrs=recipients)
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                pass
    except (smtplib.SMTPException, OSError):
        raise Stop('smtp_echec') from None
    if refused:
        raise Stop('smtp_destinataire_refuse')


def _copy_to_sent(desk, message, draft_msg):
    """Copie identique au message parti (octet pour octet), plus Bcc et clé interne : utile au rapprochement d'apprentissage."""
    from email.parser import BytesParser
    copy = BytesParser(policy=policy.SMTP).parsebytes(message.as_bytes(policy=policy.SMTP))
    if draft_msg.get('Bcc'):
        copy['Bcc'] = str(draft_msg.get('Bcc'))
    if draft_key(draft_msg):
        copy['X-AxiorHub-Draft-Key'] = draft_key(draft_msg)
    box = Mailbox(desk.c['mail'])
    try:
        status_, _ = box.conn.append(imap_quote(modified_utf7(desk.c['mail']['sent'])), '(\\Seen)',
                                     imaplib.Time2Internaldate(datetime.now(timezone.utc)), copy.as_bytes(policy=policy.SMTP))
        return status_ == 'OK'
    finally:
        box.close()


def confirm(desk, token, now=None):
    """Second clic : relit le brouillon, compare l'empreinte, envoie, copie dans Envoyés."""
    ensure_schema(desk)
    now = time.time() if now is None else now
    row = desk.db.execute('SELECT * FROM send_pending490 WHERE token=?', (str(token or ''),)).fetchone()
    if not row or row['used']:
        raise Stop('apercu_envoi_absent')
    if now - row['created'] > TOKEN_TTL:
        raise Stop('apercu_envoi_expire')
    if now - row['created'] < MIN_DELAY:
        raise Stop('confirmation_trop_rapide')
    claimed = desk.db.execute('UPDATE send_pending490 SET used=1 WHERE token=? AND used=0', (token,)).rowcount
    if claimed != 1:
        raise Stop('apercu_envoi_absent')
    desk.db.commit()
    mail = _load(desk, row['uid'], row['validity'])
    matter, recipients = _check_sendable(desk, mail)
    fp = fingerprint(mail.msg)
    if fp != row['fingerprint'] or matter != row['matter']:
        raise Stop('brouillon_modifie_depuis_apercu')
    window = (datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_WINDOW_HOURS)).isoformat()
    if desk.db.execute("SELECT 1 FROM send_log490 WHERE fingerprint=? AND at>=? AND status IN ('envoye','envoye_copie_manquante')",
                       (fp, window)).fetchone():
        raise Stop('deja_envoye')
    uncertain = (datetime.now(timezone.utc) - timedelta(hours=UNCERTAIN_WINDOW_HOURS)).isoformat()
    if desk.db.execute("SELECT 1 FROM send_log490 WHERE fingerprint=? AND at>=? AND status='en_cours'", (fp, uncertain)).fetchone():
        raise Stop('envoi_incertain_verifiez_les_envoyes')
    if _hourly_count(desk) >= MAX_PER_HOUR:
        raise Stop('plafond_horaire_envois')
    cur = desk.db.execute('INSERT INTO send_log490(at,matter,fingerprint,recipients,status) VALUES(?,?,?,?,?)',
                          (now_iso(), matter, fp, len(recipients), 'en_cours'))
    log_id = cur.lastrowid
    desk.db.commit()
    smtp_cfg = smtp_config(desk)
    message = _outgoing(mail.msg, desk.c['mail'])
    try:
        _smtp_send(smtp_cfg, message, desk.c['mail']['from_address'], recipients)
    except Stop as ex:
        desk.db.execute("UPDATE send_log490 SET status='echec', detail=? WHERE id=?", (str(ex), log_id))
        desk.db.commit()
        raise
    copy_saved = False
    try:
        copy_saved = _copy_to_sent(desk, message, mail.msg)
    except (Stop, imaplib.IMAP4.error, OSError):
        copy_saved = False
    removed = False
    try:
        from .drafts440 import discard_draft
        discard_draft(desk.c['mail'], mail.uid, mail.uidvalidity, 'yes')
        removed = True
    except Exception:
        removed = False
    desk.db.execute('UPDATE send_log490 SET status=?, detail=? WHERE id=?',
                    ('envoye' if copy_saved else 'envoye_copie_manquante', '' if copy_saved else 'copie_envoyes_non_enregistree', log_id))
    desk.db.commit()
    desk.audit('envoi_490_envoye', {'matter': matter, 'recipients': len(recipients), 'copy': copy_saved})
    return {'sent': True, 'copy_saved': copy_saved, 'draft_removed': removed, 'recipient_count': len(recipients),
            'message': ('Message envoyé ; copie enregistrée dans les éléments envoyés.' if copy_saved else
                        'Message ENVOYÉ, mais la copie n’a pas pu être enregistrée dans les éléments envoyés : ajoutez-la à la main.')}


# ---------------------------------------------------------------------------- alternative sans envoi
def webmail_link(desk, uid, validity=''):
    """Lien qui ouvre dans la messagerie le brouillon EXACT (même dossier, même UID). Jamais d'envoi."""
    from .workstation import external_links
    links = external_links(desk)
    base = links.get('roundcube_drafts') or links.get('roundcube') or ''
    uid = re.sub(r'\D', '', str(uid))[:12]
    if not base or not uid:
        return {'url': '', 'kind': 'absent'}
    if '_mbox=' in base:
        mbox = re.search(r'_mbox=([^&]+)', base).group(1)
        root = base.split('?', 1)[0]
        return {'url': '%s?_task=mail&_mbox=%s&_uid=%s&_action=edit' % (root, mbox, uid), 'kind': 'roundcube'}
    root = base.split('?', 1)[0]
    from urllib.parse import quote
    return {'url': '%s?_task=mail&_mbox=%s&_uid=%s&_action=edit' % (root, quote(desk.c['mail']['drafts'], safe=''), uid),
            'kind': 'roundcube'}
