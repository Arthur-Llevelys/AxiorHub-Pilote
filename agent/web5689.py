"""5.6.89 : routes ``/api440/m5689/*``, réparties par domaine.

Chaque domaine a son module avec ``allowed(role, route)`` et ``api(env, desk, auth, prefix, name, args, method)``. Le compte et le rôle viennent de la requête authentifiée,
jamais du corps ; un domaine inconnu est refusé. Le contrôle d'une route n'exempte pas le service d'effet de refaire le sien."""
from importlib import import_module

DOMAINES = {
    'horaires': 'horaires_web5689',       # pauses programmées de l'IA
    'livrables': 'livrables_web5689',     # registre commun, visionneuse, téléchargement, export
    'documents': 'livrables_web5689',     # correctifs et vérification d'un document
    'politique': 'gouvernance_web5689',   # décision commune sur les données
    'budget': 'gouvernance_web5689',      # réservations et plafonds
    'cabinet': 'cabinet_web5689',         # profil, préparation
    'connexions': 'cabinet_web5689',      # preuves de capacité
    'dossiers': 'dossier_web5689',        # données structurées du dossier
    'assertions': 'dossier_web5689',
    'missions': 'dossier_web5689',        # consultation, contrats
    'playbooks': 'dossier_web5689',
    'accords': 'dossier_web5689',
    'contentieux': 'contentieux_web5689',  # matrice, pièces, délais, paquets
    'ui': 'ui_web5689',                   # contexte de l'interface et capacités IA par fonction
    'capacites': 'ui_web5689',
    'benchmarks': 'qualif_web5689',       # banc d'essai et état des fonctions
    'fonctions': 'qualif_web5689',
    'charge': 'charge_web5693',           # 5.6.93 : charge du serveur, fréquence de surveillance des documents
    'registre': 'registre_web5693',       # 5.6.93 : collecte des journaux, suivi des fichiers, doublons suggérés
}


def _module(route):
    nom = DOMAINES.get(route.split('?', 1)[0].split('/', 1)[0])
    return import_module('.' + nom, __package__) if nom else None


def allowed(role, route):
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    try:
        module = _module(route)
    except ImportError:
        return False
    return bool(module) and bool(module.allowed(role, route.split('?', 1)[0]))


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    route = name[len('m5689/'):]
    module = _module(route)
    if module is None:
        return json_out({'error': 'route_inconnue'}, '404 Not Found')
    return module.api(env, desk, auth, prefix, name, args, method)
