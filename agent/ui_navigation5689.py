"""5.6.89 : navigation commune (audit 5.6.88, § 14) : retour contextuel, icônes du menu, détails connus du navigateur.

Le lien d'ouverture d'un détail transporte ``return_to`` : l'adresse de la liste quittée. Le serveur la revalide à chaque affichage ; une valeur refusée donne le repli pertinent de la page,
jamais une erreur. Le navigateur ne fournit que le chemin et les paramètres déjà visibles dans l'adresse : ni jeton, ni contenu. Défilement et focus restent dans ``sessionStorage``
(``v5689-navigation.js``), jamais dans l'adresse. ``history.back`` n'est pas employé : l'historique peut être vide ou mener hors de l'application.
"""
from html import escape as e
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit

from .ui_components5689 import icone

TAILLE_MAX = 600
# Chemins jamais visés par un retour : ils n'affichent pas une page de travail, ou produisent un effet.
EXCLUS = re.compile(r'^/(?:api|api440|static|live|deconnexion|connexion|logout|login|office|integration|pwa|healthz|readyz|action|dictation|assistant/attachment|templates/upload|hearing/upload|extensions/upload|nextcloud-session|poste-session)(?:/|$)')
PARAMS_INTERDITS = re.compile(r'^(?:token|jeton|csrf|csrf_token|secret|password|mot_de_passe|passe|auth|authorization|session|sid|code|otp|signature|access_token|refresh_token|api_key|cle_api)$', re.I)
OBLIQUES = '\\／＼⧵∕⁄﹨'
ICONES_MENU = {'/aujourdhui': 'accueil', '/courriels': 'courriel', '/messagerie': 'discussion', '/dossiers': 'dossier', '/planning': 'agenda', '/talk': 'video', '/production': 'document_taches', '/parametres': 'reglages'}
# Détail -> (liste de repli, libellé). Un paramètre requis distingue le détail de la liste quand les deux partagent la même adresse.
DETAILS = (('/mail', '', '/courriels', 'Retour aux courriels'), ('/dossier', '', '/dossiers', 'Retour aux dossiers'), ('/matter', '', '/dossiers', 'Retour aux dossiers'), ('/fiche', '', '/dossiers', 'Retour aux dossiers'),
           ('/chronologie', '', '/dossiers', 'Retour aux dossiers'), ('/livrables/', '', '/livrables', 'Retour aux documents'), ('/documents/edit', '', '/documents', 'Retour aux documents'),
           ('/facturation', 'document', '/facturation', 'Retour à la facturation'), ('/missions', 'id', '/missions', 'Retour aux missions'), ('/cessions', 'id', '/cessions', 'Retour aux cessions'),
           ('/corporate', 'operation', '/corporate', 'Retour au secrétariat juridique'), ('/audiences', 'id', '/audiences', 'Retour aux audiences'), ('/projets', 'id', '/projets', 'Retour aux projets'),
           ('/recherche', 'document', '/recherche', 'Retour à la recherche'), ('/contentieux', '', '/dossiers', 'Retour aux dossiers'), ('/operation', 'id', '/facturation', 'Retour à la facturation'),
           ('/courriels', 'fil', '/courriels?vue=assistante', 'Retour à la liste'))   # 5.6.96 (I03) : fil ouvert dans la vue « À traiter avec le Pilote »
SOURCES = {'/aujourdhui': 'Retour à Aujourd’hui', '/courriels': 'Retour aux courriels', '/dossiers': 'Retour aux dossiers', '/dossier': 'Retour au dossier', '/matter': 'Retour au dossier', '/livrables': 'Retour aux documents',
           '/production': 'Retour à Produire', '/projets': 'Retour aux projets', '/studio': 'Retour à Produire', '/documents': 'Retour aux documents', '/facturation': 'Retour à la facturation', '/planning': 'Retour à l’agenda',
           '/missions': 'Retour aux missions', '/recherche': 'Retour à la recherche', '/echeances': 'Retour aux échéances', '/parametres': 'Retour aux paramètres', '/cessions': 'Retour aux cessions', '/corporate': 'Retour au secrétariat juridique', '/contentieux': 'Retour au contentieux'}
