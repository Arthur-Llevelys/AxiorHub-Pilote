"""5.6.57 (parcours 1, A09) : une seule file « À décider / À relire » sur Aujourd'hui, Dossier et Produire.

Avant : trois listes demandaient parfois la même décision — la file unique 5.6.51, le centre « À décider » (décisions attendues par
l'agent : missions, demandes de documents, classements, règles, engagements) et le cadre « À relire » d'Aujourd'hui (brouillons déposés
dans la messagerie, documents produits). Ce module fournit les deux dernières sources à la file unique (``file5651``), avec les mêmes
règles : un objet = une clé stable ; une révision qui change fait réapparaître l'objet ; « Reporter » et « Relu » sont enregistrés une
seule fois et valent pour toutes les vues. Les actions détaillées (valider une version, répondre aux champs manquants…) restent sur la
page de l'objet (« Ouvrir »). Rien n'est envoyé, déposé ni signé depuis la file.
"""
import hashlib
from urllib.parse import quote

CLASSES = {'decision_agent': 'Décision attendue par l’agent', 'projet': 'Projet à relire'}


def _rev(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:16]


def _hidden():
    try:
        from .membres5650 import viewer_hidden
        return viewer_hidden() or frozenset()
    except Exception:   # noqa: BLE001
        return frozenset()


def decision_items(desk, prefix='', actor='cabinet'):
    from . import aujourdhui5614
    out = []
    hidden = _hidden()
    for c in aujourdhui5614.items(desk, actor, prefix):
        mid = str(c.get('matter') or '')
        if mid in hidden or (c.get('kind') == 'mission5614' and c.get('decision_kind') == 'validation'):
            continue                                           # version de mission à valider : déjà dans la file (« Projet à relire »)
        out.append({'class': 'decision_agent', 'object': 'decision:' + c['key'], 'revision': _rev(c.get('state'), c.get('title'), c.get('question')),
                    'matter': mid, 'matter_name': c.get('matter_label') or 'Cabinet', 'title': c.get('title') or 'Décision attendue',
                    # « Ouvrir » : la carte d'action de cette décision (réponses, choix du dossier, adoption d'une règle…) sur Aujourd'hui
                    'consequence': c.get('consequence') or c.get('question') or '', 'href': prefix + '/aujourdhui#decision=' + quote(c['key'], safe=''),
                    'choices': ('ouvrir', 'reporter'), 'priority': 'haute' if c.get('urgent') else 'normale',
                    'priority_reason': 'Échec à reprendre' if c.get('urgent') else ''})
    return out


def review_items(desk, prefix=''):
    """Brouillons déposés dans la messagerie et documents produits (lecture du cache : aucun accès réseau pendant l'affichage)."""
    from . import cockpit530
    out = []
    hidden = _hidden()
    for x in cockpit530.review_items(desk, cached_only=True):
        mid = str(x.get('matter') or '')
        if mid in hidden or (x.get('request') and mid):        # demandes documentaires d'un dossier : déjà dans la file (« livrable »)
            continue
        out.append({'class': 'projet', 'object': 'relire:' + x['id'], 'revision': _rev(x.get('when'), x.get('title')), 'matter': mid,
                    'matter_name': x.get('matter_label') or 'Dossier non identifié', 'title': '%s : %s' % (x.get('type') or 'Projet', x.get('title') or ''),
                    'consequence': 'Destination : %s.' % x['dest'] if x.get('dest') else '', 'href': prefix + '/aujourdhui#relire=' + quote(x['id'], safe=''),
                    'choices': ('ouvrir', 'relu', 'reporter')})
    return out


def queue_items(desk, prefix='', actor='cabinet', role='administrateur'):
    out = []
    for fn in (lambda: decision_items(desk, prefix, actor), lambda: review_items(desk, prefix)):
        try:
            out += fn()
        except Exception:   # noqa: BLE001 — une source illisible ne vide pas la file
            continue
    return out


def count(desk, prefix='', actor='cabinet', role='administrateur'):
    from . import file5651
    try:
        return len(file5651.items(desk, prefix, None, '', _hidden(), actor=actor, role=role))
    except Exception:   # noqa: BLE001
        return 0
