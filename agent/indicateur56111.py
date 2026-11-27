"""5.6.111 (lot C, L02) : indicateur d'activité htmx des pages de l'application.

- Un seul élément ``#ax-occupe`` (``role="status"``, texte « Mise à jour… », traduit par le dictionnaire des écrans) placé dans la page.
- ``hx-indicator`` sur la zone principale (``<main id="ws-main">``) : toute requête htmx partie de la page l'affiche pendant son trajet ; htmx
  le retire à la fin, réponse en erreur ou coupure comprise. ``closest *`` garde la classe ``htmx-request`` sur l'élément déclencheur
  (atténuation existante de ``v430.css``). Les requêtes de fond du panneau d'activité et du volet de l'assistant (hors zone principale)
  ne l'allument pas.
- Les styles sont dans ``static/v56111-activite.css`` (feuille unique ``app520.css``, CSP ``style-src 'self'``) : la configuration htmx garde
  ``includeIndicatorStyles:false`` (aucun style injecté), avec ``allowEval:false``, ``allowScriptTags:false`` et ``selfRequestsOnly:true``.
"""
import re

ELEMENT = '<div id="ax-occupe" class="htmx-indicator" role="status" aria-live="polite">Mise à jour…</div>'
ATTRIBUT = 'hx-indicator="closest *, #ax-occupe"'
_MAIN = re.compile(r'<main id="ws-main"(?![^>]*\bhx-indicator=)')


def marquer(html):
    """Ajoute l'indicateur à une page qui a une zone principale ; une page sans ``<main id="ws-main">`` ou déjà marquée reste inchangée."""
    if 'id="ax-occupe"' in html or not _MAIN.search(html):
        return html
    html = _MAIN.sub('<main id="ws-main" ' + ATTRIBUT, html, count=1)
    return html.replace('</body>', ELEMENT + '</body>', 1)
