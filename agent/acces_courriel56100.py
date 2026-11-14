"""5.6.100 (lot A, R04, V100-01) : garde unique des courriels — projet local, message IMAP, brouillon IMAP, message ou projet Gmail.

Un avocat ne doit voir ni objet, ni extrait, ni corps, ni pièce, ni compteur, ni lien d'un courriel rattaché à un dossier réservé à un autre
compte. Ce module fait la porte commune des routes de la messagerie ; il ne crée aucun registre : les droits restent ceux de ``membres5650``
(``acces5697.masques``), les rattachements ceux du serveur (``messages5697.dossiers_par_cle``), complétés ici par :

- le dossier enregistré dans le projet de réponse (``manual_drafts.data.matter``) : un projet hérite des restrictions de son message, et le
  message de celles de son projet (une valeur « sans dossier » ne neutralise aucun rattachement réservé) ;
- pour un brouillon IMAP, l'origine établie côté serveur : liaison ``reply_drafts5687`` (brouillon déposé par l'agent), clé de l'agent, ou
  message désigné par ``In-Reply-To`` retrouvé **sur ses en-têtes** (recherche ``HEADER Message-ID``, jamais un corps lu). Un lien déclaré
  qui ne peut pas être vérifié rend la référence « incertaine » : refusée à un compte cloisonné, jamais lue comme « sans dossier » ;
- pour Gmail, la connexion autorisée de la personne et l'identifiant Gmail lus sur le serveur, plus le dossier du projet.

Contrat (feuille de route 5.6.100, lots A et B) :

- ``reference(desk, acteur, objet, box=None)`` → ``Ref`` (genre ``projet`` | ``imap`` | ``gmail``) ou ``Stop('introuvable')`` ;
- ``exiger(desk, acteur, ref)`` → rien, ``Stop('introuvable')`` (404 neutre : absent et interdit répondent pareil) ou
  ``Stop('controle_indisponible')`` (503 : registre ou base illisible ; jamais un accès élargi) ;
- ``filtrer(desk, acteur, refs)`` → seulement les éléments autorisés, à appeler AVANT compteurs, tri et pagination.

``acteur`` : ``(compte, rôle)`` comme ``acces5697`` ; ``None`` : acteur de la requête en cours (``membres5650.gate``), pour les pages
historiques rendues sans environnement. Les champs ``matter`` ou ``Message-ID`` envoyés par le navigateur ne donnent jamais de droit : seuls
les registres du serveur sont lus. Aucun journal ne contient d'objet, de corps ni de jeton.
"""
from collections import namedtuple
import re
import sqlite3

from .common import Stop

NEUTRE = 'introuvable'
PANNE = 'controle_indisponible'
CLE = re.compile(r'^[0-9a-f]{64}$')
PREFIXES = re.compile(r'^(?:projet|reponse5671|reponse|mail|courriel):([0-9a-f]{64})$')
GMAIL = re.compile(r'^gmail:([A-Za-z0-9_-]{1,80}):([A-Za-z0-9_-]{1,120})$')
ORIGINES_MAX = 5                      # candidats d'origine examinés par dossier de la messagerie (Message-ID dupliqué : tous contrôlés)

# genre ; cle (clé canonique du message ou du brouillon) ; compte, boite, uidvalidity, uid (IMAP, UIDVALIDITY confirmée par la session) ;
# gmail_connexion, gmail_id ; cles (clés dont les rattachements comptent : l'objet et ses origines) ; extra (dossiers lus hors des clés :
# projet Gmail) ; incertain (lien déclaré non vérifiable) ; dossiers (rattachements lus à la résolution, informatifs : ``exiger`` les relit).
Ref = namedtuple('Ref', 'genre cle compte boite uidvalidity uid gmail_connexion gmail_id cles extra incertain dossiers')


def _ref(genre, cle, compte='', boite='', uidvalidity='', uid='', gmail_connexion='', gmail_id='', cles=(), extra=(), incertain=False, dossiers=frozenset()):
    return Ref(genre, cle, compte, boite, str(uidvalidity or ''), str(uid or ''), gmail_connexion, gmail_id,
               tuple(dict.fromkeys((cle,) + tuple(cles))), frozenset(extra), bool(incertain), frozenset(dossiers))


