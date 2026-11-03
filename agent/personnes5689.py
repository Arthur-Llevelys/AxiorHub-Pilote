"""5.6.89 (chapitre 33) : personnes et rôles.

L'identité reste ``d51_parties`` et ses alias (``modele5651``), avec ses fusions réversibles et ses séparations durables : pas de deuxième CRM. Ce module ajoute ce qui manquait :
- des **rôles** distincts de l'identité, plusieurs par personne et par dossier, sans afficher la même personne plusieurs fois ;
- des **rapprochements** entre mentions, entre dossiers compris, qui restent des propositions réversibles. Une similarité de nom n'autorise jamais une fusion, même proposée comme certaine : seul un identifiant identique
  (SIREN, numéro de contact) renforce la proposition, et la décision reste à l'avocat.
"""
from datetime import datetime, timezone
import json
import re
import uuid

from .common import Stop

SCHEMA = '''
CREATE TABLE IF NOT EXISTS person_roles5689(id TEXT PRIMARY KEY, matter TEXT NOT NULL, party_id TEXT NOT NULL, role TEXT NOT NULL, source_ref TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'proposed', author TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(matter, party_id, role));
CREATE INDEX IF NOT EXISTS person_roles5689_matter ON person_roles5689(matter, party_id);
CREATE TABLE IF NOT EXISTS rapprochements5689(id TEXT PRIMARY KEY, matter_a TEXT NOT NULL, party_a TEXT NOT NULL, matter_b TEXT NOT NULL, party_b TEXT NOT NULL, motif TEXT NOT NULL, identifiants TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'proposee',
  decide_par TEXT NOT NULL DEFAULT '', decide_le TEXT NOT NULL DEFAULT '', fusion INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(party_a, party_b));
CREATE INDEX IF NOT EXISTS rapprochements5689_state ON rapprochements5689(state, matter_a);
'''
ETATS = {'proposee': 'À décider', 'acceptee': 'Même personne (réversible)', 'separee': 'Personnes distinctes', 'annulee': 'Rapprochement annulé'}
IDENTIFIANTS = ('siren', 'siret', 'contact_id', 'rcs')
MENTION = 'mention:'          # côté B d'un rapprochement : une mention lue dans une source, pas encore une fiche


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def ensure_schema(desk):
    if desk.db.in_transaction and desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='rapprochements5689'").fetchone():
        return
    from . import modele5651
    desk.db.executescript(SCHEMA)
    desk.db.commit()
    modele5651.ensure_schema(desk)


def _partie(desk, mid, party_id):
    row = desk.db.execute('SELECT * FROM d51_parties WHERE id=? AND matter=?', (str(party_id), str(mid))).fetchone()
    if not row:
        raise Stop('partie_absente')
    return row


