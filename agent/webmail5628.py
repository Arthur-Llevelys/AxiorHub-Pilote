"""5.6.28 : fonctions de messagerie dans « Courriels › Boîte de réception » — répondre, répondre à tous, transférer, nouveau message
(À, Cc, Cci, objet, texte mis en forme, pièces jointes), archiver, déplacer, indésirable, supprimer (corbeille), lu / non lu, suivi.

Garde-fous :
- chaque action est un clic de l'avocat (rôles avocat et administrateur), jamais une décision de l'agent ; tout est journalisé ;
- « Supprimer » déplace dans la corbeille de la messagerie (récupérable) ; rien n'est supprimé définitivement ici ;
- un message rédigé est d'abord enregistré dans Brouillons ; l'envoi passe par le circuit d'envoi existant (interrupteur général,
  aperçu puis confirmation, plafond horaire, copie dans Envoyés). Les messages rédigés ici portent l'en-tête interne
  ``X-AxiorHub-Origine: webmail5628`` (retiré à l'envoi) : le rattachement à un dossier n'y est pas exigé ;
- le texte mis en forme est nettoyé côté serveur (liste fermée de balises, liens http, https et mailto seulement).
"""
import base64
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, format_datetime, getaddresses, make_msgid
from html import escape
from html.parser import HTMLParser
import imaplib
import re

from .common import Stop
from . import mailbox as mbx
from .boite5625 import _folder, _folder_rows, _uid

ORIGIN = 'webmail5628'
MAX_FILES = 10
MAX_UPLOAD = 15 * 1024 * 1024
ALLOWED = {'p', 'br', 'b', 'strong', 'i', 'em', 'u', 's', 'strike', 'ul', 'ol', 'li', 'a', 'blockquote', 'div', 'span', 'h1', 'h2', 'h3',
           'pre', 'code', 'hr', 'sub', 'sup'}
VOID = {'br', 'hr'}
DROP = {'script', 'style', 'head', 'title', 'iframe', 'object', 'embed', 'svg', 'math', 'template', 'form', 'input', 'button', 'select', 'textarea'}


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.hidden = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            self.hidden += 1
            return
        if self.hidden or tag not in ALLOWED:
            return
        extra = ''
        if tag == 'a':
            href = dict(attrs).get('href') or ''
            if re.match(r'(https?:|mailto:)', href.strip(), re.I):
                extra = ' href="%s" rel="noopener noreferrer"' % escape(href.strip(), quote=True)
        self.out.append('<%s%s>' % (tag, extra))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in DROP:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden or tag not in ALLOWED or tag in VOID or tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append('</%s>' % top)
            if top == tag:
                break

    def handle_data(self, data):
        if not self.hidden:
            self.out.append(escape(data, quote=False))

    def result(self):
        return ''.join(self.out) + ''.join('</%s>' % t for t in reversed(self.stack))


def clean_html(html):
    """Texte mis en forme réduit à une liste fermée de balises, sans attribut hormis les liens sûrs."""
    p = _Clean()
    p.feed(str(html or ''))
    p.close()
    return p.result()


def html_to_text(html):
    p = mbx.PlainHTML()
    p.feed(html)
    return re.sub(r'\n{3,}', '\n\n', ''.join(p.parts)).strip()


def text_to_html(text):
    return '<br>'.join(escape(line, quote=False) for line in str(text or '').replace('\r\n', '\n').split('\n'))


# ------------------------------------------------------------------------------------------------ connexion et dossiers
def _open(desk):
    return mbx.Mailbox(desk.c['mail'])


def _select_rw(box, folder):
    from .drafts440 import _select
    return _select(box, folder, False)


def _caps(box):
    return {(c.decode() if isinstance(c, bytes) else str(c)).upper() for c in getattr(box.conn, 'capabilities', ())}


def _role_folder(box, cfg, role):
    return next((r['name'] for r in _folder_rows(box, cfg) if r['role'] == role), '')


def _archive_folder(box, cfg):
    found = _role_folder(box, cfg, 'archive')
    if found:
        return found
    base = cfg.get('drafts') or cfg.get('sent') or ''
    name = ('INBOX.Archives' if base.upper().startswith('INBOX.') else ('INBOX/Archives' if base.upper().startswith('INBOX/') else 'Archives'))
    status, _ = box.conn.create(mbx.imap_quote(mbx.modified_utf7(name)))
    if status != 'OK':
        raise Stop('dossier_archives_impossible_a_creer')
    try:
        box.conn.subscribe(mbx.imap_quote(mbx.modified_utf7(name)))
    except (imaplib.IMAP4.error, AttributeError, OSError):
        pass
    return name