# ------------------------------------------------------------------------------------------------ droits et rattachements
def _acteur(acteur):
    from . import acces5697 as acces
    if acteur is None:
        return acces.acteur_requete()
    try:
        return acces.acteur_de(acteur)
    except Stop:
        raise Stop(PANNE) from None          # acteur absent ou illisible : contrôle impossible, jamais « aucune restriction »


def masques(desk, acteur):
    """Dossiers masqués pour l'acteur ; registre illisible ou panne : ``Stop('controle_indisponible')``. Rôle inconnu : ``role_insuffisant``."""
    from . import acces5697 as acces
    if isinstance(acteur, (set, frozenset)):     # masques déjà établis par l'appelant (projection appelée avec ``hidden``)
        return frozenset(str(x) for x in acteur)
    try:
        if acteur is None:
            return frozenset(acces.masques_requete(desk))
        return frozenset(acces.masques(desk, _acteur(acteur)))
    except Stop as ex:
        if str(ex) in ('role_insuffisant', PANNE):
            raise
        raise Stop(PANNE) from None
    except Exception:   # noqa: BLE001 — base ou registre en panne : refus, jamais un périmètre élargi
        raise Stop(PANNE) from None


def dossiers(desk, cles):
    """{clé: frozenset(dossiers)} : registres du serveur (``messages5697``) et dossier du projet de réponse. Panne : ``controle_indisponible``."""
    from .messages5697 import dossiers_par_cle
    cles = list(dict.fromkeys(str(c) for c in cles if CLE.match(str(c or ''))))
    if not cles:
        return {}
    try:   # registres du serveur, dossier du projet de réponse compris (``messages5697._projets``, 5.6.100)
        out = {c: set(v) for c, v in dossiers_par_cle(desk, cles).items()}
    except Stop:
        raise Stop(PANNE) from None
    except Exception:   # noqa: BLE001 — base illisible : refus
        raise Stop(PANNE) from None
    return {c: frozenset(out.get(c, ())) for c in cles}


def _visible(rattachements, caches):
    from .acces5697 import visible
    return visible(rattachements, caches)


# ------------------------------------------------------------------------------------------------ résolution
def _existe(desk, cle):
    """Objet connu du serveur : rapport, projet, élément de travail ou rattachement (lecture locale seulement)."""
    from pathlib import Path
    if (Path(str(desk.c['state_dir'])) / 'reports' / (cle + '.json')).is_file():
        return True
    for sql in ('SELECT 1 FROM manual_drafts WHERE mail_key=?', 'SELECT 1 FROM work_items WHERE mail_key=?'):
        try:
            if desk.db.execute(sql, (cle,)).fetchone():
                return True
        except sqlite3.OperationalError:
            continue
    return bool(dossiers(desk, [cle]).get(cle))


def _projet_existe(desk, cle):
    try:
        return bool(desk.db.execute('SELECT 1 FROM manual_drafts WHERE mail_key=?', (cle,)).fetchone())
    except sqlite3.OperationalError:
        return False


def _origine_agent(desk, cle_agent):
    """Courriel d'origine d'un brouillon de l'agent : liaison ``reply_drafts5687``, sinon la clé elle-même si elle désigne un courriel connu."""
    if not CLE.match(str(cle_agent or '')):
        return ''
    from .reponses5671 import mail_of_draft
    lie = mail_of_draft(desk, cle_agent)
    if lie and CLE.match(str(lie)):
        return str(lie)
    return cle_agent if _existe(desk, cle_agent) else ''


