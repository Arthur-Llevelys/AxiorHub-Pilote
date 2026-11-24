"""5.6.109 (lot P) : contrôles de transmission autour de la pseudonymisation existante (``pseudo540``) — ni second pseudonymiseur, ni
second registre de pseudonymes, ni nouveau service réseau.

Ce module ne masque rien et ne détecte pas toutes les données personnelles : il encadre l'émission et le retour d'UN appel externe de
``model.Model`` (OpenAI, Mistral, OpenRouter, API Anthropic, abonnement ChatGPT), avec le pseudonymiseur de cet appel.

- ``emission`` : autour de ``Model._leaf`` ; les identités sont relues du registre pour cette émission (jamais du cache) et servent au
  texte, à la lecture des images (``vision56106``) et au contrôle de l'enveloppe (P01, P02) ; l'état réel de la ligne du journal des
  envois est noté à la sortie (préparé, émis avec son reçu, refusé avant l'envoi, restitution à vérifier).
- ``controler_schema`` : un schéma de réponse ne porte aucune identité (intitulé, description, clé, énumération, exemple) (P05).
- ``figer`` / ``figer_octets`` : l'enveloppe finale du fournisseur est sérialisée, relue depuis ces octets, contrôlée en entier (messages,
  consignes, schéma, noms, métadonnées, blocs d'image), puis ce sont ces octets qui partent ; le reçu ne garde que leur empreinte et leur
  taille (P05). Une image n'est admise que si elle vient du masquage local de CET appel, avec son reçu de préparation.
- ``restituer`` : un marqueur inconnu ou altéré dans la réponse arrête tout dépôt ; aucun nom n'est deviné (P04).

Un refus de confidentialité est un arrêt expliqué (``Stop`` et son seul code public, sans valeur d'origine) : jamais une erreur de
transport, jamais un résultat vide, et aucune autre IA — fournisseur de secours ou modèle local — ne prend le relais (``eligible_au_repli``).
"""
from contextlib import contextmanager
import hashlib
import json
import re

from .common import Stop

# Codes publics des refus de confidentialité : aucun n'est éligible à un repli (secours, hybride, routeur) ni à une escalade.
CODES_RETOUR = frozenset(('pseudo_marqueur_inconnu', 'pseudo_marqueur_altere'))
CODES = CODES_RETOUR | frozenset(('registre_identites_illisible', 'pseudo_identite_residuelle', 'pseudo_schema_avec_identite',
                                  'pseudo_canal_binaire_non_qualifie', 'pseudo_enveloppe_invalide'))
_SUITE = ' Votre demande est conservée ; aucune autre IA n’a pris le relais. Vous pouvez la traiter avec le modèle local.'
MESSAGES = {
    'registre_identites_illisible': 'Le registre des identités du cabinet (dossiers, parties, contacts) n’a pas pu être lu : rien n’a été envoyé à '
                                    'l’IA externe, car les noms connus n’auraient pas été masqués. Réessayez dans un instant ; si ce message revient, '
                                    'consultez État du logiciel.' + _SUITE,
    'pseudo_identite_residuelle': 'Contrôle avant l’envoi : une identité connue du cabinet figure encore dans la demande préparée pour l’IA externe, '
                                  'dans un champ que le masquage du texte ne couvre pas (nom technique, métadonnée, pièce jointe). Rien n’a été envoyé.'
                                  + _SUITE,
    'pseudo_schema_avec_identite': 'Contrôle avant l’envoi : le format de réponse demandé à l’IA externe contient une identité du dossier (intitulé, '
                                   'description, choix ou exemple). Rien n’a été envoyé. Les noms se placent dans le texte de la demande, où ils sont '
                                   'masqués, jamais dans le format de réponse.' + _SUITE,
    'pseudo_canal_binaire_non_qualifie': 'Contrôle avant l’envoi : la demande contient une image ou un fichier qui ne vient pas du masquage local des '
                                         'images. Rien n’a été envoyé. Joignez la pièce comme image du dossier (lecture locale, zones noircies) ou '
                                         'transmettez son texte seul.' + _SUITE,
    'pseudo_enveloppe_invalide': 'Contrôle avant l’envoi : la demande préparée pour l’IA externe n’a pas pu être figée telle qu’elle a été vérifiée. '
                                 'Rien n’a été envoyé. Relancez la demande.' + _SUITE,
    'pseudo_marqueur_inconnu': 'Restitution incomplète : la réponse de l’IA externe contient un marqueur qui ne correspond à aucune identité de cette '
                               'demande. Aucun nom n’a été deviné et rien n’a été déposé. La réponse reçue est gardée à vérifier dans le journal des '
                               'envois (Paramètres, Intelligence artificielle). Relancez la demande.' + _SUITE,
    'pseudo_marqueur_altere': 'Restitution incomplète : l’IA externe a recopié un marqueur sous une autre forme (casse, accent, espace). Aucun nom n’a '
                              'été deviné et rien n’a été déposé. La réponse reçue est gardée à vérifier dans le journal des envois (Paramètres, '
                              'Intelligence artificielle). Relancez la demande.' + _SUITE,
}
# Blocs qui portent autre chose que du texte : admis seulement s'ils sont l'image masquée de cet appel.
BINAIRES = frozenset(('image', 'image_url', 'input_image', 'input_file', 'input_audio', 'file', 'document', 'audio', 'video'))
CLES_BINAIRES = ('images', 'image_url', 'file_data', 'file_id', 'file_url', 'filename')
_TECHNIQUE = re.compile(r'[A-Za-z0-9_/.+\-]{1,40}')