def _move(box, cfg, folder, uid, target):
    if target == folder:
        raise Stop('courriel_deja_dans_ce_dossier')
    _select_rw(box, folder)
    quoted = mbx.imap_quote(mbx.modified_utf7(target))
    if 'MOVE' in _caps(box):
        status, _ = box.conn.uid('MOVE', uid, quoted)
        if status != 'OK':
            raise Stop('deplacement_imap_refuse')
        return
    status, _ = box.conn.uid('COPY', uid, quoted)
    if status != 'OK':
        raise Stop('deplacement_imap_refuse')
    status, _ = box.conn.uid('STORE', uid, '+FLAGS.SILENT', '(\\Deleted)')
    if status != 'OK':
        raise Stop('deplacement_imap_incomplet_copie_faite')
    if 'UIDPLUS' in _caps(box):
        box.conn.uid('EXPUNGE', uid)


LABELS = {'archive': 'archivé', 'junk': 'déplacé dans les indésirables', 'trash': 'déplacé dans la corbeille (récupérable)', 'move': 'déplacé'}


def act(desk, action, uid, folder=None, target=None, owner='cabinet'):
    """Archiver, déplacer, indésirable, supprimer (corbeille), lu/non lu, suivi : un clic de l'avocat sur un courriel."""
    cfg = desk.c['mail']
    uid = _uid(uid)
    box = _open(desk)
    try:
        folder = _folder(box, cfg, folder)
        if action in ('lu', 'non_lu', 'suivi', 'non_suivi'):
            flag = '(\\Seen)' if action in ('lu', 'non_lu') else '(\\Flagged)'
            _select_rw(box, folder)
            status, _ = box.conn.uid('STORE', uid, '+FLAGS.SILENT' if action in ('lu', 'suivi') else '-FLAGS.SILENT', flag)
            if status != 'OK':
                raise Stop('marquage_imap_refuse')
            desk.audit('webmail5628_marquage', {'action': action, 'owner': owner})
            return {'message': {'lu': 'Marqué comme lu.', 'non_lu': 'Marqué comme non lu.', 'suivi': 'Marqué pour suivi.', 'non_suivi': 'Suivi retiré.'}[action]}
        if action == 'archive':
            dest = _archive_folder(box, cfg)
        elif action == 'junk':
            dest = _role_folder(box, cfg, 'junk')
            if not dest:
                raise Stop('dossier_indesirables_absent')
        elif action == 'trash':
            dest = _role_folder(box, cfg, 'trash')
            if not dest:
                from .drafts440 import find_trash
                dest = find_trash(box)
            if not dest:
                raise Stop('corbeille_absente')
            if folder == dest:
                raise Stop('deja_dans_la_corbeille_suppression_definitive_dans_la_messagerie')
        elif action == 'move':
            dest = _folder(box, cfg, target)
        else:
            raise Stop('action_inconnue')
        _move(box, cfg, folder, uid, dest)
        desk.audit('webmail5628_deplacement', {'action': action, 'owner': owner})
        return {'message': 'Courriel %s.' % LABELS[action], 'folder': dest}
    finally:
        box.close()


# ------------------------------------------------------------------------------------------------ rédaction
def _addr_list(value):
    return [(n, a) for n, a in getaddresses([str(value or '')]) if a]


def _display(name, address):
    """Adresse lisible dans le champ À/Cc (accents non encodés) ; l'encodage MIME est fait à la construction du message."""
    name = re.sub('[\r\n"]+', ' ', str(name or '')).strip()
    if not name:
        return address
    return ('"%s" <%s>' % (name, address)) if re.search(r'[,;:<>@()\[\]]', name) else '%s <%s>' % (name, address)


def _fmt(pairs):
    return ', '.join(_display(n, a) for n, a in pairs)


def _prefixed(subject, prefix):
    subject = str(subject or '').strip()
    return subject if re.match(r'(?i)^\s*%s\s*:' % re.escape(prefix.rstrip(' :')), subject) else '%s %s' % (prefix, subject)


