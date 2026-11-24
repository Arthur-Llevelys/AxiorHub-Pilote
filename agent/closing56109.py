"""5.6.109 (lot G3, G04) : « À finaliser » dans Corporate et Cession, sur l'opération EXISTANTE (aucune seconde opération, aucune page Closing).

L'opération reste celle de ``operation_dossiers5683`` (cessions de fonds, de bail, de parts et d'actions) ou de ``operations5636`` (projets
Corporate : transfert de siège, dirigeants…). Ce module ajoute la liste persistante de ce qui empêche de finaliser :

- **Conditions** (``closing_conditions56109``) groupées Documents / Conditions / Signature / Paiements / Formalités, chacune avec
  responsable, date cible, source, caractère obligatoire, jalon (avant la signature, avant la réalisation, avant la fin des formalités),
  état (À obtenir, Reçu à vérifier, Vérifié, Non applicable motivé, Bloqué), preuve et valideur. Un fichier présent n'est pas une condition
  réalisée : « Vérifié » exige une preuve et un valideur humain (contrainte de la table) ; « Non applicable » exige un motif et un valideur.
- **Dépendances** (``closing_dependances56109``) dans la même opération : référence absente, autre opération et cycle refusés.
- **Preuves versionnées** : chaque changement est inscrit au journal immuable (``closing_journal56109``) ; une nouvelle preuve ne fait pas
  disparaître l'ancienne. Une condition peut porter sur un acte : la validation retient sa version ; **un acte modifié (nouvelle version ou
  édition après « prêt à signer ») invalide la validation** (état « Reçu à vérifier », historique conservé).
- **Blocages déterministes** (``blocages``) : calculés sans modèle ni écriture ; le modèle peut proposer des lignes (« proposée »), jamais
  décider qu'une condition est réalisée. **Aucun « prêt à signer » avec une condition non vérifiée** (``cessions5684.ready_to_sign_blockers``).
- **Décision qualifiée** (``closing_decisions56109``) : auteur, motif, liste EXPLICITE des blocages concernés (et leur version) ; une case
  globale ne lève pas ces nouveaux contrôles.
- **États distincts** : À finaliser, Prêt à signer, Signé, Opération réalisée, Formalités terminées.

Aucun paiement, aucun mouvement de fonds, aucun dépôt : ce tableau n'exécute rien (``cessions5684.release_sequestre`` reste interdit).
Tables créées au démarrage (``Desk._prepare``), jamais pendant une lecture ; routes ``m56109/operations/…`` (``api`` ci-dessous, partagée
avec ``suites56109`` et ``formalites56109``) : droits relus par le service, 404 neutre, 409, 422, no-store, clé d'idempotence.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape as e
import hashlib
import json
import re
import sqlite3
import uuid

from .common import Stop, load_matters

PREFIXE = 'm56109/operations/'
KINDS = ('dossier5683', 'corporate5636')
GROUPES = {'documents': 'Documents', 'conditions': 'Conditions', 'signature': 'Signature', 'paiements': 'Paiements', 'formalites': 'Formalités'}
JALONS = {'signature': 'Avant la signature', 'realisation': 'Avant la réalisation', 'formalites': 'Avant la fin des formalités'}
ORDRE_JALONS = ('signature', 'realisation', 'formalites')
ETATS = {'a_obtenir': 'À obtenir', 'recu_a_verifier': 'Reçu à vérifier', 'verifie': 'Vérifié', 'non_applicable': 'Non applicable motivé', 'bloque': 'Bloqué'}
GLOBAL = {'a_finaliser': 'À finaliser', 'prete_a_signer': 'Prêt à signer', 'signee': 'Signé', 'realisee': 'Opération réalisée',
          'formalites_terminees': 'Formalités terminées'}
MOTIFS = {'a_obtenir': 'à obtenir', 'recu_a_verifier': 'pièce présente, non vérifiée', 'bloque': 'bloquée', 'preuve_manquante': 'preuve ou valideur manquant',
          'acte_modifie': 'acte modifié depuis la validation', 'dependance': 'dépend d’une condition non levée', 'graphe_invalide': 'dépendances à corriger'}
VALIDEURS = ('administrateur', 'avocat')
HUMAINS = ('administrateur', 'avocat', 'assistant')
MAX_CONDITIONS = 200
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,80}$')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS closing_conditions56109(id TEXT PRIMARY KEY, op_kind TEXT NOT NULL CHECK(op_kind IN ('dossier5683','corporate5636')), op_id TEXT NOT NULL,
  matter TEXT NOT NULL DEFAULT '', cle TEXT NOT NULL, groupe TEXT NOT NULL CHECK(groupe IN ('documents','conditions','signature','paiements','formalites')),
  jalon TEXT NOT NULL CHECK(jalon IN ('signature','realisation','formalites')), libelle TEXT NOT NULL, objet TEXT NOT NULL DEFAULT '',
  obligatoire INTEGER NOT NULL DEFAULT 1 CHECK(obligatoire IN (0,1)), source TEXT NOT NULL DEFAULT '', responsable TEXT NOT NULL DEFAULT '',
  date_cible TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL DEFAULT 'a_obtenir' CHECK(etat IN ('a_obtenir','recu_a_verifier','verifie','non_applicable','bloque')),
  preuve TEXT NOT NULL DEFAULT '', preuve_version TEXT NOT NULL DEFAULT '', acte TEXT NOT NULL DEFAULT '', acte_version TEXT NOT NULL DEFAULT '',
  validateur TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', decide_le TEXT NOT NULL DEFAULT '',
  origine TEXT NOT NULL DEFAULT 'avocat' CHECK(origine IN ('avocat','parcours','proposee')), version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
  cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL, UNIQUE(op_kind, op_id, cle),
  CHECK(etat<>'verifie' OR (preuve<>'' AND validateur<>'')), CHECK(etat<>'non_applicable' OR (motif<>'' AND validateur<>'')));
CREATE INDEX IF NOT EXISTS closing_conditions56109_op ON closing_conditions56109(op_kind, op_id);
CREATE TABLE IF NOT EXISTS closing_dependances56109(condition TEXT NOT NULL, depend_de TEXT NOT NULL, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL,
  PRIMARY KEY(condition, depend_de), CHECK(condition<>depend_de));
CREATE TABLE IF NOT EXISTS closing_actes56109(op_kind TEXT NOT NULL, op_id TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', acte TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
  modifie_le TEXT NOT NULL, PRIMARY KEY(op_kind, op_id, acte));
CREATE TABLE IF NOT EXISTS closing_journal56109(id INTEGER PRIMARY KEY AUTOINCREMENT, condition TEXT NOT NULL, op_kind TEXT NOT NULL, op_id TEXT NOT NULL,
  at TEXT NOT NULL, action TEXT NOT NULL, auteur TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', avant TEXT NOT NULL DEFAULT '{}', apres TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS closing_journal56109_c ON closing_journal56109(condition, id);
CREATE TRIGGER IF NOT EXISTS closing_journal56109_fige_maj BEFORE UPDATE ON closing_journal56109 BEGIN SELECT RAISE(ABORT, 'journal_immuable'); END;
CREATE TRIGGER IF NOT EXISTS closing_journal56109_fige_sup BEFORE DELETE ON closing_journal56109 BEGIN SELECT RAISE(ABORT, 'journal_immuable'); END;
CREATE TABLE IF NOT EXISTS closing_decisions56109(id TEXT PRIMARY KEY, op_kind TEXT NOT NULL, op_id TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', jalon TEXT NOT NULL, auteur TEXT NOT NULL,
  motif TEXT NOT NULL, blocages TEXT NOT NULL, origine TEXT NOT NULL DEFAULT 'closing', cree_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations_recus56109(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, reponse TEXT NOT NULL,
  quand TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
'''
TABLES = ('closing_conditions56109', 'closing_dependances56109', 'closing_actes56109', 'closing_journal56109', 'closing_decisions56109', 'operations_recus56109')


class Refus(Stop):
    """422 : champs à compléter (``detail`` : champ → message)."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.detail = detail or {}


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha(valeur):
    return hashlib.sha256(json.dumps(valeur, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def texte(valeur, n=300):
    return re.sub(r'\s+', ' ', str(valeur or '')).strip()[:n]


def jour(valeur, requis=False):
    v = str(valeur or '').strip()[:10]
    if not v:
        if requis:
            raise Stop('date_invalide')
        return ''
    try:
        datetime.strptime(v, '%Y-%m-%d')
    except ValueError:
        raise Stop('date_invalide') from None
    return v


def acteur_de(acteur):
    """(compte, rôle) : jamais un administrateur par défaut."""
    from .acces5697 import acteur_de as a
    return a(acteur)


def humain(acteur, valideur=False):
    a = acteur_de(acteur)
    if a.role not in (VALIDEURS if valideur else HUMAINS):
        raise Stop('verification_humaine_requise' if a.role == 'agent' else 'role_insuffisant')
    return a


def dossier_visible(desk, acteur, matter):
    """Dossier existant ET visible de cet acteur ; sinon le même 404 neutre (« introuvable »)."""
    from .acces5697 import peut_lire_dossier
    matter = str(matter or '')
    if not matter or not any(m['id'] == matter for m in load_matters(desk.c)) or not peut_lire_dossier(desk, acteur_de(acteur), matter):
        raise Stop('introuvable')
    return matter


@contextmanager
def transaction(desk):
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        yield
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise


def version_attendue(row, attendue):
    if attendue is None or str(attendue) == '':
        raise Refus('champs_invalides', {'version': 'Version lue requise : rechargez la page.'})
    if str(attendue) != str(row['version']):
        raise Stop('version_perimee')


# ------------------------------------------------------------------------------------------------ opération existante
def operation(desk, acteur, op_kind, op_id):
    """L'opération existante et son dossier (contrôlé avant tout contenu). Absente ou interdite : le même 404 neutre."""
    op_kind, op_id = str(op_kind or ''), str(op_id or '')
    if op_kind not in KINDS or not op_id:
        raise Stop('introuvable')
    if op_kind == 'dossier5683':
        try:
            row = desk.db.execute('SELECT * FROM operation_dossiers5683 WHERE id=?', (op_id,)).fetchone()
        except sqlite3.Error:
            row = None
        if row is None:
            raise Stop('introuvable')
        matter = row['matter']
        if not matter and row['company']:
            c = desk.db.execute('SELECT matter FROM companies5636 WHERE id=?', (row['company'],)).fetchone()
            matter = c['matter'] if c else ''
        data = json.loads(row['data'] or '{}')
        info = {'kind': op_kind, 'id': op_id, 'type': row['kind'], 'matter': matter, 'company': row['company'], 'state': row['state'],
                'nom': data.get('nom') or '%s → %s' % (data.get('cedant') or 'cédant', data.get('cessionnaire_nom') or data.get('cessionnaire') or 'cessionnaire'),
                'data': data}
    else:
        try:
            row = desk.db.execute('SELECT o.*, c.matter AS matter, c.denomination AS denomination, c.siren AS siren FROM operations5636 o '
                                  'JOIN companies5636 c ON c.id=o.company WHERE o.id=?', (op_id,)).fetchone()
        except sqlite3.Error:
            row = None
        if row is None:
            raise Stop('introuvable')
        info = {'kind': op_kind, 'id': op_id, 'type': row['kind'], 'matter': row['matter'], 'company': row['company'], 'state': row['state'],
                'nom': '%s — %s' % (row['kind'], row['denomination']), 'data': json.loads(row['data'] or '{}'), 'siren': row['siren'],
                'docs': json.loads(row['docs'] or '[]'), 'result': json.loads(row['result'] or '{}')}
    dossier_visible(desk, acteur, info['matter'])
    return info


def versions_actes(desk, op):
    """Version courante de chaque acte de l'opération (lecture seule) : numéro de version du livrable et révision (édition après validation)."""
    revs = {r['acte']: r['revision'] for r in desk.db.execute('SELECT acte, revision FROM closing_actes56109 WHERE op_kind=? AND op_id=?', (op['kind'], op['id']))}
    out = {}
    if op['kind'] == 'dossier5683':
        try:
            for r in desk.db.execute('SELECT doc, MAX(version) AS v FROM deliverables5683 WHERE dossier=? GROUP BY doc', (op['id'],)):
                out[r['doc']] = 'v%d.r%d' % (r['v'], revs.get(r['doc'], 0))
        except sqlite3.Error:
            pass
    else:
        fichiers = (op.get('result') or {}).get('files') or []
        for d in op.get('docs') or []:
            trace = [f for f in fichiers if d in str(f)]
            out[d] = '%s.r%d' % (hashlib.sha256('|'.join(trace).encode()).hexdigest()[:12], revs.get(d, 0))
    for acte, rev in revs.items():
        out.setdefault(acte, 'v0.r%d' % rev)
    return out


# ------------------------------------------------------------------------------------------------ lecture déterministe
def conditions(desk, op):
    return [dict(r) for r in desk.db.execute('SELECT * FROM closing_conditions56109 WHERE op_kind=? AND op_id=? ORDER BY cree_le, id', (op['kind'], op['id']))]


def dependances(desk, ids):
    if not ids:
        return {}
    out = {i: [] for i in ids}
    marks = ','.join('?' * len(ids))
    for r in desk.db.execute('SELECT condition, depend_de FROM closing_dependances56109 WHERE condition IN (%s)' % marks, tuple(ids)):
        out.setdefault(r['condition'], []).append(r['depend_de'])
    return out


def _cycle(deps):
    """Premier cycle trouvé (liste), ou ``None``. Parcours en profondeur itératif borné par le nombre de conditions."""
    blanc, gris, noir = 0, 1, 2
    couleur = {k: blanc for k in deps}
    for depart in deps:
        if couleur[depart] != blanc:
            continue
        pile = [(depart, iter(deps.get(depart, ())))]
        couleur[depart] = gris
        chemin = [depart]
        while pile:
            noeud, suite = pile[-1]
            nxt = next(suite, None)
            if nxt is None:
                couleur[noeud] = noir
                pile.pop()
                chemin.pop()
                continue
            if couleur.get(nxt, noir) == gris:
                return chemin[chemin.index(nxt):] + [nxt]
            if couleur.get(nxt) == blanc:
                couleur[nxt] = gris
                pile.append((nxt, iter(deps.get(nxt, ()))))
                chemin.append(nxt)
    return None


def _propre(r, actes):
    """La ligne elle-même est-elle levée ? (vrai, '') ou (faux, motif). Un fichier présent ne suffit jamais."""
    if r['etat'] == 'verifie':
        if not (r['preuve'] and r['validateur']):
            return False, 'preuve_manquante'
        if r['acte'] and actes.get(r['acte']) != r['acte_version']:
            return False, 'acte_modifie'
        return True, ''
    if r['etat'] == 'non_applicable':
        return (True, '') if (r['motif'] and r['validateur']) else (False, 'preuve_manquante')
    return False, r['etat']


def evaluer(desk, op):
    """Évaluation déterministe de toutes les conditions : levée ou non, motif, dépendances. Aucune écriture, aucun modèle."""
    rows = conditions(desk, op)
    ids = [r['id'] for r in rows]
    deps = dependances(desk, ids)
    actes = versions_actes(desk, op)
    index = {r['id']: r for r in rows}
    cycle = _cycle(deps)
    propre = {r['id']: _propre(r, actes) for r in rows}
    leve = {}

    def est_levee(i, vus=()):
        if i in leve:
            return leve[i]
        if cycle or i in vus:
            leve[i] = False
            return False
        ok = propre[i][0] and all(d in index and est_levee(d, vus + (i,)) for d in deps.get(i, ()))
        leve[i] = ok
        return ok

    out = []
    for r in rows:
        ok = est_levee(r['id'])
        motif = '' if ok else ('graphe_invalide' if cycle else (propre[r['id']][1] if not propre[r['id']][0] else 'dependance'))
        out.append({**r, 'levee': ok, 'motif_blocage': motif, 'depend_de': deps.get(r['id'], []), 'acte_courant': actes.get(r['acte'], '') if r['acte'] else ''})
    return out, cycle


def _jalons_jusqua(jalon):
    return ORDRE_JALONS[:ORDRE_JALONS.index(jalon) + 1]


def blocages(desk, op, jalon='formalites'):
    """Blocages obligatoires jusqu'au jalon donné, triés (jalon, groupe, date cible, libellé) : déterministes."""
    lignes, cycle = evaluer(desk, op)
    garde = _jalons_jusqua(jalon)
    out = [x for x in lignes if x['obligatoire'] and x['jalon'] in garde and not x['levee']]
    out.sort(key=lambda x: (ORDRE_JALONS.index(x['jalon']), list(GROUPES).index(x['groupe']), x['date_cible'] or '9999', x['libelle']))
    return out


def libelles_blocages(desk, op_kind, op_id, jalon):
    """Pour les services existants (``cessions5684.ready_to_sign_blockers``, réalisation) : libellés lisibles, sans contrôle d'accès
    supplémentaire (l'appelant a déjà contrôlé l'opération). Une panne de lecture n'est jamais lue comme « aucun blocage » : elle remonte."""
    op = _operation_brute(desk, op_kind, op_id)
    if op is None:
        return []
    return ['À finaliser : %s (%s)' % (x['libelle'], MOTIFS.get(x['motif_blocage'], x['motif_blocage'])) for x in blocages(desk, op, jalon)]


def _operation_brute(desk, op_kind, op_id):
    """Opération sans contrôle d'accès (usage interne des services qui l'ont déjà fait)."""
    if op_kind == 'dossier5683':
        row = desk.db.execute("SELECT o.id, o.kind, o.data, o.state, COALESCE(NULLIF(o.matter, ''), c.matter, '') AS matter FROM operation_dossiers5683 o "
                              'LEFT JOIN companies5636 c ON c.id=o.company WHERE o.id=?', (str(op_id),)).fetchone()
        return {'kind': op_kind, 'id': row['id'], 'type': row['kind'], 'data': json.loads(row['data'] or '{}'), 'state': row['state'],
                'matter': row['matter'] or ''} if row else None
    row = desk.db.execute('SELECT o.id, o.kind, o.docs, o.result, o.state, c.matter AS matter FROM operations5636 o LEFT JOIN companies5636 c ON c.id=o.company '
                          'WHERE o.id=?', (str(op_id),)).fetchone()
    return {'kind': op_kind, 'id': row['id'], 'type': row['kind'], 'docs': json.loads(row['docs'] or '[]'), 'result': json.loads(row['result'] or '{}'),
            'state': row['state'], 'matter': row['matter'] or ''} if row else None


def etat_global(desk, op):
    """À finaliser → Prêt à signer → Signé → Opération réalisée → Formalités terminées : chaque état exige le précédent ET sa preuve."""
    signe = realise = False
    if op['kind'] == 'dossier5683':
        dates = (op.get('data') or {}).get('dates') or {}
        signe = bool(dates.get('signature')) or bool(desk.db.execute("SELECT 1 FROM events5683 WHERE dossier=? AND kind IN ('signature_constatee','signature_verifiee')",
                                                                      (op['id'],)).fetchone())
        realise = bool(desk.db.execute("SELECT 1 FROM workflow_steps5683 WHERE dossier=? AND step='realisation' AND state='fait'", (op['id'],)).fetchone()) \
            or op.get('state') == 'realisee'
    else:
        realise = op.get('state') == 'realisee'
        signe = realise
    if realise:
        return 'formalites_terminees' if not blocages(desk, op, 'formalites') else 'realisee'
    if signe:
        return 'signee'
    return 'prete_a_signer' if not blocages(desk, op, 'signature') else 'a_finaliser'


def vue(desk, acteur, op_kind, op_id):
    op = operation(desk, acteur, op_kind, op_id)
    lignes, cycle = evaluer(desk, op)
    bl = blocages(desk, op, 'formalites')
    cibles = sorted(x['date_cible'] for x in lignes if x['date_cible'])
    return {'operation': {'kind': op['kind'], 'id': op['id'], 'nom': op['nom'], 'matter': op['matter']}, 'etat': etat_global(desk, op),
            'etat_libelle': GLOBAL[etat_global(desk, op)], 'date_cible': cibles[-1] if cibles else '', 'priorites': [_publique(x) for x in bl[:3]],
            'blocages': len(bl), 'cycle': cycle or [], 'groupes': {g: [_publique(x) for x in lignes if x['groupe'] == g] for g in GROUPES},
            'mentions': ['Un fichier présent n’est pas une condition réalisée ; une date calculée n’est pas une preuve.',
                         'Aucun paiement, aucune libération de séquestre ni aucun dépôt ne découle de ce tableau.']}


def _publique(x):
    return {k: x.get(k) for k in ('id', 'cle', 'groupe', 'jalon', 'libelle', 'objet', 'obligatoire', 'source', 'responsable', 'date_cible', 'etat', 'preuve',
                                  'preuve_version', 'acte', 'acte_version', 'acte_courant', 'validateur', 'motif', 'decide_le', 'origine', 'version', 'levee',
                                  'motif_blocage', 'depend_de')} | {'etat_libelle': ETATS.get(x.get('etat'), x.get('etat'))}


# ------------------------------------------------------------------------------------------------ écritures (routes POST)
def _journal(desk, r, action, auteur, motif='', avant=None, apres=None):
    desk.db.execute('INSERT INTO closing_journal56109(condition,op_kind,op_id,at,action,auteur,motif,avant,apres) VALUES(?,?,?,?,?,?,?,?,?)',
                    (r['id'], r['op_kind'], r['op_id'], maintenant(), action, auteur, texte(motif, 400), json.dumps(avant or {}, ensure_ascii=False),
                     json.dumps(apres or {}, ensure_ascii=False)))


def invalider_perimees(desk, op, auteur='systeme'):
    """Persistance de ce que ``evaluer`` constate : une validation portant sur une ancienne version d'acte redevient « Reçu à vérifier »
    (validateur effacé de la ligne, conservé au journal). Appelée par les écritures, jamais par un affichage."""
    actes = versions_actes(desk, op)
    n = 0
    for r in conditions(desk, op):
        if r['etat'] == 'verifie' and r['acte'] and actes.get(r['acte']) != r['acte_version']:
            desk.db.execute("UPDATE closing_conditions56109 SET etat='recu_a_verifier', validateur='', motif=?, version=version+1, modifie_le=? WHERE id=? AND version=?",
                            ('validation_invalidee_acte_modifie', maintenant(), r['id'], r['version']))
            _journal(desk, r, 'validation_invalidee', auteur, 'acte %s : version %s → %s' % (r['acte'], r['acte_version'], actes.get(r['acte'], 'absente')),
                     {'etat': 'verifie', 'validateur': r['validateur'], 'preuve': r['preuve'], 'acte_version': r['acte_version']}, {'etat': 'recu_a_verifier'})
            n += 1
    return n


def acte_modifie(desk, op_kind, op_id, acte, auteur='systeme'):
    """Point d'accroche des services existants (édition d'un acte après « prêt à signer », nouvelle version signée) : révision de l'acte,
    puis invalidation des validations qui portaient sur l'ancienne version. Sans commit : la transaction de l'appelant la porte."""
    op = _operation_brute(desk, op_kind, op_id)
    if op is None or not acte:
        return 0
    desk.db.execute('INSERT INTO closing_actes56109(op_kind,op_id,matter,acte,revision,modifie_le) VALUES(?,?,?,?,1,?) ON CONFLICT(op_kind,op_id,acte) DO UPDATE SET '
                    'revision=revision+1, modifie_le=excluded.modifie_le', (op_kind, op['id'], op['matter'], str(acte)[:120], maintenant()))
    return invalider_perimees(desk, op, auteur)


def _modele_initial(desk, op):
    """Lignes initiales reprises des étapes, conditions, documents et formalités DÉJÀ définis par l'opération (aucun parcours parallèle)."""
    out = []
    if op['kind'] == 'dossier5683' and op['type'] in ('fonds_commerce', 'droit_bail'):
        from . import cessions5684 as ces
        dos = ces.load(desk, op['id'])
        for c in ces.conditions(desk, dos):
            out.append(('cond:' + c['id'], 'conditions', 'signature', c['label'], c.get('source') or '', c.get('preuve') or '', ''))
        for ref in ('acte_cession_fonds', 'acte_cession_bail', 'promesse_fonds', 'consentement_bailleur'):
            if ref in ces.EXPECTED[dos['kind']]:
                out.append(('doc:' + ref, 'documents', 'signature', 'Acte relu : ' + ces._label(ref), 'Bibliothèque des cessions', '', ref))
        out.append(('signature', 'signature', 'realisation', 'Signature constatée (pièce signée)', 'Dates prouvées de l’opération', '', ''))
        out.append(('paiement', 'paiements', 'realisation', 'Paiement documenté (justificatif, aucun paiement exécuté ici)', 'Acte et convention de séquestre', '', ''))
        for kind, label, target in ces.FILINGS[dos['kind']]:
            out.append(('formalite:' + kind, 'formalites', 'formalites', '%s — %s' % (label, target), 'Formalités de l’opération', '', ''))
    elif op['kind'] == 'dossier5683':
        out.append(('signature', 'signature', 'realisation', 'Ordre de mouvement ou acte signé', 'Dossier de la cession', '', ''))
        out.append(('paiement', 'paiements', 'realisation', 'Paiement du prix documenté (aucun paiement exécuté ici)', 'Acte de cession', '', ''))
    else:
        from .corporate_modeles5636 import DOCS
        for d in op.get('docs') or []:
            if not str(d).startswith('perso_'):
                out.append(('doc:' + d, 'documents', 'signature', 'Acte relu : ' + str(DOCS.get(d, (d,))[0]), 'Actes préparés', '', d))
        out.append(('decision', 'signature', 'realisation', 'Décision prise et signée (PV, décision de l’associé unique…)', 'Registre des décisions', '', ''))
        if op['type'] == 'siege':
            out.append(('formalite:inpi', 'formalites', 'formalites', 'Formalité de modification sur le Guichet unique (INPI)', 'Transfert de siège', '', ''))
    return out


def initialiser(desk, acteur, data):
    """« Préparer la liste » : lignes reprises de l'opération (origine « parcours »), idempotentes (clé unique par opération)."""
    a = humain(acteur)
    op = operation(desk, acteur, data.get('op_kind'), data.get('op_id'))
    lignes = _modele_initial(desk, op)
    n = 0
    with transaction(desk):
        for cle, groupe, jalon, libelle, source, objet, acte in lignes:
            cid = 'cl-' + uuid.uuid4().hex[:20]
            cur = desk.db.execute('INSERT OR IGNORE INTO closing_conditions56109(id,op_kind,op_id,matter,cle,groupe,jalon,libelle,objet,source,acte,origine,cree_par,cree_le,modifie_le) '
                                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,'parcours',?,?,?)", (cid, op['kind'], op['id'], op['matter'], cle, groupe, jalon, texte(libelle, 300), texte(objet, 300),
                                                                                   texte(source, 300), acte, a.compte, maintenant(), maintenant()))
            if cur.rowcount:
                n += 1
                _journal(desk, {'id': cid, 'op_kind': op['kind'], 'op_id': op['id']}, 'creation', a.compte, 'reprise de l’opération', {}, {'libelle': libelle})
    return {'ajoutees': n, 'message': '%d ligne(s) reprise(s) de l’opération.' % n if n else 'Liste déjà préparée : rien n’a été ajouté.'}


