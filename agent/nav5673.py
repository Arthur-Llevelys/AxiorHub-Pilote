"""5.6.73 : ce que l'agent voit de la page, ce qu'il doit faire valider, où il peut aller.

Adapté de Row-Bot (https://github.com/siddsachar/row-bot), modules ``browser/snapshot.py``, ``browser/policy.py`` et
``browser/addresses.py`` — Copyright 2026 Row-Bot Contributors, sous licence Apache 2.0 (voir ``LICENSES/Apache-2.0.txt`` et
``NOTICE-row-bot``). Modifications pour AxiorHub : plan de page construit depuis l'arbre d'accessibilité DevTools (au lieu de
l'instantané Playwright), vocabulaire français et juridique (déposer, signer, transmettre, notifier, valider…), liste de sites
autorisés du cabinet, aucun accès à ce poste ni au réseau local.

- **Plan de page** : rôles et noms accessibles des contrôles, avec des repères stables (« e12 ») tant que le contrôle reste le même ;
  les conteneurs sans nom disparaissent ; jamais la valeur d'un champ de mot de passe.
- **Conséquences** : payer, réserver avec un montant, envoyer / déposer / signer / transmettre, supprimer, modifier un compte,
  téléverser un fichier attendent la validation de l'avocat ; le reste s'exécute comme un clic ordinaire.
- **Adresses** : http(s) seulement, sans identifiants dans l'adresse ; ni ce poste ni le réseau local.
"""
from dataclasses import dataclass, field
import ipaddress
import re
import unicodedata
from urllib.parse import urlparse, urlsplit, urlunparse

# ------------------------------------------------------------------------------------------------------- adresses
_METADATA_HOSTS = frozenset({'metadata', 'metadata.aws.internal', 'metadata.google.internal', 'instance-data'})
# Les adresses de métadonnées des clouds sont dans des plages non publiques (lien local, CGNAT, réservées IETF) et donc refusées par
# la règle générale ci-dessous ; la liste explicite de Row-Bot n'est pas reprise (poste d'avocat, pas de machine virtuelle de cloud).


