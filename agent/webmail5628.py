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
# 5.6.106 (lot R) : mise en forme de la rédaction. Seul attribut accepté hors des liens : ``style``, sur les balises ci-dessous, réduit à une
# LISTE FERMÉE de propriétés dont chaque valeur est ramenée à une valeur canonique (palette fixe, trois tailles, retrait par pas de 40 px).
# Le style rendu est toujours reconstruit à partir de ces valeurs canoniques : rien de ce qu'envoie le navigateur n'est recopié tel quel.
STYLED = {'p', 'div', 'span', 'h1', 'h2', 'h3', 'li', 'blockquote', 'ul', 'ol', 'b', 'strong', 'i', 'em', 'u', 's'}
PALETTE_TEXTE = {'#000000': 'Noir', '#5b6676': 'Gris', '#b42318': 'Rouge', '#1f4e9c': 'Bleu', '#1a7f37': 'Vert', '#b54708': 'Orange'}
PALETTE_SURLIGNAGE = {'#fff3a3': 'Jaune', '#c9f2d0': 'Vert clair', '#cfe3ff': 'Bleu clair', '#ffd6e0': 'Rose'}
TAILLES = ('small', 'medium', 'large')
RETRAITS = tuple('%dpx' % (40 * n) for n in range(1, 7))


def _couleur(value):
    """#rgb, #rrggbb ou rgb(r, g, b) → #rrggbb en minuscules ; toute autre écriture : refusée (``''``)."""
    v = value.strip().lower()
    m = re.fullmatch(r'#([0-9a-f]{3})', v)
    if m:
        return '#' + ''.join(c * 2 for c in m.group(1))
    if re.fullmatch(r'#[0-9a-f]{6}', v):
        return v
    m = re.fullmatch(r'rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)', v)
    if m and all(int(x) <= 255 for x in m.groups()):
        return '#%02x%02x%02x' % tuple(int(x) for x in m.groups())
    return ''


def _dans(palette):
    return lambda v: _couleur(v) if _couleur(v) in palette else ''


PROPRIETES = {
    'text-align': lambda v: {'start': 'left', 'end': 'right'}.get(v, v) if v in ('left', 'center', 'right', 'start', 'end') else '',
    'color': _dans(PALETTE_TEXTE),
    'background-color': _dans(PALETTE_SURLIGNAGE),
    'font-size': lambda v: v if v in TAILLES else '',
    'margin-left': lambda v: v if v in RETRAITS else '',
}


def clean_style(value):
    """Style ramené à la liste fermée ``PROPRIETES`` ; propriété inconnue ou valeur hors liste : retirée (jamais d'expression, d'url, de
    commentaire ni d'échappement CSS : seules des valeurs canoniques sont réécrites)."""
    out = {}
    for decl in str(value or '')[:600].split(';'):
        prop, sep, val = decl.partition(':')
        prop = prop.strip().lower()
        if not sep or prop not in PROPRIETES:
            continue
        canon = PROPRIETES[prop](val.strip().lower())
        if canon:
            out[prop] = canon
    return '; '.join('%s: %s' % kv for kv in sorted(out.items()))


# 5.6.106 (lot R) : images collées dans la rédaction. ``<img>`` n'est accepté que si l'appelant le demande (``images=True`` : rédaction,
# brouillon, envoi d'un message de la messagerie), avec un ``src`` ``cid:`` ou une image ``data:`` PNG, JPEG, GIF ou WebP encore à convertir
# en pièce intégrée ; seuls ``alt`` et ``width`` (trois largeurs fixes) sont gardés. Jamais d'adresse distante, jamais de SVG.
IMG_SRC = re.compile(r'(cid:[A-Za-z0-9._@$+-]{1,200}|data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+)$')
LARGEURS = ('200', '400', '640')
IMAGES_MAX = 10
IMAGE_MAX = 2 * 1024 * 1024
SIGNATURES = {'png': (b'\x89PNG\r\n\x1a\n',), 'jpeg': (b'\xff\xd8\xff',), 'gif': (b'GIF87a', b'GIF89a')}