def ajouter_role(desk, mid, party_id, role, auteur, source_ref=None, status='proposed'):
    """Un rôle de plus sur la même identité. Répété : aucune ligne de plus."""
    ensure_schema(desk)
    _partie(desk, mid, party_id)
    role = re.sub(r'\s+', ' ', str(role or '')).strip()[:80]
    if len(role) < 2:
        raise Stop('role_partie_invalide')
    desk.db.execute('INSERT OR IGNORE INTO person_roles5689(id,matter,party_id,role,source_ref,status,author,created) VALUES(?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, mid, party_id, role, json.dumps(source_ref or {}, ensure_ascii=False), status, str(auteur)[:200], _now()))
    desk.db.commit()
    return roles(desk, mid, party_id)


def roles(desk, mid, party_id):
    return [r['role'] for r in desk.db.execute('SELECT role FROM person_roles5689 WHERE matter=? AND party_id=? ORDER BY created', (mid, party_id))]


def projection(desk, mid):
    """Une ligne par personne, tous ses rôles dessus (qualités de procédure comprises)."""
    from . import modele5651
    ensure_schema(desk)
    out = []
    for p in modele5651.parties(desk, mid):
        noms = sorted({r['role'] for r in p.get('roles') or [] if r.get('role')} | set(roles(desk, mid, p['id'])))
        out.append({'party_id': p['id'], 'nom': p['display_name'], 'type': p['kind'], 'client': bool(p['client']), 'roles': noms})
    return out


def _identifiants(row):
    try:
        data = json.loads(row['identifiers'] or '{}')
    except ValueError:
        return {}
    return {k: str(v) for k, v in data.items() if k in IDENTIFIANTS and v} if isinstance(data, dict) else {}


def _proposer(desk, a, b, motif, communs):
    x, y = sorted((a, b), key=lambda r: r['id'])
    separe = desk.db.execute("SELECT 1 FROM d51_party_decisions WHERE kind='separation' AND a=? AND b=? AND reverted=0", (x['id'], y['id'])).fetchone()
    if separe or desk.db.execute("SELECT 1 FROM rapprochements5689 WHERE party_a=? AND party_b=? AND state='separee'", (x['id'], y['id'])).fetchone():
        return None
    now = _now()
    cur = desk.db.execute('INSERT OR IGNORE INTO rapprochements5689(id,matter_a,party_a,matter_b,party_b,motif,identifiants,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                          (uuid.uuid4().hex, x['matter'], x['id'], y['matter'], y['id'], str(motif)[:300], json.dumps(communs), now, now))
    return cur.rowcount


def rapprocher_mention(desk, mid, nom, identifiants=None):
    """Étape 5 du pipeline. Compare une mention aux parties du dossier. Rend (partie exacte ou None, propositions créées). Rien n'est fusionné : une mention proche devient une proposition à décider,
    et un identifiant différent écarte même un nom identique (homonyme possible)."""
    from . import modele5651
    ensure_schema(desk)
    ident = modele5651.identity(nom)
    if not ident['core']:
        return None, 0
    identifiants = {k: str(v) for k, v in (identifiants or {}).items() if k in IDENTIFIANTS and v}
    exacte, nouvelles, now = None, 0, _now()
    for r in desk.db.execute("SELECT * FROM d51_parties WHERE matter=? AND status='active'", (mid,)).fetchall():
        verdict, motif = modele5651.compare(ident, {'kind': r['kind'], 'gender': r['gender'], 'forme': r['forme'], 'core': r['core'], 'tokens': r['core'].split()})
        connus = _identifiants(r)
        differents = any(k in connus and connus[k] != v for k, v in identifiants.items())
        if verdict == 'meme' and not differents:
            exacte = exacte or r
        elif verdict:
            motif = 'Même nom, identifiant différent' if differents else motif
            cur = desk.db.execute('INSERT OR IGNORE INTO rapprochements5689(id,matter_a,party_a,matter_b,party_b,motif,identifiants,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                                  (uuid.uuid4().hex, mid, r['id'], mid, MENTION + ident['core'], 'Mention « %s » : %s' % (str(nom)[:120], motif), '[]', now, now))
            nouvelles += cur.rowcount
    desk.db.commit()
    return exacte, nouvelles


def rapprochements_entre_dossiers(desk, mid):
    """Propose les personnes de ce dossier présentes ailleurs. Identifiant commun : proposition forte. Même nom seul : proposition, jamais plus."""
    from . import modele5651
    ensure_schema(desk)
    mine = desk.db.execute("SELECT * FROM d51_parties WHERE matter=? AND status='active'", (mid,)).fetchall()
    autres = desk.db.execute("SELECT * FROM d51_parties WHERE matter<>? AND status='active' LIMIT 5000", (mid,)).fetchall()
    n = 0
    for a in mine:
        ids_a = _identifiants(a)
        for b in autres:
            ids_b = _identifiants(b)
            communs = sorted(k for k in ids_a if ids_b.get(k) == ids_a[k])
            different = any(k in ids_b and ids_b[k] != v for k, v in ids_a.items())
            if different:
                continue
            verdict, motif = modele5651.compare({'kind': a['kind'], 'gender': a['gender'], 'forme': a['forme'], 'core': a['core'], 'tokens': a['core'].split()}, {'kind': b['kind'], 'gender': b['gender'], 'forme': b['forme'], 'core': b['core'], 'tokens': b['core'].split()})
            if communs:
                n += _proposer(desk, a, b, 'Identifiant identique (%s)' % ', '.join(communs), communs) or 0
            elif verdict:
                n += _proposer(desk, a, b, 'Nom proche, sans identifiant commun : %s' % motif, []) or 0
    desk.db.commit()
    return n


def lister(desk, mid):
    ensure_schema(desk)
    out = []
    for r in desk.db.execute('SELECT * FROM rapprochements5689 WHERE matter_a=? OR matter_b=? ORDER BY created DESC LIMIT 200', (mid, mid)).fetchall():
        a = desk.db.execute('SELECT display_name FROM d51_parties WHERE id=?', (r['party_a'],)).fetchone()
        b = desk.db.execute('SELECT display_name FROM d51_parties WHERE id=?', (r['party_b'],)).fetchone()
        out.append({'id': r['id'], 'a': a['display_name'] if a else '', 'b': b['display_name'] if b else r['party_b'].replace(MENTION, '', 1), 'dossier_a': r['matter_a'], 'dossier_b': r['matter_b'], 'motif': r['motif'], 'identifiants': json.loads(r['identifiants']), 'state': r['state'], 'state_label': ETATS[r['state']]})
    return out


def decider(desk, ident, decision, auteur, hidden=frozenset()):
    """Accepter relie deux fiches ; dans un même dossier, la fusion réversible de ``modele5651`` s'applique. Séparer est durable. Annuler rétablit l'état d'avant."""
    from . import modele5651
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM rapprochements5689 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('rapprochement_absent')
    if {row['matter_a'], row['matter_b']} & set(hidden):
        raise Stop('dossier_reserve')
    if decision not in ('accepter', 'separer', 'annuler'):
        raise Stop('decision_invalide')
    state, fusion = row['state'], row['fusion']
    mention = row['party_b'].startswith(MENTION)
    if decision == 'annuler':
        if state != 'acceptee':
            raise Stop('rapprochement_non_accepte')
        if mention:
            desk.db.execute('DELETE FROM d51_aliases WHERE party_id=? AND via=?', (row['party_a'], 'rapprochement:' + row['id']))
        elif fusion:
            modele5651.undo_merge(desk, row['matter_a'], fusion, auteur)
        state, fusion = 'annulee', None
    elif state != 'proposee':
        raise Stop('rapprochement_deja_traite')
    elif decision == 'separer':
        if row['matter_a'] == row['matter_b'] and not mention:
            modele5651.separate(desk, row['matter_a'], row['party_a'], row['party_b'], auteur)
        state = 'separee'
    else:
        if mention:
            modele5651._add_alias(desk, row['party_a'], row['matter_a'], row['party_b'].replace(MENTION, '', 1), 'rapprochement', 'rapprochement:' + row['id'])
        elif row['matter_a'] == row['matter_b']:
            fusion = modele5651.merge(desk, row['matter_a'], row['party_a'], row['party_b'], auteur, 'Rapprochement accepté')['decision']
        state = 'acceptee'
    desk.db.execute('UPDATE rapprochements5689 SET state=?, fusion=?, decide_par=?, decide_le=?, updated=? WHERE id=?', (state, fusion, str(auteur)[:200], _now(), _now(), row['id']))
    desk.db.commit()
    desk.audit('rapprochement5689', {'id': row['id'], 'decision': decision, 'par': str(auteur)[:80]})
    return {'id': row['id'], 'state': state, 'state_label': ETATS[state]}