def _canonical(value):
    address = ipaddress.ip_address(str(value).strip('[]').split('%', 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def host_kind(host):
    """``public``, ``this_computer`` ou ``local_network`` d'après le nom seul (aucune résolution DNS)."""
    name = str(host or '').strip().strip('[]').rstrip('.').casefold()
    if not name:
        return 'local_network'
    if name == 'localhost' or name.endswith('.localhost'):
        return 'this_computer'
    try:
        address = _canonical(name)
    except ValueError:
        address = None
    if address is not None:
        if address.is_loopback:
            return 'this_computer'
        return 'public' if address.is_global and not address.is_link_local else 'local_network'
    if name in _METADATA_HOSTS or name.endswith(('.local', '.internal', '.lan', '.home.arpa')) or '.' not in name:
        return 'local_network'
    return 'public'


def site_of(url):
    """``infogreffe.fr`` pour ``https://www.infogreffe.fr/recherche``."""
    host = str(urlparse(str(url or '')).hostname or '').casefold()
    return host[4:] if host.startswith('www.') else host


def history_url(url):
    """Adresse sans paramètres ni ancre (historique et état)."""
    try:
        p = urlparse(str(url or ''))
        return urlunparse((p.scheme, p.netloc, p.path, '', '', ''))
    except ValueError:
        return '[adresse invalide]'


def navigation_refusal(url):
    """Motif de refus d'une adresse, ou '' si elle peut s'ouvrir (avant le contrôle de la liste des sites)."""
    try:
        p = urlsplit(str(url or ''))
    except ValueError:
        return 'adresse_invalide'
    if p.scheme not in ('http', 'https') or not p.hostname:
        return 'adresse_non_web'
    if p.username or p.password:
        return 'adresse_avec_identifiants'
    kind = host_kind(p.hostname)
    if kind != 'public':
        return 'adresse_du_poste_ou_du_reseau_local'
    return ''


def site_allowed(url, sites):
    """Vrai si le site de l'adresse (ou un domaine parent) figure dans la liste du cabinet."""
    site = site_of(url)
    for allowed in sites or ():
        allowed = str(allowed or '').strip().casefold()
        allowed = allowed[4:] if allowed.startswith('www.') else allowed
        if allowed and (site == allowed or site.endswith('.' + allowed)):
            return True
    return False


# ------------------------------------------------------------------------------------------------------- conséquences
@dataclass(frozen=True)
class PageFacts:
    """Ce que montre la page autour d'un contrôle (jamais ses valeurs)."""
    url: str = ''
    title: str = ''
    card_fields: bool = False
    shows_price: bool = False
    search_form: bool = False
    form_submit_label: str = ''


@dataclass(frozen=True)
class Consequence:
    kind: str
    site: str
    control: str

    def question(self):
        return {'pay': 'Payer sur %s ?', 'book': 'Réserver sur %s ?', 'send': 'Envoyer ou déposer sur %s ?', 'delete': 'Supprimer sur %s ?',
                'account': 'Modifier le compte sur %s ?', 'upload': 'Téléverser un fichier sur %s ?'}[self.kind] % self.site

    def description(self):
        what = {'pay': 'Cela peut engager une dépense.', 'book': 'Cela réserve, et la page affiche un montant.',
                'send': 'Cela envoie, dépose ou signe au nom du cabinet.', 'delete': 'Cela peut être irréversible.',
                'account': 'Cela modifie le compte du cabinet sur ce site.', 'upload': 'Le site reçoit une copie du fichier.'}[self.kind]
        return 'L’agent veut activer « %s » sur %s. %s' % (self.control, self.site, what)


def _words(*parts):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', ' '.join(str(p or '') for p in parts))).strip()


def _rx(pattern):
    return re.compile(r'(?<![\w-])(?:' + pattern + r')(?![\w-])', re.IGNORECASE)


_PAY = _rx(r"pay(?: now| securely)?|buy(?: now)?|purchase|place (?:your |my )?order|complete (?:your |my )?(?:order|purchase|payment)|"
           r"confirm (?:and|&) pay|confirm (?:order|payment|purchase)|subscribe(?: now)?|donate|add payment(?: method)?|save card|"
           r"payer(?: maintenant| en ligne| par carte)?|r[ée]gler(?: maintenant| la commande)?|acheter|commander|"
           r"valider (?:le |la |ma |mon )?(?:commande|paiement|transaction|r[èe]glement)|confirmer (?:le |la |ma |mon )?(?:commande|paiement|r[èe]glement)|proc[ée]der au (?:paiement|r[èe]glement)|"
           r"souscrire|s['’]abonner|ajouter (?:au )?panier|enregistrer (?:la |ma )?carte|faire un don")
_BOOK = _rx(r"book(?: now)?|reserve(?: now)?|confirm (?:my |the )?(?:booking|appointment|reservation)|"
            r"r[ée]server|prendre (?:un )?rendez-vous|confirmer (?:la |ma )?(?:r[ée]servation|rendez-vous)")
_SEND = _rx(r"send(?: message| email| now| reply)?|post(?: comment| reply)?|publish|reply|share|invite|"
            r"submit(?: claim| request| application| form| complaint)?|sign(?: now| document)?|"
            r"envoyer(?: le message| la demande| maintenant)?|transmettre|d[ée]poser(?: la demande| les conclusions| l['’]acte)?|"
            r"soumettre|publier|notifier|signer(?: le document| [ée]lectroniquement)?|r[ée]pondre|partager|inviter|"
            r"valider(?: l['’]envoi| le d[ée]p[ôo]t| la demande| la saisine| et envoyer| et d[ée]poser)?|"
            r"confirmer(?: l['’]envoi| le d[ée]p[ôo]t| la demande)?|terminer (?:et|la) (?:envoyer|d[ée]marche|demande)|saisir la juridiction")
_DELETE = _rx(r"delete(?: \w+)?|remove(?: \w+)?|erase|discard|unsubscribe|deactivate|terminate|close (?:my |this )?account|"
              r"supprimer(?: \w+)?|effacer|retirer|vider (?:la )?corbeille|d[ée]sinscrire|se d[ée]sabonner|r[ée]silier|"
              r"annuler (?:la |ma |le |mon |l['’])?(?:commande|r[ée]servation|rendez-vous|abonnement|demande|d[ée]p[ôo]t|requ[êe]te)|"
              r"cl[ôo]turer (?:le |mon )?compte|d[ée]sactiver")
_ACCOUNT = _rx(r"change (?:my |your )?(?:password|email|e-mail|username|phone(?: number)?|address|details|plan)|"
               r"update (?:my |your )?(?:password|email|account|details|profile|payment(?: details)?)|"
               r"modifier (?:le |mon |l['’])?(?:mot de passe|adresse|e-mail|courriel|identifiant|t[ée]l[ée]phone|coordonn[ée]es|profil)|"
               r"changer (?:le |mon |l['’])?(?:mot de passe|adresse|e-mail|identifiant)|d[ée]connecter (?:tous|partout)")
_SAVE = _rx(r"save(?: changes| settings)?|apply(?: changes)?|update|enregistrer(?: les modifications)?|appliquer|mettre [àa] jour")
_SETTINGS_PAGE = re.compile(r'account|settings|profile|preferences|security|privacy|compte|param[eè]tres|profil|pr[ée]f[ée]rences|s[ée]curit[ée]',
                            re.IGNORECASE)
_GENERIC_SUBMIT = _rx(r"confirm|continue|next|submit|done|ok|proceed|finish|complete|go|valider|confirmer|continuer|suivant|terminer|ok")
_SEARCH = _rx(r"search|find|filter|look ?up|rechercher|recherche|chercher|trouver|filtrer|consulter|afficher|voir")
_CLICKABLE = frozenset({'button', 'link', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'tab', 'option', 'checkbox', 'radio', 'switch',
                        'generic', ''})


def classify(action, control, role='button', page=None):
    """Conséquence d'une action (``click``, ``submit`` = Entrée dans un champ, ``select``), ou None si elle s'exécute sans validation."""
    page = page or PageFacts()
    site = site_of(page.url) or 'ce site'
    role = str(role or '').casefold()
    name = _words(control)[:160]
    if action == 'submit':
        if page.search_form or _SEARCH.search(name):
            return None
        name = _words(page.form_submit_label)[:160] or name
        role = 'button'
    elif action == 'select' or role not in _CLICKABLE:
        return None
    shown = name or 'le bouton'
    if role == 'link' and not (_PAY.search(name) or _DELETE.search(name)):
        return None   # un lien ouvre une page ; ce qu'elle propose d'envoyer passe par son propre bouton
    if _PAY.search(name) or (page.card_fields and _GENERIC_SUBMIT.search(name)):
        return Consequence('pay', site, shown)
    if _DELETE.search(name):
        return Consequence('delete', site, shown)
    if _ACCOUNT.search(name) or (_SAVE.search(name) and _SETTINGS_PAGE.search(_words(urlparse(page.url).path, page.title))):
        return Consequence('account', site, shown)
    if _BOOK.search(name):
        return Consequence('book', site, shown) if page.shows_price else None
    if _SEND.search(name) and not page.search_form and not _SEARCH.search(name):
        return Consequence('send', site, shown)
    return None


def upload_consequence(url, names):
    shown = ', '.join(list(names)[:3]) + (' et %d autre(s)' % (len(names) - 3) if len(names) > 3 else '')
    return Consequence('upload', site_of(url) or 'ce site', shown or 'un fichier')


# ------------------------------------------------------------------------------------------------------- plan de page
ACTIONABLE = frozenset({'button', 'link', 'textbox', 'searchbox', 'combobox', 'listbox', 'option', 'checkbox', 'radio', 'switch', 'slider',
                        'spinbutton', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'treeitem', 'gridcell', 'cell', 'columnheader',
                        'summary', 'disclosuretriangle'})
SKIP = frozenset({'generic', 'none', 'presentation', 'group', 'section', 'div', 'linebreak', 'inlinetextbox', 'ignored'})
TEXT_ROLES = frozenset({'statictext', 'text'})
MAX_CHARS = 12_000
STATES = ('checked', 'expanded', 'disabled', 'required', 'invalid', 'selected', 'focused')


@dataclass
class RefMap:
    """Repères stables d'un onglet : (rôle, nom, rang parmi les mêmes) → « eN », et eN → nœud DOM courant."""
    _by_key: dict = field(default_factory=dict)
    _targets: dict = field(default_factory=dict)
    _next: int = 0

    def reset(self):
        self._by_key.clear()
        self._targets.clear()

    def assign(self, nodes):
        """nodes : [(rôle, nom, backendDOMNodeId)] dans l'ordre de la page → {backend: eN}."""
        seen, current = {}, {}
        for ref in self._targets:
            key, _ = self._targets[ref]
            self._targets[ref] = (key, None)
        for role, name, backend in nodes:
            base = (role, name)
            index = seen.get(base, 0)
            seen[base] = index + 1
            key = (role, name, index)
            ours = self._by_key.get(key)
            if ours is None:
                self._next += 1
                ours = 'e%d' % self._next
                self._by_key[key] = ours
            self._targets[ours] = (key, backend)
            current[backend] = ours
        return current

    def backend(self, ref):
        entry = self._targets.get(str(ref or '').strip())
        return entry[1] if entry else None

    def describe(self, ref):
        entry = self._targets.get(str(ref or '').strip())
        return (entry[0][0], entry[0][1]) if entry else None


def _prop(node, name):
    for p in node.get('properties') or ():
        if p.get('name') == name:
            return (p.get('value') or {}).get('value')
    return None


def _val(node, key):
    return str(((node.get(key) or {}).get('value')) or '')


def outline(ax_nodes, refs, max_chars=MAX_CHARS):
    """(plan texte avec repères, nombre de contrôles) depuis ``Accessibility.getFullAXTree``."""
    by_id = {n.get('nodeId'): n for n in ax_nodes or ()}
    roots = [n for n in ax_nodes or () if not n.get('parentId') or n.get('parentId') not in by_id]
    lines, actionable = [], []

    def walk(node, depth, parent_name=''):
        role = _val(node, 'role').casefold()
        if role == 'inlinetextbox':   # fragments de rendu d'un texte déjà présent
            return
        name = re.sub(r'\s+', ' ', _val(node, 'name')).strip()[:200]
        ignored = bool(node.get('ignored'))
        shown = not ignored and role not in ('rootwebarea', 'webarea')
        backend = node.get('backendDOMNodeId')
        line = None
        if shown and role in TEXT_ROLES:
            if name and name != parent_name:   # le libellé d'un bouton ou d'un lien n'est pas répété
                line = ('text', name)
        elif shown and role in ACTIONABLE and backend is not None:
            actionable.append((role, name, backend))
            line = ('ref', node)
        elif shown and role in SKIP and not name:
            shown = False
        elif shown and (name or role in ('heading', 'list', 'table', 'form', 'dialog', 'alert', 'navigation', 'main')):
            line = ('node', node)
        else:
            shown = False
        if line is not None:
            lines.append((depth, line))
        for child in node.get('childIds') or ():
            if child in by_id:
                walk(by_id[child], depth + (1 if shown and line is not None else 0), name if line is not None and line[0] != 'text' else parent_name)

    for root in roots:
        walk(root, 0)
    ours = refs.assign(actionable)
    out = []
    for depth, (kind, item) in lines:
        pad = '  ' * min(depth, 12)
        if kind == 'text':
            out.append('%s- texte : %s' % (pad, item))
            continue
        role = _val(item, 'role').casefold()
        name = re.sub(r'\s+', ' ', _val(item, 'name')).strip()[:200]
        extra = []
        if role == 'heading' and _prop(item, 'level'):
            extra.append('niveau=%s' % _prop(item, 'level'))
        for st in STATES:
            value = _prop(item, st)
            if value not in (None, False, 'false'):
                extra.append(st if value is True or value == 'true' else '%s=%s' % (st, value))
        value = _val(item, 'value')
        if value and role in ('textbox', 'searchbox', 'combobox', 'spinbutton', 'slider') and not _prop(item, 'protected'):
            extra.append('valeur="%s"' % value[:80].replace('"', "'"))
        ref = (' [ref=%s]' % ours[item['backendDOMNodeId']]) if kind == 'ref' and item.get('backendDOMNodeId') in ours else ''
        out.append('%s- %s%s%s%s' % (pad, role, (' "%s"' % name.replace('"', "'")) if name else '', (' (' + ', '.join(extra) + ')') if extra else '', ref))
    text = '\n'.join(out)
    if len(text) > max_chars:
        cut = text.rfind('\n', 0, max_chars)
        shown = text[:cut if cut > 0 else max_chars]
        text = shown + '\n… %d ligne(s) non affichée(s) : faites défiler, ou « extraire » pour lire le texte de la page.' % (text.count('\n', len(shown)) + 1)
    return text, len(actionable)