def template(desk, mode, uid=None, folder=None):
    """Champs pré-remplis pour répondre, répondre à tous, transférer ou écrire un nouveau message."""
    cfg = desk.c['mail']
    own = {a.lower() for a in mbx.own_addresses(cfg)}
    sender = _display(cfg.get('from_name') or '', cfg.get('from_address') or '')
    from . import courrier5632 as c32   # 5.6.32 : signature, police et copie cachée automatique
    if mode not in ('nouveau', 'repondre', 'repondre_tous', 'transferer'):
        raise Stop('mode_de_redaction_inconnu')
    sig = c32.signature_for(desk, mode)
    sig_html = ('<p><br></p>' + sig) if sig else ''
    extra = {'font': c32.font_css(desk), 'bcc': ', '.join(c32.auto_bcc(desk, mode)), 'signature': bool(sig)}
    if mode == 'nouveau':
        return {'mode': mode, 'from': sender, 'to': '', 'cc': '', 'subject': '', 'html': '<p><br></p>' + sig_html, 'attachments': [], **extra}
    box = _open(desk)
    try:
        folder = _folder(box, cfg, folder)
        mail = box.fetch(folder, _uid(uid))
    finally:
        box.close()
    msg = mail.msg
    when = mail.timestamp.astimezone().strftime('%d/%m/%Y à %H:%M')
    original = text_to_html(mail.text[:60000])
    out = {'mode': mode, 'from': sender, 'source_uid': mail.uid, 'source_folder': folder, 'attachments': [], **extra}
    if mode == 'transferer':
        out.update({'to': '', 'cc': '', 'subject': _prefixed(mail.subject, 'TR :'),
                    'html': ('<p><br></p>%s<p>-------- Message transféré --------<br>De : %s<br>Date : %s<br>Objet : %s<br>À : %s</p><blockquote>%s</blockquote>' % (
                        sig_html, escape(str(msg.get('From', ''))), escape(when), escape(mail.subject), escape(str(msg.get('To', ''))), original)),
                    'attachments': [{'index': n, 'name': str(p.get_filename() or 'pièce %d' % (n + 1)), 'size': len(p.get_payload(decode=True) or b'')}
                                    for n, p in enumerate(msg.iter_attachments())]})
        return out
    reply_to = _addr_list(msg.get('Reply-To')) or _addr_list(msg.get('From'))
    to = [p for p in reply_to if p[1].lower() not in own] or reply_to
    cc = []
    if mode == 'repondre_tous':
        seen = {p[1].lower() for p in to} | own
        for p in _addr_list(msg.get('To')) + _addr_list(msg.get('Cc')):
            if p[1].lower() not in seen:
                seen.add(p[1].lower())
                cc.append(p)
    out.update({'to': _fmt(to), 'cc': _fmt(cc), 'subject': _prefixed(mail.subject, 'Re :'),
                'html': '<p><br></p>%s<p>Le %s, %s a écrit :</p><blockquote>%s</blockquote>' % (sig_html, escape(when), escape(str(msg.get('From', ''))), original)})
    return out


def _recipients(value, label):
    from .drafts440 import parse_recipients
    return parse_recipients(str(value or ''), label) if str(value or '').strip() else ''


def build(desk, data):
    """Message prêt à déposer dans Brouillons (texte et version mise en forme, pièces jointes)."""
    cfg = desk.c['mail']
    mode = str(data.get('mode') or 'nouveau')
    to, cc, bcc = _recipients(data.get('to'), 'destinataires'), _recipients(data.get('cc'), 'copies'), _recipients(data.get('bcc'), 'copies_cachees')
    subject = re.sub(r'[\r\n]+', ' ', str(data.get('subject') or '')).strip()[:300]
    html = clean_html(str(data.get('html') or '')[:400000])
    text = html_to_text(html)
    if not (to or cc or bcc) and data.get('intent') == 'envoyer':
        raise Stop('destinataire_requis')
    msg = EmailMessage(policy=policy.SMTP)
    msg['From'] = formataddr((cfg.get('from_name') or '', cfg['from_address']))
    for name, value in (('To', to), ('Cc', cc), ('Bcc', bcc)):
        if value:
            msg[name] = value
    msg['Subject'] = subject
    msg['Date'] = format_datetime(datetime.now(timezone.utc))
    msg['Message-ID'] = make_msgid(domain=(cfg['from_address'].rsplit('@', 1)[-1] or 'localhost'))
    msg['X-AxiorHub-Origine'] = ORIGIN
    stamp = datetime.now(timezone.utc).isoformat()
    msg['X-AxiorHub-Compose'] = stamp
    source = None
    if mode in ('repondre', 'repondre_tous', 'transferer') and data.get('source_uid'):
        box = _open(desk)
        try:
            source = box.fetch(_folder(box, cfg, data.get('source_folder')), _uid(data.get('source_uid')))
        finally:
            box.close()
        if mode != 'transferer' and source.mid:
            msg['In-Reply-To'] = source.mid
            msg['References'] = ' '.join(([str(source.msg.get('References', '')).strip()] if source.msg.get('References') else []) + [source.mid])
    msg.set_content(text + '\n')
    from .courrier5632 import wrap   # 5.6.32 : police choisie dans les réglages de messagerie
    msg.add_alternative('<!doctype html><html><body>%s</body></html>' % wrap(desk, html), subtype='html')
    total = 0
    if source is not None and mode == 'transferer':
        keep = {int(i) for i in (data.get('keep') or []) if str(i).isdigit()}
        for n, part in enumerate(source.msg.iter_attachments()):
            if n in keep:
                raw = part.get_payload(decode=True) or b''
                total += len(raw)
                msg.add_attachment(raw, maintype=part.get_content_maintype(), subtype=part.get_content_subtype(), filename=part.get_filename() or 'piece')
    files = data.get('files') or []
    if len(files) > MAX_FILES:
        raise Stop('dix_pieces_jointes_maximum')
    for f in files:
        name = re.sub(r'[\\/\r\n\x00]+', '_', str(f.get('name') or 'piece'))[:150] or 'piece'
        try:
            raw = base64.b64decode(str(f.get('data') or ''), validate=True)
        except (ValueError, TypeError):
            raise Stop('piece_jointe_illisible') from None
        total += len(raw)
        kind = str(f.get('type') or 'application/octet-stream')
        main, _, sub = kind.partition('/')
        if not re.fullmatch(r'[a-z]+', main or '') or not re.fullmatch(r'[a-z0-9.+-]+', sub or ''):
            main, sub = 'application', 'octet-stream'
        msg.add_attachment(raw, maintype=main, subtype=sub, filename=name)
    if total > MAX_UPLOAD:
        raise Stop('pieces_jointes_trop_lourdes_15_mo_maximum')
    return msg, stamp


