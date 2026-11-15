"""5.6.101 (lot V) : volet de relecture à droite d'Aujourd'hui — chaque proposition (courriel à relire, document, note de l'IA, échéance à
inscrire, audience, décision de la file) s'affiche à droite de la liste, ou en fenêtre flottante, avec la même barre de quatre actions.

Demande de l'avocat : voir directement le résultat proposé (le message à envoyer, le document, la note…) puis décider avec des boutons simples :
**Valider**, **Modifier manuellement**, **Instruction complémentaire**, **Écarter**. Ce module ne crée aucun service de décision : chaque
bouton appelle le service existant de la nature (décrit par ``barre`` et exécuté par ``v56101-volet.js`` sur la route existante), ou devient un
lien vers la page complète, ou reste désactivé avec une explication courte.

- Natures ajoutées à la table fermée de la fenêtre (``accueil_web5696.NATURES``) : ``gmail`` (projet ``g-…``, propre à son auteur), ``note_ia``
  (``n-…``), ``audience`` (registre ``audiences5688``) et ``file`` (tout autre élément de ``file5651``, par sa clé). Les trois natures
  historiques (réponse, échéance, document) gardent leur détail et reçoivent les mêmes compléments (``complements``).
- Droits : ceux de la page d'origine, relus à chaque lecture (``relecture56100.detail`` et ses gardes pour g-/n-/p-/d-, ``file5651.items``
  avec le compte et le rôle de la requête, dossiers réservés pour une audience). Absent et interdit : même 404 neutre ; contrôle en panne : 503.
- Lecture seule (R05) : aucune écriture pendant ``fragment`` et ``complements`` (les projections et schémas ne sont jamais créés ici).
- HTML construit et échappé ici ; le texte d'une pièce, d'une note ou d'un courriel n'est jamais interprété comme HTML (``escape``).
- Document Word ou PDF : quand le serveur de documents (ONLYOFFICE / Euro-Office, ``office440``) est relié, le volet le montre dans le cadre
  de l'éditeur en lecture, puis en édition sur « Modifier manuellement » (Word seulement). Le cadre est une page d'AxiorHub
  (``/documents/edit?volet=…``) qui garde la politique de sécurité de la page éditeur existante ; seul ``frame-ancestors`` passe de ``'none'``
  à ``'self'`` (affichage dans une page d'AxiorHub uniquement). La configuration de l'éditeur est servie par ``accueil/office`` après le
  même contrôle de droits que le document.
"""
from html import escape as e
from pathlib import PurePosixPath
import json
import re
from urllib.parse import urlencode, urlsplit, parse_qs

from .common import Stop

NATURES = ('gmail', 'note_ia', 'audience', 'file')
CLE_FILE = re.compile(r'^[0-9a-f]{20}$')                    # file5651._sha
CLE_AUDIENCE = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
CLE_DOCUMENT = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
CLE_RELECTURE = {'gmail': re.compile(r'^g-[A-Za-z0-9_-]{1,126}$'), 'note_ia': re.compile(r'^n-[A-Za-z0-9_-]{1,126}$')}
ACTIONS = ('valider', 'modifier', 'instruction', 'ecarter')   # barre identique pour toutes les natures, dans cet ordre
NON_ECARTABLES = frozenset(('echec_agent', 'securite_serveur', 'identite'))    # décisions propres (relancer, fusionner…) ou alerte de sécurité
MOTS_VALIDER = ('valider', 'approuver', 'accepter', 'retenir', 'garder', 'confirmer')
MOTS_ECARTER = ('écarter', 'ecarter', 'rejeter', 'refuser', 'ignorer')


def T(cle, **valeurs):
    from .i18n5631 import t
    return t('f96.volet.' + cle, **valeurs)


# ------------------------------------------------------------------------------------------------ barre de décision (descripteurs)
def commande(api, corps, **options):
    """Bouton actif : POST JSON sur une route existante (``/api440/<api>``) ; ``v56101-volet.js`` ajoute la clé d'idempotence (``cle``) et,
    selon ``texte`` / ``champ`` / ``motif`` / ``jusqu_a``, le texte modifié, la consigne, le motif ou l'échéance du report."""
    return {'etat': 'actif', 'api': api, 'corps': corps, **options}


def lien(href, explication=''):
    return {'etat': 'lien', 'href': href, 'explication': explication}


def indisponible(explication):
    return {'etat': 'indisponible', 'explication': explication}


def barre_vide(page):
    """Rien d'actionnable ici : chaque bouton dit pourquoi et la page complète reste à un clic."""
    return {'valider': lien(page, T('voir_page')), 'modifier': lien(page, T('voir_page')), 'instruction': indisponible(T('pas_de_reprise')),
            'ecarter': lien(page, T('voir_page')), 'page': page}


def _relecture_id(desk, ident):
    """L'élément est-il projeté dans « À relire » ? (lecture seule ; table absente : non)."""
    try:
        from .affichage5696 import table_presente
        if not table_presente(desk.db, 'file56100_items'):
            return ''
        from .relecture56100 import CABINET
        row = desk.db.execute("SELECT id FROM file56100_items WHERE cabinet=? AND id=?", (CABINET, ident)).fetchone()
    except Exception:   # noqa: BLE001 — projection illisible : pas de commande de relecture, la page reste ouverte
        return ''
    return str(row[0]) if row else ''


def detail_relecture(desk, owner, role, ident, prefix):
    """Détail du lecteur commun (mêmes gardes que Courriels › À relire) ; refus traduits pour la fenêtre (404 neutre, 503)."""
    from . import relecture56100 as rl
    try:
        d = rl.detail(desk, (owner, role), ident, prefix)
    except rl.Refus as ex:
        raise Stop('controle_indisponible' if ex.status == 503 else 'introuvable') from None
    return _signature_envoi(desk, owner, d)


