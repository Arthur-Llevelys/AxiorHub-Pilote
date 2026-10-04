import base64
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, formataddr, format_datetime
from html.parser import HTMLParser
import imaplib
import re
import ssl

from .common import Stop, digest, fold, read_secret

MID = re.compile(r'<[^<>\s\x00-\x20\x7f-\uffff]+@[^<>\s\x00-\x20\x7f-\uffff]+>')


def ids(s):
    return MID.findall(str(s))[:80]


def addresses(value):
    return [a.lower() for _, a in getaddresses([str(value)]) if a and '@' in a]


def subject_key(value):
    return re.sub(r'^(?:(?:re|tr|fwd?|aw)\s*:\s*)+', '', fold(value)).strip()


def imap_quote(s):
    if '\r' in s or '\n' in s or '\x00' in s:
        raise Stop('parametre_imap_invalide')
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def modified_utf7(s):
    out, buf = [], []
    def flush():
        if buf:
            out.append('&' + base64.b64encode(''.join(buf).encode('utf-16be')).decode().rstrip('=').replace('/', ',') + '-')
            buf.clear()
    for ch in s:
        if ' ' <= ch <= '~':
            flush()
            out.append('&-' if ch == '&' else ch)
        else:
            buf.append(ch)
    flush()
    return ''.join(out)


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'): self.hidden += 1
        if tag in ('p', 'br', 'div', 'li', 'tr'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head') and self.hidden: self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def body_text(msg):
    body = msg.get_body(preferencelist=('plain', 'html'))
    if body is None:
        return ''
    try:
        text = body.get_content()
    except (LookupError, UnicodeError):
        return ''
    if not isinstance(text, str): return ''
    if body.get_content_type() == 'text/html':
        p = PlainHTML()
        p.feed(text)
        text = ''.join(p.parts)
    return text.strip()


def draft_key(msg):
    """Canonicalize only outer whitespace from a folded private IMAP header.

    Python 3.12.3 can retain the continuation space when a 64-character key
    is serialized on a separate line. Internal whitespace and duplicate keys
    must still fail the read-back check.
    """
    values=msg.get_all('X-AxiorHub-Draft-Key',[])
    if len(values)!=1:return ''
    value=str(values[0]).strip(' \t\r\n')
    return value if re.fullmatch(r'[a-f0-9]{64}',value) else ''


@dataclass
class Mail:
    uid: str
    uidvalidity: str
    mailbox: str
    flags: set
    timestamp: datetime
    msg: EmailMessage
    @property
    def mid(self):
        values = ids(self.msg.get('Message-ID', ''))
        return values[0] if len(values) == 1 else ''
    @property
    def refs(self):
        return ids(self.msg.get('References', '')) + ids(self.msg.get('In-Reply-To', ''))
    @property
    def root(self): return self.refs[0] if self.refs else self.mid
    @property
    def sender(self):
        aa = addresses(self.msg.get('From', ''))
        return aa[0] if len(aa) == 1 else ''
    @property
    def subject(self): return str(self.msg.get('Subject', ''))[:500]
    @property
    def text(self): return body_text(self.msg)
    def public(self):
        return {'id': self.mid, 'sender': self.sender, 'subject': self.subject,
                'received_at': self.timestamp.isoformat(), 'text': self.text}
    def key(self, account):
        return digest('|'.join([account, self.mailbox, self.uidvalidity, self.uid]))


def exclusion(mail, cfg, allow_seen=False):
    # ``\\Seen`` is a user-interface state, not a durable ingestion marker.
    # Since 3.1.0, the periodic collector may inspect a bounded recent window
    # of read messages.  UIDVALIDITY/UID remains the idempotency boundary.
    if '\\Seen' in mail.flags and not allow_seen: return 'deja_lu'
    if '\\Answered' in mail.flags: return 'deja_repondu'
    if '\\Deleted' in mail.flags: return 'marque_supprime'
    own = {x.lower() for x in cfg['own_addresses']}
    if mail.sender in own: return 'message_du_cabinet'
    sender = mail.sender
    if sender in {x.lower() for x in cfg.get('excluded_senders', [])}: return 'expediteur_exclu'
    if sender.rsplit('@', 1)[-1] in cfg.get('excluded_domains', []): return 'domaine_exclu'
    subject = fold(mail.subject)
    if re.match(r'^(?:preview\s+)?report domain\s*:',subject) and 'report-id' in subject:
        return 'rapport_dmarc'
    if (re.search(r'\brecrutements?\b|\bcandidature\b',subject) and
            re.search(r'\bprofil\b|\bjuriste\b|\brecherche active\b|\bcv\b',subject)):
        return 'sollicitation_recrutement'
    # Match a replacement offer, never the isolated words garde à vue or RPVA.
    if (re.search(r'remplacement|remplacant|cherche.{0,25}confrere|permanence', subject)
            and re.search(r'garde.{0,8}vue|\bgav\b', subject)):
        return 'remplacement_garde_a_vue'
    if re.search(r'\b(rpva|e.?barreau)\b', subject) and re.search(r'notification|accuse.{0,8}reception|avis automatique', subject):
        return 'notification_rpva'
    if mail.msg.get('List-Id') or mail.msg.get('List-Unsubscribe'): return 'liste_diffusion'
    auto = str(mail.msg.get('Auto-Submitted', '')).lower().strip()
    if auto and auto != 'no': return 'message_automatique'
    if str(mail.msg.get('Precedence', '')).lower() in ('bulk', 'junk', 'list'): return 'envoi_en_masse'
    if str(mail.msg.get('X-Spam-Flag', '')).upper() == 'YES': return 'indesirable'
    if re.match(r'^(no-?reply|ne-?pas-?repondre|mailer-daemon|postmaster)@', sender): return 'adresse_automatique'
    for pattern in cfg.get('excluded_subject_patterns', []):
        if re.search(pattern, subject): return 'objet_exclu'
    return ''


def message_issue(mail, cfg):
    if not mail.mid: return 'message_id_absent_ou_invalide'
    if not mail.sender or len(mail.msg.get_all('From', [])) != 1: return 'expediteur_ambigu'
    reply = addresses(mail.msg.get('Reply-To', ''))
    if reply and reply != [mail.sender]: return 'reply_to_different_a_verifier'
    if len(mail.msg.get_all('Reply-To', [])) > 1: return 'reply_to_ambigu'
    if not mail.text: return 'corps_non_lisible'
    if len(mail.text) > cfg.get('max_body_chars', 24000): return 'message_trop_long'
    return ''


def recipient_issue(mail, cfg, allowed_recipients=()):
    reason=message_issue(mail,cfg)
    if reason:return reason
    own = {x.lower() for x in cfg['own_addresses']}
    recipients = addresses(mail.msg.get('To', '')) + addresses(mail.msg.get('Cc', ''))
    if not own.intersection(recipients): return 'destinataire_cabinet_non_etabli'
    if any(x not in own and x != mail.sender and x not in allowed_recipients for x in recipients):
        return 'destinataires_multiples_a_verifier'
    return ''


class Mailbox:
    def __init__(self, cfg):
        self.cfg = cfg
        try:
            self.conn = imaplib.IMAP4_SSL(cfg['host'], cfg.get('port', 993),
                                        ssl_context=ssl.create_default_context(), timeout=45)
            self.conn.login(cfg['username'], read_secret(cfg['password_file']))
        except (imaplib.IMAP4.error, OSError):
            raise Stop('connexion_imap_echouee') from None
        self.selected, self.validity = '', ''

    def close(self):
        try: self.conn.logout()
        except (imaplib.IMAP4.error, OSError): pass

    def select(self, folder):
        result, _ = self.conn.select(imap_quote(modified_utf7(folder)), readonly=True)
        if result != 'OK': raise Stop('dossier_imap_inaccessible')
        _, vv = self.conn.response('UIDVALIDITY')
        if not vv or not vv[0]: raise Stop('uidvalidity_absent')
        self.selected, self.validity = folder, vv[0].decode()
        return self.validity

    def search(self, folder, *criteria):
        self.select(folder)
        status, result = self.conn.uid('SEARCH', None, *criteria)
        if status != 'OK': raise Stop('recherche_imap_echouee')
        return result[0].decode().split() if result and result[0] else []

    def fetch(self, folder, uid, headers_only=False):
        validity = self.select(folder)
        status, rr = self.conn.uid('FETCH', uid, '(UID FLAGS INTERNALDATE RFC822.SIZE)')
        meta = b' '.join(x for x in rr if isinstance(x, bytes))
        if status != 'OK' or not meta: raise Stop('message_imap_disparu')
        size = re.search(br'RFC822.SIZE (\d+)', meta)
        if not headers_only and (not size or int(size[1]) > self.cfg.get('max_message_bytes', 20_000_000)):
            raise Stop('message_trop_volumineux')
        flags = {x.decode() for x in imaplib.ParseFlags(meta)}
        date = re.search(br'INTERNALDATE "([^"]+)"', meta)
        if not date: raise Stop('date_imap_absente')
        try:
            stamp = datetime.strptime(date[1].decode(), '%d-%b-%Y %H:%M:%S %z')
        except ValueError: raise Stop('date_imap_invalide') from None
        query = '(BODY.PEEK[HEADER])' if headers_only else '(BODY.PEEK[])'
        status, data = self.conn.uid('FETCH', uid, query)
        raw = b''.join(x[1] for x in data if isinstance(x, tuple) and isinstance(x[1], bytes))
        if status != 'OK' or not raw: raise Stop('lecture_imap_echouee')
        message = BytesParser(policy=policy.default).parsebytes(raw)
        return Mail(uid, validity, folder, flags, stamp, message)

    def unseen(self):
        criteria = ['UNSEEN','UNDELETED']
        days = self.cfg.get('lookback_days',0)
        if days > 0:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%d-%b-%Y')
            criteria += ['SINCE',since]
        return list(reversed(self.search(self.cfg['inbox'], *criteria)))

    def candidates(self):
        """Return bounded new/recent UIDs, newest first, without duplicates.

        UNSEEN is still queried first for compatibility.  A second bounded
        search includes messages already opened by a user in Roundcube.  The
        state database, keyed by account/mailbox/UIDVALIDITY/UID, decides
        whether a UID has already been processed.
        """
        unseen = self.unseen()
        if not self.cfg.get('process_seen_recent', True):
            return unseen
        days = max(1, min(int(self.cfg.get('retroactive_lookback_days', 7)), 31))
        limit = max(1, min(int(self.cfg.get('retroactive_max_candidates_per_run', 80)), 500))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%d-%b-%Y')
        recent = self.search(self.cfg['inbox'], 'UNDELETED', 'SINCE', since)
        # IMAP UIDs are decimal and monotonically increasing within one
        # UIDVALIDITY epoch.  Fall back to lexical ordering for defensive
        # compatibility with test doubles.
        def order(uid):
            try: return (1, int(uid))
            except (TypeError, ValueError): return (0, str(uid))
        merged = {str(uid) for uid in unseen}
        merged.update(str(uid) for uid in recent)
        return sorted(merged, key=order, reverse=True)[:limit]

    def thread(self, mail):
        if not mail.root: return []
        found = []
        for folder in [self.cfg['inbox'], self.cfg['sent']]:
            uu = self.search(folder, 'UNDELETED', 'OR', 'HEADER', 'Message-ID', imap_quote(mail.root),
                             'OR', 'HEADER', 'References', imap_quote(mail.root),
                             'HEADER', 'In-Reply-To', imap_quote(mail.root))
            if len(uu) > self.cfg.get('max_thread_messages', 35):
                raise Stop('fil_trop_long')
            for uid in uu:
                m = self.fetch(folder, uid)
                if m.mid == mail.mid: continue
                # No subject-only joins: they can blend separate legal matters.
                if m.root == mail.root or mail.root in m.refs or m.mid == mail.root:
                    if len(m.text) > self.cfg.get('max_body_chars', 24000):
                        raise Stop('historique_trop_long')
                    found.append(m)
        unique = {}
        for m in found: unique[m.mid or (m.mailbox + ':' + m.uid)] = m
        return sorted(unique.values(), key=lambda m: m.timestamp)

    def existing_draft(self, mail, draft_mid=None):
        folder = self.cfg['drafts']
        if draft_mid and self.find_own_draft(draft_mid):
            return True
        for key in set([mail.mid, mail.root] + mail.refs):
            if key and self.search(folder, 'UNDELETED', 'OR', 'HEADER', 'In-Reply-To', imap_quote(key),
                                   'HEADER', 'References', imap_quote(key)):
                return True
        # Some clients save drafts without reply headers. Conservatively inspect
        # recent drafts to the same correspondent with the same normalized subject.
        since = (datetime.now(timezone.utc) - timedelta(days=60)).strftime('%d-%b-%Y')
        uu = self.search(folder, 'UNDELETED', 'SINCE', since, 'TO', imap_quote(mail.sender))
        if len(uu) > 60: raise Stop('trop_de_brouillons_a_controler')
        for uid in uu:
            d = self.fetch(folder, uid, headers_only=True)
            if subject_key(d.subject) == subject_key(mail.subject): return True
        return False

    def find_own_draft(self, draft_mid):
        folder = self.cfg['drafts']
        matches = []
        for uid in self.search(folder, 'UNDELETED', 'HEADER', 'Message-ID', imap_quote(draft_mid)):
            draft = self.fetch(folder, uid)
            key = draft_mid.removeprefix('<axiorhub-').removesuffix('@mail-agent.local>')
            if (re.fullmatch(r'[a-f0-9]{64}',key) and draft.mid == draft_mid and '\\Draft' in draft.flags and
                    draft_key(draft.msg) == key and draft.text):
                matches.append(uid)
        if len(matches) > 1: raise Stop('plusieurs_brouillons_meme_identifiant')
        return bool(matches)

    def verify_draft(self, expected):
        """Read the stored message back from the configured Drafts folder.

        An APPEND OK response alone does not establish that the right draft is
        visible in the right folder. Never append again after this fails.
        """
        mid = str(expected.get('Message-ID', ''))
        matches = []
        for uid in self.search(self.cfg['drafts'], 'UNDELETED', 'HEADER',
                               'Message-ID', imap_quote(mid)):
            saved = self.fetch(self.cfg['drafts'], uid)
            if saved.mid == mid: matches.append(saved)
        if not matches: raise Stop('brouillon_non_retrouve')
        if len(matches) != 1: raise Stop('plusieurs_brouillons_meme_identifiant')
        saved = matches[0]
        expected_key=draft_key(expected)
        if ('\\Draft' not in saved.flags or
                not expected_key or draft_key(saved.msg) != expected_key or
                saved.msg.get('In-Reply-To') != expected.get('In-Reply-To') or
                saved.subject != str(expected.get('Subject', '')) or
                addresses(saved.msg.get('From', '')) != addresses(expected.get('From', '')) or
                addresses(saved.msg.get('To', '')) != addresses(expected.get('To', '')) or
                addresses(saved.msg.get('Cc', '')) != addresses(expected.get('Cc', '')) or
                saved.text.replace('\r\n', '\n') != body_text(expected).replace('\r\n', '\n')):
            raise Stop('brouillon_contenu_non_conforme')
        return {'uid': saved.uid, 'uidvalidity': saved.uidvalidity,
                'folder': self.cfg['drafts'], 'verified_at': datetime.now(timezone.utc).isoformat()}

    def preflight(self, mail, draft_mid=None, allow_seen=False):
        current = self.fetch(mail.mailbox, mail.uid, headers_only=True)
        if current.uidvalidity != mail.uidvalidity or current.mid != mail.mid:
            return 'message_source_change'
        reason = exclusion(current, self.cfg, allow_seen=allow_seen)
        if reason: return reason
        if self.existing_draft(mail, draft_mid): return 'brouillon_existant'
        own = {a.lower() for a in self.cfg['own_addresses']}
        for m in self.thread(mail):
            if m.timestamp >= mail.timestamp:
                if m.sender in own: return 'reponse_envoyee_depuis'
                return 'message_plus_recent_dans_fil'
        # A manually composed reply may lack References / In-Reply-To.
        # A matching outgoing subject is sufficient to abstain, not to merge data.
        since = mail.timestamp.strftime('%d-%b-%Y')
        outgoing = self.search(self.cfg['sent'], 'UNDELETED', 'SINCE', since, 'TO', imap_quote(mail.sender))
        if len(outgoing)>60: raise Stop('historique_envois_a_verifier')
        for uid in outgoing:
            sent = self.fetch(self.cfg['sent'],uid,headers_only=True)
            if sent.sender in own and sent.timestamp>=mail.timestamp and subject_key(sent.subject)==subject_key(mail.subject):
                return 'reponse_envoyee_objet_identique'
        return ''

    def append_draft(self, message):
        # Mailbox mutations: this draft APPEND and, since 5.2.0, append_reminder (own INBOX only). No STORE, COPY, EXPUNGE,
        # SMTP, sendmail, or calendar/file mutation is exposed.
        status, _ = self.conn.append(imap_quote(modified_utf7(self.cfg['drafts'])),
                                     '(\\Draft)', imaplib.Time2Internaldate(datetime.now(timezone.utc)),
                                     message.as_bytes(policy=policy.SMTP))
        if status != 'OK': raise Stop('append_imap_incertain')

    def append_reminder(self, message):
        # 5.2.0 : rappel d'échéance déposé dans la boîte de réception du cabinet lui-même (non lu, sans envoi SMTP).
        # Expéditeur et destinataire doivent être l'adresse du cabinet : aucun message à destination d'un tiers ne peut être créé ici.
        own = self.cfg.get('from_address', '').strip().lower()
        if not own or str(message.get('From', '')).strip().lower() != own or str(message.get('To', '')).strip().lower() != own:
            raise Stop('rappel_destinataire_refuse')
        status, _ = self.conn.append('INBOX', '', imaplib.Time2Internaldate(datetime.now(timezone.utc)), message.as_bytes(policy=policy.SMTP))
        if status != 'OK': raise Stop('append_imap_incertain')


def make_draft(mail, cfg, body, key, cc=()):
    if '\r' in cfg['from_name'] or '\n' in cfg['from_name']: raise Stop('identite_invalide')
    result = EmailMessage(policy=policy.SMTP)
    result['From'] = formataddr((cfg['from_name'], cfg['from_address']))
    result['To'] = mail.sender
    if cc:
        if any(not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+', a) for a in cc): raise Stop('copie_invalide')
        result['Cc'] = ', '.join(cc)
    result['Subject'] = mail.subject if re.match(r'(?i)^re\s*:', mail.subject) else 'Re: ' + mail.subject
    result['Date'] = format_datetime(datetime.now(timezone.utc))
    result['Message-ID'] = '<axiorhub-' + key + '@mail-agent.local>'
    result['In-Reply-To'] = mail.mid
    chain = list(dict.fromkeys(mail.refs + [mail.mid]))
    result['References'] = ' '.join(chain[-40:])
    result['X-AxiorHub-Draft-Key'] = key
    result.set_content(body.strip() + '\n\n' + cfg['signature'].strip() + '\n')
    return result