def ajouter(desk, acteur, data, origine='avocat'):
    """Ligne ajoutée par l'avocat ; ``origine='proposee'`` pour une proposition (modèle, extraction) : elle n'a aucun effet sur l'état."""
    a = acteur_de(acteur)
    if origine == 'avocat':
        humain(acteur)
    op = operation(desk, acteur, data.get('op_kind'), data.get('op_id'))
    err = {}
    groupe, jalon = str(data.get('groupe') or ''), str(data.get('jalon') or 'signature')
    if groupe not in GROUPES:
        err['groupe'] = 'Groupe : documents, conditions, signature, paiements ou formalités.'
    if jalon not in JALONS:
        err['jalon'] = 'Jalon : avant la signature, la réalisation ou la fin des formalités.'
    libelle = texte(data.get('libelle'), 300)
    if len(libelle) < 3:
        err['libelle'] = 'Libellé requis.'
    try:
        cible = jour(data.get('date_cible'))
    except Stop:
        err['date_cible'] = 'Date invalide.'
        cible = ''
    acte = texte(data.get('acte'), 120)
    if err:
        raise Refus('champs_invalides', err)
    if desk.db.execute('SELECT COUNT(*) FROM closing_conditions56109 WHERE op_kind=? AND op_id=?', (op['kind'], op['id'])).fetchone()[0] >= MAX_CONDITIONS:
        raise Stop('trop_de_conditions')
    cid = 'cl-' + uuid.uuid4().hex[:20]
    cle = 'libre:' + (texte(data.get('cle_metier'), 60) or cid)
    with transaction(desk):
        desk.db.execute('INSERT INTO closing_conditions56109(id,op_kind,op_id,matter,cle,groupe,jalon,libelle,objet,obligatoire,source,responsable,date_cible,acte,origine,cree_par,'
                        'cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (cid, op['kind'], op['id'], op['matter'], cle, groupe, jalon, libelle, texte(data.get('objet'), 300), 0 if str(data.get('obligatoire')) in ('0', 'false', 'non') else 1,
                         texte(data.get('source'), 300), texte(data.get('responsable'), 160), cible, acte, origine, a.compte, maintenant(), maintenant()))
        _journal(desk, {'id': cid, 'op_kind': op['kind'], 'op_id': op['id']}, 'creation' if origine == 'avocat' else 'proposition', a.compte, '', {}, {'libelle': libelle})
    return {'condition': cid, 'message': 'Ligne ajoutée.' if origine == 'avocat' else 'Ligne proposée : à confirmer par l’avocat.'}


