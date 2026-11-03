"""5.6.88 : palette unique Ctrl/Cmd+K : dossiers, contacts, factures, documents, courriels et fonctions, groupés.

Tout est calculé sur le serveur, à chaque frappe, avec les droits de la personne connectée (compte et rôle lus sur la requête, jamais envoyés
par le navigateur) : les dossiers réservés sont retirés AVANT les limites, et les factures ne sont cherchées qu'avec le droit « facturation ».
Aucun modèle, aucun réseau : base locale et index plein texte, budget de temps court, résultat marqué ``partiel`` s'il est dépassé.
"""
import json
import re
import sqlite3
import time

from .common import Stop, fold, load_matters, matter_display
from . import recherche5682

PAR_GROUPE = 5
TIME_BUDGET = 0.5           # secondes : au-delà, la recherche plein texte s'arrête et le résultat est partiel
SUREVALUE = 4               # lignes lues par ligne affichée : les dossiers masqués sont retirés avant la limite
GROUPES = (('dossiers', 'Dossiers'), ('contacts', 'Contacts'), ('factures', 'Factures'), ('documents', 'Documents'), ('courriels', 'Courriels'), ('fonctions', 'Fonctions'))


def _q(value):
    return recherche5682._quote(value)


def _liste(values):
    """Liste passée à SQLite en un seul paramètre (``json_each``) : le nombre de dossiers n'a pas de limite de requête."""
    return json.dumps(sorted(str(v) for v in values), ensure_ascii=False)


def _dossiers(matters, query, hidden):
    found = []
    for m in matters:
        if m.get('id') in hidden:
            continue
        label = matter_display(m)
        names = [label, str(m.get('client_name') or ''), str(m.get('id') or '')] + [str(x) for x in (m.get('aliases') or [])] + [str(x) for x in (m.get('references') or [])]
        best = max(recherche5682.score(query, n) for n in names)
        if best >= recherche5682.MIN_SCORE:
            found.append((best, {'titre': label, 'detail': str(m.get('client_name') or ''), 'url': '/dossier?id=%s' % _q(m['id'])}))
    return _top(found)


def _top(found):
    found.sort(key=lambda x: (-x[0], x[1]['titre'].lower()))
    best = found[0][0] if found else 0
    return [r for s, r in found if s >= best - 0.15][:PAR_GROUPE]


def _contacts(desk, query, hidden):
    from . import crm5636
    crm5636.schema(desk)
    where, params = ['archived=0'], []
    for word in [w for w in re.split(r'\s+', query.lower()) if w][:6]:
        where.append("(lower(name||' '||first_name||' '||company||' '||email||' '||phone||' '||siren) LIKE ?)")
        params.append('%' + word.replace('%', '').replace('_', '') + '%')
    if hidden:      # 5.6.89 (B04) : même règle que la fiche (relié à un dossier masqué = soustrait au lecteur), appliquée DANS la requête : une série de lignes masquées ne remplit plus la fenêtre lue
        where.append('NOT EXISTS (SELECT 1 FROM contact_links5636 l WHERE l.contact=contacts5636.id AND l.matter IN (SELECT value FROM json_each(?)))')
        params.append(_liste(hidden))
    rows = desk.db.execute('SELECT * FROM contacts5636 WHERE %s LIMIT ?' % ' AND '.join(where), params + [PAR_GROUPE * SUREVALUE * 4]).fetchall()
    found = []
    for r in rows:
        c = dict(r)
        label = crm5636.label(c)
        found.append((recherche5682.score(query, label), {'titre': label, 'detail': crm5636.CATEGORIES.get(c['category'], c['category']), 'url': '/contacts-crm?id=%s' % _q(c['id'])}))
    return _top(found)


