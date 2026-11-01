"""5.6.88 : page Aujourd'hui personnalisable, pour chaque personne : ordre et masquage des blocs, couleur d'accent, retour aux réglages d'origine.

Le serveur range réellement les blocs dans l'ordre choisi : l'ordre du code est celui de la lecture au clavier et au lecteur d'écran (pas d'ordre
visuel en CSS). Les bandeaux d'alerte (plafond de l'IA, modèle injoignable) ne sont pas des blocs : ils ne se masquent pas.
Les couleurs forment une palette fermée : le contraste de chacune est contrôlé en clair et en sombre (``tests/test_v5688_crm.py``), et une
couleur hors de la liste est refusée par le serveur. Les valeurs sont reprises à l'identique dans ``static/v5688-crm.css``.
"""
from datetime import datetime, timezone
from html import escape as e
import json
import re

from .common import Stop

BLOCS = (('routines', 'Comptes rendus des routines'), ('style', 'Style du cabinet'), ('file', 'À décider / À relire'), ('activite', 'Ce que fait l’agent'),
         ('journee', 'Ma journée'), ('veille', 'Veille juridique'), ('societes', 'Veille des sociétés'), ('memoire', 'À retenir'), ('reprise', 'Reprendre là où j’en étais'))
IDS = tuple(k for k, _ in BLOCS)
LARGES = ('file', 'veille', 'societes', 'memoire', 'reprise')     # toute la largeur quand les blocs sont réordonnés
# Début de chaque bloc dans le gabarit de ``cockpit530.page`` ; sa fin est le repère <!--/b88:id-->.
DEBUTS = {'routines': '<section class="c530-card" id="c530-routines"', 'style': '<section class="c530-card" id="c530-style"', 'file': '<div class="c5662-file" id="c5656-file">',
          'activite': '<section class="c530-card" id="c530-feed"', 'journee': '<section class="c530-card" id="c530-day"', 'veille': '<section class="c530-card c5656-wide" id="c5632-veille"',
          'reprise': '<div class="c5656-wide c5662-reprise">'}
# nom : (libellé, clair : accent, survol, fond doux ; sombre : accent, survol, fond doux). Texte sur l'accent : blanc en clair, ENCRE_SOMBRE en sombre.
ACCENTS = {'': ('Bleu d’origine', None, None),
           'violet': ('Violet', ('#5b34b8', '#472a91', '#efeafb'), ('#c4adff', '#ddd0ff', '#332a52')),
           'vert': ('Vert', ('#136c4a', '#0e5238', '#e4f4ec'), ('#63d3a4', '#9be6c6', '#1f3d33')),
           'bordeaux': ('Bordeaux', ('#96244b', '#771a3b', '#fbe9ef'), ('#ffa3bd', '#ffc9d8', '#4a2433')),
           'ardoise': ('Ardoise', ('#3f5569', '#2f4254', '#e9eef3'), ('#adc4da', '#cfdeec', '#2c3a48')),
           'cuivre': ('Cuivre', ('#8f4a12', '#723a0d', '#f9ecdf'), ('#f2b47c', '#f8d2ae', '#46301e'))}
ENCRE_SOMBRE = '#0b1220'
SCHEMA = 'CREATE TABLE IF NOT EXISTS tableau5688(owner TEXT PRIMARY KEY, data TEXT NOT NULL, modifie TEXT NOT NULL)'


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    desk.db.execute(SCHEMA)


def _ordre(valeurs):
    """Ordre complet : identifiants connus dans l'ordre donné, puis les blocs absents à leur place d'origine (un bloc ajouté par une version future reste visible)."""
    known = [v for v in dict.fromkeys(str(x) for x in valeurs) if v in IDS]
    return known + [k for k in IDS if k not in known]