PROPRE_BOUTON = ('/livrables/', '/contentieux')    # ces pages gardent leur bouton dans le corps (même attribut, même validation)


def _douteux(texte):
    """Forme lue par un navigateur ou un proxy : obliques cachées, retour à la ligne, schéma, segments parents. Contrôlée sur chaque niveau de décodage."""
    normal = unicodedata.normalize('NFKC', texte)
    if any(c in texte or c in normal for c in OBLIQUES):
        return True
    if re.search(r'[\x00-\x1f\x7f\x80-\x9f  ​-‏﻿]', normal):
        return True
    chemin = normal.split('?', 1)[0].split('#', 1)[0]
    if '//' in chemin or any(s in ('..', '.') for s in chemin.split('/')):
        return True
    return bool(re.match(r'\s*[a-z][a-z0-9+.-]*:', normal, re.I)) or bool(re.search(r'(?:javascript|data|vbscript)\s*:', normal, re.I))


def valider_retour(valeur, prefix=''):
    """Adresse de retour acceptée, ou ``''`` : chemin local sous le préfixe de l'application, une seule lecture possible. Refus : autre origine, ``//``, schéma, caractère de contrôle, variante encodée."""
    if not isinstance(valeur, str) or not valeur or len(valeur) > TAILLE_MAX or not valeur.isascii():
        return ''
    if not valeur.startswith((prefix or '') + '/') or valeur.startswith('//'):
        return ''
    texte = valeur
    for _ in range(4):                  # un encodage double (%252f) reste une variante ambiguë
        if _douteux(texte):
            return ''
        suivant = unquote(texte)
        if suivant == texte:
            break
        texte = suivant
    else:
        return ''                       # encodage en cascade : refus, sans chercher à le comprendre
    parts = urlsplit(valeur)
    if parts.scheme or parts.netloc or parts.fragment and len(parts.fragment) > 80:
        return ''
    chemin = unquote(parts.path)
    if prefix:
        if not chemin.startswith(prefix + '/'):
            return ''
        chemin = chemin[len(prefix):]
    if EXCLUS.match(chemin):
        return ''
    if any(PARAMS_INTERDITS.match(k) for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return ''
    return valeur


def _detail(path, args):
    for route, param, liste, libelle in DETAILS:
        if (path.startswith(route) if route.endswith('/') else path == route) and (not param or args.get(param)):
            return liste, libelle
    return None


def libelle_source(cible, prefix=''):
    chemin = unquote(urlsplit(cible).path)
    if prefix and chemin.startswith(prefix):
        chemin = chemin[len(prefix):]
    return SOURCES.get(chemin) or SOURCES.get('/' + chemin.strip('/').split('/', 1)[0]) or 'Retour à la page précédente'


def retour(prefix, path, args):
    """(adresse, libellé) du bouton Retour, ou ``None`` hors détail sans contexte. Un ``return_to`` refusé vaut comme absent : repli de la page, lien direct compris."""
    args = args or {}
    cible = valider_retour(args.get('return_to') or args.get('retour') or '', prefix)
    if cible:
        return cible, libelle_source(cible, prefix)
    connu = _detail(path, args)
    return (prefix + connu[0], connu[1]) if connu else None


def retour_html(prefix, path, args):
    if path.startswith(PROPRE_BOUTON):
        return ''
    trouve = retour(prefix, path, args)
    if not trouve:
        return ''
    return '<a class="ui89-retour" href="%s" data-ui89-retour>%s<span>%s</span></a>' % (e(trouve[0], quote=True), icone('retour', taille=16), e(trouve[1]))


def details_json():
    """Chemins dont les liens reçoivent ``return_to`` au clic. Donnée lue par ``v5689-navigation.js`` ; aucune valeur de l'utilisateur."""
    return ','.join('%s|%s' % (route, param) for route, param, *_ in DETAILS)


def icone_menu(route, repli):
    """Icône SVG d'une entrée de menu ; un autre chemin garde son signe d'origine, décoratif."""
    nom = ICONES_MENU.get(route)
    return icone(nom) if nom else e(repli)