def origine_brouillon(desk, box, cfg, boite, uid, validite):
    """(clés d'origine, incertain) d'un brouillon IMAP, établies sur ses EN-TÊTES seulement (``BODY.PEEK[HEADER]``), puis sur les en-têtes
    des candidats (recherche ``Message-ID``) : aucun corps n'est lu. ``incertain`` : un lien est déclaré mais aucun n'est vérifiable."""
    from . import mailbox as mbx
    try:
        tete = box.fetch(boite, uid, headers_only=True, expected_validity=validite)
    except Stop as ex:
        if str(ex) in ('uidvalidity_modifiee', 'message_imap_disparu', 'lecture_imap_echouee', 'date_imap_absente', 'date_imap_invalide'):
            raise Stop(NEUTRE) from None
        raise
    origines, declare, verifie = [], False, False
    agent = mbx.draft_key(tete.msg)
    if agent:
        declare = True
        cle = _origine_agent(desk, agent)
        if cle:
            origines.append(cle)
            verifie = True
    reponse = ''.join(mbx.MID.findall(str(tete.msg.get('In-Reply-To', '')))[:1])
    if reponse:
        declare = True
        trouves = candidats(box, cfg, reponse)
        origines += trouves
        verifie = verifie or bool(trouves)
    return origines, declare and not verifie


def candidats(box, cfg, message_id):
    """Clés des messages de la réception et des envoyés qui portent ce ``Message-ID`` (recherche sur en-têtes ; aucun FETCH)."""
    from . import mailbox as mbx
    from .messages5697 import cle_canonique
    compte, out = _compte(cfg), []
    for dossier in dict.fromkeys(x for x in (cfg.get('inbox'), cfg.get('sent')) if x):
        try:
            uids = box.search(dossier, 'UNDELETED', 'HEADER', 'Message-ID', mbx.imap_quote(message_id))
        except Stop:
            continue
        v = str(box.validity or '')
        out += [cle_canonique(compte, dossier, v, u) for u in sorted(uids, key=int)[-ORIGINES_MAX:]]
    return out


def cles_par_message_id(desk, cfg, message_id):
    """Clés locales d'un ``Message-ID`` (sans connexion IMAP) : état du collecteur (empreinte compte + Message-ID) et rattachements
    ``portfolio_mail_links``. Vide : origine non vérifiable localement."""
    from .common import digest
    compte, out = _compte(cfg), []
    formes = sorted({str(message_id).strip(), '<' + str(message_id).strip().strip('<>') + '>', str(message_id).strip().strip('<>')} - {'', '<>'})
    from pathlib import Path
    chemin = Path(str(desk.c['state_dir'])) / 'state.sqlite3'
    if chemin.is_file():   # lecture seule : aucune écriture (même de schéma) pendant un affichage
        try:
            db = sqlite3.connect('file:%s?mode=ro' % chemin.as_posix(), uri=True, timeout=5)
            try:
                for forme in formes:
                    out += [str(r[0]) for r in db.execute('SELECT key FROM messages WHERE mid=? OR draft_mid=?', (digest(compte + forme), forme))]
            finally:
                db.close()
        except sqlite3.Error:
            raise Stop(PANNE) from None
    for forme in formes:
        try:
            out += [str(r[0]) for r in desk.db.execute("SELECT mail_key FROM portfolio_mail_links WHERE message_id=? AND mail_key<>''", (forme,))]
        except sqlite3.OperationalError:
            break
    return [c for c in dict.fromkeys(out) if CLE.match(c)]


def filtrer_brouillons_caches(desk, acteur, cfg, validite, items):
    """Brouillons de la copie locale (``cockpit530:drafts``) que l'acteur peut voir, sans connexion IMAP : chaque brouillon résolu
    séparément (liaison de l'agent, Message-ID d'origine connu localement). Origine déclarée mais inconnue ici : masqué pour un compte
    cloisonné (périmètre non vérifiable)."""
    from . import mailbox as mbx
    from .messages5697 import cle_canonique
    items = list(items or [])
    if not items or not masques(desk, acteur):
        return items
    cfg = cfg or desk.c['mail']
    compte, boite, refs = _compte(cfg), str(cfg.get('drafts') or ''), []
    for it in items:
        origines, declare, verifie = [], False, False
        agent = str(it.get('agent_key') or '')
        if CLE.match(agent):
            declare = True
            cle = _origine_agent(desk, agent)
            if cle:
                origines.append(cle)
                verifie = True
        reponse = ''.join(mbx.MID.findall(str(it.get('in_reply_to') or ''))[:1]) or str(it.get('in_reply_to') or '').strip()
        if reponse:
            declare = True
            trouves = cles_par_message_id(desk, cfg, reponse)
            origines += trouves
            verifie = verifie or bool(trouves)
        refs.append(_ref('imap', cle_canonique(compte, boite, str(validite or ''), str(it.get('uid') or '')), cles=origines, incertain=declare and not verifie))
    gardes = {r.cle for r in filtrer(desk, acteur, refs)}
    return [it for it, r in zip(items, refs) if r.cle in gardes]