def type_image(raw):
    """Type réel d'une image d'après ses premiers octets (PNG, JPEG, GIF, WebP) ; autre chose (SVG, HTML…) : ``''``."""
    raw = bytes(raw or b'')
    for kind, heads in SIGNATURES.items():
        if raw.startswith(heads):
            return kind
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'webp'
    return ''


def _src_image(src):
    src = str(src or '').strip()
    return src if IMG_SRC.match(src) else ''


class _Clean(HTMLParser):
    def __init__(self, images=False):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.hidden, self.images = [], [], 0, images

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            self.hidden += 1
            return
        if not self.hidden and tag == 'img' and self.images:   # 5.6.106 (lot R) : image intégrée (cid) ou à intégrer (data:)
            a = dict(attrs)
            src = _src_image(a.get('src'))
            if src:
                width = str(a.get('width') or '').strip()
                self.out.append('<img src="%s" alt="%s"%s>' % (escape(re.sub(r'\s+', '', src), quote=True), escape(str(a.get('alt') or '')[:200], quote=True),
                                                             (' width="%s"' % width) if width in LARGEURS else ''))
            return
        if self.hidden or tag not in ALLOWED:
            return
        extra = ''
        if tag == 'a':
            href = dict(attrs).get('href') or ''
            if re.match(r'(https?:|mailto:)', href.strip(), re.I):
                extra = ' href="%s" rel="noopener noreferrer"' % escape(href.strip(), quote=True)
        if tag in STYLED:   # 5.6.106 (lot R) : style réduit à la liste fermée, réécrit à partir des valeurs canoniques
            style = clean_style(dict(attrs).get('style') or '')
            if style:
                extra += ' style="%s"' % escape(style, quote=True)
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


def clean_html(html, images=False):
    """Texte mis en forme réduit à une liste fermée de balises, sans attribut hormis les liens sûrs (et, 5.6.106, les styles de la liste
    fermée ; avec ``images=True``, les images ``cid:`` ou ``data:`` décrites plus haut)."""
    p = _Clean(images)
    p.feed(str(html or ''))
    p.close()
    return p.result()


IMG_DATA = re.compile(r'<img src="data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)"')
IMG_CID = re.compile(r'<img src="cid:([^"]+)"([^>]*)>')


def images_en_cid(html, domaine='axiorhub.local'):
    """5.6.106 (lot R) : images ``data:`` d'un HTML déjà nettoyé → ``(html avec src="cid:…", [(cid, octets, sous-type)])``. Type contrôlé par
    la signature des octets (le type déclaré ne compte pas), 2 Mo par image, 10 images ; une même image n'est jointe qu'une fois. Une image
    ``cid:`` qui ne vient pas de cette conversion est retirée (aucune référence vers une partie absente)."""
    import hashlib
    images, vus = [], {}

    def convertir(m):
        try:
            raw = base64.b64decode(m.group(2), validate=True)
        except (ValueError, TypeError):
            raise Stop('image_illisible') from None
        kind = type_image(raw)
        if not kind:
            raise Stop('image_refusee_png_jpeg_gif_webp_seulement')
        if len(raw) > IMAGE_MAX:
            raise Stop('image_trop_lourde_2_mo_maximum')
        cle = hashlib.sha256(raw).hexdigest()[:32]
        if cle not in vus:
            if len(images) >= IMAGES_MAX:
                raise Stop('dix_images_maximum')
            vus[cle] = '%s@%s' % (cle, domaine)
            images.append((vus[cle], raw, kind))
        return '<img src="cid:%s"' % vus[cle]
    html = IMG_DATA.sub(convertir, html)
    connus = {cid for cid, _, _ in images}
    html = IMG_CID.sub(lambda m: m.group(0) if m.group(1) in connus else '', html)
    return html, images


def images_liees(msg):
    """{cid: (octets, sous-type)} des images intégrées (``multipart/related``) d'un message, type contrôlé par la signature des octets."""
    out = {}
    for part in msg.walk():
        if part.get_content_maintype() != 'image' or not part.get('Content-ID'):
            continue
        raw = part.get_payload(decode=True) or b''
        kind = type_image(raw)
        if kind and len(raw) <= IMAGE_MAX:
            out[str(part.get('Content-ID')).strip().strip('<>')] = (raw, kind)
    return out


