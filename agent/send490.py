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
  - le journal des envois ne contient ni texte ni objet : dossier, empreinte, nombre de destinataires, issue ; 5.6.108 (lot C, B03, B04) :
    plus le Message-ID et la décision de l'avocat ; le retour du serveur par destinataire (adresse, accepté ou refusé, code et date)
    n'est gardé que pour une acceptation partielle ou un résultat incertain, le temps du rapprochement : la décision de l'avocat
    retire les adresses (``sans_adresses``). Un départ complet ou un échec franc n'y laissent aucune adresse.
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
  recipients INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '',
  destinataires TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS send_log490_fp ON send_log490(fingerprint,at);
'''

smtp_factory = None        # remplaçable dans les tests


JOURNAL = (('message_id', "TEXT NOT NULL DEFAULT ''"), ('destinataires', "TEXT NOT NULL DEFAULT ''"), ('decision', "TEXT NOT NULL DEFAULT ''"))


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    colonnes = {r[1] for r in desk.db.execute('PRAGMA table_info(send_log490)')}   # 5.6.108 : ajoutées au démarrage ; ici, filet de sécurité
    for nom, decl in JOURNAL:
        if nom not in colonnes:
            desk.db.execute('ALTER TABLE send_log490 ADD COLUMN %s %s' % (nom, decl))
    desk.db.commit()
    migrer_jetons(desk)


# 5.6.111 (lot C, C07) : le jeton d'aperçu n'est plus conservé en clair. La colonne ``token`` garde son nom (clé primaire, lecture par les
# versions précédentes impossible donc sans risque) mais contient l'empreinte « h1: » + SHA-256 du jeton ; le navigateur seul détient le jeton.
EMPREINTE = 'h1:'


def empreinte_jeton(token):
    return EMPREINTE + hashlib.sha256(str(token or '').encode()).hexdigest()


def migrer_jetons(desk):
    """Migration idempotente des lignes de l'ancienne forme (jeton en clair) : empreinte calculée, clair effacé, état ``used`` et date
    conservés. Un aperçu encore valable reste confirmable avec le même jeton (son empreinte est retrouvée) ; un jeton déjà utilisé reste
    utilisé : aucune reprise ne peut renvoyer un message."""
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='send_pending490'").fetchone():
        return 0   # au démarrage, avant le premier aperçu : rien à migrer
    anciennes = desk.db.execute("SELECT token FROM send_pending490 WHERE substr(token,1,3)<>?", (EMPREINTE,)).fetchall()
    if not anciennes:
        return 0
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        n = 0
        for (clair,) in desk.db.execute("SELECT token FROM send_pending490 WHERE substr(token,1,3)<>?", (EMPREINTE,)).fetchall():
            h = empreinte_jeton(clair)
            if desk.db.execute('SELECT 1 FROM send_pending490 WHERE token=?', (h,)).fetchone():
                desk.db.execute('UPDATE send_pending490 SET used=1 WHERE token=?', (h,))   # doublon improbable : jamais rouvert
                desk.db.execute('DELETE FROM send_pending490 WHERE token=?', (clair,))
            else:
                desk.db.execute('UPDATE send_pending490 SET token=? WHERE token=?', (h, clair))
            n += 1
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return n


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
            'all_matters': bool(desk.settings('send5636:all_matters', False)),   # 5.6.36 : tous les dossiers d'un coup
            'confirm_each': bool(desk.settings('send5636:confirm_each', True)),  # 5.6.36 : confirmation avant chaque envoi (recommandée)
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


def set_options(desk, all_matters=None, confirm_each=None, confirm=''):
    """5.6.36 : « tous les dossiers » (au lieu d'une activation dossier par dossier) et confirmation avant chaque envoi."""
    ensure_schema(desk)
    if all_matters is not None:
        if all_matters and confirm != 'yes':
            raise Stop('confirmation_envoi_requise')
        desk.setting('send5636:all_matters', bool(all_matters))
    if confirm_each is not None:
        desk.setting('send5636:confirm_each', bool(confirm_each))
    desk.audit('envoi_5636_options', {'all_matters': all_matters, 'confirm_each': confirm_each})
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


def _text_parts(msg):
    """5.6.95 : toutes les parties texte et HTML hors pièces jointes (une messagerie peut ne corriger que l'une des deux)."""
    from html import unescape
    out = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == 'attachment' or part.get_content_type() not in ('text/plain', 'text/html'):
            continue
        try:
            text = part.get_content()
        except Exception:   # noqa: BLE001 — partie illisible : le corps principal reste contrôlé par body_text
            payload = part.get_payload(decode=True) or b''
            text = payload.decode('utf-8', 'replace') if isinstance(payload, bytes) else str(payload)
        if part.get_content_type() == 'text/html':
            text = unescape(re.sub(r'<[^>]*>', ' ', str(text)))
        out.append(str(text))
    return out


def _check_sendable(desk, mail):
    if not desk.settings('send490:enabled', False):
        raise Stop('envoi_desactive')
    if smtp_config(desk) is None:
        raise Stop('smtp_non_configure')
    msg = mail.msg
    matter = _matter_of(desk, draft_key(msg), '')
    webmail = str(msg.get('X-AxiorHub-Origine', '')).strip() == 'webmail5628'   # 5.6.28 : message rédigé par l'avocat dans la messagerie
    if not webmail:
        if not matter:
            raise Stop('envoi_dossier_non_identifie')
        if matter not in set(desk.settings('send490:matters', [])) and not desk.settings('send5636:all_matters', False):
            raise Stop('envoi_non_active_pour_ce_dossier')
    subject = str(msg.get('Subject', ''))
    if VERIFY_MARK.search(subject) or VERIFY_MARK.search(body_text(msg)[:400]):
        raise Stop('brouillon_a_verifier')
    # 5.6.67 : un projet « à compléter » n'est jamais envoyé en l'état ; 5.6.95 (D04, D06) : ni un bloc de variantes non choisi, ni la mention
    # « À RELIRE », ancien ou nouveau format, dans l'objet ou dans N'IMPORTE QUELLE partie texte ou HTML (retirer la mention de l'objet ne suffit pas).
    from .sens5643 import blocage_envoi
    blocked = blocage_envoi(subject, body_text(msg), *_text_parts(msg))
    if blocked:
        raise Stop(blocked)
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
                    (empreinte_jeton(token), fp, str(mail.uid), mail.uidvalidity, matter, time.time()))   # 5.6.111 (C07) : empreinte seule
    desk.db.commit()
    desk.audit('envoi_490_apercu', {'matter': matter, 'recipients': len(recipients)})
    return {'token': token, 'expires_in': TOKEN_TTL, 'min_delay': MIN_DELAY,
            'summary': _summary(mail.msg, recipients, matter), 'fingerprint': fp[:12],
            'confirm_each': bool(desk.settings('send5636:confirm_each', True))}


# ------------------------------------------------------------------------------------------- envoi
def _outgoing(msg, from_cfg, message_id=None, extra_headers=None):
    """``message_id`` et ``extra_headers`` (5.6.106, lot E) : identifiant fixé avant le départ et en-têtes d'options (importance, accusé de
    lecture) ; un en-tête d'option déjà présent dans le brouillon est remplacé, jamais doublé."""
    out = EmailMessage(policy=policy.SMTP)
    extra = [(str(k), str(v)) for k, v in (extra_headers or ())]
    replaced = {k.lower() for k, _ in extra}
    for name, value in msg.items():
        lower = name.lower()
        if lower in ('bcc', 'message-id', 'date', 'content-type', 'content-transfer-encoding', 'mime-version',
                     'content-disposition') or lower.startswith('x-axiorhub') or lower in replaced:
            continue
        out[name] = str(value)
    for name, value in extra:
        if re.search(r'[\r\n]', name + value):
            raise Stop('en_tete_invalide')
        out[name] = value
    domain = from_cfg.get('from_address', 'localhost').rsplit('@', 1)[-1] or 'localhost'
    out['Message-ID'] = message_id or make_msgid(domain=domain)
    out['Date'] = format_datetime(datetime.now(timezone.utc))
    out.set_content(body_text(msg).replace('\r\n', '\n').rstrip() + '\n')
    html = msg.get_body(preferencelist=('html',))   # 5.6.28 : la version mise en forme d'un message de la messagerie accompagne le texte
    if html is not None and str(msg.get('X-AxiorHub-Origine', '')).strip() == 'webmail5628':
        try:
            from . import webmail5628
            if hasattr(webmail5628, 'joindre_html'):   # 5.6.106 (lot R) : version HTML nettoyée et images intégrées (Content-ID), jamais distantes
                webmail5628.joindre_html(out, msg)
            else:
                out.add_alternative('<!doctype html><html><body>%s</body></html>' % webmail5628.clean_html(html.get_content()), subtype='html')
        except (LookupError, UnicodeError):
            pass
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True)
        out.add_attachment(data, maintype=part.get_content_maintype(), subtype=part.get_content_subtype(),
                           filename=part.get_filename() or 'piece')
    return out


def _reponse_smtp(value):
    """(code, texte) d'un retour SMTP, borné et sans saut de ligne (le texte du serveur peut citer l'adresse, jamais le message)."""
    try:
        code, text = value
    except (TypeError, ValueError):
        return None, ''
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    return (int(code) if str(code).isdigit() else None), re.sub(r'\s+', ' ', str(text or '')).strip()[:200]


class ArretSmtp(Stop):
    """5.6.108 (lot C) : arrêt du SMTP qui porte les refus par adresse (``refuses`` : {adresse: (code, texte)}) ; le message reste le code."""

    def __init__(self, code, refuses=None):
        super().__init__(code)
        self.refuses = dict(refuses or {})


def _smtp_send(smtp_cfg, message, envelope_from, recipients):
    """5.6.108 (lot C, B03, B04) : un refus de certains destinataires seulement lève ``smtp_envoi_partiel``, une rupture pendant la
    transmission ``smtp_envoi_incertain`` (jamais un échec certain) ; les refus par adresse accompagnent l'arrêt (``ArretSmtp.refuses``)."""
    trace = {'refuses': {}}
    password = read_secret(smtp_cfg['password_file'])
    factory = smtp_factory
    transmission = False
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
            transmission = True
            refused = client.send_message(message, from_addr=envelope_from, to_addrs=recipients)
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                pass
    except smtplib.SMTPRecipientsRefused as ex:
        raise ArretSmtp('smtp_destinataire_refuse', {str(k): _reponse_smtp(v) for k, v in (getattr(ex, 'recipients', None) or {}).items()}) from None
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise Stop('smtp_echec') from None
    except (smtplib.SMTPException, OSError):
        raise Stop('smtp_envoi_incertain' if transmission else 'smtp_echec') from None
    if refused:
        trace['refuses'] = {str(k): _reponse_smtp(v) for k, v in dict(refused).items()}
        if not {k.lower() for k in trace['refuses']} >= {str(r).lower() for r in recipients}:
            raise ArretSmtp('smtp_envoi_partiel', trace['refuses'])
        raise ArretSmtp('smtp_destinataire_refuse', trace['refuses'])   # dictionnaire couvrant tous les destinataires : aucun n'a été accepté


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
    now = time.time() if now is None else now
    mail, matter, recipients, fp = _claim(desk, token, now)
    return _deliver(desk, mail, matter, recipients, fp)


def _claim(desk, token, now):
    """5.6.106 (lot E) : consomme le jeton de l'aperçu (usage unique) et relit le brouillon ; il doit être identique à l'aperçu."""
    ensure_schema(desk)
    h = empreinte_jeton(token)   # 5.6.111 (C07) : recherche par empreinte ; le jeton n'est jamais écrit en base
    row = desk.db.execute('SELECT * FROM send_pending490 WHERE token=?', (h,)).fetchone() if token else None
    if not row or row['used']:
        raise Stop('apercu_envoi_absent')
    if now - row['created'] > TOKEN_TTL:
        raise Stop('apercu_envoi_expire')
    if now - row['created'] < MIN_DELAY:
        raise Stop('confirmation_trop_rapide')
    claimed = desk.db.execute('UPDATE send_pending490 SET used=1 WHERE token=? AND used=0', (h,)).rowcount
    if claimed != 1:
        raise Stop('apercu_envoi_absent')
    desk.db.commit()
    mail = _load(desk, row['uid'], row['validity'])
    matter, recipients = _check_sendable(desk, mail)
    fp = fingerprint(mail.msg)
    if fp != row['fingerprint'] or matter != row['matter']:
        raise Stop('brouillon_modifie_depuis_apercu')
    return mail, matter, recipients, fp


def _duplicate_check(desk, fp, relance_de=None):
    """Doublons : déjà envoyé (ou confirmé parti par l'avocat, 5.6.108) dans les 24 h ; départ incertain ou partiel non rapproché.
    ``relance_de`` (5.6.108, B03) : seule la tentative partielle que l'avocat a décidé de compléter est écartée du contrôle."""
    window = (datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_WINDOW_HOURS)).isoformat()
    if desk.db.execute("SELECT 1 FROM send_log490 WHERE fingerprint=? AND at>=? AND status IN ('envoye','envoye_copie_manquante','envoye_confirme')",
                       (fp, window)).fetchone():
        raise Stop('deja_envoye')
    uncertain = (datetime.now(timezone.utc) - timedelta(hours=UNCERTAIN_WINDOW_HOURS)).isoformat()
    if desk.db.execute("SELECT 1 FROM send_log490 WHERE fingerprint=? AND id<>? AND (status IN ('a_verifier','partiel') OR (at>=? AND status='en_cours'))",
                       (fp, int(relance_de or 0), uncertain)).fetchone():
        raise Stop('envoi_incertain_verifiez_les_envoyes')


def sans_adresses(destinataires):
    """Retour par destinataire sans les adresses (résultat, code, date) : ce qui reste au journal après la décision de l'avocat."""
    try:
        data = json.loads(destinataires or '[]')
    except ValueError:
        return ''
    if not isinstance(data, list):
        return ''
    return json.dumps([{k: v for k, v in x.items() if k in ('etat', 'code', 'date')} for x in data if isinstance(x, dict)], ensure_ascii=False)


def _detail_destinataires(recipients, trace, issue):
    """5.6.108 (B03, B04) : retour du serveur par destinataire : ``accepte``, ``refuse`` (code, texte), ``inconnu`` (réponse perdue) ou
    ``non_transmis`` (refus avant la transmission)."""
    quand = now_iso()
    refuses = {str(k).lower(): v for k, v in (trace.get('refuses') or {}).items()}
    out = []
    for adresse in recipients:
        code, texte = refuses.get(str(adresse).lower(), (None, ''))
        if str(adresse).lower() in refuses:
            etat = 'refuse'
        elif issue in ('envoye', 'partiel'):
            etat = 'accepte'
        elif issue == 'a_verifier':
            etat = 'inconnu'
        else:
            etat = 'non_transmis'
        out.append({'adresse': str(adresse)[:320], 'etat': etat, 'code': code, 'reponse': texte, 'date': quand})
    return json.dumps(out, ensure_ascii=False)


def _deliver(desk, mail, matter, recipients, fp, message_id=None, extra_headers=None, before_effect=None, on_log=None, on_sent=None,
             relance_de=None):
    """Départ réel : doublons, plafond horaire (compté au départ), journal écrit AVANT l'envoi, SMTP, copie dans Envoyés, brouillon retiré.
    5.6.106 (lot E) : appelé aussi par le travailleur pour la file d'envoi (``envoi56106``) : ``message_id`` fixé à l'avance (pour retrouver
    le message dans Envoyés après une interruption), en-têtes d'options, contrôle de la pause juste avant l'effet (``before_effect``),
    étape notée dans la même transaction que le journal (``on_log``) puis juste après le SMTP (``on_sent``).
    5.6.108 (lot C) : Message-ID et retour par destinataire écrits au journal ; ``relance_de`` : renvoi aux seuls refusés d'une tentative
    partielle (``recipients`` réduit à ces adresses), qui close la tentative d'origine quand il aboutit."""
    if relance_de:
        _duplicate_check(desk, fp, relance_de)
    else:
        _duplicate_check(desk, fp)
    if _hourly_count(desk) >= MAX_PER_HOUR:
        raise Stop('plafond_horaire_envois')
    if before_effect is not None:
        before_effect()
    smtp_cfg = smtp_config(desk)
    message = _outgoing(mail.msg, desk.c['mail'], message_id=message_id, extra_headers=extra_headers)   # construit avant le journal : une erreur ici n'envoie rien
    cur = desk.db.execute('INSERT INTO send_log490(at,matter,fingerprint,recipients,status,message_id) VALUES(?,?,?,?,?,?)',
                          (now_iso(), matter, fp, len(recipients), 'en_cours', str(message['Message-ID'] or '')[:300]))
    log_id = cur.lastrowid
    if on_log is not None:
        on_log(log_id)   # même transaction que la ligne du journal : une interruption ne sépare jamais les deux
    desk.db.commit()
    try:
        _smtp_send(smtp_cfg, message, desk.c['mail']['from_address'], recipients)
    except Stop as ex:
        trace = {'refuses': getattr(ex, 'refuses', {})}
        status_ = {'smtp_envoi_incertain': 'a_verifier', 'smtp_envoi_partiel': 'partiel'}.get(str(ex), 'echec')
        desk.db.execute("UPDATE send_log490 SET status=?, detail=?, destinataires=? WHERE id=?",   # adresses : rapprochement seulement
                        (status_, str(ex), _detail_destinataires(recipients, trace, status_) if status_ != 'echec' else '', log_id))
        if relance_de and status_ != 'echec':   # le renvoi lui-même est à vérifier : la tentative d'origine est relayée par celui-ci
            desk.db.execute("UPDATE send_log490 SET status='partiel_relance' WHERE id=? AND status='partiel'", (int(relance_de),))
        desk.db.commit()
        raise
    if on_sent is not None:
        on_sent()
    if relance_de:   # tentative d'origine complétée : ses adresses ne sont plus nécessaires
        try:
            row = desk.db.execute('SELECT destinataires FROM send_log490 WHERE id=?', (int(relance_de),)).fetchone()
            desk.db.execute("UPDATE send_log490 SET status='partiel_complete', destinataires=? WHERE id=? AND status='partiel'",
                            (sans_adresses(row[0] if row else ''), int(relance_de)))
            desk.db.commit()
        except Exception:   # noqa: BLE001 — le départ a eu lieu : la suite (copie, brouillon) continue
            desk.db.rollback()
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


# ------------------------------------------------------------------- 5.6.106 (lot E) : file d'envoi
def pending_fingerprint(desk, token):
    """Empreinte annoncée par un jeton d'aperçu encore valable (lecture seule ; vide si le jeton est inconnu ou déjà utilisé)."""
    ensure_schema(desk)
    row = desk.db.execute('SELECT fingerprint FROM send_pending490 WHERE token=? AND used=0', (empreinte_jeton(token),)).fetchone() if token else None
    return str(row[0]) if row else ''


def brouillon_du_jeton(desk, token):
    """5.6.111 (C07) : brouillon (UID, UIDVALIDITY) visé par un jeton d'aperçu, retrouvé par son empreinte ; vide si inconnu."""
    ensure_schema(desk)
    row = desk.db.execute('SELECT uid, validity FROM send_pending490 WHERE token=?', (empreinte_jeton(token),)).fetchone() if token else None
    return {'uid': str(row[0]), 'uidvalidity': str(row[1])} if row else {}


def confirm_to_queue(desk, token, now=None):
    """Second clic, circuit de la file (5.6.106, lot E) : mêmes contrôles que ``confirm`` (jeton à usage unique, délai minimal, brouillon
    identique à l'aperçu, déjà envoyé, envoi incertain) mais RIEN n'est envoyé : rend de quoi mettre le message dans la file. Le départ
    réel est fait plus tard par le travailleur (``deliver_queued``)."""
    now = time.time() if now is None else now
    mail, matter, recipients, fp = _claim(desk, token, now)
    _duplicate_check(desk, fp)
    return {'uid': str(mail.uid), 'validity': str(mail.uidvalidity), 'fingerprint': fp, 'matter': matter,
            'summary': _summary(mail.msg, recipients, matter), 'sender': sender_address(desk, mail.msg)}


def sender_address(desk, msg):
    """Adresse de l'expéditeur (en-tête From du brouillon, sinon l'adresse d'envoi configurée), contrôlée."""
    found = parseaddr(str(msg.get('From', '')))[1] or str(desk.c['mail'].get('from_address') or '')
    return found if re.fullmatch(r'[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+', found or '') else ''


def deliver_queued(desk, uid, validity, fp, matter, message_id, extra_headers=None, before_effect=None, on_log=None, on_sent=None,
                   seuls=None, relance_de=None):
    """Départ d'un message de la file (travailleur seulement) : le brouillon est relu et doit être EXACTEMENT celui confirmé (empreinte) ;
    l'envoi doit toujours être activé et permis ; puis le départ ordinaire (doublons, plafond horaire, journal, SMTP, Envoyés).
    5.6.108 (lot C, B03) : ``seuls`` + ``relance_de`` : renvoi décidé par l'avocat ; les adresses doivent être des destinataires du message
    ET avoir été refusées par la tentative partielle ``relance_de`` (toujours « partiel », même empreinte, même Message-ID)."""
    ensure_schema(desk)
    mail = _load(desk, uid, validity)
    current, recipients = _check_sendable(desk, mail)
    if fingerprint(mail.msg) != fp or current != matter:
        raise Stop('brouillon_modifie_depuis_confirmation')
    if seuls or relance_de:
        recipients = _seuls_refuses(desk, recipients, fp, message_id, seuls, relance_de)
    return _deliver(desk, mail, matter, recipients, fp, message_id=message_id, extra_headers=extra_headers,
                    before_effect=before_effect, on_log=on_log, on_sent=on_sent, relance_de=relance_de)


def _seuls_refuses(desk, recipients, fp, message_id, seuls, relance_de):
    """Destinataires d'un renvoi partiel, contrôlés contre le message ET contre le journal de la tentative d'origine."""
    try:
        log_id = int(relance_de)
    except (TypeError, ValueError):
        raise Stop('relance_invalide') from None
    row = desk.db.execute('SELECT * FROM send_log490 WHERE id=?', (log_id,)).fetchone()
    if not row or row['status'] != 'partiel' or row['fingerprint'] != fp or (row['message_id'] and row['message_id'] != str(message_id or '')):
        raise Stop('relance_invalide')
    try:
        refuses = {str(x.get('adresse')).lower() for x in json.loads(row['destinataires'] or '[]') if isinstance(x, dict) and x.get('etat') == 'refuse'}
    except ValueError:
        refuses = set()
    voulus = [str(x) for x in (seuls or [])]
    du_message = {str(r).lower(): r for r in recipients}
    if not voulus or any(v.lower() not in refuses or v.lower() not in du_message for v in voulus):
        raise Stop('relance_invalide')
    return list(dict.fromkeys(du_message[v.lower()] for v in voulus))


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
