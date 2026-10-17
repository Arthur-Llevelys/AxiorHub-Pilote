"""5.6.57 (A10) : « Reprendre là où j'en étais » et recherche transversale complétée.

- Point de reprise par utilisateur : les dernières pages de travail ouvertes (dossier et onglet, document de facturation, mission,
  relecture, décision), enregistrées par identifiant — jamais un résumé. À l'affichage, chaque élément est revérifié : un dossier
  devenu réservé, un document supprimé ou un objet inaccessible disparaît du point de reprise.
- Recherche : la recherche locale existante (courriels, fichiers, agenda, notes) est complétée par les documents de facturation
  (numéro, client, dossier) et les décisions enregistrées du cabinet, avec le même filtrage des dossiers réservés.
"""
from datetime import datetime, timezone
from html import escape as e
from urllib.parse import urlencode

from .common import Stop, fold, load_matters, matter_display

SCHEMA = '''CREATE TABLE IF NOT EXISTS p57_trail(owner TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, PRIMARY KEY(owner, kind, ref));'''
TRACKED = {'/dossier': ('dossier', 'id'), '/facturation': ('facturation', 'document'), '/missions-complexes': ('mission', 'id'),
           '/decision': ('decision', 'id')}
KEEP = 40


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def note(desk, owner, path, args):
    """Visite d'une page de travail (GET réussi) ; écriture brève, sans effet si la page n'est pas suivie."""
    spec = TRACKED.get(path)
    if not spec:
        return
    ref = str(args.get(spec[1]) or '')[:120]
    if not ref:
        return
    kind = spec[0]
    detail = str(args.get('vue') or args.get('type') or '')[:40]
    if kind == 'decision':
        ref = '%s:%s' % (str(args.get('type') or '')[:20], ref)
    ensure_schema(desk)
    desk.db.execute('INSERT OR REPLACE INTO p57_trail(owner,kind,ref,matter,detail,at) VALUES(?,?,?,?,?,?)',
                    (str(owner or 'cabinet'), kind, ref, ref if kind == 'dossier' else '', detail, _now()))
    desk.db.execute('DELETE FROM p57_trail WHERE owner=? AND at NOT IN (SELECT at FROM p57_trail WHERE owner=? ORDER BY at DESC LIMIT ?)',
                    (str(owner or 'cabinet'), str(owner or 'cabinet'), KEEP))
    desk.db.commit()


def items(desk, owner, prefix='', limit=6):
    """Derniers éléments ENCORE accessibles (revérifiés), du plus récent au plus ancien."""
    ensure_schema(desk)
    names = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}  # liste filtrée : dossiers réservés exclus
    out = []
    for r in desk.db.execute('SELECT * FROM p57_trail WHERE owner=? ORDER BY at DESC LIMIT ?', (str(owner or 'cabinet'), KEEP)):
        label = url = ''
        if r['kind'] == 'dossier':
            if r['ref'] not in names:
                continue
            tab = r['detail'] or 'fiche'
            label = 'Dossier %s%s' % (names[r['ref']], (' — %s' % tab) if r['detail'] else '')
            url = prefix + '/dossier?' + urlencode({'id': r['ref'], **({'vue': r['detail']} if r['detail'] else {})})
        elif r['kind'] == 'facturation':
            try:
                from . import facturation5653 as fact
                d = fact.get(desk, r['ref'])
            except Stop:
                continue
            label = '%s %s — %s' % (d['kind_label'], d['number'] or '(brouillon)', names.get(d['matter'], d['matter']))
            url = prefix + '/facturation?' + urlencode({'document': r['ref']})
        elif r['kind'] == 'mission':
            row = None
            try:
                row = desk.db.execute('SELECT matter,title FROM missions5614 WHERE id=?', (r['ref'],)).fetchone()
            except Exception:   # noqa: BLE001
                pass
            if not row or (row['matter'] and row['matter'] not in names):
                continue
            label = 'Mission — %s' % (str(row['title'] or '')[:80] or names.get(row['matter'], ''))
            url = prefix + '/missions-complexes?' + urlencode({'id': r['ref']})
        elif r['kind'] == 'decision':
            typ, _, ident = r['ref'].partition(':')
            label = 'Décision en attente (%s)' % typ
            url = prefix + '/decision?' + urlencode({'type': typ, 'id': ident})
        if label:
            out.append({'label': label, 'url': url, 'at': r['at'], 'kind': r['kind']})
        if len(out) >= limit:
            break
    return out


