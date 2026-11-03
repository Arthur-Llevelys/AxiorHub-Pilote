"""5.6.89 : drapeaux de la nouvelle interface (audit 5.6.88, § 22). Réglage du cabinet, distinct pour chaque partie.

- ``theme`` : présentation Carbon. Éteint : les feuilles d'origine reprennent, aucune route ni service ne change.
- ``accueil`` : nouvelle page Aujourd'hui. Éteint : l'ancienne page revient, sur les mêmes données.
- ``actions_ia`` : actions IA contextuelles (``ui_ai5689``, ``capacites5689``). Allumé : l'assistant existant reçoit le contexte de la page et de l'objet, et l'icône IA s'affiche. Éteint : l'assistant reste tel qu'avant, aucun service ne change.
- Le registre agrégé des livrables n'a pas d'interrupteur : Aujourd'hui, Produire et Dossier le lisent déjà, y compris sur l'ancienne page. Il figure dans ``etat`` pour être dit.

Rétablir l'ancienne interface n'efface rien : décisions, relectures et versions vivent dans leurs tables, qu'aucun drapeau ne touche (OP04). Une lecture en échec rend les valeurs par
défaut : la page s'affiche toujours.
"""
from html import escape as e
import re

CLE = 'ui5689:drapeaux'
DEFAUT = {'theme': True, 'accueil': True, 'actions_ia': True}
LIBELLES = {'theme': ('Présentation Carbon', 'Thèmes clair et sombre, icônes et composants communs. Désactivée, l’ancienne présentation revient ; tous les services restent disponibles.'),
            'accueil': ('Nouvelle page Aujourd’hui', 'Travail préparé d’abord, routines compactes, journée en colonne. Désactivée, l’ancienne page revient avec les mêmes décisions et les mêmes documents.'),
            'actions_ia': ('Actions IA contextuelles', 'L’assistant reprend la page, le dossier et l’objet affichés, et propose les actions de cette page. Il prépare ; envoyer, signer, déposer, facturer et payer restent à vous. Désactivées, l’assistant reste disponible, sans ces actions.')}
DISPONIBLES = ('theme', 'accueil', 'actions_ia')


def lire(desk):
    """Drapeaux du cabinet : valeurs enregistrées sur les valeurs par défaut ; une mise à jour sans réglage reçoit donc thème et accueil."""
    try:
        saved = desk.settings(CLE, None)
    except Exception:   # noqa: BLE001 — base occupée ou absente : valeurs par défaut, la page s'affiche
        saved = None
    saved = saved if isinstance(saved, dict) else {}
    return {k: bool(saved.get(k, v)) for k, v in DEFAUT.items()}


def enregistrer(desk, data):
    """Cases absentes = faux pour les drapeaux réglables. Seul un administrateur arrive ici (``ADMIN_API`` et contrôle de la route)."""
    courant = lire(desk)
    courant.update({k: bool(data.get(k)) for k in DISPONIBLES})
    desk.setting(CLE, courant)
    return {'message': 'Réglages de l’interface enregistrés.', 'drapeaux': courant, 'reload': True}


def de_config(cfg):
    """Lecture hors bureau ouvert (gabarit commun) : base en lecture seule, comme l'épinglage du menu."""
    from .shell501 import _Desk53
    return lire(_Desk53(cfg or {}))


def etat(desk):
    return {**lire(desk), 'registre': True}


def marquer(html, drapeaux):
    """Pose ``data-carbon`` sur ``<body>`` : tout le thème est porté par ce seul attribut, sans autre changement du HTML ; ``<html lang>`` reste tel quel."""
    if not drapeaux.get('theme'):
        return html
    return re.sub(r'<body(?=[\s>])', '<body data-carbon="1"', html, count=1)


def reglages_html(desk, peut_modifier=True):
    """Rubrique Paramètres › Interface : des interrupteurs réels, l'état du registre et les anciennes vues, rangées ici plutôt que sur l'accueil."""
    from .ui_components5689 import case, formulaire
    valeurs = lire(desk)
    cases = ''.join(case(k, LIBELLES[k][0], valeurs[k], aide=LIBELLES[k][1], desactive=not peut_modifier) for k in DISPONIBLES)
    bouton = '<button class="ax-btn" type="submit">Enregistrer</button>' if peut_modifier else '<p class="ax-muted">Seul un administrateur modifie ces réglages.</p>'
    return ('<section class="ax-card ui89-reglages" id="interface5689" aria-labelledby="interface5689-t"><h2 id="interface5689-t">Interface</h2>'
            '<p class="ax-muted">Revenir à l’ancienne interface ne supprime aucune décision, relecture ni version de document.</p>' +
            formulaire('m5689/ui/drapeaux', cases + bouton, recharge=True) +
            '<p class="ax-muted">Registre commun des documents préparés : toujours actif. Il alimente Aujourd’hui, Produire et Dossier.</p></section>')


def anciennes_vues_html(prefix):
    """Anciennes vues : adresses conservées, accès rangé dans les réglages avancés (§ 14), sans concurrence avec l'accueil."""
    vues = (('/aujourdhui?vue=essentiel', 'Vue « essentiel du jour »'), ('/aujourdhui?vue=cockpit', 'Ancien cockpit'), ('/aujourdhui?vue=detail', 'Vue détaillée'), ('/accueil', 'Ancien tableau de bord'))
    return ('<details class="ui89-anciennes"><summary>Anciennes vues</summary><ul>%s</ul></details>' % ''.join(
        '<li><a href="%s">%s</a></li>' % (e(prefix + url, quote=True), e(label)) for url, label in vues))