def images_en_data(html, liees):
    """Reprise d'un brouillon : ``src="cid:…"`` → image ``data:`` réaffichable dans l'éditeur ; référence inconnue : image retirée."""
    def remplacer(m):
        item = liees.get(m.group(1))
        if not item:
            return ''
        return '<img src="data:image/%s;base64,%s"%s>' % (item[1], base64.b64encode(item[0]).decode('ascii'), m.group(2))
    return IMG_CID.sub(remplacer, html)


def joindre_html(out, source):
    """5.6.106 (lot R) — point de montage pour le module d'envoi : ajoute à ``out`` la version HTML nettoyée de ``source`` (message de la
    messagerie) avec ses images intégrées (``multipart/related``, ``Content-ID``) ; rend ``False`` s'il n'y a pas de version HTML. Aucune
    image distante, aucune image ``data:`` dans le HTML produit."""
    part = source.get_body(preferencelist=('html',))
    if part is None:
        return False
    liees = images_liees(source)
    html = clean_html(part.get_content(), images=True)
    html = IMG_DATA.sub('<img src=""', html)
    html = re.sub(r'<img src=""[^>]*>', '', html)
    html = IMG_CID.sub(lambda m: m.group(0) if m.group(1) in liees else '', html)
    out.add_alternative('<!doctype html><html><body>%s</body></html>' % html, subtype='html')
    cids = list(dict.fromkeys(m[0] for m in IMG_CID.findall(html)))
    _relier(out, [(cid, liees[cid][0], liees[cid][1]) for cid in cids])
    return True


def _relier(msg, images):
    """Images ajoutées en parties ``multipart/related`` de la version HTML de ``msg`` (``Content-ID``, en ligne)."""
    if not images:
        return
    html_part = msg.get_body(preferencelist=('html',))
    for cid, raw, kind in images:
        html_part.add_related(raw, maintype='image', subtype=kind, cid='<%s>' % cid, disposition='inline', filename='image-%s.%s' % (cid[:8], 'jpg' if kind == 'jpeg' else kind))


def html_to_text(html):
    p = mbx.PlainHTML()
    p.feed(html)
    return re.sub(r'\n{3,}', '\n\n', ''.join(p.parts)).strip()


def text_to_html(text):
    return '<br>'.join(escape(line, quote=False) for line in str(text or '').replace('\r\n', '\n').split('\n'))


# ------------------------------------------------------------------------------------------------ connexion et dossiers
def _open(desk):
    return mbx.Mailbox(desk.c['mail'])


def _select_rw(box, folder, expected=None):
    """Sélection en écriture ; 5.6.100 (R04) : ``expected`` = UIDVALIDITY contrôlée par le garde (autre valeur : l'UID peut désigner un
    autre message → 404 neutre, aucun effet)."""
    from .drafts440 import _select
    validity = _select(box, folder, False)
    if expected is not None and str(validity) != str(expected):
        raise Stop('introuvable')
    return validity


def _garde(desk, acteur, box, cfg, folder, uid, validity=None):
    """5.6.100 (R04) : courriel contrôlé dans la session qui va agir (droits, UIDVALIDITY affichée revalidée par la sélection en écriture)
    avant STORE, MOVE ou COPY ; rend l'UIDVALIDITY contrôlée. Sans acteur : appels internes historiques (``None``)."""
    if acteur is None:
        return None
    from .acces_courriel56100 import selectionner_ecriture
    return selectionner_ecriture(desk, acteur, box, cfg, folder, uid, validity).uidvalidity