def charger(desk, owner):
    """Réglages de la personne : tolérant à un contenu ancien ou abîmé (retour aux réglages d'origine, jamais d'erreur sur Aujourd'hui)."""
    ensure_schema(desk)
    row = desk.db.execute('SELECT data FROM tableau5688 WHERE owner=?', (str(owner),)).fetchone()
    try:
        raw = json.loads(row[0]) if row else {}
    except (ValueError, TypeError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    accent = raw.get('accent') if raw.get('accent') in ACCENTS else ''
    masques = [m for m in (raw.get('masques') if isinstance(raw.get('masques'), list) else []) if m in IDS]
    ordre = _ordre(raw.get('ordre') if isinstance(raw.get('ordre'), list) else [])
    return {'ordre': ordre, 'masques': masques, 'accent': accent, 'perso': ordre != list(IDS) or bool(masques)}


def enregistrer(desk, owner, data):
    accent = str(data.get('accent') or '')
    if accent not in ACCENTS:
        raise Stop('couleur_hors_palette')
    ordre = data.get('ordre')
    ordre = ordre if isinstance(ordre, list) else re.split(r'[,\s]+', str(ordre or ''))
    masques = [k for k in IDS if data.get('voir:' + k) is False]
    if isinstance(data.get('masques'), list):
        masques = [k for k in IDS if k in data['masques']]
    ensure_schema(desk)
    desk.db.execute('INSERT OR REPLACE INTO tableau5688 VALUES(?,?,?)', (str(owner), json.dumps({'ordre': _ordre(ordre), 'masques': masques, 'accent': accent}), _now()))
    desk.db.commit()
    return {'message': 'Votre page Aujourd’hui est enregistrée.'}


def reinitialiser(desk, owner):
    ensure_schema(desk)
    desk.db.execute('DELETE FROM tableau5688 WHERE owner=?', (str(owner),))
    desk.db.commit()
    return {'message': 'Réglages d’origine rétablis.'}


def _sans_reperes(html):
    return re.sub(r'<!--/?(?:b88:\w+|t88-\w+)-->', '', html)


def _bloc(html, ident):
    start = html.find(DEBUTS[ident])
    end = html.find('<!--/b88:%s-->' % ident)
    return html[start:end] if 0 <= start < end else ''


def appliquer(desk, html, owner, extras, role='administrateur'):
    """Range les blocs de la page. ``extras`` : {id: html} des blocs facultatifs. Sans personnalisation, la page garde sa disposition en colonnes."""
    prefs = charger(desk, owner)
    panel = panneau(prefs, role, desk)
    accent = (' data-accent="%s"' % e(prefs['accent'], quote=True)) if prefs['accent'] else ''
    html = html.replace('<div class="c530" id="c530"', '<div class="c530" id="c530"' + accent, 1)
    if not prefs['perso']:
        return _sans_reperes(html.replace('<!--t88-extra-->', ''.join(v for k, v in extras.items() if k in IDS)).replace('<!--t88-panneau-->', panel))
    blocs = {k: _bloc(html, k) for k in DEBUTS}
    blocs.update(extras)
    rows = ''.join('<div class="t88-item%s" data-bloc="%s">%s</div>' % (' t88-large' if k in LARGES else '', k, blocs[k])
                   for k in prefs['ordre'] if k not in prefs['masques'] and blocs.get(k))
    note = ('<p class="c530-note">%d bloc(s) masqué(s) sur cette page. Vous pouvez les rétablir dans « Personnaliser cette page ».</p>' % len(prefs['masques'])) if prefs['masques'] else ''
    for debut, fin, contenu in (('<!--t88-haut-->', '<!--/t88-haut-->', ''), ('<!--t88-bas-->', '<!--/t88-bas-->', note + '<div class="t88-grid">' + rows + '</div>')):
        start, end = html.find(debut), html.find(fin)
        if 0 <= start < end:
            html = html[:start] + contenu + html[end + len(fin):]
    return _sans_reperes(html.replace('<!--t88-panneau-->', panel))


def panneau(prefs, role='administrateur', desk=None):
    """Réglages : case à cocher, Monter et Descendre au clavier ; glisser-déposer en plus à la souris (v5688-crm.js)."""
    labels = dict(BLOCS)
    rows = ''.join(('<li class="t88-row" data-bloc="%s" draggable="true"><label class="m5-check"><input type="checkbox" name="voir:%s"%s> %s</label><span class="t88-moves">'
                    '<button type="button" class="ax-btn ghost" data-t88-move="-1" aria-label="Monter : %s">Monter</button>'
                    '<button type="button" class="ax-btn ghost" data-t88-move="1" aria-label="Descendre : %s">Descendre</button></span></li>') % (
        k, k, '' if k in prefs['masques'] else ' checked', e(labels[k]), e(labels[k], quote=True), e(labels[k], quote=True)) for k in prefs['ordre'])
    colors = ''.join('<label class="t88-color"><input type="radio" name="accent" value="%s"%s><span class="t88-swatch" data-swatch="%s" aria-hidden="true"></span> %s</label>' % (
        e(k, quote=True), ' checked' if k == prefs['accent'] else '', e(k or 'origine', quote=True), e(v[0])) for k, v in ACCENTS.items())
    memoire = ''
    if role == 'administrateur' and desk is not None:
        from .memoire5688 import reglage_html
        memoire = reglage_html(desk)
    return ('<details class="t88-panel" id="t88-panel"><summary>Personnaliser cette page</summary>'
            '<form class="m5-form" data-api="m5688/tableau/enregistrer" data-reload="1"><input type="hidden" name="ordre" id="t88-ordre" value="%s">'
            '<p class="ax-muted">Cochez les blocs à afficher. Monter et Descendre changent leur ordre ; à la souris, vous pouvez aussi les glisser.</p><ol class="t88-list" id="t88-list">%s</ol>'
            '<fieldset class="t88-colors"><legend>Couleur d’accent</legend>%s</fieldset><p id="t88-status" role="status" class="c530-sr"></p><button class="ax-btn" type="submit">Enregistrer</button></form>'
            '<form class="m5-form m5-inline" data-api="m5688/tableau/reinitialiser" data-reload="1"><button class="ax-btn ghost" type="submit">Revenir aux réglages d’origine</button></form>%s</details>') % (
        e(','.join(prefs['ordre']), quote=True), rows, colors, memoire)