def eligible_au_repli(code):
    """Faux pour un refus de confidentialité : changer de modèle ne corrige pas un défaut de masquage, et l'arrêt doit être vu."""
    return str(code).split(':')[0] not in CODES


def _pause(cfg):
    """Point de contrôle : pause de l'IA, pause du dossier, plage horaire, annulation — les mêmes règles que tout appel au modèle."""
    from .arret5643 import reason_to_stop
    motif = reason_to_stop((cfg or {}).get('state_dir'))
    if motif:
        raise Stop(motif)


def _sources(cfg):
    return (cfg or {}).get('pseudo') or {'state_dir': (cfg or {}).get('state_dir', '')}


def _pseudo(model):
    """Pseudonymiseur de l'appel. Transport appelé hors de ``Model._leaf`` (rien n'a été masqué) : le registre est relu, et une identité
    connue restée en clair sera refusée par le contrôle de l'enveloppe."""
    pseudo = getattr(model, '_pseudo56109', None)
    if pseudo is None:
        from .pseudo540 import Pseudonymizer
        pseudo = Pseudonymizer(_sources(getattr(model, 'cfg', None)))
    return pseudo


# ------------------------------------------------------------------------------------------------ une émission
class emission:
    """``with emission(model):`` autour de ``Model._leaf``. Fournisseur externe : un instantané des identités, relu du registre, pour tout
    l'appel ; à la sortie, l'état réel de la ligne du journal. Modèle local : rien (aucune lecture du registre, aucun contrôle ajouté)."""

    def __init__(self, model):
        self.model, self.jeton = model, None

    def __enter__(self):
        m = self.model
        for nom in ('_pseudo56109', '_journal56109', '_recu56109', '_tente56109', '_emis56109', '_retour56109'):
            m.__dict__.pop(nom, None)
        m.restitution56109 = None
        if getattr(m, 'provider_type', 'ollama') != 'ollama':
            from . import pseudo540
            self.jeton = pseudo540.INSTANTANE.set(pseudo540.instantane(_sources(getattr(m, 'cfg', None))))    # Stop si le registre est illisible
        return self

    def __exit__(self, kind, error, tb):
        m = self.model
        try:
            ident = getattr(m, '_journal56109', None)
            if ident:
                from .pseudo540 import noter_etat
                code = str(error).split(':')[0] if isinstance(error, Stop) else ''
                emis, tente = bool(getattr(m, '_emis56109', False)), bool(getattr(m, '_tente56109', False))
                if code in CODES_RETOUR:
                    etat = 'a_verifier'
                elif code in CODES:
                    etat = 'refuse'
                elif emis:
                    etat = 'emis'
                elif tente:
                    etat = 'echec'
                else:
                    etat = 'prepare'              # arrêt avant le transport (pause, plafond) : préparé, jamais émis
                if etat != 'prepare' or code:
                    noter_etat(m.cfg, ident, etat, getattr(m, '_recu56109', None) if (emis or tente) else None, code, getattr(m, '_retour56109', ''))
        finally:
            for nom in ('_pseudo56109', '_journal56109', '_recu56109', '_tente56109', '_emis56109', '_retour56109'):
                m.__dict__.pop(nom, None)
            if self.jeton is not None:
                from . import pseudo540
                pseudo540.INSTANTANE.reset(self.jeton)
        return False