def _source(desk, acteur, box, cfg, folder, uid, validity=None, exiger=False):
    """Courriel d'origine d'une réponse ou d'un transfert, lu après le garde (corps seulement s'il est autorisé).
    5.6.108 (lot C, B02) : une génération (UIDVALIDITY) transmise est toujours contrôlée, appel interne compris ; ``exiger`` (requête d'une
    session) : sans génération, refus lisible ``uidvalidity_requise`` APRÈS le garde (un objet interdit garde son 404 neutre), jamais
    complétée par la valeur actuelle du serveur."""
    if acteur is None:
        mail = box.fetch(folder, uid)
        if validity and str(mail.uidvalidity) != str(validity):
            raise Stop('uidvalidity_modifiee')   # autre génération : ni son corps ni ses pièces ne sont repris
        return mail
    from .acces_courriel56100 import lire_imap
    if exiger and not str(validity or '').isdigit():
        lire_imap(desk, acteur, box, cfg, folder, uid, None, headers_only=True)   # objet absent ou interdit : même 404 neutre, corps jamais lu
        raise Stop('uidvalidity_requise')
    return lire_imap(desk, acteur, box, cfg, folder, uid, validity)[0]


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


def _move(box, cfg, folder, uid, target, expected=None):
    if target == folder:
        raise Stop('courriel_deja_dans_ce_dossier')
    _select_rw(box, folder, expected)
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


def act(desk, action, uid, folder=None, target=None, owner='cabinet', acteur=None, validity=None):
    """Archiver, déplacer, indésirable, supprimer (corbeille), lu/non lu, suivi : un clic de l'avocat sur un courriel."""
    cfg = desk.c['mail']
    uid = _uid(uid)
    box = _open(desk)
    try:
        folder = _folder(box, cfg, folder)
        controlee = _garde(desk, acteur, box, cfg, folder, uid, validity)   # 5.6.100 (R04) : avant tout effet
        if action in ('lu', 'non_lu', 'suivi', 'non_suivi'):
            flag = '(\\Seen)' if action in ('lu', 'non_lu') else '(\\Flagged)'
            _select_rw(box, folder, controlee)
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
        _move(box, cfg, folder, uid, dest, controlee)
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


MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def jour_du_cabinet(desk):
    """5.6.106 (lot R) : date du jour dans le fuseau du cabinet, en toutes lettres (« 16 novembre 2026 »)."""
    from .acces5683 import today_of
    jour = today_of(desk)[0]
    return '%d%s %s %d' % (jour.day, 'er' if jour.day == 1 else '', MOIS[jour.month - 1], jour.year)


def empreinte(raw):
    """5.6.106 (lot R) : identifiant stable d'une pièce jointe d'un brouillon (le même fichier garde la même empreinte d'un
    enregistrement à l'autre, alors que son UID et son rang changent)."""
    import hashlib
    return hashlib.sha256(raw or b'').hexdigest()[:24]


def _corps_html(msg):
    part = msg.get_body(preferencelist=('html', 'plain'))
    if part is None:
        return '<p><br></p>'
    try:
        content = part.get_content()
    except (LookupError, UnicodeError, AttributeError):
        content = ''
    content = str(content or '')[:400000]
    if part.get_content_subtype() != 'html':
        return text_to_html(content)
    return images_en_data(clean_html(content, images=True), images_liees(msg))   # 5.6.106 (lot R) : images du brouillon réaffichées


def reprendre(desk, uid, acteur=None, validity=None):
    """5.6.106 (lot R) : brouillon du dossier Brouillons rouvert dans la rédaction (destinataires, objet, corps nettoyé, pièces jointes
    désignées par leur empreinte). Le brouillon est contrôlé (droits, origine, UIDVALIDITY) avant son corps ; le dossier est toujours celui
    des brouillons, jamais un dossier désigné par le navigateur. L'enregistrement suivant remplace ce brouillon (``reprise_uid``)."""
    from . import courrier5632 as c32
    cfg = desk.c['mail']
    uid = _uid(uid)
    box = _open(desk)
    try:
        mail = _source(desk, acteur, box, cfg, cfg['drafts'], uid, validity, exiger=acteur is not None)   # 5.6.108 (B02)
    finally:
        box.close()
    msg = mail.msg
    pieces = []
    for n, part in enumerate(msg.iter_attachments()):
        raw = part.get_payload(decode=True) or b''
        pieces.append({'empreinte': empreinte(raw), 'name': str(part.get_filename() or 'pièce %d' % (n + 1)), 'size': len(raw)})
    entete = lambda name: ', '.join(_display(n, a) for n, a in _addr_list(msg.get(name)))   # noqa: E731
    return {'mode': 'reprise', 'from': _display(cfg.get('from_name') or '', cfg.get('from_address') or ''), 'to': entete('To'),
            'cc': entete('Cc'), 'bcc': entete('Bcc'), 'subject': re.sub(r'[\r\n]+', ' ', mail.subject).strip()[:300],
            'html': _corps_html(msg), 'attachments': [], 'pieces_brouillon': pieces, 'draft_uid': mail.uid,
            'reprise_uid': mail.uid, 'draft_uidvalidity': mail.uidvalidity, 'font': c32.font_css(desk), 'signature': False,
            'jour': jour_du_cabinet(desk)}