def _ligne(desk, acteur, cid):
    r = desk.db.execute('SELECT * FROM closing_conditions56109 WHERE id=?', (str(cid or ''),)).fetchone()
    if r is None:
        raise Stop('introuvable')
    op = operation(desk, acteur, r['op_kind'], r['op_id'])
    return dict(r), op


def dependre(desk, acteur, data):
    """Dépendance dans la MÊME opération ; référence absente, autre opération ou cycle : refus explicite, rien n'est écrit."""
    a = humain(acteur)
    r, op = _ligne(desk, acteur, data.get('condition'))
    cible = desk.db.execute('SELECT * FROM closing_conditions56109 WHERE id=?', (str(data.get('depend_de') or ''),)).fetchone()
    if cible is None:
        raise Refus('dependance_absente', {'depend_de': 'Condition introuvable.'})
    if (cible['op_kind'], cible['op_id']) != (r['op_kind'], r['op_id']):
        raise Refus('dependance_hors_operation', {'depend_de': 'Une dépendance reste dans la même opération.'})
    if cible['id'] == r['id']:
        raise Refus('cycle_conditions', {'depend_de': 'Une condition ne peut pas dépendre d’elle-même.'})
    rows = conditions(desk, op)
    deps = dependances(desk, [x['id'] for x in rows])
    deps.setdefault(r['id'], []).append(cible['id'])
    cycle = _cycle(deps)
    if cycle:
        raise Refus('cycle_conditions', {'depend_de': 'Dépendance circulaire refusée : %s.' % ' → '.join(
            next((x['libelle'] for x in rows if x['id'] == i), i) for i in cycle)})
    with transaction(desk):
        desk.db.execute('INSERT OR IGNORE INTO closing_dependances56109(condition,depend_de,cree_par,cree_le) VALUES(?,?,?,?)', (r['id'], cible['id'], a.compte, maintenant()))
        _journal(desk, r, 'dependance', a.compte, '', {}, {'depend_de': cible['id']})
    return {'condition': r['id'], 'message': 'Dépendance enregistrée.'}