# ------------------------------------------------------------------------------------------------ schéma (P05)
def _textes(valeur):
    """Toutes les chaînes d'une valeur, clés comprises."""
    if isinstance(valeur, str):
        yield valeur
    elif isinstance(valeur, dict):
        for k, v in valeur.items():
            yield str(k)
            yield from _textes(v)
    elif isinstance(valeur, (list, tuple)):
        for v in valeur:
            yield from _textes(v)


def controler_schema(model, pseudo, json_schema):
    """Un schéma de réponse est technique : clés et identifiants stables. Une identité du registre ou de la requête dans un intitulé, une
    description, une clé, une énumération ou un exemple est refusée avant toute construction de l'enveloppe (les trois fournisseurs placent
    le schéma à des endroits différents, tous après le masquage des messages)."""
    if json_schema is None:
        return
    for texte in _textes(json_schema):
        if pseudo.residuelle(texte, technique=True):
            raise Stop('pseudo_schema_avec_identite')


# ------------------------------------------------------------------------------------------------ enveloppe (P05)
class Corps:
    """Enveloppe contrôlée : les octets à émettre, l'objet relu depuis ces octets (plus aucune référence ailleurs), le reçu sans contenu."""
    __slots__ = ('octets', 'objet', 'recu')

    def __init__(self, octets, objet, recu):
        self.octets, self.objet, self.recu = octets, objet, recu


def _images_admises(model):
    """Images de CET appel préparées par le masquage local, reconnues à leur reçu (empreinte des octets masqués, version des règles) :
    ``masquee=True`` seul ne prouve rien."""
    from .pseudo540 import VERSION_DETECTEUR
    admises = set()
    for image in getattr(model, '_images56106', None) or ():
        recu = getattr(image, 'recu', None)
        if (getattr(image, 'masquee', False) and isinstance(recu, dict) and recu.get('detecteur') == VERSION_DETECTEUR
                and recu.get('empreinte') == hashlib.sha256(image.data).hexdigest()):
            admises.update((image.b64(), image.data_url()))
    return admises


def _est_binaire(obj):
    return (isinstance(obj.get('type'), str) and obj['type'] in BINAIRES) or any(k in obj for k in CLES_BINAIRES)


def _bloc_binaire(obj, admises):
    """Bloc d'image : sa charge doit être une image admise ; tout le reste (adresse, identifiant de fichier, nom de fichier, autre charge)
    est refusé plutôt que déclaré inspecté."""
    charges = 0
    for texte in _textes(obj):
        if texte in admises:
            charges += 1
        elif not _TECHNIQUE.fullmatch(texte):
            raise Stop('pseudo_canal_binaire_non_qualifie')
    if not charges:
        raise Stop('pseudo_canal_binaire_non_qualifie')


_DONNEES = re.compile(r'data:[\w.+\-]+/[\w.+\-]+[;,]')     # adresse « data: » d'un fichier incorporé, hors d'un bloc d'image admis


def _chaine(texte, pseudo, technique):
    if _DONNEES.match(texte):
        raise Stop('pseudo_canal_binaire_non_qualifie')
    if pseudo.residuelle(texte, technique=technique):
        raise Stop('pseudo_identite_residuelle')