def template(desk, mode, uid=None, folder=None, acteur=None, validity=None):
    """Champs pré-remplis pour répondre, répondre à tous, transférer ou écrire un nouveau message."""
    cfg = desk.c['mail']
    own = {a.lower() for a in mbx.own_addresses(cfg)}
    sender = _display(cfg.get('from_name') or '', cfg.get('from_address') or '')
    from . import courrier5632 as c32   # 5.6.32 : signature, police et copie cachée automatique
    if mode == 'brouillon':   # 5.6.106 (lot R) : « Modifier » depuis le dossier Brouillons
        return reprendre(desk, uid, acteur, validity)
    if mode not in ('nouveau', 'repondre', 'repondre_tous', 'transferer'):
        raise Stop('mode_de_redaction_inconnu')
    sig = c32.signature_for(desk, mode)
    sig_html = ('<p><br></p>' + sig) if sig else ''
    extra = {'font': c32.font_css(desk), 'bcc': ', '.join(c32.auto_bcc(desk, mode)), 'signature': bool(sig),
             'jour': jour_du_cabinet(desk)}   # 5.6.106 (lot R) : « Date du jour » de la barre de mise en forme (jour du cabinet)
    if mode == 'nouveau':
        return {'mode': mode, 'from': sender, 'to': '', 'cc': '', 'subject': '', 'html': '<p><br></p>' + sig_html, 'attachments': [], **extra}
    box = _open(desk)
    try:
        folder = _folder(box, cfg, folder)
        mail = _source(desk, acteur, box, cfg, folder, _uid(uid), validity, exiger=acteur is not None)   # 5.6.100 (R04) : garde avant le corps cité ; 5.6.108 (B02) : génération exigée
    finally:
        box.close()
    msg = mail.msg
    when = mail.timestamp.astimezone().strftime('%d/%m/%Y à %H:%M')
    original = text_to_html(mail.text[:60000])
    out = {'mode': mode, 'from': sender, 'source_uid': mail.uid, 'source_uidvalidity': mail.uidvalidity,
           'source_folder': folder, 'attachments': [], **extra}
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


def build(desk, data, acteur=None):
    """Message prêt à déposer dans Brouillons (texte et version mise en forme, pièces jointes)."""
    msg, stamp, _pieces = build_detail(desk, data, acteur)
    return msg, stamp


def _validite_brouillon(data):
    """Un UID seul n'identifie pas un message après reconstruction d'une boîte."""
    if not str(data.get('draft_uid') or '').isdigit():
        return None
    value = str(data.get('draft_uidvalidity') or '')
    if not value.isdigit():
        raise Stop('uidvalidity_requise')
    return value


def _brouillon_precedent(desk, acteur, cfg, data):
    """5.6.106 (lot R) : brouillon précédent de la rédaction (``draft_uid``), relu dans le dossier Brouillons après le garde, quand il faut
    en reprendre des pièces (``draft_keep`` : empreintes) ou, pour un brouillon repris, les en-têtes de fil (In-Reply-To, References)."""
    previous = str(data.get('draft_uid') or '')
    garder = data.get('draft_keep') if isinstance(data.get('draft_keep'), list) else []
    if len(garder) > 3 * MAX_FILES or any(not re.fullmatch(r'[0-9a-f]{24}', str(x)) for x in garder):
        raise Stop('pieces_du_brouillon_invalides')
    if not previous.isdigit() or not (garder or str(data.get('mode') or '') == 'reprise'):
        return None, set()
    box = _open(desk)
    try:
        return _source(desk, acteur, box, cfg, cfg['drafts'], _uid(previous), _validite_brouillon(data)), {str(x) for x in garder}
    finally:
        box.close()