def filtrer_brouillons(desk, acteur, cfg, listing, box=None):
    """Liste de la boîte Brouillons (``drafts440.list_drafts``) réduite aux brouillons que l'acteur peut voir : chaque brouillon est résolu
    séparément (sa clé, sa liaison d'agent, le message désigné par ``In-Reply-To`` retrouvé sur ses en-têtes). Lien déclaré non vérifiable :
    masqué pour un compte cloisonné. Une seule session IMAP, ouverte seulement s'il faut rechercher une origine."""
    from . import mailbox as mbx
    from .messages5697 import cle_canonique
    caches = masques(desk, acteur)
    items = list((listing or {}).get('items') or [])
    if not caches or not items:
        return listing
    cfg = cfg or desk.c['mail']
    compte, boite, v = _compte(cfg), str(listing.get('folder') or cfg.get('drafts') or ''), str(listing.get('uidvalidity') or '')
    refs, vus, propre = [], {}, box is None
    try:
        for it in items:
            origines, declare, verifie = [], False, False
            agent = str(it.get('agent_key') or '')
            if CLE.match(agent):
                declare = True
                cle = _origine_agent(desk, agent)
                if cle:
                    origines.append(cle)
                    verifie = True
            reponse = ''.join(mbx.MID.findall(str(it.get('in_reply_to') or ''))[:1])
            if reponse:
                declare = True
                if reponse not in vus:
                    if box is None:
                        box = mbx.Mailbox(cfg)
                    vus[reponse] = candidats(box, cfg, reponse)
                origines += vus[reponse]
                verifie = verifie or bool(vus[reponse])
            uid = str(it.get('uid') or '')
            refs.append(_ref('imap', cle_canonique(compte, boite, v, uid), compte, boite, v, uid, cles=origines, incertain=declare and not verifie))
    finally:
        if propre and box is not None:
            box.close()
    gardes = {r.uid for r in filtrer(desk, acteur, refs)}
    return {**listing, 'items': [it for it in items if str(it.get('uid') or '') in gardes]}


def _compte(cfg):
    from .messages5697 import compte_de
    return compte_de(cfg)


def _imap(desk, caches, objet, box, cfg):
    from .messages5697 import cle_canonique
    if box is None:
        raise Stop(PANNE)                    # programmation : une référence IMAP se confirme dans la session qui lira le message
    cfg = cfg or desk.c['mail']
    boite = str(objet.get('boite') or objet.get('folder') or objet.get('dossier_imap') or cfg.get('inbox') or '')
    uid = str(objet.get('uid') or '')
    if not boite or not re.fullmatch(r'\d{1,12}', uid):
        raise Stop(NEUTRE)
    compte = _compte(cfg)
    if objet.get('compte') not in (None, '', compte):
        raise Stop(NEUTRE)                   # autre boîte : jamais lue avec la session de celle-ci
    try:
        actuelle = str(box.select(boite))
    except Stop as ex:
        if str(ex) in ('dossier_imap_inaccessible', 'uidvalidity_absent'):
            raise Stop(NEUTRE) from None
        raise
    attendue = str(objet.get('uidvalidity') or '')
    if attendue and attendue != actuelle:
        raise Stop(NEUTRE)                   # UIDVALIDITY affichée périmée : l'UID peut désigner un autre message
    cle = cle_canonique(compte, boite, actuelle, uid)
    if objet.get('cle') and str(objet['cle']) != cle:
        raise Stop(NEUTRE)
    origines, incertain = [], False
    if caches and boite == cfg.get('drafts'):
        origines, incertain = origine_brouillon(desk, box, cfg, boite, uid, actuelle)
    return _ref('imap', cle, compte, boite, actuelle, uid, cles=origines, incertain=incertain)