def decider(desk, acteur, data):
    """Changement d'état d'une ligne, versionné et tracé. « Vérifié » : preuve (pièce, version) et valideur (avocat ou administrateur) ;
    « Non applicable » : motif et valideur ; un assistant inscrit « Reçu à vérifier », jamais une validation ; l'IA ne décide jamais."""
    a = humain(acteur)
    r, op = _ligne(desk, acteur, data.get('condition'))
    version_attendue(r, data.get('version'))
    etat = str(data.get('etat') or '')
    if etat not in ETATS:
        raise Refus('champs_invalides', {'etat': 'État inconnu.'})
    err = {}
    maj = {'etat': etat, 'motif': texte(data.get('motif'), 400), 'responsable': texte(data.get('responsable', r['responsable']), 160),
           'validateur': '', 'decide_le': maintenant(), 'preuve': r['preuve'], 'preuve_version': r['preuve_version'], 'acte_version': r['acte_version']}
    if 'date_cible' in data:
        try:
            maj['date_cible'] = jour(data.get('date_cible'))
        except Stop:
            err['date_cible'] = 'Date invalide.'
    if data.get('preuve'):
        maj['preuve'], maj['preuve_version'] = texte(data.get('preuve'), 400), texte(data.get('preuve_version'), 120)
    if etat in ('verifie', 'non_applicable'):
        humain(acteur, valideur=True)
        maj['validateur'] = a.compte
    if etat == 'verifie':
        if not maj['preuve']:
            err['preuve'] = 'La preuve (pièce ou document de l’opération) est requise : un fichier présent ne suffit pas, il faut le désigner.'
        if not maj['preuve_version']:
            err['preuve_version'] = 'Version ou empreinte de la preuve requise.'
        if r['acte']:
            maj['acte_version'] = versions_actes(desk, op).get(r['acte'], '')
            if not maj['acte_version']:
                err['acte'] = 'L’acte de référence n’existe pas encore dans l’opération.'
    if etat == 'non_applicable' and len(maj['motif']) < 5:
        err['motif'] = 'Motif de la décision requis.'
    if err:
        raise Refus('champs_invalides', err)
    avant = {k: r[k] for k in ('etat', 'preuve', 'preuve_version', 'validateur', 'motif', 'acte_version')}
    with transaction(desk):
        invalider_perimees(desk, op, a.compte)
        cols = ', '.join('%s=?' % k for k in maj)
        cur = desk.db.execute('UPDATE closing_conditions56109 SET %s, origine=CASE WHEN origine=\'proposee\' THEN \'avocat\' ELSE origine END, version=version+1, modifie_le=? '
                              'WHERE id=? AND version=?' % cols, tuple(maj.values()) + (maintenant(), r['id'], r['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, r, 'etat', a.compte, maj['motif'], avant, {k: maj[k] for k in avant})
    return {'condition': r['id'], 'etat': etat, 'message': '%s : %s.' % (r['libelle'], ETATS[etat])}


def decision_qualifiee(desk, acteur, data):
    """Décision qualifiée tracée : auteur (avocat ou administrateur), motif, blocages EXPLICITEMENT énumérés (identifiant et version). Elle ne
    couvre que ces blocages-là, dans cet état-là : un nouveau blocage ou une ligne modifiée ensuite n'est pas couvert."""
    a = humain(acteur, valideur=True)
    op = operation(desk, acteur, data.get('op_kind'), data.get('op_id'))
    jalon = str(data.get('jalon') or 'realisation')
    if jalon not in JALONS:
        raise Refus('champs_invalides', {'jalon': 'Jalon inconnu.'})
    motif = texte(data.get('motif'), 600)
    ids = data.get('blocages')
    if isinstance(ids, str):
        ids = [x for x in re.split(r'[\s,;]+', ids) if x]
    if not ids:      # formulaire de l'écran : une case par blocage (« bl_<identifiant> »)
        ids = [k[3:] for k, v in data.items() if str(k).startswith('bl_') and v in (True, 'true', 'on', '1', 1)]
    err = {}
    if len(motif) < 10:
        err['motif'] = 'Motif de la décision requis (au moins une phrase).'
    if not isinstance(ids, list) or not ids:
        err['blocages'] = 'Énumérez les blocages concernés : une case globale ne suffit pas.'
    if err:
        raise Refus('champs_invalides', err)
    courants = {x['id']: x for x in blocages(desk, op, jalon)}
    inconnus = [i for i in ids if i not in courants]
    if inconnus:
        raise Refus('champs_invalides', {'blocages': 'Ces lignes ne sont pas des blocages actuels : %s.' % ', '.join(inconnus[:5])})
    did = 'dq-' + uuid.uuid4().hex[:20]
    couverts = [[i, courants[i]['version']] for i in ids]
    with transaction(desk):
        desk.db.execute('INSERT INTO closing_decisions56109(id,op_kind,op_id,matter,jalon,auteur,motif,blocages,cree_le) VALUES(?,?,?,?,?,?,?,?,?)',
                        (did, op['kind'], op['id'], op['matter'], jalon, a.compte, motif, json.dumps(couverts), maintenant()))
        for i in ids:
            _journal(desk, courants[i], 'decision_qualifiee', a.compte, motif, {}, {'decision': did})
    return {'decision': did, 'couverts': len(couverts), 'message': 'Décision qualifiée enregistrée (%d blocage(s) énuméré(s)).' % len(couverts)}


def decision_couvre(desk, op_kind, op_id, decision_id, jalon):
    """Vrai si la décision ``decision_id`` couvre TOUS les blocages actuels jusqu'au jalon, chacun dans la version décidée."""
    op = _operation_brute(desk, op_kind, op_id)
    if op is None:
        return False
    actuels = blocages(desk, op, jalon)
    if not actuels:
        return True
    row = desk.db.execute('SELECT * FROM closing_decisions56109 WHERE id=? AND op_kind=? AND op_id=?', (str(decision_id or ''), op_kind, str(op_id))).fetchone()
    if row is None or ORDRE_JALONS.index(row['jalon']) < ORDRE_JALONS.index(jalon):
        return False
    couverts = {i: v for i, v in json.loads(row['blocages'])}
    return all(couverts.get(x['id']) == x['version'] for x in actuels)


def tracer_decision_case(desk, auteur, op_kind, op_id, libelles, motif=''):
    """Ancienne case « décision qualifiée » (parcours 5.6.84, conditions calculées) : acceptée pour ces seules conditions, mais tracée ici
    (auteur, libellés énumérés). Elle ne lève jamais les nouvelles conditions persistantes."""
    op = _operation_brute(desk, op_kind, op_id) or {'matter': ''}
    desk.db.execute("INSERT INTO closing_decisions56109(id,op_kind,op_id,matter,jalon,auteur,motif,blocages,origine,cree_le) VALUES(?,?,?,?,'realisation',?,?,?,'case_5684',?)",
                    ('dq-' + uuid.uuid4().hex[:20], op_kind, str(op_id), op['matter'], str(auteur)[:160], texte(motif, 600) or 'Case « décision qualifiée » du parcours (conditions calculées)',
                     json.dumps([[x, 0] for x in libelles], ensure_ascii=False), maintenant()))


def exiger_levee(desk, op_kind, op_id, jalon, decision_id=''):
    """Contrôle pour les services de réalisation existants : blocages persistants levés, ou décision qualifiée qui les couvre."""
    from .erreurs5634 import Qualified
    restants = libelles_blocages(desk, op_kind, op_id, jalon)      # une panne remonte : jamais « aucun blocage » par défaut
    if restants and not decision_couvre(desk, op_kind, op_id, decision_id, jalon):
        raise Qualified('closing_a_finaliser', precision=' ; '.join(restants[:6]))


# ------------------------------------------------------------------------------------------------ écran « À finaliser » (dans les pages existantes)
def _form(route, corps, bouton, cls=''):
    return ('<form class="m5-form m5-inline g3-form%s" data-api="%s" data-reload="1"><input type="hidden" name="cle" value="%s">%s'
            '<button class="ax-btn%s" type="submit">%s</button></form>') % ((' ' + cls) if cls else '', e(PREFIXE + route, quote=True), uuid.uuid4().hex, corps,
                                                                           ' ghost' if 'ghost' in cls else '', e(bouton))


def _cache(**kw):
    return ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in kw.items())


def resume_html(desk, op_kind, op_id, hidden=()):
    """Résumé (état global et trois blocages prioritaires) pour la vue d'ensemble ; ne lève jamais, n'écrit jamais."""
    try:
        op = _operation_brute(desk, op_kind, op_id)
        if op is None or not conditions(desk, op):
            return ''
        bl = blocages(desk, op, 'formalites')
        etat = etat_global(desk, op)
        return ('<section class="ax-card g3-resume"><h3>À finaliser <span class="g3-etat g3-etat-%s">%s</span></h3>%s</section>') % (
            e(etat), e(GLOBAL[etat]), ('<ol class="g3-priorites">%s</ol>' % ''.join('<li><strong>%s</strong> — %s</li>' % (
                e(x['libelle']), e(MOTIFS.get(x['motif_blocage'], x['motif_blocage']))) for x in bl[:3])) if bl else '<p class="ax-muted">Aucun blocage.</p>')
    except (Stop, sqlite3.Error):
        return ''


def section_html(desk, prefix, op_kind, op_id, hidden=()):
    """Liste groupée « À finaliser » d'une opération existante : responsable, dépendances, date, état, preuve, action. Lecture seule ;
    les gestes passent par les routes ``m56109/operations/closing/…``. Ne lève jamais (la page existante reste affichée)."""
    try:
        op = _operation_brute(desk, op_kind, op_id)
        if op is None:
            return ''
        lignes, cycle = evaluer(desk, op)
        etat = etat_global(desk, op)
        bl = [x for x in lignes if x['obligatoire'] and not x['levee']]
    except (Stop, sqlite3.Error):
        return ('<section class="ax-card g3-closing" id="g3-closing"><h3>À finaliser</h3><p class="c5637-alert" role="alert">Liste indisponible pour le moment : '
                'aucun état « prêt » n’est affiché.</p></section>')
    ident = _cache(op_kind=op_kind, op_id=op_id)
    if not lignes:
        return ('<section class="ax-card g3-closing" id="g3-closing"><h3>À finaliser</h3><p class="ax-muted">Aucune liste : reprenez les conditions, documents, '
                'signature, paiements et formalités déjà définis pour cette opération.</p>%s</section>') % _form('closing/initialiser', ident, 'Préparer la liste')
    noms = {x['id']: x['libelle'] for x in lignes}
    groupes = ''
    for g, titre in GROUPES.items():
        rows = [x for x in lignes if x['groupe'] == g]
        if not rows:
            continue
        items = ''
        for x in rows:
            options = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == x['etat'] else '', e(v)) for k, v in ETATS.items())
            deps = ', '.join(noms.get(d, d) for d in x['depend_de'])
            items += ('<li class="g3-ligne%s"><div class="g3-tete"><strong>%s</strong>%s <span class="g3-badge">%s</span>%s</div><p class="g3-detail">%s · %s%s%s%s%s</p>%s</li>') % (
                '' if x['levee'] else ' g3-bloque', e(x['libelle']), ' <small>(proposée, à confirmer)</small>' if x['origine'] == 'proposee' else '', e(ETATS[x['etat']]),
                '' if x['levee'] else ' <span class="g3-motif">%s</span>' % e(MOTIFS.get(x['motif_blocage'], x['motif_blocage'])),
                e(JALONS[x['jalon']]), e('Responsable : ' + (x['responsable'] or 'à désigner')), (' · date cible ' + e(x['date_cible'])) if x['date_cible'] else '',
                (' · dépend de : ' + e(deps)) if deps else '', (' · preuve : %s (%s), validée par %s' % (e(x['preuve']), e(x['preuve_version']), e(x['validateur'] or '—'))) if x['preuve'] else '',
                (' · source : ' + e(x['source'])) if x['source'] else '',
                '<details class="g3-geste"><summary>Mettre à jour</summary>%s%s</details>' % (
                    _form('closing/decider', _cache(condition=x['id'], version=x['version']) + '<label>État<select name="etat">%s</select></label>'
                          '<label>Preuve (pièce ou document)<input name="preuve" value="%s"></label><label>Version ou empreinte<input name="preuve_version" value="%s"></label>'
                          '<label>Motif<input name="motif"></label><label>Responsable<input name="responsable" value="%s"></label>' % (
                              options, e(x['preuve'], quote=True), e(x['preuve_version'], quote=True), e(x['responsable'], quote=True)), 'Enregistrer', 'ghost'),
                    _form('closing/dependance', _cache(condition=x['id']) + '<label>Dépend de<select name="depend_de">%s</select></label>' % ''.join(
                        '<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in noms.items() if k != x['id']), 'Ajouter la dépendance', 'ghost')))
        groupes += '<div class="g3-groupe"><h4>%s</h4><ul class="g3-liste">%s</ul></div>' % (e(titre), items)
    ajout = _form('closing/ajouter', ident + '<label>Libellé<input name="libelle" required></label><label>Groupe<select name="groupe">%s</select></label>'
                  '<label>Jalon<select name="jalon">%s</select></label><label>Responsable<input name="responsable"></label><label>Date cible<input type="date" name="date_cible"></label>'
                  '<label>Source (acte, clause)<input name="source"></label>' % (
                      ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in GROUPES.items()), ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in JALONS.items())),
                  'Ajouter une ligne', 'ghost')
    decision = ''
    if bl:
        decision = '<details class="g3-decision"><summary>Décision qualifiée sur des blocages précis</summary>%s</details>' % _form(
            'closing/decision', ident + '<input type="hidden" name="jalon" value="realisation"><fieldset><legend>Blocages concernés</legend>%s</fieldset>'
            '<label>Motif<textarea name="motif" rows="2" required></textarea></label>' % ''.join(
                '<label class="g3-case"><input type="checkbox" name="bl_%s" value="1"> %s</label>' % (e(x['id'], quote=True), e(x['libelle'])) for x in bl),
            'Enregistrer la décision', 'ghost')
    alerte = ('<p class="c5637-alert" role="alert">Dépendance circulaire à corriger : %s.</p>' % e(' → '.join(noms.get(i, i) for i in cycle))) if cycle else ''
    return ('<section class="ax-card g3-closing" id="g3-closing"><h3>À finaliser <span class="g3-etat g3-etat-%s">%s</span></h3>%s<p class="ax-muted">Un fichier présent '
            'n’est pas une condition réalisée ; aucun paiement, aucune libération de séquestre ni aucun dépôt n’est exécuté depuis cette liste.</p>'
            '<ol class="g3-priorites">%s</ol>%s<details><summary>Ajouter une ligne</summary>%s</details>%s</section>') % (
        e(etat), e(GLOBAL[etat]), alerte, ''.join('<li><strong>%s</strong> — %s</li>' % (e(x['libelle']), e(MOTIFS.get(x['motif_blocage'], ''))) for x in sorted(
            bl, key=lambda x: (ORDRE_JALONS.index(x['jalon']), list(GROUPES).index(x['groupe']), x['date_cible'] or '9999', x['libelle']))[:3]),
        groupes, ajout, decision)


# ================================================================================================ routes m56109/operations/
ROLES = ('administrateur', 'avocat', 'assistant')
ROUTES_GET = ('closing', 'suites', 'formalites', 'formalite', 'effet')
ROUTES_POST = ('closing/initialiser', 'closing/ajouter', 'closing/dependance', 'closing/decider', 'closing/decision',
               'suites/creer', 'suites/proposer', 'suites/confirmer', 'suites/action', 'suites/accomplir', 'suites/echeance', 'suites/lier',
               'formalites/preparer', 'formalites/donnees', 'formalites/piece', 'formalites/synthese', 'formalites/etat', 'formalites/authentification',
               'formalites/demander', 'formalites/autoriser', 'formalites/annuler', 'formalites/constater', 'formalites/document', 'formalites/facture',
               'formalites/panier')
VALIDEURS_SEULS = ('closing/decision', 'suites/confirmer', 'formalites/autoriser', 'formalites/constater')
STATUTS_HTTP = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
                'confirmation_humaine_requise': '403 Forbidden', 'verification_humaine_requise': '403 Forbidden', 'version_perimee': '409 Conflict',
                'cle_reutilisee': '409 Conflict', 'fiche_modifiee_depuis_affichage': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large',
                'champs_invalides': '422 Unprocessable Entity', 'cycle_conditions': '422 Unprocessable Entity', 'dependance_absente': '422 Unprocessable Entity',
                'dependance_hors_operation': '422 Unprocessable Entity', 'secret_refuse': '422 Unprocessable Entity'}
MESSAGES = {'introuvable': 'Élément introuvable.', 'route_inconnue': 'Route inconnue.', 'role_insuffisant': 'Action réservée à l’avocat ou à l’administrateur.',
            'version_perimee': 'L’élément a changé depuis son affichage : rechargez la page (votre saisie est conservée).',
            'cle_reutilisee': 'Cette demande a déjà servi pour un autre contenu.', 'champs_invalides': 'Champs à compléter.',
            'cycle_conditions': 'Dépendance circulaire refusée.', 'confirmation_humaine_requise': 'Seul l’avocat autorise, depuis l’interface.',
            'verification_humaine_requise': 'Une personne habilitée doit décider : jamais l’IA.', 'secret_refuse': 'Aucun code, PIN ni mot de passe n’est saisi ici : '
            'il se saisit dans le portail ou la fenêtre du système.', 'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.'}


def allowed(role, method, api_):
    """Contrôle de rôle sans état : trois rôles du cabinet ; autoriser un effet, décider d'un blocage, confirmer une obligation et constater un
    effet : jamais un assistant (ni l'IA). Le service relit dossier, droits et version."""
    route = str(api_ or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else None
    if role not in ROLES or route is None:
        return False
    if method in ('GET', 'HEAD'):
        return route in ROUTES_GET
    if method != 'POST' or route not in ROUTES_POST:
        return False
    return role != 'assistant' or route not in VALIDEURS_SEULS


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


SECRETS = re.compile(r'(?i)^(pin|otp|code(_|-)?(pin|secret|sms|otp)?|mot_?de_?passe|password|passcode|secret|totp)$')


def _sans_secret(data):
    """Aucun PIN, code ni mot de passe ne transite par AxiorHub : ils se saisissent dans le portail ou la fenêtre du système."""
    for k in data:
        if SECRETS.match(str(k)):
            raise Stop('secret_refuse')


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        code = str(ex).split(':')[0]
        corps = {'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}
        if getattr(ex, 'detail', None) and isinstance(ex.detail, dict):
            corps['champs'] = ex.detail
        return _out(corps, STATUTS_HTTP.get(code, '400 Bad Request'))
    except sqlite3.IntegrityError as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        return _out({'error': 'conflit', 'message': 'Enregistrement refusé par une contrainte d’intégrité : rien n’a été écrit (%s).' % str(ex)[:80]}, '409 Conflict')


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    from . import formalites56109, suites56109
    owner, role = actor(env)
    acteur = (owner, role)
    route = name.split('?', 1)[0][len(PREFIXE):] if name.startswith(PREFIXE) else ''
    if route not in ROUTES_GET + ROUTES_POST:
        raise Stop('route_inconnue')
    if not allowed(role, method, PREFIXE + route):
        raise Stop('role_insuffisant' if (role in ROLES and method == 'POST') else 'route_inconnue')
    if method in ('GET', 'HEAD'):
        if route == 'closing':
            return _out(vue(desk, acteur, args.get('op_kind'), args.get('op_id')))
        if route == 'suites':
            return _out(suites56109.vue(desk, acteur, args.get('dossier')))
        if route == 'formalites':
            return _out(formalites56109.liste(desk, acteur, args.get('op_kind'), args.get('op_id')))
        if route == 'formalite':
            return _out(formalites56109.fiche(desk, acteur, args.get('id')))
        return _out(formalites56109.fiche_effet(desk, acteur, args.get('id')))
    from .web440 import check_post
    data = check_post(env, auth)   # origine, jeton CSRF, corps borné, objet JSON
    _sans_secret(data)
    cle = str(data.get('cle') or '')
    if not CLE.match(cle):
        raise Refus('champs_invalides', {'cle': 'Clé d’opération requise : rechargez la page.'})
    empreinte = sha({k: v for k, v in data.items() if k != 'cle'} | {'route': route})
    ancien = desk.db.execute('SELECT * FROM operations_recus56109 WHERE compte=? AND route=? AND cle=?', (owner, route, cle)).fetchone()
    if ancien is not None:
        if ancien['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        return _out({**json.loads(ancien['reponse']), 'rejoue': True})
    fn = {'closing/initialiser': initialiser, 'closing/ajouter': ajouter, 'closing/dependance': dependre, 'closing/decider': decider,
          'closing/decision': decision_qualifiee}.get(route)
    if fn is None and route.startswith('suites/'):
        fn = suites56109.ROUTES[route[len('suites/'):]]
    if fn is None:
        fn = formalites56109.ROUTES[route[len('formalites/'):]]
    from .bland56109 import requete_humaine     # marque « confirmation de l'avocat » du mécanisme commun : posée ici seulement (route POST)
    with requete_humaine(owner):
        out = fn(desk, acteur, data)
    desk.db.execute('INSERT OR IGNORE INTO operations_recus56109(compte,route,cle,empreinte,reponse,quand) VALUES(?,?,?,?,?,?)',
                    (owner, route, cle, empreinte, json.dumps(out, ensure_ascii=False, default=str)[:20000], maintenant()))
    desk.db.commit()
    return _out(out)