def _contenu(valeur, pseudo, admises):
    """Contenu d'un message : texte déjà masqué, ou blocs (texte masqué, image admise)."""
    if isinstance(valeur, str):
        _chaine(valeur, pseudo, False)
    elif isinstance(valeur, list):
        for bloc in valeur:
            if isinstance(bloc, dict) and _est_binaire(bloc):
                _bloc_binaire(bloc, admises)
            elif isinstance(bloc, dict):
                for k, v in bloc.items():
                    _chaine(str(k), pseudo, True)
                    if k == 'text' and isinstance(v, str):
                        _chaine(v, pseudo, False)
                    else:
                        _technique(v, pseudo, admises)
            else:
                _contenu(bloc, pseudo, admises)
    else:
        _technique(valeur, pseudo, admises)


def _technique(valeur, pseudo, admises):
    """Tout ce qui n'est pas le texte d'un message : jamais passé par le masquage, donc contrôlé aussi contre les identités de la requête."""
    if isinstance(valeur, str):
        _chaine(valeur, pseudo, True)
    elif isinstance(valeur, dict):
        if _est_binaire(valeur):
            _bloc_binaire(valeur, admises)
            return
        for k, v in valeur.items():
            _chaine(str(k), pseudo, True)
            _technique(v, pseudo, admises)
    elif isinstance(valeur, list):
        for v in valeur:
            _technique(v, pseudo, admises)


def _enveloppe(objet, pseudo, admises):
    if not isinstance(objet, dict):
        raise Stop('pseudo_enveloppe_invalide')
    for cle, valeur in objet.items():
        _chaine(str(cle), pseudo, True)
        if cle in ('system', 'instructions'):
            _contenu(valeur, pseudo, admises)
        elif cle in ('messages', 'input') and isinstance(valeur, list):
            for message in valeur:
                if not isinstance(message, dict):
                    _contenu(message, pseudo, admises)
                    continue
                for k, v in message.items():
                    _chaine(str(k), pseudo, True)
                    if k == 'content':
                        _contenu(v, pseudo, admises)
                    else:
                        _technique(v, pseudo, admises)     # « name », métadonnées d'un message : jamais masqués
        else:
            _technique(valeur, pseudo, admises)


def _controler(model, octets, ascii):
    cfg = getattr(model, 'cfg', None) or {}
    try:
        objet = json.loads(octets)
        fidele = json.dumps(objet, ensure_ascii=ascii, allow_nan=False).encode() == octets
    except (TypeError, ValueError, UnicodeError):
        raise Stop('pseudo_enveloppe_invalide') from None
    if not fidele:        # clés en double, valeur non reproductible : ce qui partirait ne serait pas ce qui a été contrôlé
        raise Stop('pseudo_enveloppe_invalide')
    _enveloppe(objet, _pseudo(model), _images_admises(model))
    _pause(cfg)
    recu = {'sha256': hashlib.sha256(octets).hexdigest(), 'octets': len(octets)}
    model._recu56109 = recu
    return Corps(octets, objet, recu)


def figer(model, corps):
    """Enveloppe finale d'un fournisseur (après le schéma, les images et tous ses champs) → ``Corps``. La sérialisation est celle du
    transport (``common.HTTP.json``) : l'objet remis au transport est relu depuis ces octets, donc les octets émis sont exactement ceux
    qui ont été contrôlés ; modifier ``corps`` ensuite n'a plus aucun effet."""
    _pause(getattr(model, 'cfg', None))
    try:
        octets = json.dumps(corps, allow_nan=False).encode()
    except (TypeError, ValueError):
        raise Stop('pseudo_enveloppe_invalide') from None
    return _controler(model, octets, True)


def figer_octets(model, octets):
    """Canal ChatGPT : le corps est déjà en octets (``chatgpt5662.respond``, une fois par essai). Rend ces mêmes octets, contrôlés."""
    _pause(getattr(model, 'cfg', None))
    corps = _controler(model, bytes(octets), False)
    model._tente56109 = True
    return corps.octets


def emettre(model, corps):
    """Rend l'objet à remettre au transport : celui relu depuis les octets contrôlés, et lui seul. L'appel du transport reste écrit dans
    ``model.py`` (``interruptible(lambda:self.http.json('POST', chemin, emettre(self, corps)), self.cfg)``) : chaque envoi y reste visible,
    interruptible (pause relue et émission marquée juste avant le réseau) et précédé de ``preparer_emission``. Note que l'envoi est tenté ;
    « émis » est noté quand la réponse revient (``restituer``)."""
    model._tente56109 = True
    return corps.objet