def _signature_envoi(desk, owner, d):
    """5.6.102 (lot S) : signature qui partira avec une proposition ou un projet Gmail (le brouillon déjà déposé la contient déjà) ; lecture des
    seuls réglages (``signature56102.effective`` : aucun appel réseau, aucune écriture)."""
    if isinstance(d, dict) and d.get('type') in ('proposition', 'gmail'):
        try:
            from .signature56102 import effective
            d['signature_envoi'] = effective(desk, owner, d.get('compte_envoi') if d.get('type') == 'gmail' else None)
        except Exception:   # noqa: BLE001 — réglages illisibles : le courriel reste lisible, sans mention
            d['signature_envoi'] = None
    return d


def barre_relecture(desk, d, prefix, cle_courriel=''):
    """Quatre actions d'un élément « À relire » (proposition, Gmail, document, note de l'IA) : commandes du lecteur commun 5.6.100/5.6.101
    (révision attendue, clé d'idempotence, 409 sans effet), jamais un envoi ni un dépôt."""
    ident, revision, actions, kind = d['id'], str(d.get('revision') or ''), list(d.get('actions') or ()), d.get('type')
    base = 'm56100/relecture/' + ident
    page = (d.get('liens') or {}).get('pleine_page') or ''
    libelles = d.get('libelles') or {}
    b = {'page': page}
    if 'retenir' in actions:                       # « Retenir » : rien n'est envoyé ni déposé
        b['valider'] = commande(base + '/decision', {'action': 'retenir', 'revision': revision}, texte='corps', message=T('retenu'))
    elif 'valider' in actions:
        b['valider'] = commande(base + '/decision', {'action': 'valider', 'revision': revision}, libelle=libelles.get('valider') or '')
    else:
        b['valider'] = indisponible(T('deja_decide') if d.get('etat') != 'a_relire' else T('role_valider'))
    if 'enregistrer' in actions and d.get('editable'):
        b['modifier'] = {'etat': 'edition', 'api': base + '/enregistrer', 'corps': {'revision': revision}, 'texte': 'corps', 'valeur': str(d.get('corps') or '')}
    elif kind == 'document' and (d.get('liens') or {}).get('editeur'):
        b['modifier'] = lien(d['liens']['editeur'], T('modifier_editeur'))
    elif page:
        b['modifier'] = lien(page, T('voir_page'))
    else:
        b['modifier'] = indisponible(T('pas_de_modification'))
    b['instruction'] = indisponible(T('pas_de_reprise'))
    if 'corriger' in actions:
        b['instruction'] = commande(base + '/corriger', {'revision': revision}, champ='instruction', message=T('reprise'))
    elif kind == 'proposition' and cle_courriel:
        b['instruction'] = instruction_reponse(desk, cle_courriel)
    elif kind == 'gmail':
        b['instruction'] = instruction_gmail(d)
    if 'ecarter' in actions:
        b['ecarter'] = commande(base + '/decision', {'action': 'ecarter', 'revision': revision}, confirmer=T('confirmer_ecarter'))
    elif 'rejeter' in actions:
        b['ecarter'] = commande(base + '/decision', {'action': 'rejeter', 'revision': revision}, motif='motif', libelle=T('rejeter'))
    else:
        b['ecarter'] = indisponible(T('deja_decide') if d.get('etat') != 'a_relire' else T('role_valider'))
    if 'reporter' in actions:
        b['reporter'] = commande(base + '/decision', {'action': 'reporter', 'revision': revision}, jusqu_a=86400)
    return b


def instruction_reponse(desk, cle):
    """Réponse préparée : « Relancer avec une autre IA » (5.6.87, ``reponses5671.relancer``) avec la consigne ; aucune IA reliée : désactivé."""
    try:
        from .reponses5671 import connections
        options = [[k, v] for k, v in connections(desk)]
    except Exception:   # noqa: BLE001
        options = []
    if not options:
        return indisponible(T('pas_d_ia'))
    return commande('m5671/reponse/relancer', {'key': cle}, champ='instruction', choix={'nom': 'fournisseur', 'options': options}, message=T('reprise'))


def instruction_gmail(d):
    """Projet Gmail : « Préparer avec l'IA » (``gmail5697.demander_ia``) sur le même fil et le même dossier ; sans dossier : désactivé."""
    page = (d.get('liens') or {}).get('pleine_page') or ''
    fil = (parse_qs(urlsplit(page).query).get('fil') or [''])[0]
    dossier = str((d.get('dossier') or {}).get('id') or '')
    if not fil or not dossier:
        return indisponible(T('gmail_sans_dossier'))
    return commande('m5689/gmail/preparer_ia', {'fil': fil, 'dossier': dossier}, champ='instruction', message=T('reprise'))


# ------------------------------------------------------------------------------------------------ contenu produit (affiché en premier)
def _liste(valeurs):
    return ', '.join(str(v) for v in valeurs if str(v).strip())


COURRIELS = ('proposition', 'imap', 'gmail')
CADRE_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"   # même politique que le cadre des livrables
CADRE_CSS = ('body{font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:12px 14px;color:#161616;background:#fff;overflow-wrap:anywhere}'
             'p{margin:0 0 .75em}blockquote{border-left:3px solid #8d8d8d;margin:0 0 .75em;padding-left:.75em;color:#393939}'
             'pre{white-space:pre-wrap;font:inherit;margin:0}.v101-img{color:#525252;font-style:italic}.v101-alerte{color:#a2191f;font-style:italic}'
             'a{color:#0f62fe}@media (prefers-color-scheme:dark){body{color:#f4f4f4;background:#262626}blockquote{color:#c6c6c6}a{color:#78a9ff}.v101-alerte{color:#ff8389}}')