def _gmail(desk, acteur, objet):
    from . import gmail5697 as gm
    from .messages5697 import cle_gmail
    a = _acteur(acteur)
    gm.ensure_schema(desk)
    extra = set()
    if objet.get('projet'):
        try:
            p = gm.projet(desk, a.compte, str(objet['projet']))     # projet d'une autre personne : absent
        except Stop:
            raise Stop(NEUTRE) from None
        connexion, gid = str(p['connexion_id']), str(p['source_id'])
        if p.get('matter'):
            extra.add(str(p['matter']))
    else:
        connexion, gid = str(objet.get('connexion') or objet.get('connexion_id') or ''), str(objet.get('gmail_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', connexion) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', gid):
        raise Stop(NEUTRE)
    try:
        row = desk.db.execute('SELECT owner FROM google5697_connexions WHERE id=?', (connexion,)).fetchone()
    except sqlite3.Error:
        raise Stop(PANNE) from None
    if not row or str(row[0]) != a.compte:
        raise Stop(NEUTRE)                   # connexion d'une autre personne : jamais lue pour celle-ci
    if not objet.get('projet') and not desk.db.execute('SELECT 1 FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=?', (connexion, gid)).fetchone():
        raise Stop(NEUTRE)
    return _ref('gmail', cle_gmail(connexion, gid), 'gmail:' + connexion, 'Gmail', gmail_connexion=connexion, gmail_id=gid, extra=extra)


def reference(desk, acteur, objet, box=None, cfg=None):
    """Référence serveur de l'objet désigné, ou ``Stop('introuvable')``.

    ``objet`` : clé (``<64 hex>``, ``projet:<clé>``, ``reponse5671:<clé>``) ; ``{'genre': 'projet', 'cle'}`` ; ``{'genre': 'imap', 'boite',
    'uid', 'uidvalidity'?}`` avec ``box`` (session IMAP ouverte : l'UIDVALIDITY est confirmée par cette session ; un brouillon de la boîte
    Brouillons reçoit ses origines) ; ``gmail:<connexion>:<id>``, ``{'genre': 'gmail', 'connexion', 'gmail_id'}`` ou ``{'genre': 'gmail',
    'projet'}``. Un ``matter`` joint est ignoré."""
    if isinstance(objet, Ref):
        return objet
    caches = masques(desk, acteur)
    if isinstance(objet, str):
        trouve = PREFIXES.match(objet)
        if trouve or CLE.match(objet):
            objet = {'genre': 'projet', 'cle': trouve.group(1) if trouve else objet}
        elif GMAIL.match(objet):
            connexion, gid = GMAIL.match(objet).groups()
            objet = {'genre': 'gmail', 'connexion': connexion, 'gmail_id': gid}
        else:
            raise Stop(NEUTRE)
    if not isinstance(objet, dict):
        raise Stop(NEUTRE)
    genre = str(objet.get('genre') or ('imap' if objet.get('uid') else 'projet'))
    if genre == 'imap':
        ref = _imap(desk, caches, objet, box, cfg)
    elif genre == 'gmail':
        ref = _gmail(desk, acteur, objet)
    elif genre in ('projet', 'cle'):
        cle = str(objet.get('cle') or objet.get('mail_key') or objet.get('key') or '')
        if not CLE.match(cle):
            raise Stop(NEUTRE)
        if not _existe(desk, cle):
            raise Stop(NEUTRE)
        ref = _ref('projet' if _projet_existe(desk, cle) else 'imap', cle)
    else:
        raise Stop(NEUTRE)
    return ref._replace(dossiers=_rattachements(desk, ref))


def _rattachements(desk, ref):
    out = set(ref.extra)
    for valeurs in dossiers(desk, list(ref.cles)).values():
        out |= valeurs
    return frozenset(out)


# ------------------------------------------------------------------------------------------------ garde
def exiger(desk, acteur, ref, box=None, cfg=None):
    """Rien si l'acteur peut voir l'objet ; ``Stop('introuvable')`` sinon (même réponse qu'un objet absent) ; ``Stop('controle_indisponible')``
    si le contrôle ne peut pas être fait. Les rattachements sont RELUS à chaque appel (retrait d'accès ou reclassement entre liste et action)."""
    caches = masques(desk, acteur)
    if not isinstance(ref, Ref):
        ref = reference(desk, acteur, ref, box=box, cfg=cfg)
    if not caches:
        return ref
    if ref.incertain:
        raise Stop(NEUTRE)
    if not _visible(_rattachements(desk, ref), caches):
        raise Stop(NEUTRE)
    return ref


def _cle_de(element, cle=None):
    if isinstance(element, Ref):
        return element.cle
    if cle is not None:
        return str(cle(element) or '')
    if isinstance(element, str):
        trouve = PREFIXES.match(element)
        return trouve.group(1) if trouve else element
    if isinstance(element, dict):
        trouve = re.search(r'(?:^|:)([0-9a-f]{64})$', str(element.get('mail_key') or element.get('key') or element.get('cle') or element.get('object') or ''))
        return trouve.group(1) if trouve else ''
    return ''


def filtrer(desk, acteur, refs, cle=None, dossier=None):
    """Éléments autorisés, dans leur ordre : références (``Ref``), clés, ou éléments d'une file (``mail_key``/``key``/``object``). ``cle`` :
    fonction qui donne la clé d'un élément ; ``dossier`` : fonction qui donne le dossier déclaré par l'élément (compté en plus, jamais à la
    place des rattachements). À appeler AVANT compteurs, tri et pagination ; un élément sans clé de courriel ne passe que par son dossier."""
    refs = list(refs)
    caches = masques(desk, acteur)
    if not caches:
        return refs
    cles = {}
    for n, element in enumerate(refs):
        valeur = _cle_de(element, cle)
        if CLE.match(valeur):
            cles[n] = valeur
    toutes = set(cles.values())
    for element in refs:
        if isinstance(element, Ref):
            toutes.update(element.cles)
    lus = dossiers(desk, list(toutes))
    out = []
    for n, element in enumerate(refs):
        vus = set()
        if isinstance(element, Ref):
            if element.incertain:
                continue
            vus |= element.extra
            for c in element.cles:
                vus |= lus.get(c, frozenset())
        elif n in cles:
            vus |= lus.get(cles[n], frozenset())
        declare = dossier(element) if dossier is not None else (element.get('matter') if isinstance(element, dict) else '')
        if declare:
            vus.add(str(declare))
        if _visible(vus, caches):
            out.append(element)
    return out


def cles_masquees(desk, acteur, cles):
    """Clés de la liste que l'acteur ne peut pas voir (pour une clause SQL ``NOT IN`` posée avant comptes et LIMIT)."""
    caches = masques(desk, acteur)
    cles = [c for c in dict.fromkeys(str(x) for x in cles) if CLE.match(c)]
    if not caches or not cles:
        return frozenset()
    lus = dossiers(desk, cles)
    return frozenset(c for c in cles if not _visible(lus.get(c, ()), caches))


def exiger_contexte(desk, acteur, cle='', dossier=''):
    """Courriel et dossier envoyés par l'écran comme contexte d'une demande (assistant, réécriture) : ils ne donnent aucun droit ; l'un ou
    l'autre interdit → ``introuvable``. Une clé vide ou mal formée n'est pas un courriel."""
    caches = masques(desk, acteur)
    if dossier and str(dossier) in caches:
        raise Stop(NEUTRE)
    if CLE.match(str(cle or '')) and caches:
        exiger(desk, acteur, str(cle))


def garde_brouillon(desk, acteur, cfg=None):
    """Garde à passer aux fonctions de ``drafts440`` : appelée avec la session ouverte, AVANT toute lecture du corps ; rend l'UIDVALIDITY
    confirmée par cette session (``introuvable`` si elle diffère de celle affichée, si le brouillon ou son origine sont interdits)."""
    def garde(box, boite, uid, validite=''):
        return exiger(desk, acteur, {'genre': 'imap', 'boite': boite, 'uid': uid, 'uidvalidity': validite or ''}, box=box, cfg=cfg).uidvalidity
    garde.cloisonne = bool(masques(desk, acteur))   # compte cloisonné : brouillon disparu = interdit (même 404) ; sinon message historique
    return garde


def garde_envoi(desk, acteur, cfg, data):
    """Aperçu puis confirmation d'envoi d'un brouillon : le brouillon (et son origine) est recontrôlé à chaque clic, y compris au moment de
    l'effet. ``data`` : ``uid`` et ``uidvalidity`` du brouillon ; pour une confirmation, la couche web d'envoi (seule autorisée à lire ses
    jetons) les a résolus depuis le jeton. Compte sans restriction : aucune connexion supplémentaire."""
    if not masques(desk, acteur):
        return
    cfg = cfg or desk.c['mail']
    uid, validite = str(data.get('uid') or ''), str(data.get('uidvalidity') or '')
    if not re.fullmatch(r'\d{1,12}', uid):
        return                                # jeton inconnu ou UID mal formé : refusé par la couche d'envoi elle-même (aucune lecture)
    from . import mailbox as mbx
    box = mbx.Mailbox(cfg)
    try:
        exiger(desk, acteur, {'genre': 'imap', 'boite': cfg['drafts'], 'uid': uid, 'uidvalidity': validite}, box=box, cfg=cfg)
    finally:
        box.close()


CHAMPS_CLES = ('key', 'mail_key', 'source_key')


def garde_formulaire(desk, form, acteur=None):
    """Anciennes actions ``/action`` : chaque courriel désigné par le formulaire (clé ``key``/``mail_key``/``source_key``, ou proposition de
    date liée à un courriel) est contrôlé avant tout effet. Compte cloisonné : objet absent ou interdit → ``introuvable`` (404 neutre).
    Compte sans restriction : comportement inchangé (aucune lecture)."""
    caches = masques(desk, acteur)
    if not caches:
        return
    cles = [str(form.get(c) or '') for c in CHAMPS_CLES if CLE.match(str(form.get(c) or ''))]
    proposition = str(form.get('proposal') or '')
    if proposition:
        try:
            row = desk.db.execute('SELECT mail_key FROM deadline_proposals WHERE id=?', (proposition,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row and row[0]:
            cles.append(str(row[0]))
    for cle in dict.fromkeys(cles):
        exiger(desk, acteur, cle)


# ------------------------------------------------------------------------------------------------ primitives IMAP gardées
def _neutre_imap(ex):
    return str(ex) in ('uidvalidity_modifiee', 'message_imap_disparu')


def lire_imap(desk, acteur, box, cfg, boite, uid, validite=None, headers_only=False, cle_attendue=''):
    """Message IMAP lu APRÈS le garde : sélection, UIDVALIDITY confirmée, droits (origine d'un brouillon comprise), puis FETCH dans la même
    session avec l'UIDVALIDITY confirmée. Rend ``(mail, ref)``. Refus : ``introuvable`` avant toute commande de lecture du corps."""
    ref = exiger(desk, acteur, {'genre': 'imap', 'boite': boite, 'uid': uid, 'uidvalidity': validite or '', 'cle': cle_attendue or ''}, box=box, cfg=cfg)
    try:
        return box.fetch(boite, uid, headers_only=headers_only, expected_validity=ref.uidvalidity), ref
    except Stop as ex:
        if _neutre_imap(ex):
            raise Stop(NEUTRE) from None
        raise


def selectionner_ecriture(desk, acteur, box, cfg, boite, uid, validite=None):
    """Boîte sélectionnée en écriture pour STORE, MOVE, COPY ou EXPUNGE sur ``uid``, après le garde ; l'UIDVALIDITY de la sélection en
    écriture doit être celle qui a été contrôlée (sinon ``introuvable`` : l'UID pourrait désigner un autre message)."""
    from .drafts440 import _select
    ref = exiger(desk, acteur, {'genre': 'imap', 'boite': boite, 'uid': uid, 'uidvalidity': validite or ''}, box=box, cfg=cfg)
    if str(_select(box, boite, False)) != ref.uidvalidity:
        raise Stop(NEUTRE)
    return ref