# ------------------------------------------------------------------------------------------------ retour (P04)
def restituer(model, pseudo, contenu, json_mode=False):
    """Rétablit localement les marqueurs exacts de cette requête. Un marqueur inconnu ou altéré : arrêt, sans deviner ; la réponse reçue
    (encore pseudonymisée) est gardée « à vérifier » au journal des envois, rien n'est rendu à l'appelant, donc rien n'est déposé."""
    model._emis56109 = True
    controle = getattr(pseudo, 'controle_retour', None)     # absent d'une doublure d'essai à l'ancienne interface (apply_messages, restore)
    bilan = controle(contenu) if controle is not None else {'inconnus': [], 'alteres': []}
    if bilan['inconnus'] or bilan['alteres']:
        model._retour56109 = contenu if isinstance(contenu, str) else ''
        model.restitution56109 = {'etat': 'a_verifier', 'inconnus': bilan['inconnus'], 'alteres': bilan['alteres']}
        raise Stop('pseudo_marqueur_inconnu' if bilan['inconnus'] else 'pseudo_marqueur_altere')
    return pseudo.restore(contenu, json_mode=json_mode)


# ------------------------------------------------------------------------------------------------ information de l'utilisateur
TITRE = 'Ce qui est masqué, ce qui ne l’est pas'
MASQUE = ('Avant tout envoi à une IA externe, les noms connus du cabinet (clients, parties et contacts des dossiers, parties lues dans les pièces, '
          'références et intitulés de dossier), les courriels, téléphones, IBAN, numéros et adresses reconnus sont remplacés par des marqueurs, '
          'puis rétablis sur le serveur dans la réponse. Une partie ajoutée à un dossier est masquée dès l’envoi suivant.')
CONTROLES = ('La demande préparée est contrôlée en entier juste avant de partir (texte, format de réponse, champs techniques, images) : si une '
             'identité connue y figure encore, rien n’est envoyé. Si la réponse contient un marqueur inconnu ou modifié, rien n’est déposé et '
             'elle reste à vérifier. Dans les deux cas, aucune autre IA ne prend le relais.')
ETATS_TITRE = 'États du journal des envois'
ETATS_AIDE = {'prepare': 'La demande a été préparée mais n’est pas partie (pause, plafond, arrêt).',
              'emis': 'La demande est partie ; l’empreinte et la taille du corps exact sont gardées, sans autre contenu que le texte préparé.',
              'refuse': 'Le contrôle avant l’envoi a refusé la demande : rien n’est parti.',
              'a_verifier': 'La réponse reçue contient un marqueur inconnu ou modifié : elle est gardée telle que reçue, rien n’a été déposé.',
              'echec': 'L’envoi a été tenté sans réponse exploitable : la transmission n’est ni prouvée ni exclue.'}


def information_html():
    """Paramètres › Intelligence artificielle : ce qui est réellement masqué, les contrôles, les limites, les états du journal."""
    from html import escape as e
    from .pseudo540 import ETATS, LIMITES
    return ('<section class="ax-card" id="p56109-information"><h2>%s</h2><p>%s</p><p>%s</p><h3>%s</h3><ul>%s</ul><h3>%s</h3><dl>%s</dl></section>' % (
        e(TITRE), e(MASQUE), e(CONTROLES), e(LIMITES_TITRE), ''.join('<li>%s</li>' % e(x) for x in LIMITES), e(ETATS_TITRE),
        ''.join('<dt>%s</dt><dd>%s</dd>' % (e(ETATS[k]), e(v)) for k, v in ETATS_AIDE.items())))


LIMITES_TITRE = 'Ce que le masquage ne reconnaît pas'


def traduire(texte):
    """Texte d'une réponse JSON hors du champ « message » (note et limites de l'aperçu) dans la langue de l'écran ; le français tel quel."""
    try:
        from .ecrans5632 import dictionary, phrase
        from .i18n5631 import current
        langue = current()
        if langue in ('fr', '', None):
            return texte
        return phrase(texte, dictionary(langue)) or texte
    except Exception:   # noqa: BLE001 — une traduction ne doit jamais empêcher l'affichage
        return texte