def save(desk, data, owner='cabinet'):
    """Dépose le message dans Brouillons et le relit ; remplace le brouillon précédent de la même rédaction."""
    from .drafts440 import _append_uid, _search_uids
    cfg = desk.c['mail']
    msg, stamp = build(desk, data)
    box = _open(desk)
    try:
        folder = cfg['drafts']
        _select_rw(box, folder)
        status, response = box.conn.append(mbx.imap_quote(mbx.modified_utf7(folder)), '(\\Draft)',
                                           imaplib.Time2Internaldate(datetime.now(timezone.utc)), msg.as_bytes(policy=policy.SMTP))
        if status != 'OK':
            raise Stop('append_imap_incertain')
        uid = _append_uid(response)
        if not uid:
            _select_rw(box, folder)
            found = _search_uids(box, 'UNDELETED', 'HEADER', 'X-AxiorHub-Compose', mbx.imap_quote(stamp))
            uid = found[-1] if found else ''
        if not uid:
            raise Stop('brouillon_enregistre_non_retrouve')
        saved = box.fetch(folder, uid)
        if str(saved.msg.get('X-AxiorHub-Compose', '')).strip() != stamp:
            raise Stop('brouillon_enregistre_non_conforme')
        previous = str(data.get('draft_uid') or '')
        if previous.isdigit() and previous != uid:   # brouillon précédent de cette même rédaction : retiré (il est remplacé)
            try:
                old = box.fetch(folder, previous)
                if str(old.msg.get('X-AxiorHub-Origine', '')).strip() == ORIGIN:
                    _select_rw(box, folder)
                    box.conn.uid('STORE', previous, '+FLAGS.SILENT', '(\\Deleted)')
                    if 'UIDPLUS' in _caps(box):
                        box.conn.uid('EXPUNGE', previous)
            except (Stop, imaplib.IMAP4.error, OSError):
                pass
        desk.audit('webmail5628_brouillon', {'mode': str(data.get('mode') or 'nouveau'), 'owner': owner})
        return {'uid': uid, 'uidvalidity': saved.uidvalidity, 'message': 'Enregistré dans Brouillons.'}
    finally:
        box.close()


# ------------------------------------------------------------------------------------------------ API
def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    route = name[len('m5628/'):].split('?', 1)[0]
    if method == 'GET':
        if route == 'modele':
            return json_out(template(desk, str(args.get('mode') or 'nouveau'), args.get('uid'), args.get('dossier') or None))
        if route == 'envoi':
            from . import web490
            return json_out(web490.webmail_status(desk))
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    if route == 'action':
        return json_out(act(desk, str(data.get('action') or ''), data.get('uid'), data.get('dossier') or None, data.get('cible') or None, owner))
    if route == 'brouillon':
        return json_out(save(desk, data, owner))
    if route in ('envoyer', 'envoyer/confirmer'):   # l'envoi reste dans la couche web d'envoi (aperçu puis confirmation)
        from . import web490
        if route == 'envoyer':
            return json_out(web490.webmail_send(desk, data, owner))
        return json_out(web490.webmail_confirm(desk, str(data.get('token') or '')))
    raise Stop('route_inconnue')