def _factures(desk, query, hidden, labels):
    """Seulement avec le droit « facturation » : l'appelant ne l'appelle pas sinon."""
    # 5.6.89 (B04) : seules les factures des dossiers accessibles sont lues ; le filtre précède la limite. ``labels`` ne contient déjà que les dossiers autorisés.
    allowed = _liste(m for m in labels if m not in hidden)
    try:
        rows = desk.db.execute("SELECT id,kind,number,state,matter,issue_date,snapshot_json FROM b53_documents WHERE lower(COALESCE(number,'')||' '||kind||' '||matter) LIKE ? "
                               'AND matter IN (SELECT value FROM json_each(?)) ORDER BY updated DESC LIMIT ?',
                               ('%' + query.lower().replace('%', '').replace('_', '') + '%', allowed, PAR_GROUPE * SUREVALUE)).fetchall()
    except sqlite3.Error:
        rows = []                              # module de facturation jamais ouvert
    extra = []
    folded = fold(query)
    wanted = {mid for mid, label in labels.items() if folded in fold(label) and mid not in hidden}
    if wanted:
        try:
            extra = desk.db.execute('SELECT id,kind,number,state,matter,issue_date,snapshot_json FROM b53_documents WHERE matter IN (SELECT value FROM json_each(?)) ORDER BY updated DESC LIMIT ?',
                                    (_liste(wanted), PAR_GROUPE * SUREVALUE)).fetchall()
        except sqlite3.Error:
            extra = []
    seen, out = set(), []
    for r in list(rows) + list(extra):
        if r['id'] in seen or r['matter'] in hidden or r['matter'] not in labels:   # double contrôle : la requête a déjà filtré
            continue
        seen.add(r['id'])
        out.append({'titre': (r['number'] or 'Projet sans numéro') + ' · ' + r['kind'].replace('_', ' '), 'detail': ' · '.join(x for x in (labels[r['matter']], str(r['issue_date'] or '')) if x),
                    'url': '/facturation?document=%s' % _q(r['id'])})
        if len(out) >= PAR_GROUPE:
            break
    return out


def _plein_texte(desk, query, hidden, labels, deadline):
    """Courriels et documents : index locaux en lecture, sans rafraîchissement ; les lignes masquées sont exclues par la requête, avant sa limite (5.6.89, B04)."""
    from . import search490
    try:
        terms = search490.build_query(query)
    except Stop:
        return [], [], False
    expr = ' AND '.join(terms)
    limit = PAR_GROUPE * SUREVALUE
    mails, docs, partial = [], [], False
    db = None
    try:
        db = search490.connect(desk)
        rows, partial = search490._local(db, expr, ['courriel'], '', '', '', deadline, limit, hidden)
        mails = [r for r in rows if r['matter'] not in hidden]
    except sqlite3.Error:
        partial = True
    finally:
        if db is not None:
            db.close()
    if time.monotonic() > deadline:
        partial = True
    else:
        try:
            rows, late = search490._files(desk, expr, '', '', '', deadline, limit, hidden)
            docs, partial = [r for r in rows if r['matter'] not in hidden], partial or late
        except sqlite3.Error:
            partial = True
    def extrait(r):
        return ' '.join(s['t'] for s in r.get('segments', []))[:120]
    courriels = [{'titre': r['title'], 'detail': ' · '.join(x for x in (labels.get(r['matter'], ''), r['date'], extrait(r)) if x),
                  'url': '/recherche?q=' + _q(query)} for r in mails[:PAR_GROUPE]]
    documents = [{'titre': r['title'], 'detail': ' · '.join(x for x in (labels.get(r['matter'], ''), r['date'], extrait(r)) if x),
                  'url': '/dossier?id=%s&vue=pieces' % _q(r['matter']) if r['matter'] else '/recherche?q=' + _q(query)} for r in docs[:PAR_GROUPE]]
    return documents, courriels, partial


def search(desk, query, owner='cabinet', role='administrateur', lang=None):
    """{'groupes': [{'id', 'titre', 'resultats': [{'titre', 'detail', 'url'}]}], 'partiel': bool} ; groupes vides retirés."""
    from .fonctions5631 import entries
    from .membres5650 import effective_rights, hidden_for
    query = re.sub(r'\s+', ' ', str(query or '')).strip()[:80]
    if len(query) < 2:
        return {'groupes': [], 'partiel': False}
    deadline = time.monotonic() + TIME_BUDGET
    hidden = hidden_for(desk.c['state_dir'], owner, role)
    matters = [m for m in load_matters(desk.c) if m.get('id') not in hidden]
    labels = {m['id']: matter_display(m) for m in matters}
    out = {'dossiers': _dossiers(matters, query, hidden), 'contacts': _contacts(desk, query, hidden)}
    if effective_rights(desk.c['state_dir'], owner, role).get('facturation'):
        out['factures'] = _factures(desk, query, hidden, labels)
    out['documents'], out['courriels'], partial = _plein_texte(desk, query, hidden, labels, deadline)
    found = []
    for f in entries(desk.c, role, lang):
        best = max([recherche5682.score(query, f['label'])] + [recherche5682.score(query, a) for a in f['aliases']])
        if best >= recherche5682.MIN_SCORE:
            found.append((best, {'titre': f['label'], 'detail': f['desc'], 'url': f['route']}))
    out['fonctions'] = _top(found)
    return {'groupes': [{'id': k, 'titre': t, 'resultats': out[k]} for k, t in GROUPES if out.get(k)], 'partiel': bool(partial)}