def build_detail(desk, data, acteur=None):
    """Comme ``build`` ; rend aussi la liste des pièces jointes du message (empreinte, nom, taille, origines), pour que la rédaction
    remplace ses pièces locales par celles du brouillon enregistré (5.6.106, lot R). Une même pièce (même contenu, même nom) n'est
    jointe qu'une fois, d'où qu'elle vienne."""
    cfg = desk.c['mail']
    mode = str(data.get('mode') or 'nouveau')
    to, cc, bcc = _recipients(data.get('to'), 'destinataires'), _recipients(data.get('cc'), 'copies'), _recipients(data.get('bcc'), 'copies_cachees')
    subject = re.sub(r'[\r\n]+', ' ', str(data.get('subject') or '')).strip()[:300]
    html = clean_html(str(data.get('html') or '')[:30000000], images=True)   # 5.6.106 (lot R) : images collées (data:) gardées pour conversion
    domaine = cfg['from_address'].rsplit('@', 1)[-1] or 'localhost'
    html, images = images_en_cid(html, domaine)
    if len(re.sub(r'<img [^>]*>', '', html)) > 2000000:
        raise Stop('message_trop_long')
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
            source = _source(desk, acteur, box, cfg, _folder(box, cfg, data.get('source_folder')), _uid(data.get('source_uid')),
                             data.get('source_uidvalidity') or None, exiger=acteur is not None)   # 5.6.100 (R04) : courriel cité ou transféré contrôlé avant son corps ; 5.6.108 (B02) : génération de l'origine exigée
        finally:
            box.close()
        if mode != 'transferer' and source.mid:
            msg['In-Reply-To'] = source.mid
            msg['References'] = ' '.join(([str(source.msg.get('References', '')).strip()] if source.msg.get('References') else []) + [source.mid])
    precedent, garder = _brouillon_precedent(desk, acteur, cfg, data)   # 5.6.106 (lot R) : pièces et fil du brouillon précédent
    if precedent is not None and mode == 'reprise':
        for name in ('In-Reply-To', 'References'):
            value = re.sub(r'[\r\n]+', ' ', str(precedent.msg.get(name, '') or '')).strip()[:4000]
            if value:
                msg[name] = value
    msg.set_content(text + '\n')
    from .courrier5632 import wrap   # 5.6.32 : police choisie dans les réglages de messagerie
    msg.add_alternative('<!doctype html><html><body>%s</body></html>' % wrap(desk, html), subtype='html')
    _relier(msg, images)   # 5.6.106 (lot R) : images intégrées (multipart/related, Content-ID), comptées dans le plafond du message
    total, pieces, vues = sum(len(raw) for _, raw, _ in images), [], {}

    def joindre(raw, main, sub, filename, origine):
        nonlocal total
        cle = (empreinte(raw), filename)
        if cle in vues:   # même pièce déjà jointe (par exemple fichier local déjà présent dans le brouillon) : une seule fois
            vues[cle]['origines'].append(origine)
            return
        total += len(raw)
        msg.add_attachment(raw, maintype=main, subtype=sub, filename=filename)
        vues[cle] = {'empreinte': cle[0], 'name': filename, 'size': len(raw), 'origines': [origine]}
        pieces.append(vues[cle])

    if precedent is not None and garder:
        for part in precedent.msg.iter_attachments():
            raw = part.get_payload(decode=True) or b''
            if empreinte(raw) in garder:
                joindre(raw, part.get_content_maintype(), part.get_content_subtype(), part.get_filename() or 'piece',
                        {'type': 'brouillon', 'empreinte': empreinte(raw)})
    if source is not None and mode == 'transferer':
        keep = {int(i) for i in (data.get('keep') or []) if str(i).isdigit()}
        for n, part in enumerate(source.msg.iter_attachments()):
            if n in keep:
                raw = part.get_payload(decode=True) or b''
                joindre(raw, part.get_content_maintype(), part.get_content_subtype(), part.get_filename() or 'piece', {'type': 'source', 'index': n})
    files = data.get('files') or []
    if len(files) > MAX_FILES:
        raise Stop('dix_pieces_jointes_maximum')
    for f in files:
        name = re.sub(r'[\\/\r\n\x00]+', '_', str(f.get('name') or 'piece'))[:150] or 'piece'
        try:
            raw = base64.b64decode(str(f.get('data') or ''), validate=True)
        except (ValueError, TypeError):
            raise Stop('piece_jointe_illisible') from None
        kind = str(f.get('type') or 'application/octet-stream')
        main, _, sub = kind.partition('/')
        if not re.fullmatch(r'[a-z]+', main or '') or not re.fullmatch(r'[a-z0-9.+-]+', sub or ''):
            main, sub = 'application', 'octet-stream'
        joindre(raw, main, sub, name, {'type': 'local', 'cle': re.sub(r'[^A-Za-z0-9_-]', '', str(f.get('cle') or ''))[:40]})
    if data.get('nextcloud'):   # 5.6.70 : documents de l'espace de fichiers, relus au moment du dépôt
        from .fichiers5670 import attachments as _nc_files
        paths = data.get('nextcloud') if isinstance(data.get('nextcloud'), list) else []
        if len(files) + len(paths) > MAX_FILES:
            raise Stop('dix_pieces_jointes_maximum')
        chemins = [str(p) for p in paths if str(p).strip()]   # même filtre et même ordre que ``fichiers5670.attachments``
        for n, (name, raw, kind) in enumerate(_nc_files(desk, paths)):
            main, _, sub_ = kind.partition('/')
            joindre(raw, main or 'application', sub_ or 'octet-stream', name, {'type': 'nextcloud', 'chemin': chemins[n] if n < len(chemins) else ''})
    if total > MAX_UPLOAD:
        raise Stop('pieces_jointes_trop_lourdes_15_mo_maximum')
    return msg, stamp, pieces


