"""5.6.82 : recherche rapide, disponible partout (bouton de la barre du haut, Ctrl+K).

Le classement se fait sur le serveur, avec les droits de la personne connectée : les dossiers réservés sont retirés AVANT le calcul et
aucun index n'est envoyé au navigateur. Une faute de frappe dans un nom est tolérée (rapprochement approché, sans dépendance).
"""
from difflib import SequenceMatcher

from .common import fold, load_matters, matter_display

LIMIT = 8
MIN_SCORE = 0.62


def score(query, text):
    """Score de 0 à 1 : contient la saisie, mot commençant par elle, sinon meilleur rapprochement mot à mot."""
    query, text = fold(query).strip(), fold(text)
    if not query or not text:
        return 0.0
    if query in text:
        return 1.0 if text.startswith(query) else 0.92
    words = [w for w in text.replace('-', ' ').replace('/', ' ').split() if len(w) >= 2]
    best = max((SequenceMatcher(None, query, w).ratio() for w in words), default=0.0)
    parts = query.split()
    if len(parts) > 1:
        found = [max((SequenceMatcher(None, p, w).ratio() for w in words), default=0.0) for p in parts]
        best = max(best, sum(found) / len(found) * 0.95)
    return max(best, SequenceMatcher(None, query, text[:len(query) + 4]).ratio())


def search(desk, query, owner='cabinet', role='administrateur', lang=None):
    from .fonctions5631 import entries
    from .membres5650 import hidden_for
    query = str(query or '').strip()[:80]
    if len(query) < 2:
        return {'resultats': []}
    hidden = hidden_for(desk.c['state_dir'], owner, role)
    found = []
    for m in load_matters(desk.c):
        if m.get('id') in hidden:
            continue
        label = matter_display(m)
        names = [label, str(m.get('client_name') or ''), str(m.get('id') or '')] + [str(x) for x in (m.get('aliases') or [])] + [str(x) for x in (m.get('references') or [])]
        best = max(score(query, n) for n in names)
        if best >= MIN_SCORE:
            found.append({'type': 'Dossier', 'titre': label, 'detail': str(m.get('client_name') or ''), 'url': '/dossier?id=%s' % _quote(m['id']), 'score': best + 0.03})
    for f in entries(desk.c, role, lang):
        best = max([score(query, f['label'])] + [score(query, a) for a in f['aliases']])
        if best >= MIN_SCORE:
            found.append({'type': 'Fonction', 'titre': f['label'], 'detail': f['desc'], 'url': f['route'], 'score': best})
    found.sort(key=lambda r: (-r['score'], r['titre'].lower()))
    top = found[0]['score'] if found else 0
    return {'resultats': [r for r in found if r['score'] >= top - 0.15][:LIMIT]}   # rapprochements lointains écartés dès qu'un bon résultat existe


def _quote(value):
    from urllib.parse import quote
    return quote(str(value), safe='')