def paragraphes(texte):
    """Texte d'un courriel → HTML sobre : paragraphes (ligne vide), listes (« - », « * », « • »), citations (« > »), retours à la ligne. Tout
    est échappé : rien du texte n'est interprété comme balise."""
    blocs = re.split(r'\n\s*\n', str(texte or '').replace('\r\n', '\n').replace('\r', '\n').strip())
    out = []
    for bloc in blocs:
        lignes = [l for l in bloc.split('\n')]
        if not bloc.strip():
            continue
        if all(re.match(r'^\s*>', l) for l in lignes):
            out.append('<blockquote>%s</blockquote>' % paragraphes('\n'.join(re.sub(r'^\s*> ?', '', l) for l in lignes)))
        elif all(re.match(r'^\s*[-*•]\s+', l) for l in lignes):
            out.append('<ul>%s</ul>' % ''.join('<li>%s</li>' % e(re.sub(r'^\s*[-*•]\s+', '', l), quote=False) for l in lignes))
        else:
            out.append('<p>%s</p>' % '<br>'.join(e(l, quote=False) for l in lignes))
    return ''.join(out)


class _CourrielRecu(__import__('html.parser', fromlist=['HTMLParser']).HTMLParser):
    """HTML d'un courriel reçu, nettoyé pour le cadre : liste fermée de balises du webmail (``webmail5628.ALLOWED``), aucun attribut hors lien
    sûr (ouvert dans un nouvel onglet), aucune image ni ressource distante (libellé à la place), et le texte **masqué** (``display:none``, taille
    nulle… : même règle que ``mailbox.PlainHTML``) jamais rendu visible : il est retiré et signalé (``bouclier5659.hidden_marker``)."""

    VIDES = {'br', 'hr', 'img', 'meta', 'link', 'input', 'col', 'wbr', 'area', 'base', 'source', 'track', 'embed', 'param', 'image'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        from .webmail5628 import ALLOWED, DROP
        self.permis, self.retires = ALLOWED, DROP | {'head', 'title', 'noscript', 'video', 'audio', 'picture', 'canvas', 'frameset', 'frame', 'applet'}
        self.out, self.pile, self.retire, self.masque, self.caches = [], [], 0, 0, []

    def _cache(self, a):
        from .mailbox import _MASKED
        return 'hidden' in a or bool(_MASKED.search(a.get('style') or '')) or str(a.get('aria-hidden') or '').lower() == 'true'

    def handle_starttag(self, tag, attrs):
        a = {k: (v or '') for k, v in attrs}
        if tag in self.retires:
            self.retire += 1
            return
        if self.retire:
            return
        if tag in self.VIDES:
            if self.masque:
                return
            if tag in ('img', 'image'):
                self.out.append('<span class="v101-img">[%s]</span>' % e(T('image_distante')))
            elif tag in ('br', 'hr') and tag in self.permis:
                self.out.append('<%s>' % tag)
            return
        cache = self._cache(a)
        if cache:
            self.masque += 1
        emis = not self.masque and tag in self.permis
        if emis:
            extra = ''
            if tag == 'a' and re.match(r'(https?:|mailto:)', a.get('href', '').strip(), re.I):
                extra = ' href="%s" target="_blank" rel="noopener noreferrer"' % e(a['href'].strip(), quote=True)
            self.out.append('<%s%s>' % (tag, extra))
        self.pile.append((tag, cache, emis))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VIDES and self.pile and self.pile[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.retires:
            self.retire = max(0, self.retire - 1)
            return
        if self.retire or tag in self.VIDES:
            return
        if tag not in [p[0] for p in self.pile]:
            return
        while self.pile:
            nom, cache, emis = self.pile.pop()
            if emis:
                self.out.append('</%s>' % nom)
            if cache:
                self.masque = max(0, self.masque - 1)
            if nom == tag:
                break

    def handle_data(self, data):
        if self.retire:
            return
        if self.masque:
            self.caches.append(data)
            return
        self.out.append(e(data, quote=False))

    def resultat(self):
        while self.pile:
            nom, _cache, emis = self.pile.pop()
            if emis:
                self.out.append('</%s>' % nom)
        html = ''.join(self.out)
        from .bouclier5659 import hidden_marker
        marque = hidden_marker(''.join(self.caches))
        if marque.strip():
            html += '<p class="v101-alerte">%s</p>' % e(marque.strip())
        return html


def nettoyer_recu(html):
    p = _CourrielRecu()
    p.feed(str(html or '')[:400000])
    p.close()
    return p.resultat()


def cadre_html(corps, titre):
    """Document isolé du cadre (``<iframe sandbox="">`` côté page) : CSS interne, aucun script, aucun chargement extérieur possible."""
    return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'frame_self': True, 'csp': CADRE_CSP,
            'headers': [('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'private, no-store'), ('Referrer-Policy', 'no-referrer')],
            'body': '<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>%s</title><style>%s</style></head><body>%s</body></html>' % (e(titre), CADRE_CSS, corps)}


def _recu(desk, owner, role, d, prefix):
    """Message reçu auquel le projet répond : HTML d'origine nettoyé (sinon son texte), relu dans la messagerie après le contrôle d'accès, comme
    la sous-route « Message reçu » du lecteur commun ; une messagerie en panne est dite. Rend (html, texte)."""
    from . import relecture56100 as rl
    if d.get('type') == 'proposition':
        ligne = rl._ligne_projetee(desk, d['id']) or {}
        cle = str(ligne.get('mail_key') or '')
        rapport = rl._rapport(desk, cle) if cle else {}
        uid, boite = str(rapport.get('source_uid') or ''), str(rapport.get('source_mailbox') or '')
        if not uid or not boite:
            raise Stop('message_indisponible')
        from .mailbox import Mailbox
        try:
            box = Mailbox(desk.c['mail'])
            try:
                mail = box.fetch(boite, uid)
            finally:
                box.close()
        except Exception:   # noqa: BLE001 — messagerie injoignable : le projet reste lisible
            raise Stop('message_indisponible') from None
        if mail.key(rl._compte_mail(desk)) != cle:
            raise Stop('message_indisponible')
        texte = str(mail.text or '')[:60000]
        partie = mail.msg.get_body(preferencelist=('html',))
        html = nettoyer_recu(partie.get_content()) if partie is not None else paragraphes(texte)
        return html, texte
    try:
        sec = rl.section(desk, (owner, role), d['id'], str(d.get('revision') or ''), 'message')
    except rl.Refus as ex:
        raise Stop('controle_indisponible' if ex.status == 503 else 'introuvable') from None
    if sec.get('indisponible'):
        raise Stop('message_indisponible')
    texte = str(sec.get('texte') or '')
    return paragraphes(texte), texte


def courriel_cadre(desk, prefix, ident, quoi, format_, owner, role):
    """``GET accueil/courriel?id=&quoi=envoi|recu&format=html|texte`` : le courriel à envoyer (tel qu'il partira : le circuit de dépôt envoie le
    texte, présenté ici en paragraphes ; la police du cabinet, ``courrier5632.wrap``) ou le message reçu, dans un document isolé. Droits du
    lecteur commun avant toute lecture ; aucune écriture."""
    from . import relecture56100 as rl
    if not rl.IDENT.fullmatch(str(ident or '')) or quoi not in ('envoi', 'recu'):
        raise Stop('introuvable')
    d = detail_relecture(desk, owner, role, ident, prefix)
    if d.get('type') not in COURRIELS:
        raise Stop('introuvable')
    if quoi == 'recu':
        if d.get('type') == 'imap':
            raise Stop('introuvable')
        try:
            html, texte = _recu(desk, owner, role, d, prefix)
        except Stop as ex:
            if str(ex) != 'message_indisponible':
                raise
            return cadre_html('<p>%s</p>' % e(T('recu_indisponible')), T('recu'))
        titre = T('recu')
    else:
        texte = str(d.get('corps') or '')
        html = ''
        sig = d.get('signature_envoi')
        if sig and texte.strip():   # 5.6.102 (lot S) : composé par la fonction du dépôt (signature une seule fois, HTML si mise en forme)
            from .signature56102 import composer
            compose = composer(texte, sig)
            texte = compose['texte'].rstrip('\n')
            m = re.match(r'(?s)^<!doctype html><html><body>(.*)</body></html>$', compose['html'])
            html = m.group(1) if m else ''
        elif d.get('type') == 'imap' and d.get('html_envoi'):
            html = str(d['html_envoi'])   # brouillon déposé : sa version HTML (nettoyée à la lecture), telle qu'elle partira
        if not html:
            try:
                from .courrier5632 import wrap
                html = wrap(desk, paragraphes(texte))
            except Exception:   # noqa: BLE001 — réglage de police illisible : paragraphes seuls
                html = paragraphes(texte)
        titre = T('envoi')
    if format_ == 'texte':
        return cadre_html('<pre>%s</pre>' % e(texte, quote=False), titre)
    return cadre_html(html or '<p>%s</p>' % e(T('texte_vide')), titre)


def contenu_relecture(d, prefix=''):
    """Le résultat proposé lui-même : message à envoyer (objet, destinataires, corps en HTML dans un cadre isolé, bascule « Texte brut »), note de
    l'IA, texte d'une version. Texte échappé ; aucun identifiant, aucune révision ni empreinte."""
    dest = d.get('destinataires') or {}
    lignes = []
    courriel = d.get('type') in COURRIELS
    if courriel:
        for libelle, valeur in ((T('objet'), d.get('sujet')), (T('a'), _liste(dest.get('a') or ())), (T('cc'), _liste(dest.get('cc') or ()))):
            if valeur:
                lignes.append('<div><dt>%s</dt><dd>%s</dd></div>' % (e(libelle), e(str(valeur))))
    entetes = ('<dl class="v101-entetes">%s</dl>' % ''.join(lignes)) if lignes else ''
    corps = str(d.get('corps') or '')
    if courriel:
        base = prefix + '/api440/m5689/accueil/courriel?'
        envoi = base + urlencode({'id': d['id'], 'quoi': 'envoi', 'format': 'html'})
        bascule = ('<div class="v101-format" role="group" aria-label="%s"><button type="button" class="v101-lien" data-v101-format="html" aria-pressed="true">HTML</button>'
                   '<span aria-hidden="true">·</span><button type="button" class="v101-lien" data-v101-format="texte" aria-pressed="false">%s</button></div>') % (
            e(T('format'), quote=True), e(T('texte_brut')))
        texte = ('%s<iframe class="v101-cadre-courriel" sandbox="" referrerpolicy="no-referrer" title="%s" src="%s" data-v101-cadre="envoi"></iframe>' % (
            bascule, e(T('envoi'), quote=True), e(envoi, quote=True)))
        from .signature56102 import mention   # 5.6.102 (lot S) : d'où vient la signature ajoutée (mention discrète, hors du courriel)
        signature = mention(d.get('signature_envoi'))
        if signature:
            note = d['signature_envoi'].get('note') or ''
            texte += '<p class="v101-note" data-s56102-signature="%s">%s</p>' % (e(d['signature_envoi']['source'], quote=True),
                                                                                e(signature + ((' (' + note + ')') if note else '')))
        if d.get('type') != 'imap':
            recu = base + urlencode({'id': d['id'], 'quoi': 'recu', 'format': 'html'})
            texte += ('<details class="v101-recu" data-v101-recu><summary>%s</summary><iframe class="v101-cadre-courriel" sandbox="" referrerpolicy="no-referrer" '
                      'title="%s" data-src="%s" data-v101-cadre="recu"></iframe></details>') % (e(T('recu')), e(T('recu'), quote=True), e(recu, quote=True))
    else:
        texte = ('<div class="v101-texte" data-v101-texte tabindex="0" aria-label="%s">%s</div>' % (e(d.get('libelle_texte') or T('texte'), quote=True), e(corps))
                 if corps else '<p class="v101-vide">%s</p>' % e(T('texte_vide')))
    pieces = ''
    if d.get('pieces_jointes'):
        pieces = pieces_html(d['pieces_jointes'])
    elif d.get('pieces'):
        pieces = '<p class="v101-note">%s</p>' % e(T('pieces', n=int(d['pieces'])))
    motif = ('<p class="v101-note">%s</p>' % e(str(d['motif']))) if d.get('motif') and d.get('type') not in ('document',) else ''
    return '<article class="v101-contenu" data-v101-contenu data-type="%s">%s%s%s%s</article>' % (e(str(d.get('type') or ''), quote=True), entetes, texte, pieces, motif)


def pieces_html(pieces):
    """5.6.102 (lot K) : pièces jointes d'un projet de courriel (nom échappé ; lien vers l'éditeur pour un projet Word du dossier, chemin
    d'AxiorHub seulement)."""
    lignes = []
    for p in list(pieces or ())[:20]:
        nom, href = str((p or {}).get('nom') or ''), str((p or {}).get('href') or '')
        if not nom:
            continue
        if href.startswith('/') and not href.startswith('//'):
            lignes.append('<li><a href="%s">%s</a></li>' % (e(href, quote=True), e(nom)))
        else:
            lignes.append('<li>%s</li>' % e(nom))
    if not lignes:
        return ''
    return '<section class="v101-pieces"><p class="v101-note">%s</p><ul>%s</ul></section>' % (e(T('pieces', n=len(lignes))), ''.join(lignes))


def meta_relecture(d):
    """Une seule ligne de méta discrète : nature, état, version (jamais d'empreinte ni d'identifiant)."""
    morceaux = [str(d.get('type_libelle') or ''), str(d.get('etat_libelle') or '')]
    if (d.get('version') or {}).get('numero'):
        morceaux.append(T('version', n=int(d['version']['numero'])))
    return ' · '.join(m for m in morceaux if m)


# ------------------------------------------------------------------------------------------------ natures nouvelles
def fragment(env, desk, auth, prefix, nature, cle, owner, role):
    if nature in ('gmail', 'note_ia'):
        return _fragment_relecture(desk, prefix, nature, cle, owner, role)
    if nature == 'audience':
        return _fragment_audience(desk, prefix, cle, owner, role)
    if nature == 'file':
        return _fragment_file(desk, prefix, cle, owner, role)
    raise Stop('introuvable')


def _fragment_relecture(desk, prefix, nature, cle, owner, role):
    if not CLE_RELECTURE[nature].fullmatch(cle):
        raise Stop('introuvable')
    d = detail_relecture(desk, owner, role, cle, prefix)
    page = (d.get('liens') or {}).get('pleine_page') or (prefix + '/a-relire?' + urlencode({'item': cle}))   # 5.6.102 (lot N) : page « À relire »
    sources = _sources_note(desk, owner, role, d) if nature == 'note_ia' else ''
    ouvrir = d.get('ouvrir') or {}
    if ouvrir.get('href'):
        sources += '<p class="v101-liens"><a href="%s">%s</a></p>' % (e(ouvrir['href'], quote=True), e(ouvrir.get('libelle') or T('voir_page')))
    return {'titre': str(d.get('sujet') or '')[:200], 'corps': sources, 'page': page, 'apercu': contenu_relecture(d, prefix), 'meta': meta_relecture(d),
            'barre': barre_relecture(desk, d, prefix), 'dossier': str((d.get('dossier') or {}).get('libelle') or '')}


def _sources_note(desk, owner, role, d):
    """Sources de la note (passages de la nouvelle pièce et de la base, questions, dates relevées) : sous-route « Sources » du lecteur commun,
    même révision ; une source illisible est dite, jamais inventée."""
    from . import relecture56100 as rl
    try:
        sec = rl.section(desk, (owner, role), d['id'], str(d.get('revision') or ''), 'sources')
    except rl.Refus as ex:
        if ex.status == 503:
            raise Stop('controle_indisponible') from None
        return '<p class="v101-note">%s</p>' % e(T('sources_indisponibles'))
    items = ''.join('<li>%s</li>' % e(str(x.get('titre') or '')) for x in sec.get('sources') or [])
    limites = ''.join('<li>%s</li>' % e(str(x)) for x in sec.get('limites') or [])
    if not items and not limites:
        return ''
    return '<section class="v101-sources"><h3>%s</h3>%s%s</section>' % (e(T('sources')), ('<ul>%s</ul>' % items) if items else '',
                                                                         ('<ul class="v101-limites">%s</ul>' % limites) if limites else '')


def _masques(desk, owner, role):
    from .accueil_web5696 import _masques as masques_accueil
    caches = set(masques_accueil(desk, owner, role))
    try:
        from .acces5697 import masques
        caches |= set(masques(desk, (owner, role)))
    except Stop:
        raise
    except Exception:   # noqa: BLE001 — contrôle des droits illisible : rien n'est montré
        raise Stop('controle_indisponible') from None
    return caches


def _fragment_audience(desk, prefix, cle, owner, role):
    """Audience enregistrée : aperçu en lecture seule (la page Audiences reste le lieu des décisions)."""
    if not CLE_AUDIENCE.fullmatch(cle):
        raise Stop('introuvable')
    from .affichage5696 import table_presente
    if not table_presente(desk.db, 'audiences5688'):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT id,matter,title,type_audience,juridiction,chambre,rg,location,starts,all_day,statut,notes FROM audiences5688 WHERE id=?', (cle,)).fetchone()
    if not row or str(row['matter'] or '') in _masques(desk, owner, role):
        raise Stop('introuvable')                      # réservé = absent : même 404 neutre
    from .audiences5688 import STATUTS, TYPES
    from .accueil56100 import fuseau, _jour, _heure
    from datetime import datetime, timezone
    quand = str(row['starts'] or '')
    try:
        z = fuseau(desk)
        debut = datetime.fromisoformat(quand.replace('Z', '+00:00'))
        debut = (debut if debut.tzinfo else debut.replace(tzinfo=timezone.utc)).astimezone(z)
        quand = _jour(debut.date(), datetime.now(z).date()) + ('' if row['all_day'] else ' à ' + _heure(debut))
    except (ValueError, TypeError):
        pass
    champs = ((T('quand'), quand), (T('type_audience'), TYPES.get(row['type_audience'], '')), (T('juridiction'), ' — '.join(x for x in (row['juridiction'], row['chambre']) if x)),
              (T('lieu'), row['location']), (T('statut'), STATUTS.get(row['statut'], '')))
    notes = ('<div class="v101-texte">%s</div>' % e(str(row['notes']))) if row['notes'] else ''
    apercu = '<article class="v101-contenu" data-v101-contenu data-type="audience"><dl class="v101-entetes">%s</dl>%s</article>' % (
        ''.join('<div><dt>%s</dt><dd>%s</dd></div>' % (e(k), e(str(v))) for k, v in champs if v), notes)
    from .livrables5689 import _labels
    try:
        libelles, _m = _labels(desk)
    except Exception:   # noqa: BLE001
        libelles = {}
    page = prefix + '/audiences'
    b = barre_vide(page)
    b['valider'] = indisponible(T('audience_enregistree'))
    b['modifier'] = lien(page, T('voir_page'))
    b['ecarter'] = lien(page, T('voir_page'))
    return {'titre': str(row['title'] or TYPES.get(row['type_audience'], 'Audience'))[:200], 'corps': '', 'page': page, 'apercu': apercu,
            'meta': TYPES.get(row['type_audience'], ''), 'barre': b, 'dossier': libelles.get(row['matter'], '')}


def element_file(desk, prefix, cle, owner, role):
    """Élément de la file commune lu avec le compte et le rôle de la requête (droits filtrés par ``file5651.items`` avant tout)."""
    if not CLE_FILE.fullmatch(cle):
        raise Stop('introuvable')
    from . import file5651
    from .accueil_web5696 import _masques as masques_accueil
    try:
        tous = file5651.items(desk, prefix, None, '', masques_accueil(desk, owner, role), actor=owner, role=role)
    except Stop as ex:
        raise Stop('controle_indisponible' if str(ex).split(':')[0] in ('controle_indisponible', 'controle_acces_indisponible') else 'introuvable') from None
    it = next((i for i in tous if i.get('key') == cle), None)
    if it is None:
        raise Stop('introuvable')
    return it


def _choix_dict(it, mots):
    for c in it.get('choices') or ():
        if isinstance(c, dict) and str(c.get('label') or '').strip().lower().startswith(mots):
            return c
    return None


def barre_file(it, prefix):
    """Décision d'un élément de la file : service existant ``file5651.decide`` (révision affichée exigée ; une révision dépassée = 409 « version en
    conflit ») ou l'action propre de la source (formulaire 5.6.71) ; sinon la page de l'élément."""
    page = it.get('href') or prefix + '/aujourdhui?vue=file'
    route = 'm5651/file/decider' if it.get('matter') else 'm5652/file/decider'
    base = {'matter': it.get('matter') or '', 'key': it['key'], 'revision': str(it['revision'])}
    choix = it.get('choices') or ()
    b = {'page': page}
    propre = _choix_dict(it, MOTS_VALIDER)
    if 'approuver' in choix or 'relu' in choix:
        b['valider'] = commande(route, {**base, 'state': 'approuve'})
    elif propre:
        b['valider'] = commande(propre['api'], dict(propre.get('fields') or {}), libelle=str(propre.get('label') or ''), confirmer=propre.get('confirm') or '')
    else:
        b['valider'] = lien(page, T('decider_page'))
    b['modifier'] = lien(page, T('voir_page'))
    b['instruction'] = indisponible(T('pas_de_reprise'))
    propre = _choix_dict(it, MOTS_ECARTER)
    if propre:
        b['ecarter'] = commande(propre['api'], dict(propre.get('fields') or {}), libelle=str(propre.get('label') or ''), confirmer=propre.get('confirm') or T('confirmer_ecarter'))
    elif it.get('class') in NON_ECARTABLES:
        b['ecarter'] = lien(page, T('decider_page'))
    else:
        b['ecarter'] = commande(route, {**base, 'state': 'refuse'}, confirmer=T('confirmer_refus'))
    if 'reporter' in choix:
        b['reporter'] = commande(route, {**base, 'state': 'reporte'})
    return b


def _fragment_file(desk, prefix, cle, owner, role):
    from . import file5651
    from .file5657 import titre_lisible
    it = element_file(desk, prefix, cle, owner, role)
    classe = file5651.CLASSES.get(it.get('class'), 'À examiner')
    titre = titre_lisible(it.get('title') or '')
    proposition = ['<p class="v101-proposition">%s</p>' % e(titre)]
    if it.get('consequence'):
        proposition.append('<p class="v101-note">%s</p>' % e(str(it['consequence'])))
    if it.get('priority') == 'haute' and it.get('priority_reason'):
        proposition.append('<p class="v101-note">%s</p>' % e(T('prioritaire', raison=str(it['priority_reason']))))
    if it.get('corps'):        # 5.6.102 (lot K) : projet de courriel resté « À relire » : destinataire, texte (échappé) et pièces jointes
        if it.get('destinataire'):
            proposition.append('<dl class="v101-entetes"><div><dt>%s</dt><dd>%s</dd></div></dl>' % (e(T('a')), e(str(it['destinataire']))))
        proposition.append('<div class="v101-texte" data-v101-texte tabindex="0" aria-label="%s">%s</div>' % (e(T('texte'), quote=True), paragraphes(it['corps'])))
    if it.get('pieces_jointes'):
        proposition.append(pieces_html(it['pieces_jointes']))
    apercu = '<article class="v101-contenu" data-v101-contenu data-type="file">%s</article>' % ''.join(proposition)
    actions = '<ul class="q51-list v101-carte">%s</ul>' % file5651.carte_html(it, prefix)
    return {'titre': titre[:200], 'corps': actions, 'page': it.get('href') or prefix + '/aujourdhui?vue=file', 'apercu': apercu, 'meta': classe,
            'barre': barre_file(it, prefix), 'dossier': str(it.get('matter_name') or ''), 'replier': True}


# ------------------------------------------------------------------------------------------------ compléments des natures historiques
def complements(env, desk, auth, prefix, nature, cle, owner, role, out):
    """Champs ajoutés au détail historique (``html`` inchangé) : ``apercu`` (contenu produit), ``barre``, ``meta``, ``office``, ``replier``.
    Une source de commandes illisible ne masque jamais le détail : la barre renvoie alors à la page complète."""
    if nature in NATURES:
        return {k: out[k] for k in ('apercu', 'barre', 'meta', 'office', 'replier', 'dossier') if k in out}
    page = out.get('page') or prefix + '/aujourdhui'
    try:
        if nature == 'reponse':
            return _complement_reponse(desk, prefix, cle, owner, role, page)
        if nature == 'document':
            return _complement_document(env, desk, prefix, cle, owner, role, page)
        if nature == 'echeance':
            return _complement_echeance(desk, prefix, cle, page)
    except Stop as ex:
        if str(ex).split(':')[0] == 'controle_indisponible':
            raise
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('volet 5.6.101 : compléments %s', nature)
    return {'barre': barre_vide(page)}


def _complement_reponse(desk, prefix, cle, owner, role, page):
    from .accueil56100 import identifiant_lecteur
    ident = identifiant_lecteur(desk, 'reponse', cle)
    if not ident:
        return {'barre': barre_vide(page), 'replier': False}
    d = detail_relecture(desk, owner, role, ident, prefix)
    return {'apercu': contenu_relecture(d, prefix), 'meta': meta_relecture(d), 'barre': barre_relecture(desk, d, prefix, cle_courriel=cle), 'replier': True}


def _ligne_document(desk, env, cle):
    from . import livrables5689 as liv
    from .livrables_web5689 import _contexte
    owner, role, hidden, droits = _contexte(desk, env)
    try:
        return liv.charger(desk, cle, hidden, droits=droits), owner, role
    except Stop as ex:      # réservé, droit manquant ou absent : même 404 neutre (R04)
        raise Stop('controle_indisponible' if str(ex).split(':')[0] == 'controle_indisponible' else 'introuvable') from None


def office_document(desk, env, prefix, cle, row=None, owner='', role=''):
    """Cadre ONLYOFFICE / Euro-Office du volet pour un document Word ou PDF (et les autres formats ouvrables par l'éditeur) ; rien si le serveur
    de documents n'est pas relié ou si le fichier n'est pas encore vérifié (l'aperçu simplifié reste)."""
    from . import office440
    try:
        if not office440.settings(desk)['enabled']:
            return {'actif': False, 'raison': 'non_configure'}
    except Exception:   # noqa: BLE001
        return {'actif': False, 'raison': 'non_configure'}
    if row is None:
        row, owner, role = _ligne_document(desk, env, cle)
    path = row.get('path') or ''
    ext = office440.extension(path) if path else ''
    if not path or row.get('production_state') not in ('verified', 'published') or not office440.can_open(path):
        return {'actif': False, 'raison': 'format'}
    from . import livrables_apercu5689 as apercu
    from .fiche460 import doc_href
    vue = apercu.vue(desk, row, owner, role)
    edition = bool(vue['actions']['editor']) and ext in office440.EDITABLE
    out = {'actif': True, 'pdf': ext == 'pdf', 'format': ext,
           'vue': prefix + '/documents/edit?' + urlencode({'volet': cle, 'mode': 'view'}),
           'edition': (prefix + '/documents/edit?' + urlencode({'volet': cle, 'mode': 'edit'})) if edition else '',
           'editeur': (prefix + doc_href(path, row.get('matter_id') or '')) if vue['actions']['editor'] else ''}
    return out


def _complement_document(env, desk, prefix, cle, owner, role, page):
    from .relecture56101 import id_document
    row, owner_r, role_r = _ligne_document(desk, env, cle)
    office = office_document(desk, env, prefix, cle, row, owner_r, role_r)
    ident = _relecture_id(desk, id_document(cle))
    meta = T('version', n=int(row.get('version_number') or 1))
    if ident:
        d = detail_relecture(desk, owner, role, ident, prefix)
        b = barre_relecture(desk, d, prefix)
    else:                                              # document hors « À relire » (réponse déposée, facturation…) : relecture du registre
        base = 'm5689/livrables/%s/relecture' % cle
        b = {'page': page, 'valider': commande(base, {'decision': 'relu', 'version_id': row['version_id']}, libelle=T('marquer_relu')),
             'modifier': lien(page, T('voir_page')), 'instruction': indisponible(T('instruction_page')),
             'ecarter': commande(base, {'decision': 'ecarte', 'version_id': row['version_id']}, confirmer=T('confirmer_ecarter'))}
        try:                                           # « Modifier avec l'IA » de la page du document (propositions5689.demander_ia : version et empreinte exigées)
            from . import livrables_apercu5689 as apercu
            if apercu.vue(desk, row, owner_r, role_r)['actions']['ai_edit']:
                b['instruction'] = commande('m5689/livrables/%s/modifier-ia' % cle, {'version_id': row['version_id'], 'sha256': row['sha256']},
                                            champ='instruction', message=T('reprise'))
        except Exception:   # noqa: BLE001 — capacités illisibles : l'instruction reste dans la page du document
            pass
    if office.get('actif'):
        if office.get('pdf'):
            b['modifier'] = indisponible(T('pdf'))
        elif office.get('edition'):
            b['modifier'] = {'etat': 'office', 'href': office['edition'], 'editeur': office.get('editeur') or ''}
    elif b.get('modifier', {}).get('etat') != 'lien' and office.get('editeur'):
        b['modifier'] = lien(office['editeur'], T('modifier_editeur'))
    return {'barre': b, 'office': office, 'meta': meta, 'replier': False}


def _complement_echeance(desk, prefix, cle, page):
    """Échéance proposée : Valider = confirmer, Modifier = corriger la date (motif exigé), Écarter = fausse détection écartée (motif exigé) —
    routes existantes d'Échéances (``web450`` : version vue exigée, 409, clé d'idempotence, journal). Une échéance déjà confirmée : rien à valider."""
    from . import echeances450 as ech
    row = ech._row(desk, cle)
    if not row:
        raise Stop('introuvable')
    if row['status'] in ('terminee', 'annulee'):
        return {'barre': barre_vide(page)}
    version = row['revision'] if 'revision' in row.keys() else None
    corps = {'id': cle, 'version': version}
    b = {'page': page}
    if row['status'] in ('confirmee', 'manuel'):
        b['valider'] = indisponible(T('echeance_confirmee'))
    else:
        b['valider'] = commande('deadline/confirm', corps, confirmer=T('confirmer_echeance'), message=T('echeance_inscrite'))
    b['modifier'] = {'etat': 'echeance', 'api': 'deadline/correct', 'corps': corps, 'valeur': str(row['due'] or '')}
    b['instruction'] = indisponible(T('pas_de_reprise'))
    b['ecarter'] = commande('deadline/close', {**corps, 'outcome': 'annulee', 'ecart': True}, motif='reason', confirmer=T('confirmer_ecarter_echeance'))
    return {'barre': b, 'replier': False}


# ------------------------------------------------------------------------------------------------ cadre de l'éditeur (ONLYOFFICE / Euro-Office)
def config_office(env, desk, auth, prefix, cle, mode, owner, role):
    """``GET accueil/office?cle=&mode=`` : configuration de l'éditeur pour le cadre du volet, après le contrôle de droits du document (absent ou
    réservé : 404 neutre). Mode ``edit`` seulement si la page éditeur l'autorise à ce compte et pour un format modifiable ; sinon refus. Serveur
    non relié : refus sans aucune écriture. L'ouverture elle-même (session et journal d'audit de ``office440.open_document``) est celle de la
    page éditeur existante."""
    from . import office440
    if not CLE_DOCUMENT.fullmatch(cle):
        raise Stop('introuvable')
    row, owner_r, role_r = _ligne_document(desk, env, cle)
    info = office_document(desk, env, prefix, cle, row, owner_r, role_r)
    if not info.get('actif'):
        raise Stop('editeur_documents_non_configure' if info.get('raison') == 'non_configure' else 'format_non_modifiable')
    if mode == 'edit' and not info.get('edition'):
        raise Stop('role_insuffisant')
    from .web440 import allowed_path
    path = allowed_path(desk, row['path'])
    nom = owner if owner != 'cabinet' else desk.c.get('mail', {}).get('from_name', 'Cabinet')
    return office440.open_document(desk, auth, path, user_name=nom, mode='edit' if mode == 'edit' else 'view', user_id=owner, role=role,
                                   matter=row.get('matter_id') or '')


def cadre_page(desk, auth, prefix, env, args):
    """Page minimale affichée dans le cadre du volet : l'éditeur seul (chargeur ``v440-office.js``), même politique de sécurité que la page
    éditeur (origine du serveur de documents pour script, connexion et cadre), ``frame-ancestors 'self'`` : jamais hors d'AxiorHub."""
    from . import office440
    cle = str(args.get('volet') or '')[:80]
    mode = 'edit' if args.get('mode') == 'edit' else 'view'
    neutre = {'status': '404 Not Found', 'kind': 'text/html; charset=utf-8', 'frame_self': True,
              'csp': "default-src 'none'; style-src 'self'; frame-ancestors 'self'; base-uri 'none'",
              'headers': [('Cache-Control', 'private, no-store')],
              'body': '<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>%s</title></head><body><p>%s</p></body></html>' % (e(T('introuvable')), e(T('introuvable')))}
    try:
        if not CLE_DOCUMENT.fullmatch(cle):
            return neutre
        row, owner_r, role_r = _ligne_document(desk, env, cle)
        info = office_document(desk, env, prefix, cle, row, owner_r, role_r)
    except Stop:
        return neutre
    if not info.get('actif') or (mode == 'edit' and not info.get('edition')):
        return neutre
    cfg = office440.settings(desk)
    ds = urlsplit(cfg['server_url'])
    hote = ds.scheme + '://' + ds.netloc
    boot = json.dumps({'path': row['path'], 'mode': mode, 'server': cfg['server_url'], 'volet': True,
                       'config_api': 'm5689/accueil/office?' + urlencode({'cle': cle, 'mode': mode})}).replace('</', '<\\/')
    corps = ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
             '<meta name="axiorhub-prefix" content="%s"><title>%s</title><link rel="stylesheet" href="%s"></head>'
             '<body class="v101-cadre-corps"><p id="ax-docstate" class="v101-cadre-etat" role="status">%s</p>'
             '<div id="ax-office-wrap" class="v101-cadre-office"><div id="ax-office"></div></div>'
             '<div id="ax-office-help" class="v101-cadre-aide" hidden></div><button type="button" id="ax-diag" class="ax-btn ghost" hidden>%s</button>'
             '<script type="application/json" id="ax-office-boot">%s</script><script defer src="%s"></script></body></html>') % (
        e(prefix, quote=True), e(PurePosixPath(row['path']).name), e(prefix + '/static/v56101-volet.css', quote=True), e(T('office_chargement')),
        e(T('probleme')), boot, e(prefix + '/static/v440-office.js', quote=True))
    csp = ("default-src 'none'; script-src 'self' %s; connect-src 'self' %s; img-src 'self' data: %s; style-src 'self' 'unsafe-inline'; "
           "font-src 'self' data: %s; form-action 'self'; frame-src %s; frame-ancestors 'self'; base-uri 'none'") % (hote, hote, hote, hote, hote)
    return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'body': corps, 'csp': csp, 'frame_self': True,
            'headers': [('Cache-Control', 'private, no-store')]}


def script_html(prefix):
    """Script du volet (chargé avant ``v5696-accueil.js``, qui lui confie la barre de décision et le cadre de l'éditeur)."""
    return '<script defer src="%s"></script>' % e(prefix + '/static/v56101-volet.js', quote=True)