def save(desk, data, owner='cabinet', acteur=None):
    """Dépose le message dans Brouillons et le relit ; remplace le brouillon précédent de la même rédaction."""
    from .drafts440 import _append_uid, _search_uids
    cfg = desk.c['mail']
    validite = _validite_brouillon(data)
    msg, stamp, pieces = build_detail(desk, data, acteur)   # 5.6.106 (lot R) : pièces du brouillon rendues à la rédaction
    box = _open(desk)
    try:
        folder = cfg['drafts']
        courante = _select_rw(box, folder, validite)
        status, response = box.conn.append(mbx.imap_quote(mbx.modified_utf7(folder)), '(\\Draft)',
                                           imaplib.Time2Internaldate(datetime.now(timezone.utc)), msg.as_bytes(policy=policy.SMTP))
        if status != 'OK':
            raise Stop('append_imap_incertain')
        uid = _append_uid(response)
        if not uid:   # APPENDUID absent : nouveau dépôt identifié par son marqueur unique, puis par son Message-ID, dans la MÊME génération
            _select_rw(box, folder, courante)
            found = _search_uids(box, 'UNDELETED', 'HEADER', 'X-AxiorHub-Compose', mbx.imap_quote(stamp))
            if not found and msg.get('Message-ID'):
                found = _search_uids(box, 'UNDELETED', 'HEADER', 'Message-ID', mbx.imap_quote(str(msg['Message-ID'])))
            uid = found[-1] if found else ''   # non identifié : erreur ci-dessous, le brouillon précédent reste en place
        if not uid:
            raise Stop('brouillon_enregistre_non_retrouve')
        saved = box.fetch(folder, uid)
        if str(saved.uidvalidity) != str(courante):
            raise Stop('introuvable')
        if str(saved.msg.get('X-AxiorHub-Compose', '')).strip() != stamp:
            raise Stop('brouillon_enregistre_non_conforme')
        previous = str(data.get('draft_uid') or '')
        if previous.isdigit() and previous != uid:   # brouillon précédent de cette même rédaction : retiré (il est remplacé)
            try:   # 5.6.100 (R04) : brouillon désigné par le navigateur contrôlé avant tout ; seuls ses en-têtes sont lus
                controlee = _garde(desk, acteur, box, cfg, folder, previous, validite)
                old = box.fetch(folder, previous, headers_only=True, **({'expected_validity': controlee} if controlee is not None else {}))
                reprise = str(data.get('reprise_uid') or '')   # 5.6.106 (lot R) : brouillon rouvert par « Modifier », remplacé quelle que soit son origine
                if str(old.msg.get('X-AxiorHub-Origine', '')).strip() == ORIGIN or (reprise.isdigit() and reprise == previous):
                    _select_rw(box, folder, validite)
                    box.conn.uid('STORE', previous, '+FLAGS.SILENT', '(\\Deleted)')
                    if 'UIDPLUS' in _caps(box):
                        box.conn.uid('EXPUNGE', previous)
            except (Stop, imaplib.IMAP4.error, OSError):
                pass
        desk.audit('webmail5628_brouillon', {'mode': str(data.get('mode') or 'nouveau'), 'owner': owner})
        return {'uid': uid, 'uidvalidity': saved.uidvalidity, 'message_id': saved.mid,   # 5.6.102 (lot K) : Message-ID relu, pour retrouver le brouillon
                'pieces': pieces, 'message': 'Enregistré dans Brouillons.'}
    finally:
        box.close()