def card_html(desk, owner, prefix):
    rows = items(desk, owner, prefix)
    links = ''.join('<li><a href="%s">%s</a> <span class="ax-muted">%s</span></li>' % (e(x['url'], quote=True), e(x['label']), e(x['at'][:16].replace('T', ' ')))
                    for x in rows)
    return ('<section class="c530-card r5657" id="r5657-reprise" aria-labelledby="r5657-t"><div class="c530-head"><h2 id="r5657-t">Reprendre là où j’en étais</h2>'
            '</div><form method="get" action="%s" class="r5657-search" role="search"><input type="search" name="q" maxlength="200" '
            'placeholder="Chercher un courriel, une pièce, une décision, une facture…" aria-label="Recherche transversale">'
            '<button class="ax-btn" type="submit">Rechercher</button></form>%s</section>') % (
        e(prefix + '/recherche', quote=True), ('<ul class="r5657-list">%s</ul>' % links) if links else '<p class="ax-muted">Les dernières pages de travail '
                                                                                                       'ouvertes s’afficheront ici.</p>')


def search_extra(desk, q, prefix='', limit=20):
    """Documents de facturation et décisions enregistrées correspondant à la recherche (dossiers réservés exclus)."""
    words = [w for w in fold(q or '').split() if len(w) > 1][:6]
    if not words:
        return []
    names = {str(m['id']): (matter_display(m), str(m.get('client_name') or '')) for m in load_matters(desk.c)}
    out = []
    try:
        from . import facturation5653 as fact
        billed = {}
        for d in fact.documents(desk, limit=None):
            if d['matter'] not in billed:
                try:
                    billed[d['matter']] = str(fact.client(desk, d['matter']).get('nom') or '')
                except Stop:
                    billed[d['matter']] = ''
            hay = fold(' '.join((d['number'], d['kind_label'], d['client'], d['matter_name'], d['matter'], billed[d['matter']])))
            if d['matter'] in names and all(w in hay for w in words):
                out.append({'type': 'Facturation', 'titre': '%s %s' % (d['kind_label'], d['number'] or '(brouillon)'), 'dossier': d['matter_name'],
                            'url': prefix + '/facturation?' + urlencode({'document': d['id']}), 'date': d['issue_date'] or d['updated'][:10]})
    except Exception:   # noqa: BLE001
        pass
    try:
        for r in desk.db.execute('SELECT * FROM d51_cabinet_decisions ORDER BY rowid DESC LIMIT 500'):
            row = dict(r)
            mid = str(row.get('matter') or '')
            if mid not in names or str(row.get('status') or 'active') != 'active':
                continue
            hay = fold(' '.join(str(row.get(k) or '') for k in ('subject', 'text', 'motif', 'kind')))
            if all(w in hay for w in words):
                out.append({'type': 'Décision du cabinet', 'titre': str(row.get('subject') or row.get('text') or '')[:120], 'dossier': names[mid][0],
                            'url': prefix + '/dossier?' + urlencode({'id': mid, 'vue': 'faits'}), 'date': str(row.get('created') or '')[:10]})
    except Exception:   # noqa: BLE001
        pass
    return out[:limit]


def search_html(desk, q, prefix=''):
    rows = search_extra(desk, q, prefix)
    if not rows:
        return ''
    return ('<section class="ax-card" id="recherche5657"><h2>Facturation et décisions du cabinet</h2><ul>%s</ul></section>') % ''.join(
        '<li><span class="d51-badge">%s</span> <a href="%s">%s</a> <span class="ax-muted">%s · %s</span></li>' % (
            e(r['type']), e(r['url'], quote=True), e(r['titre']), e(r['dossier']), e(r['date'])) for r in rows)