# ------------------------------------------------------------------------------------------------ API
MAX_CORPS = 20_100_000   # 5.6.106 (lot R) : pièces jointes en base64 (borne du serveur waitress) ; la borne commune (260 ko) les refusait


def _corps(env, auth):
    """Corps JSON d'un brouillon ou d'un envoi : mêmes contrôles que ``web440.check_post`` (origine, jeton CSRF, type, objet JSON), borne
    propre aux pièces jointes (``MAX_CORPS``) ; les plafonds réels (10 fichiers, 15 Mo) restent ceux de ``build``."""
    import hmac
    import json
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    try:
        length = int(env.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        raise Stop('requete_trop_longue') from None
    if length < 0 or length > MAX_CORPS:
        raise Stop('requete_trop_longue')
    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
        raise Stop('json_requis')
    try:
        data = json.loads(env['wsgi.input'].read(length).decode('utf-8') or '{}')
    except (ValueError, UnicodeError):
        raise Stop('json_invalide') from None
    if not isinstance(data, dict):
        raise Stop('objet_json_requis')
    return data


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    route = name[len('m5628/'):].split('?', 1)[0]
    if method == 'GET':
        if route == 'modele':   # 5.6.100 (R04) : courriel cité contrôlé avant son corps (UIDVALIDITY affichée revalidée si fournie)
            return json_out(template(desk, str(args.get('mode') or 'nouveau'), args.get('uid'), args.get('dossier') or None,
                                     acteur=(owner, role), validity=args.get('uidvalidity') or None))
        if route == 'envoi':
            from . import web490
            return json_out(web490.webmail_status(desk))
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = _corps(env, auth) if route in ('brouillon', 'envoyer') else check_post(env, auth)   # 5.6.106 (lot R) : borne des pièces jointes
    if route == 'action':   # 5.6.100 (R04) : garde dans la session qui agit, UIDVALIDITY affichée revalidée avant STORE/MOVE
        return json_out(act(desk, str(data.get('action') or ''), data.get('uid'), data.get('dossier') or None, data.get('cible') or None, owner,
                            acteur=(owner, role), validity=data.get('uidvalidity') or None))
    if route == 'brouillon':
        return json_out(save(desk, data, owner, acteur=(owner, role)))
    if route in ('envoyer', 'envoyer/confirmer'):   # l'envoi reste dans la couche web d'envoi (aperçu puis confirmation)
        from . import web490
        from .acces_courriel56100 import garde_envoi
        if route == 'envoyer':
            return json_out(web490.webmail_send(desk, data, owner, acteur=(owner, role)))
        garde_envoi(desk, (owner, role), desk.c['mail'], web490.brouillon_du_jeton(desk, str(data.get('token') or '')))   # droits revus au moment de l'effet
        return json_out(web490.webmail_confirm(desk, str(data.get('token') or '')))
    raise Stop('route_inconnue')
