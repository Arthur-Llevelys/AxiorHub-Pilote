"""5.6.83 : moteur d'opération commun et onglet « Cessions de parts et actions » de Corporate (cahier complément, §§ 7 à 9).

La fiche d'opération s'appuie sur la société, ses détentions et ses registres : aucun second registre. Les effets restent ceux de
``corporate5636`` : ``operation_submit`` prépare un projet sans toucher aux registres, ``realize`` les écrit dans une transaction, après contrôle
de la quantité disponible et du justificatif. Le navigateur ne change aucun registre par un statut. Les étapes sont calculées au serveur ; une
compétence ou un modèle de langage n'accorde aucune permission. Aucune démarche externe n'est envoyée d'ici : une formalité se prépare, s'autorise,
se transmet à la main, et son récépissé revient au dossier. Fonds et bail utiliseront ce même moteur en 5.6.84."""
from datetime import date
from decimal import Decimal
from html import escape as e
import hashlib
import json
import re
import uuid

from .common import Stop, clean_path, load_matters
from .erreurs5634 import Qualified
from . import corporate5636 as co
from . import regles5683

STEPS = (('qualification', 'Qualifier l’opération', 'avocat', ()), ('collecte', 'Collecter données et pièces', 'interne', ('qualification',)),
         ('audit', 'Rapprocher registre, statuts et tableaux', 'interne', ('collecte',)), ('conditions', 'Examiner agrément, préemption et clauses', 'avocat', ('audit',)),
         ('negociation', 'Négociation : choix de l’avocat', 'avocat', ('conditions',)), ('redaction', 'Préparer les documents', 'interne', ('negociation',)),
         ('relecture', 'Relire et valider', 'avocat', ('redaction',)), ('signature', 'Signature', 'externe', ('relecture',)),
         ('realisation', 'Constater la réalisation', 'avocat', ('signature',)), ('formalites', 'Formalités', 'externe', ('realisation',)),
         ('suivi', 'Suivi', 'interne', ('formalites',)))
STEP_LABEL = {s[0]: s[1] for s in STEPS}
STEP_STATES = {'a_faire': 'À faire', 'en_attente': 'En attente', 'bloque': 'Bloquée', 'fait': 'Faite'}
AUTH = {'avocat': 'validation de l’avocat', 'interne': 'préparation interne', 'externe': 'autorisation externe distincte'}
DELIVERED = {'SARL': 'parts', 'EURL': 'parts', 'SAS': 'actions', 'SASU': 'actions'}     # CT10 : seules ces formes ont un jeu de règles testé
ANSWERS = (('', 'À confirmer'), ('oui', 'Oui'), ('non', 'Non'))
FICHE = (('cedant', 'Cédant', 'holder', True), ('cessionnaire_nom', 'Acquéreur (nom ou dénomination)', 'text', True), ('cessionnaire_email', 'Courriel de l’acquéreur', 'text', False),
         ('acquereur_qualite', 'Qualité de l’acquéreur', 'select:qualite', True), ('nombre', 'Nombre de titres cédés', 'number', True), ('categorie', 'Catégorie', 'text', True),
         ('prix_unitaire', 'Prix par titre (€)', 'number', True), ('charges', 'Charges ajoutées au prix (€)', 'number', False), ('valeur_reelle', 'Valeur réelle si supérieure (€)', 'number', False),
         ('date_cession', 'Date prévue de la cession', 'date', True), ('acte_ecrit', 'Cession constatée par un acte', 'select:oui', True),
         ('preponderance', 'Prépondérance immobilière', 'select:oui', True), ('regime', 'Régime fiscal', 'select:regime', True), ('droits_vote', 'Droits de vote des titres cédés', 'text', False),
         ('statuts_lus', 'Statuts en vigueur (pièce lue)', 'path', True), ('pacte_lu', 'Pacte d’associés (pièce lue, ou « aucun »)', 'text', True),
         ('demembrement', 'Démembrement', 'select:oui', True), ('indivision', 'Indivision', 'select:oui', True), ('regime_matrimonial', 'Régime matrimonial à examiner', 'select:oui', True),
         ('nantissement', 'Nantissement', 'select:oui', True), ('options', 'Options ou promesses', 'select:oui', True), ('financement', 'Accords de financement', 'select:oui', True),
         ('ville', 'Fait à', 'text', False))
CHOICES = {'oui': ANSWERS, 'qualite': (('', 'À confirmer'), ('associe', 'Déjà associé'), ('famille', 'Conjoint, ascendant ou descendant'), ('tiers', 'Tiers')),
           'regime': (('', 'À confirmer'), ('ordinaire', 'Régime ordinaire'), ('exclusion', 'Exclusion du II de l’article 726'))}
CONTROLS = (('agrement', 'Agrément', 'agrement.sarl'), ('preemption', 'Droit de préemption', ''), ('inalienabilite', 'Inaliénabilité', ''), ('sortie_conjointe', 'Sortie conjointe', ''),
            ('salaries', 'Information des salariés et du CSE', ''), ('pouvoirs', 'Pouvoirs des signataires', ''), ('prix', 'Méthode de détermination du prix', ''))
DATES = (('date_signature', 'Signature'), ('date_levee_conditions', 'Levée des conditions'), ('date_realisation', 'Réalisation convenue'), ('date_jouissance', 'Entrée en jouissance'),
         ('date_ordre', 'Ordre de mouvement'), ('date_inscription', 'Inscription en compte'), ('date_enregistrement', 'Enregistrement fiscal'))
EVENT_DATE = {'signature_constatee': 'date_signature', 'signature_verifiee': 'date_signature', 'levee_conditions': 'date_levee_conditions', 'ordre_signe': 'date_ordre',
              'inscription': 'date_inscription', 'enregistrement': 'date_enregistrement'}
MANUAL_EVENTS = ('signature_constatee', 'levee_conditions', 'ordre_signe', 'inscription', 'enregistrement')     # signature_verifiee : service de signature uniquement
FILING_STATES = (('a_preparer', 'À préparer'), ('prete', 'Prête à contrôler'), ('autorisee', 'Autorisée'), ('transmise', 'Transmise'), ('recue', 'Reçue'),
                 ('acceptee', 'Acceptée'), ('rejetee', 'Rejetée'))
FILING_LABEL = dict(FILING_STATES)
FILING_NEXT = {'a_preparer': ('prete',), 'prete': ('autorisee', 'a_preparer'), 'autorisee': ('transmise', 'a_preparer'), 'transmise': ('recue', 'acceptee', 'rejetee'),
               'recue': ('acceptee', 'rejetee'), 'rejetee': ('a_preparer',), 'acceptee': ()}
LAWYER_ONLY_STATES = ('autorisee',)
YES = ('1', 'on', 'true', 'True')       # case cochée, formulaire ou appel JSON
FILING_NEEDS_PIECE = ('recue', 'acceptee', 'rejetee')


def _now():
    return date.today().isoformat()


def schema(desk):
    co.schema(desk)
    regles5683.schema(desk)
    desk.db.executescript("""
    CREATE TABLE IF NOT EXISTS operation_dossiers5683(id TEXT PRIMARY KEY, kind TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL DEFAULT '', branch TEXT NOT NULL,
        perimetre TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'en_cours', version INTEGER NOT NULL DEFAULT 1, operation TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}',
        idem TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS operation_dossiers5683_idem ON operation_dossiers5683(idem) WHERE idem<>'';
    CREATE INDEX IF NOT EXISTS operation_dossiers5683_company ON operation_dossiers5683(company, created);
    CREATE TABLE IF NOT EXISTS field_evidence5683(id TEXT PRIMARY KEY, dossier TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'texte',
        origin TEXT NOT NULL, excerpt TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, confirmed_by TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS field_evidence5683_dossier ON field_evidence5683(dossier, field);
    CREATE TABLE IF NOT EXISTS workflow_steps5683(dossier TEXT NOT NULL, step TEXT NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        updated TEXT NOT NULL, PRIMARY KEY(dossier, step));
    CREATE TABLE IF NOT EXISTS deliverables5683(id TEXT PRIMARY KEY, dossier TEXT NOT NULL, doc TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL, operation TEXT NOT NULL DEFAULT '',
        inputs TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'projet', at TEXT NOT NULL, UNIQUE(dossier, path));
    CREATE TABLE IF NOT EXISTS filings5683(id TEXT PRIMARY KEY, dossier TEXT NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL, destinataire TEXT NOT NULL, preparateur TEXT NOT NULL DEFAULT '',
        validateur TEXT NOT NULL DEFAULT '', mandat TEXT NOT NULL DEFAULT '', cout TEXT NOT NULL DEFAULT '', pieces TEXT NOT NULL DEFAULT '[]', statut TEXT NOT NULL DEFAULT 'a_preparer',
        recepisse TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL, UNIQUE(dossier, kind));
    CREATE TABLE IF NOT EXISTS events5683(id TEXT PRIMARY KEY, dossier TEXT NOT NULL, kind TEXT NOT NULL, state_version INTEGER NOT NULL, day TEXT NOT NULL DEFAULT '', idem TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT '{}', at TEXT NOT NULL, UNIQUE(dossier, idem));
    """)


def branch_of(forme):
    """CO01, CT10 : parts ou actions d'après la forme ; hors SARL, EURL, SAS et SASU, périmètre limité et préparation manuelle assistée."""
    if forme in DELIVERED:
        return DELIVERED[forme], 'livre'
    return ('actions' if co.FORMS.get(forme, ('', ''))[1] == 'actions' else 'parts'), 'limite'


def _row(desk, did):
    schema(desk)
    row = desk.db.execute('SELECT * FROM operation_dossiers5683 WHERE id=?', (str(did or ''),)).fetchone()
    if not row:
        raise Stop('operation_inconnue')
    return {**dict(row), 'data': json.loads(row['data'] or '{}')}


def _save(desk, dos, bump=True):
    if bump:
        dos['version'] += 1
    desk.db.execute('UPDATE operation_dossiers5683 SET data=?, state=?, operation=?, version=?, updated=? WHERE id=?',
                    (json.dumps(dos['data'], ensure_ascii=False), dos['state'], dos['operation'], dos['version'], _now(), dos['id']))


def _version_ok(dos, data):
    if str(data.get('version') or '') and str(data['version']) != str(dos['version']):
        raise Stop('fiche_modifiee_entre_temps')


def _step(desk, did, step, state, owner='', note=''):
    desk.db.execute('INSERT INTO workflow_steps5683 VALUES(?,?,?,?,?,?) ON CONFLICT(dossier, step) DO UPDATE SET state=excluded.state, owner=excluded.owner, note=excluded.note, '
                    'updated=excluded.updated', (did, step, state, str(owner), str(note)[:600], _now()))


def steps_of(desk, did):
    known = {r['step']: dict(r) for r in desk.db.execute('SELECT * FROM workflow_steps5683 WHERE dossier=?', (did,))}
    return [{'id': s, 'label': label, 'authorization': auth, 'depends': list(deps), **(known.get(s) or {'state': 'a_faire', 'owner': '', 'note': '', 'updated': ''})}
            for s, label, auth, deps in STEPS]


def next_step(desk, did):
    return next((s for s in steps_of(desk, did) if s['state'] != 'fait'), None)


def _event(desk, dos, kind, day, idem, data=None):
    """Clé d'idempotence par fiche : un événement répété ou reçu en double n'a aucun effet (CT06, OP02)."""
    cur = desk.db.execute('INSERT OR IGNORE INTO events5683 VALUES(?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, dos['id'], kind, dos['version'], str(day or '')[:10], str(idem)[:120],
                                                                                     json.dumps(data or {}, ensure_ascii=False), _now()))
    return bool(cur.rowcount)


def events_of(desk, did):
    return [dict(r) for r in desk.db.execute('SELECT * FROM events5683 WHERE dossier=? ORDER BY at, id', (did,))]


def signature_event(desk, operation, event_id):
    """Événement de signature vérifié par un service de signature, rattaché à la fiche de cette opération (CT04)."""
    schema(desk)
    row = desk.db.execute("SELECT e.* FROM events5683 e JOIN operation_dossiers5683 d ON d.id=e.dossier WHERE e.id=? AND e.kind='signature_verifiee' AND d.operation=?",
                          (str(event_id), str(operation))).fetchone()
    return dict(row) if row else None


def record_verified_signature(desk, did, reference, day):
    """Réservé au service de signature, après contrôle du fournisseur : jamais exposé à un formulaire. Un doublon ou un ordre inattendu ne réalise rien."""
    dos = _row(desk, did)
    new = _event(desk, dos, 'signature_verifiee', day, 'signature:' + str(reference), {'reference': str(reference)[:200]})
    dos['data'].setdefault('dates', {}).setdefault('date_signature', str(day or '')[:10])
    _save(desk, dos, bump=False)
    desk.db.commit()
    return new


# ------------------------------------------------------------------------------------------------ ouverture, fiche, données sourcées
def open_cession(desk, data, owner='cabinet'):
    schema(desk)
    c = co.company(desk, str(data.get('company') or ''))
    if not c['matter']:
        raise Stop('societe_sans_dossier')
    idem = re.sub(r'[^A-Za-z0-9_-]', '', str(data.get('idem') or ''))[:64]
    if idem:
        prior = desk.db.execute('SELECT id FROM operation_dossiers5683 WHERE idem=?', (idem,)).fetchone()
        if prior:
            return {'id': prior['id'], 'company': c['id'], 'goto': _goto(c['id'], prior['id']), 'message': 'Opération déjà ouverte.'}
    branch, perimetre = branch_of(c['forme'])
    did = uuid.uuid4().hex
    desk.db.execute('INSERT INTO operation_dossiers5683(id,kind,company,matter,branch,perimetre,data,idem,owner,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (did, 'cession_titres', c['id'], c['matter'], branch, perimetre, json.dumps({'categorie': 'ordinaires', 'acte_ecrit': 'oui', 'dates': {}}, ensure_ascii=False), idem,
                     str(owner), _now(), _now()))
    desk.db.commit()
    desk.audit('operation5683_ouverte', {'id': did, 'company': c['id'], 'branch': branch, 'perimetre': perimetre, 'owner': owner})
    return {'id': did, 'company': c['id'], 'goto': _goto(c['id'], did), 'message': 'Opération ouverte (%s).' % ('parts sociales' if branch == 'parts' else 'actions')}


def _goto(cid, did):
    return '?societe=%s&vue=cessions&dossier=%s' % (cid, did)


def _clean(value, limit=500):
    return re.sub(r'[\x00-\x08\x0b-\x1f]', '', str(value if value is not None else '')).strip()[:limit]


def _inputs_of(data):
    """Empreinte des valeurs reprises dans les actes : deux fiches identiques donnent les mêmes documents."""
    keys = ('cedant', 'cessionnaire_nom', 'nombre', 'categorie', 'prix_unitaire', 'date_cession', 'ville', 'acte_ecrit', 'preparation_manuelle') + tuple('ctrl_' + c[0] for c in CONTROLS)
    return hashlib.sha256(json.dumps({k: data.get(k, '') for k in keys}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def update_fiche(desk, data, owner='cabinet'):
    """Une seule fiche par opération. Une modification qui périme des projets montre d'abord ses conséquences (UX01, § 9) ; un acte signé n'est jamais touché."""
    dos = _row(desk, data.get('id'))
    _version_ok(dos, data)
    if dos['state'] != 'en_cours':
        raise Stop('operation_deja_close')
    changes = {}
    names = [f[0] for f in FICHE] + ['ctrl_' + c[0] for c in CONTROLS] + ['preparation_manuelle', 'notifications', 'associes_a_notifier', 'total_titres']
    for key in names:
        if key in data and _clean(data[key], 2000) != str(dos['data'].get(key, '')):
            changes[key] = _clean(data[key], 2000)
    if not changes:
        return {'id': dos['id'], 'message': 'Aucune modification.'}
    merged = {**dos['data'], **changes}
    stale = []
    if dos['data'].get('inputs') and _inputs_of(merged) != dos['data']['inputs']:
        stale = [dict(r) for r in desk.db.execute("SELECT doc, version FROM deliverables5683 WHERE dossier=? AND state='projet'", (dos['id'],))]
        if stale and str(data.get('confirmer') or '') not in YES:
            raise Qualified('consequences_a_confirmer', precision='Documents à régénérer : %s. Cochez la confirmation pour enregistrer.' % ', '.join(sorted({_doc_label(s['doc']) for s in stale})))
    dos['data'] = merged
    for key, value in changes.items():
        desk.db.execute('INSERT INTO field_evidence5683 VALUES(?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, dos['id'], key, value, 'texte', 'saisie', '', 'confirme', str(owner), _now()))
    if stale:
        desk.db.execute("UPDATE deliverables5683 SET state='perime' WHERE dossier=? AND state='projet'", (dos['id'],))
        _step(desk, dos['id'], 'redaction', 'a_faire', owner, 'Fiche modifiée : documents à régénérer.')
        _step(desk, dos['id'], 'relecture', 'a_faire', owner, '')
    _save(desk, dos)
    desk.db.commit()
    desk.audit('operation5683_fiche', {'id': dos['id'], 'owner': owner, 'champs': len(changes), 'perimes': len(stale)})
    return {'id': dos['id'], 'message': 'Fiche enregistrée.' + (' %d document(s) à régénérer.' % len(stale) if stale else '')}


def _doc_label(doc):
    return co.DOCS[doc][0] if doc in co.DOCS else doc


def propose_field(desk, did, field, value, origin, excerpt='', kind='texte'):
    """Donnée extraite d'une pièce ou d'un tableau : proposée, jamais écrite dans le registre ni dans un récit du dossier (UX03)."""
    dos = _row(desk, did)
    value = _clean(value, 600)
    others = desk.db.execute("SELECT value FROM field_evidence5683 WHERE dossier=? AND field=? AND status IN ('propose','confirme','contradictoire')", (dos['id'], str(field))).fetchall()
    status = 'contradictoire' if any(_same(o['value'], value) is False for o in others) else 'propose'
    eid = uuid.uuid4().hex
    desk.db.execute('INSERT INTO field_evidence5683 VALUES(?,?,?,?,?,?,?,?,?,?)', (eid, dos['id'], str(field)[:120], value, kind, _clean(origin, 120), _clean(excerpt, 400), status, '', _now()))
    return eid


def _same(a, b):
    x, y = co._dec(a), co._dec(b)
    try:
        return x == y if x is not None and y is not None else str(a).casefold().strip() == str(b).casefold().strip()
    except Exception:   # noqa: BLE001 — valeurs non comparables : différence signalée
        return False


def set_evidence(desk, data, owner='cabinet'):
    """Confirmer ou écarter une donnée : la valeur et sa source sont conservées ; aucun fait narratif n'est créé (UX03)."""
    dos = _row(desk, data.get('id'))
    status = str(data.get('statut') or '')
    if status not in ('confirme', 'ecarte'):
        raise Stop('statut_invalide')
    row = desk.db.execute('SELECT * FROM field_evidence5683 WHERE id=? AND dossier=?', (str(data.get('champ') or ''), dos['id'])).fetchone()
    if not row:
        raise Stop('donnee_inconnue')
    desk.db.execute('UPDATE field_evidence5683 SET status=?, confirmed_by=? WHERE id=?', (status, str(owner), row['id']))
    desk.db.commit()
    desk.audit('operation5683_donnee', {'id': dos['id'], 'statut': status, 'owner': owner})
    return {'id': dos['id'], 'message': 'Donnée confirmée.' if status == 'confirme' else 'Donnée écartée ; elle reste tracée.'}


def read_excel(desk, data, owner='cabinet', client=None):
    """Tableau de détention lu dans le dossier : chaque ligne devient une donnée proposée à rapprocher du registre. Le registre n'est jamais réécrit."""
    from .apercu5679 import xlsx_sheets
    from .dav import DAV
    dos = _row(desk, data.get('id'))
    path = clean_path(str(data.get('chemin') or ''))
    matter = next((m for m in load_matters(desk.c) if m['id'] == dos['matter']), None)
    if not matter or not (path + '/').startswith(matter['path'].rstrip('/') + '/'):
        raise Stop('piece_hors_dossier')
    dav = client or DAV(desk.c['nextcloud'])
    sheets = xlsx_sheets(dav.download(dav.stat(path)))
    found = 0
    for _, rows in sheets:
        head = next(((i, r) for i, r in enumerate(rows) if any(re.search('associ|titulaire|d[ée]tenteur', c, re.I) for c in r) and any(re.search('titres|parts|actions|nombre', c, re.I) for c in r)), None)
        if not head:
            continue
        who = next(i for i, c in enumerate(head[1]) if re.search('associ|titulaire|d[ée]tenteur', c, re.I))
        qty = next(i for i, c in enumerate(head[1]) if re.search('titres|parts|actions|nombre', c, re.I))
        for r in rows[head[0] + 1:]:
            if len(r) > max(who, qty) and r[who].strip() and co._dec(r[qty]) is not None and not re.match('total', r[who].strip(), re.I):
                propose_field(desk, dos['id'], 'detention:' + r[who].strip(), r[qty], 'tableau Excel', path.rsplit('/', 1)[-1], 'nombre')
                found += 1
    desk.db.commit()
    return {'id': dos['id'], 'message': '%d ligne(s) de détention proposées : à rapprocher du registre.' % found}


def reconcile(desk, dos):
    """Rapprochement : une différence entre statuts, tableau et registre reste une anomalie à résoudre ; le registre n'est pas modifié (§ 7)."""
    rows, total = co.holdings(desk, dos['company'])
    held = {r['name'].casefold(): r['quantity'] for r in rows}
    c = co.company(desk, dos['company'])
    out = [{'champ': 'registre', 'libelle': 'Position négative : %s' % a['name'], 'registre': a['quantity'], 'source': '', 'origine': 'registre'} for a in co.anomalies(desk, dos['company'])]
    for ev in desk.db.execute("SELECT * FROM field_evidence5683 WHERE dossier=? AND status IN ('propose','confirme','contradictoire') AND (field LIKE 'detention:%' OR field IN ('capital','titres_total'))",
                              (dos['id'],)):
        if ev['field'].startswith('detention:'):
            ref, label = held.get(ev['field'][10:].casefold(), 0), 'Détention de %s' % ev['field'][10:]
        elif ev['field'] == 'capital':
            ref, label = c['capital'], 'Capital'
        else:
            ref, label = total, 'Nombre total de titres'
        if not _same(ref, ev['value']):
            out.append({'champ': ev['field'], 'libelle': label, 'registre': str(ref), 'source': ev['value'], 'origine': ev['origin'], 'evidence': ev['id']})
    return out


def impact(desk, dos):
    """Situation avant et après, par titulaire et catégorie, sans rien écrire. Capital, nombre de titres et droits de vote restent distincts."""
    rows, total = co.holdings(desk, dos['company'])
    d = dos['data']
    qty = co._dec(d.get('nombre'))
    moved = int(qty) if qty is not None and qty == qty.to_integral() and qty > 0 else 0
    cat = d.get('categorie') or 'ordinaires'
    out = {(r['name'], r['category']): {'titulaire': r['name'], 'categorie': r['category'], 'avant': r['quantity'], 'apres': r['quantity']} for r in rows}
    seller = next((r for r in rows if (r['contact'] or r['name']) == d.get('cedant') and r['category'] == cat), None)
    done = dos['state'] == 'realisee'
    if seller and moved and d.get('cessionnaire_nom') and not done:
        out[(seller['name'], cat)]['apres'] -= moved
        out.setdefault((d['cessionnaire_nom'], cat), {'titulaire': d['cessionnaire_nom'], 'categorie': cat, 'avant': 0, 'apres': 0})['apres'] += moved
    lines = list(out.values())
    for line in lines:
        line['pct_avant'] = str((Decimal(line['avant']) * 100 / Decimal(total)).quantize(Decimal('0.01'))) if total else '0'
        line['pct_apres'] = str((Decimal(line['apres']) * 100 / Decimal(total)).quantize(Decimal('0.01'))) if total else '0'
    return {'lignes': lines, 'total': total, 'droits_vote': d.get('droits_vote') or 'À confirmer', 'realisee': done}


def missing(dos):
    d = dos['data']
    return [label for key, label, _, required in FICHE if required and not str(d.get(key) or '').strip()]


def controls(desk, dos):
    """Contraintes tirées des textes, des statuts et des pactes lus, jamais choisies par un modèle d'après la seule forme sociale."""
    d, out = dos['data'], []
    for key, label, _ in CONTROLS:
        answer = d.get('ctrl_' + key) or ''
        source, statut = 'Statuts et pactes lus', answer or 'a_confirmer'
        if key == 'agrement':
            if dos['branch'] == 'actions':
                source = regles5683.RULES['agrement.sas']['source'] + ' et statuts'
            elif d.get('acquereur_qualite') == 'tiers' and co.company(desk, dos['company'])['forme'] == 'SARL':
                source, statut = regles5683.RULES['agrement.sarl']['source'], answer or 'requis'
            else:
                source = regles5683.RULES['agrement.sarl']['source'] + ' et statuts'
        out.append({'id': key, 'label': label, 'statut': statut, 'source': source})
    return out


def deadlines(desk, dos):
    d, out = dos['data'], []
    dates = d.get('dates') or {}
    act_day = dates.get('date_signature') or ''
    if str(d.get('acte_ecrit') or 'oui') == 'oui':
        out.append({'label': 'Enregistrement de l’acte', **regles5683.echeance('enregistrement.acte', act_day, 1)})
    if dos['branch'] == 'parts' and d.get('acquereur_qualite') == 'tiers':
        days = [x.strip() for x in re.split(r'[\s;,]+', str(d.get('notifications') or '')) if x.strip()]
        count = co._dec(d.get('associes_a_notifier'))
        out.append({'label': 'Réponse de la société sur l’agrément', **regles5683.delai_agrement(days, int(count) + 1 if count else 0)})
    return out


def taxes(desk, dos):
    d = dos['data']
    price, qty = co._dec(d.get('prix_unitaire')), co._dec(d.get('nombre'))
    total = co._dec(d.get('total_titres')) or co.holdings(desk, dos['company'])[1]
    return regles5683.droits_cession(desk, {'nature': dos['branch'], 'prix_total': str(price * qty) if price is not None and qty is not None else '', 'nombre': d.get('nombre'),
                                            'charges': d.get('charges'), 'valeur_reelle': d.get('valeur_reelle'), 'total_titres': str(total or ''), 'preponderance': d.get('preponderance'),
                                            'regime': d.get('regime'), 'date_cession': (d.get('dates') or {}).get('date_signature') or d.get('date_cession')})


# ------------------------------------------------------------------------------------------------ étapes
def documents_for(desk, dos):
    """CO01 : jeu de documents par branche. L'attestation d'inscription n'est produite qu'après une inscription réelle (CT02)."""
    d = dos['data']
    answers = {c['id']: c['statut'] for c in controls(desk, dos)}
    if dos['branch'] == 'parts':
        return ['notification_cession', 'acte_cession_parts', 'attestation_depot'] + (['agrement'] if answers.get('agrement') == 'requis' else [])
    docs = ['ordre_mouvement', 'instruction_teneur'] + (['acte_cession'] if str(d.get('acte_ecrit') or 'oui') == 'oui' else [])
    if answers.get('agrement') == 'requis':
        docs.append('agrement')
    if answers.get('preemption') == 'requis':
        docs.append('renonciation_preemption')
    return docs


def _generate(desk, dos, owner):
    if dos['perimetre'] == 'limite' and str(dos['data'].get('preparation_manuelle') or '') not in YES:
        raise Stop('perimetre_limite_a_confirmer')
    if dos['operation']:
        prior = desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (dos['operation'],)).fetchone()
        if prior and prior['state'] == 'realisee':
            raise Stop('operation_deja_close')
        if prior and prior['state'] != 'abandonnee':
            co.abandon(desk, {'id': dos['operation']}, owner)
    d = dos['data']
    chosen = documents_for(desk, dos)
    all_docs = set(co.documents_of('cession', co.FORMS[co.company(desk, dos['company'])['forme']][1]))
    payload = {'kind': 'cession', 'company': dos['company'], 'dossier5683': dos['id'], 'cedant': d.get('cedant'), 'cessionnaire_nom': d.get('cessionnaire_nom'),
               'cessionnaire_email': d.get('cessionnaire_email'), 'nombre': d.get('nombre'), 'categorie': d.get('categorie'), 'prix_unitaire': d.get('prix_unitaire'),
               'date_cession': d.get('date_cession'), 'agrement': 'agrement' in chosen, 'ville': d.get('ville'), 'idem': '%s-%d' % (dos['id'][:40], dos['version']),
               **{'doc_' + x: x in chosen for x in all_docs}}
    out = co.operation_submit(desk, payload, owner)
    dos['operation'] = out['id']
    dos['data']['inputs'] = _inputs_of(dos['data'])
    return out


def sync_deliverables(desk, dos):
    """Fichiers réellement déposés par ``generate`` : une nouvelle version par document ; le fichier précédent reste en place."""
    if not dos['operation']:
        return
    row = desk.db.execute('SELECT state, docs, result FROM operations5636 WHERE id=?', (dos['operation'],)).fetchone()
    if not row:
        return
    for path in json.loads(row['result'] or '{}').get('files') or []:
        name = path.rsplit('/', 1)[-1]
        doc = next((x for x in json.loads(row['docs'] or '[]') if x in co.DOCS and name.startswith(re.sub(r'[\\/:*?"<>|]', '_', co.DOCS[x][0]))), '')
        if doc and not desk.db.execute('SELECT 1 FROM deliverables5683 WHERE dossier=? AND path=?', (dos['id'], path)).fetchone():
            n = desk.db.execute('SELECT COALESCE(MAX(version),0) FROM deliverables5683 WHERE dossier=? AND doc=?', (dos['id'], doc)).fetchone()[0] + 1
            desk.db.execute('INSERT INTO deliverables5683 VALUES(?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, dos['id'], doc, n, path, dos['operation'], dos['data'].get('inputs', ''), 'projet', _now()))
    desk.db.commit()


def prepare_next(desk, data, owner='cabinet', role='avocat', client=None):
    """« Préparer la suite » : exécute l'étape courante ou dit ce qui manque. Une étape qui demande un choix de l'avocat attend sa confirmation."""
    dos = _row(desk, data.get('id'))
    _version_ok(dos, data)
    if dos['state'] == 'abandonnee':
        raise Stop('operation_deja_close')
    sync_deliverables(desk, dos)
    step = next_step(desk, dos['id'])
    if not step:
        return {'id': dos['id'], 'message': 'Toutes les étapes sont faites.'}
    sid, confirm = step['id'], str(data.get('confirmer') or '') in YES
    if step['authorization'] == 'avocat' and role == 'assistant':
        raise Stop('validation_avocat_requise')
    if sid == 'qualification':
        if not confirm:
            return _wait(desk, dos, sid, 'Confirmez la branche et le périmètre pour poursuivre.')
        return _done(desk, dos, sid, owner, 'Branche %s, périmètre %s.' % (dos['branch'], dos['perimetre']))
    if sid == 'collecte':
        lacks = missing(dos)
        return _blocked(desk, dos, sid, 'À compléter : ' + ', '.join(lacks) + '.') if lacks else _done(desk, dos, sid, owner, 'Données et pièces réunies.')
    if sid == 'audit':
        wrong = reconcile(desk, dos)
        return _blocked(desk, dos, sid, '%d anomalie(s) à résoudre.' % len(wrong)) if wrong else _done(desk, dos, sid, owner, 'Registre, statuts et tableaux concordants.')
    if sid == 'conditions':
        lacks = [c['label'] for c in controls(desk, dos) if c['statut'] == 'a_confirmer']
        rule = regles5683.formulaire_2759(dos['branch'], str(dos['data'].get('acte_ecrit') or 'oui') == 'oui')
        if dos['branch'] == 'parts' and str(dos['data'].get('acte_ecrit') or 'oui') != 'oui':
            return _blocked(desk, dos, sid, rule['motif'])
        if lacks:
            return _blocked(desk, dos, sid, 'À confirmer d’après les statuts et pactes lus : ' + ', '.join(lacks) + '.')
        if not confirm:
            return _wait(desk, dos, sid, 'Conditions renseignées : confirmez pour poursuivre.')
        return _done(desk, dos, sid, owner, 'Conditions examinées.')
    if sid == 'negociation':
        if not confirm:
            return _wait(desk, dos, sid, 'Méthode de prix, garanties, plafond, durée, franchise, séquestre et concessions : à valider par l’avocat.')
        return _done(desk, dos, sid, owner, 'Choix de l’avocat enregistrés.')
    if sid == 'redaction':
        state = desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (dos['operation'],)).fetchone() if dos['operation'] else None
        fresh = dos['data'].get('inputs') == _inputs_of(dos['data'])
        if state and fresh and state['state'] == 'actes_prets':
            return _done(desk, dos, sid, owner, 'Documents prêts à relire.')
        if state and fresh and state['state'] == 'en_file':
            return _wait(desk, dos, sid, 'Documents en préparation.')
        out = _generate(desk, dos, owner)
        _save(desk, dos)
        _step(desk, dos['id'], sid, 'en_attente', owner, 'Documents en préparation.')
        desk.db.commit()
        return {'id': dos['id'], 'job': out.get('job'), 'message': 'Documents en préparation : aucun registre n’est modifié.'}
    if sid == 'relecture':
        if desk.db.execute("SELECT 1 FROM deliverables5683 WHERE dossier=? AND state='perime' AND doc NOT IN (SELECT doc FROM deliverables5683 WHERE dossier=? AND state='projet')",
                           (dos['id'], dos['id'])).fetchone():
            return _blocked(desk, dos, sid, 'Un document périmé doit être régénéré.')
        if not confirm:
            return _wait(desk, dos, sid, 'Relisez les documents puis confirmez.')
        return _done(desk, dos, sid, owner, 'Documents relus.')
    if sid == 'signature':
        if any(ev['kind'] in ('signature_constatee', 'signature_verifiee') for ev in events_of(desk, dos['id'])):
            return _done(desk, dos, sid, owner, 'Signature rattachée au dossier.')
        return _wait(desk, dos, sid, 'Ajoutez le justificatif de signature : rien n’est envoyé d’ici.')
    if sid == 'realisation':
        return _wait(desk, dos, sid, 'Enregistrez la réalisation avec ses dates et son justificatif.')
    if sid == 'formalites':
        create_filings(desk, dos, owner)
        if dos['branch'] == 'actions':
            final_attestation(desk, dos, client)
        return _done(desk, dos, sid, owner, 'Formalités ouvertes.')
    pending = desk.db.execute("SELECT COUNT(*) FROM filings5683 WHERE dossier=? AND statut<>'acceptee'", (dos['id'],)).fetchone()[0]
    if pending:
        return _wait(desk, dos, sid, '%d formalité(s) en cours.' % pending)
    return _done(desk, dos, sid, owner, 'Opération suivie jusqu’au bout.')


def _wait(desk, dos, step, note):
    _step(desk, dos['id'], step, 'en_attente', '', note)
    desk.db.commit()
    return {'id': dos['id'], 'etat': 'en_attente', 'message': note}


def _blocked(desk, dos, step, note):
    _step(desk, dos['id'], step, 'bloque', '', note)
    desk.db.commit()
    return {'id': dos['id'], 'etat': 'bloque', 'message': note}


def _done(desk, dos, step, owner, note):
    _step(desk, dos['id'], step, 'fait', owner, note)
    _save(desk, dos)
    desk.db.commit()
    desk.audit('operation5683_etape', {'id': dos['id'], 'step': step, 'owner': owner})
    return {'id': dos['id'], 'etat': 'fait', 'message': note}


def add_proof(desk, data, owner='cabinet', client=None):
    """« Ajouter un justificatif » : pièce présente dans le dossier, date et clé d'idempotence ; aucune réalisation n'en découle. Ordre de réception libre."""
    from .dav import DAV
    dos = _row(desk, data.get('id'))
    kind = str(data.get('type') or '')
    if kind not in MANUAL_EVENTS:
        raise Stop('justificatif_invalide')
    day = str(data.get('date') or '')[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        raise Stop('date_invalide') from None
    piece = clean_path(str(data.get('piece') or ''))
    matter = next((m for m in load_matters(desk.c) if m['id'] == dos['matter']), None)
    if not piece or not matter or not (piece + '/').startswith(matter['path'].rstrip('/') + '/'):
        raise Stop('piece_hors_dossier')
    info = (client or DAV(desk.c['nextcloud'])).stat(piece)
    new = _event(desk, dos, kind, day, '%s:%s:%s' % (kind, day, piece[-80:]), {'piece': piece, 'empreinte': str(info.get('etag') or ''), 'par': str(owner)})
    if new:
        dos['data'].setdefault('dates', {})[EVENT_DATE[kind]] = day
        dos['data'].setdefault('preuves', {})[kind] = piece
        _save(desk, dos)
    desk.db.commit()
    return {'id': dos['id'], 'message': 'Justificatif rattaché.' if new else 'Justificatif déjà rattaché.'}


def realize(desk, data, owner='cabinet', client=None):
    """Constat de la réalisation : pièce ou signature vérifiée, dates distinctes, transaction de ``corporate5636.realize``. Une nouvelle tentative
    n'écrit ni mouvement ni formalité ; après une panne entre deux écritures, la fiche est rapprochée de l'opération (CT06, OP02)."""
    dos = _row(desk, data.get('id'))
    if dos['state'] == 'realisee':
        return {'id': dos['id'], 'message': 'Réalisation déjà enregistrée.', 'deja': True}
    _version_ok(dos, data)
    if not dos['operation']:
        raise Stop('operation_non_realisable')
    row = desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (dos['operation'],)).fetchone()
    externe = str(data.get('actes_externes') or '') in YES
    if row and row['state'] == 'realisee':
        return _closed(desk, dos, owner)
    if {s['id']: s['state'] for s in steps_of(desk, dos['id'])}['signature'] != 'fait' and not externe:
        raise Stop('signature_non_constatee')
    if reconcile(desk, dos):
        raise Stop('anomalie_a_resoudre')
    dates = dict(dos['data'].get('dates') or {})
    for key, _ in DATES:
        if str(data.get(key) or ''):
            dates[key] = str(data[key])[:10]
    needed = ('date_signature', 'date_ordre', 'date_inscription') if dos['branch'] == 'actions' else ('date_signature', 'date_realisation')
    lacks = [label for key, label in DATES if key in needed and not dates.get(key)]
    if lacks:
        raise Qualified('dates_a_completer', precision=', '.join(lacks))
    transfer = dates['date_inscription'] if dos['branch'] == 'actions' else dates['date_realisation']     # L228-1 : l'inscription, pas le clic « Générer »
    signed = next((ev for ev in events_of(desk, dos['id']) if ev['kind'] == 'signature_verifiee'), None)
    proofs = dos['data'].get('preuves') or {}
    payload = {'id': dos['operation'], 'date': dates['date_signature'], 'justificatif': _clean(data.get('justificatif') or 'Acte signé', 500), 'confirmer_date': '1',
               'date_transfert': transfer, 'actes_externes': '1' if externe else '', 'evenement': signed['id'] if signed else '',
               'piece': str(data.get('piece') or proofs.get('signature_constatee') or '')}
    co.realize(desk, payload, owner, externe=externe, dav=client)
    dos['data']['dates'] = dates
    return _closed(desk, dos, owner)


def _closed(desk, dos, owner):
    dos['state'] = 'realisee'
    _event(desk, dos, 'realisation', (dos['data'].get('dates') or {}).get('date_signature', ''), 'realisation', {'operation': dos['operation'], 'par': str(owner)})
    _step(desk, dos['id'], 'signature', 'fait', owner, 'Signature rattachée au dossier.')
    _step(desk, dos['id'], 'realisation', 'fait', owner, 'Réalisation enregistrée.')
    _save(desk, dos)
    desk.db.commit()
    desk.audit('operation5683_realisation', {'id': dos['id'], 'operation': dos['operation'], 'owner': owner})
    return {'id': dos['id'], 'message': 'Réalisation enregistrée : registres et détentions mis à jour.'}


def rectify(desk, data, owner='cabinet'):
    """Rectification d'une réalisation : écriture corrective tracée ; le mouvement historique n'est ni modifié ni supprimé (§ 7)."""
    dos = _row(desk, data.get('id'))
    if dos['state'] != 'realisee':
        raise Stop('operation_non_realisee')
    motif = _clean(data.get('motif'), 500)
    if len(motif) < 5:
        raise Stop('motif_requis')
    qty = co._dec(data.get('nombre'))
    if qty is None or qty <= 0 or qty != qty.to_integral():
        raise Stop('nombre_titres_invalide')
    idem = 'rectification:%s' % (re.sub(r'[^A-Za-z0-9_-]', '', str(data.get('idem') or ''))[:40] or hashlib.sha256(motif.encode()).hexdigest()[:16])
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    try:
        if not _event(desk, dos, 'rectification', _now(), idem, {'motif': motif, 'par': str(owner)}):
            desk.db.rollback()
            return {'id': dos['id'], 'message': 'Rectification déjà enregistrée.'}
        rows, _ = co.holdings(desk, dos['company'])
        d = dos['data']
        cat = d.get('categorie') or 'ordinaires'
        buyer = next((r for r in rows if r['name'] == d.get('cessionnaire_nom') and r['category'] == cat), None)
        if not buyer or buyer['quantity'] < int(qty):
            raise Stop('cession_titres_insuffisants')
        seller = json.loads(desk.db.execute('SELECT data FROM operations5636 WHERE id=?', (dos['operation'],)).fetchone()['data']).get('cedant_nom') or d.get('cedant')
        n = co._movement(desk, dos['company'], _now(), 'correction', int(qty), cat, dos['operation'], (buyer['name'], buyer['contact']), (seller, ''))
        co._decision(desk, dos['company'], _now(), 'dirigeant', 'Écriture corrective n° %d' % n, motif, dos['operation'])
        _save(desk, dos)
        desk.db.commit()
    except Exception:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('operation5683_rectification', {'id': dos['id'], 'owner': owner, 'numero': n})
    return {'id': dos['id'], 'message': 'Écriture corrective n° %d inscrite ; l’historique est conservé.' % n}


# ------------------------------------------------------------------------------------------------ formalités
def create_filings(desk, dos, owner):
    """Une fiche par démarche : le guichet unique, les impôts et le siège social ne sont pas interchangeables. Une reprise n'en crée pas de seconde (OP02)."""
    d = dos['data']
    rows = [('enregistrement', 'Enregistrement de l’acte de cession', 'Service des impôts compétent')]
    if str(d.get('acte_ecrit') or 'oui') != 'oui':
        rows = [('declaration_2759', 'Déclaration 2759-SD', 'Service des impôts compétent')]
    if dos['branch'] == 'parts':
        rows += [('opposabilite', 'Dépôt d’un original au siège ou signification', 'Société'), ('depot_statuts', 'Dépôt des statuts modifiés', 'Guichet unique')]
    rows.append(('beneficiaires', 'Bénéficiaires effectifs, si leurs conditions sont réunies', 'Guichet unique'))
    for kind, label, target in rows:
        desk.db.execute('INSERT OR IGNORE INTO filings5683(id,dossier,kind,label,destinataire,preparateur,updated) VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, dos['id'], kind, label, target, str(owner), _now()))


def filing_update(desk, data, owner='cabinet', role='avocat', client=None):
    """États de la formalité : « Transmise » reste distinct de « Acceptée » ; un récépissé est exigé au retour ; une transition répétée n'a aucun effet (OP01, OP02)."""
    from .dav import DAV
    dos = _row(desk, data.get('id'))
    row = desk.db.execute('SELECT * FROM filings5683 WHERE id=? AND dossier=?', (str(data.get('formalite') or ''), dos['id'])).fetchone()
    if not row:
        raise Stop('formalite_inconnue')
    target = str(data.get('statut') or '')
    if target == row['statut']:
        return {'id': dos['id'], 'message': 'Formalité déjà à cet état.'}
    if target not in FILING_NEXT.get(row['statut'], ()):
        raise Stop('transition_formalite_invalide')
    if target in LAWYER_ONLY_STATES and role == 'assistant':
        raise Stop('validation_avocat_requise')
    piece = row['recepisse']
    if target in FILING_NEEDS_PIECE:
        piece = clean_path(str(data.get('piece') or ''))
        matter = next((m for m in load_matters(desk.c) if m['id'] == dos['matter']), None)
        if not piece or not matter or not (piece + '/').startswith(matter['path'].rstrip('/') + '/'):
            raise Stop('recepisse_requis')
        (client or DAV(desk.c['nextcloud'])).stat(piece)
    validator = str(owner) if target in LAWYER_ONLY_STATES else row['validateur']
    cur = desk.db.execute('UPDATE filings5683 SET statut=?, recepisse=?, validateur=?, mandat=?, cout=?, updated=? WHERE id=? AND statut=?',
                          (target, piece, validator, _clean(data.get('mandat', row['mandat']), 200), _clean(data.get('cout', row['cout']), 40), _now(), row['id'], row['statut']))
    if not cur.rowcount:
        raise Stop('formalite_modifiee_entre_temps')
    _event(desk, dos, 'formalite', _now(), 'formalite:%s:%s:%s' % (row['id'][:12], target, _now()), {'statut': target, 'par': str(owner)})
    desk.db.commit()
    desk.audit('operation5683_formalite', {'id': dos['id'], 'kind': row['kind'], 'statut': target, 'owner': owner})
    return {'id': dos['id'], 'message': 'Formalité : %s.' % FILING_LABEL[target]}


def final_attestation(desk, dos, client=None):
    """CT02 : l'attestation d'inscription n'existe qu'après l'inscription réelle, avec son numéro de mouvement. Une reprise ne la dépose pas deux fois."""
    from .dav import DAV
    if desk.db.execute("SELECT 1 FROM deliverables5683 WHERE dossier=? AND doc='attestation_inscription'", (dos['id'],)).fetchone():
        return ''
    row = desk.db.execute('SELECT data, result FROM operations5636 WHERE id=?', (dos['operation'],)).fetchone()
    c = co.company(desk, dos['company'])
    d = json.loads(row['data'])
    dav = client or DAV(desk.c['nextcloud'])
    ctx = co.build_context(desk, c, 'cession', d, ['attestation_inscription'], dav, None)
    raw, _ = co.render(desk, 'attestation_inscription', ctx, dav)
    folder = json.loads(row['result'] or '{}').get('folder')
    if not folder:
        return ''
    path = '%s/%s - %s.docx' % (folder, re.sub(r'[\\/:*?"<>|]', '_', co.DOCS['attestation_inscription'][0]), re.sub(r'[\\/:*?"<>|]', '_', c['denomination']))
    dav.put_file(path, raw, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    desk.db.execute('INSERT OR IGNORE INTO deliverables5683 VALUES(?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, dos['id'], 'attestation_inscription', 1, path, dos['operation'], '', 'projet', _now()))
    return path


def mark_signed(desk, data, owner='cabinet'):
    """Un document signé reste en place : aucune régénération ne l'écrase ni ne le périme (UX01)."""
    dos = _row(desk, data.get('id'))
    cur = desk.db.execute("UPDATE deliverables5683 SET state='signe' WHERE id=? AND dossier=? AND state IN ('projet','perime')", (str(data.get('document') or ''), dos['id']))
    desk.db.commit()
    return {'id': dos['id'], 'message': 'Document marqué signé.' if cur.rowcount else 'Document déjà marqué signé.'}


def abandon(desk, data, owner='cabinet'):
    dos = _row(desk, data.get('id'))
    if dos['state'] == 'realisee':
        raise Stop('operation_deja_close')
    if dos['operation']:
        prior = desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (dos['operation'],)).fetchone()
        if prior and prior['state'] not in ('abandonnee', 'realisee'):
            co.abandon(desk, {'id': dos['operation']}, owner)
    dos['state'] = 'abandonnee'
    _save(desk, dos)
    desk.db.commit()
    return {'id': dos['id'], 'message': 'Opération abandonnée : aucun registre modifié.'}


def for_matter(desk, mid):
    schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT d.id, d.branch, d.state, d.company, c.denomination FROM operation_dossiers5683 d JOIN companies5636 c ON c.id=d.company '
                                             'WHERE d.matter=? ORDER BY d.created DESC, d.id', (str(mid),))]


def of_company(desk, cid):
    schema(desk)
    return [{**dict(r), 'data': json.loads(r['data'] or '{}')} for r in desk.db.execute('SELECT * FROM operation_dossiers5683 WHERE company=? ORDER BY created DESC, id', (cid,))]


# ------------------------------------------------------------------------------------------------ affichage
SUBVIEWS = (('dossier', 'Dossier de l’opération'), ('etapes', 'Étapes'), ('documents', 'Documents'), ('formalites', 'Formalités'))
STATUS_EVIDENCE = {'propose': 'Proposée', 'confirme': 'Confirmée', 'contradictoire': 'Contradictoire', 'ecarte': 'Écartée', 'manquant': 'Manquante'}
CONTROL_STATES = {'requis': 'Requis', 'non_requis': 'Non requis', 'a_confirmer': 'À confirmer'}


def _hidden(dos, *extra):
    return '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">%s' % (e(dos['id'], quote=True), dos['version'], ''.join(extra))


def _input(desk, dos, key, label, typ):
    value = str(dos['data'].get(key, '') or '')
    if typ == 'holder':
        options = [('', 'Choisir…')] + [(r['contact'] or r['name'], '%s — %d titres (%s)' % (r['name'], r['quantity'], r['category'])) for r in co.holdings(desk, dos['company'])[0]]
        return _select(key, label, options, value)
    if typ.startswith('select:'):
        return _select(key, label, CHOICES[typ[7:]], value)
    kinds = {'date': 'date', 'number': 'text" inputmode="decimal'}
    hint = ' placeholder="/Clients/…/Statuts.pdf"' if typ == 'path' else ''
    return '<label>%s<input type="%s" name="%s" value="%s"%s></label>' % (e(label), kinds.get(typ, 'text'), key, e(value, quote=True), hint)


def _select(key, label, options, value):
    return '<label>%s<select name="%s">%s</select></label>' % (e(label), key, ''.join(
        '<option value="%s"%s>%s</option>' % (e(v, quote=True), ' selected' if v == value else '', e(t)) for v, t in options))


def _synthesis(desk, dos, step):
    ready = [s['label'] for s in steps_of(desk, dos['id']) if s['state'] == 'fait']
    lacks = missing(dos)
    return ('<section class="ax-card o83-next"><h3>Prochaine action</h3><p><strong>%s</strong>%s</p><p class="ax-muted">Prêt : %d étape(s) sur %d. %s</p>%s</section>') % (
        e(step['label'] if step else 'Aucune : opération suivie jusqu’au bout.'), (' — ' + e(step['note'])) if step and step['note'] else '', len(ready), len(STEPS),
        e('Manque : %s.' % ', '.join(lacks[:6])) if lacks else 'Données obligatoires renseignées.', _next_form(dos, step))


def _next_form(dos, step):
    if not step or dos['state'] == 'abandonnee' or step['id'] == 'realisation':
        return ''
    confirm = ('<label class="e5636-switch"><input type="checkbox" name="confirmer" value="1"> Je valide cette étape</label>' if step['authorization'] == 'avocat' else '')
    return '<form class="m5-form m5-inline" data-api="m5683/preparer" data-reload="1">%s%s<button class="ax-btn" type="submit">Préparer la suite</button></form>' % (_hidden(dos), confirm)


def _dossier_view(desk, dos):
    html = ''
    wrong = reconcile(desk, dos)
    if wrong:
        html += ('<section class="ax-card c5637-alert" role="alert"><h3>Anomalies à résoudre</h3><p>Une différence entre statuts, tableau et registre bloque la finalisation. Rien n’est corrigé '
                 'automatiquement.</p><ul>%s</ul></section>') % ''.join('<li>%s : registre %s, %s %s</li>' % (e(w['libelle']), e(str(w['registre'])), e(w['origine']), e(str(w['source']))) for w in wrong)
    html += '<section class="ax-card"><h3>Fiche</h3><form class="m5-form o83-fiche" data-api="m5683/fiche" data-reload="1">%s%s<label class="e5636-switch"><input type="checkbox" name="confirmer" value="1"> ' \
            'J’ai vu les conséquences sur les documents</label><button class="ax-btn ghost" type="submit">Modifier</button></form></section>' % (
                _hidden(dos), ''.join(_input(desk, dos, k, lab, typ) for k, lab, typ, _ in FICHE) + _extra_fields(dos))
    im = impact(desk, dos)
    html += ('<section class="ax-card"><h3>Situation avant et après</h3><div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Titulaire</th><th>Catégorie</th><th>Avant</th><th>%% avant</th>'
             '<th>Après</th><th>%% après</th></tr></thead><tbody>%s</tbody></table></div><p class="ax-muted">Total : %d titres. Droits de vote : %s.</p></section>') % (
        ''.join('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s %%</td><td>%d</td><td>%s %%</td></tr>' % (e(x['titulaire']), e(x['categorie']), x['avant'], x['pct_avant'], x['apres'], x['pct_apres']) for x in im['lignes'])
        or '<tr><td colspan="6" class="ax-muted">Aucun titre inscrit.</td></tr>', im['total'], e(im['droits_vote']))
    html += _controls_view(desk, dos) + _taxes_view(desk, dos) + _evidence_view(desk, dos)
    return html


def _extra_fields(dos):
    d = dos['data']
    out = ''.join(_select('ctrl_' + k, label, (('', 'À confirmer'), ('requis', 'Requis'), ('non_requis', 'Non requis')), str(d.get('ctrl_' + k) or '')) for k, label, _ in CONTROLS)
    if dos['branch'] == 'parts':
        out += ('<label>Associés à notifier (nombre)<input name="associes_a_notifier" inputmode="numeric" value="%s"></label><label>Dates des notifications établies<input name="notifications" '
                'placeholder="AAAA-MM-JJ ; AAAA-MM-JJ" value="%s"></label>') % (e(str(d.get('associes_a_notifier') or ''), quote=True), e(str(d.get('notifications') or ''), quote=True))
    if dos['perimetre'] == 'limite':
        out += '<label class="e5636-switch"><input type="checkbox" name="preparation_manuelle" value="1"%s> Préparation manuelle assistée : je choisis et relis chaque document</label>' % (
            ' checked' if d.get('preparation_manuelle') else '')
    return out


def _controls_view(desk, dos):
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(c['label']), e(CONTROL_STATES.get(c['statut'], c['statut'])), e(c['source'])) for c in controls(desk, dos))
    dl = ''.join('<li>%s : %s <small>(%s, %s)</small></li>' % (e(x['label']), e(x['date'] or 'À confirmer'), e(x['source']), e(x['nature'])) for x in deadlines(desk, dos))
    return ('<section class="ax-card"><h3>Conditions et délais</h3><div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Contrôle</th><th>État</th><th>Source</th></tr></thead><tbody>%s</tbody>'
            '</table></div>%s</section>') % (rows, '<ul>%s</ul>' % dl if dl else '')


def _taxes_view(desk, dos):
    t = taxes(desk, dos)
    if t['etat'] == 'a_confirmer':
        return '<section class="ax-card"><h3>Droits d’enregistrement</h3><p><strong>À confirmer.</strong> Manque : %s.</p></section>' % e(', '.join(t['manque']))
    rows = ''.join('<tr><td>%s</td><td>%s</td></tr>' % (e(a), e(b)) for a, b in t['composantes'])
    return ('<section class="ax-card"><h3>Droits d’enregistrement</h3><div class="o5636-scroll"><table class="o5636-grid"><tbody>%s<tr><th>Droits (arrondis)</th><th>%s €</th></tr><tr><td>Montant exact</td><td>%s €</td></tr>'
            '</tbody></table></div><p class="ax-muted">%s %s Décompte déterministe, règles à faire valider par un avocat : %s.</p></section>') % (
        rows, e(t['droits']), e(t['droits_exacts']), e(' '.join(t['hypotheses'])), e(t.get('minimum', '')), e(' ; '.join(t.get('sources') or [])))


def _evidence_view(desk, dos):
    rows = desk.db.execute("SELECT * FROM field_evidence5683 WHERE dossier=? AND origin<>'saisie' ORDER BY at DESC, id LIMIT 200", (dos['id'],)).fetchall()
    lines = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['field']), e(r['value']), e('%s %s' % (r['origin'], r['excerpt'])), e(STATUS_EVIDENCE.get(r['status'], r['status'])), ''.join(
        '<form class="m5-form m5-inline" data-api="m5683/donnee" data-reload="1">%s<button class="ax-btn ghost" type="submit">%s</button></form>' % (
            _hidden(dos, '<input type="hidden" name="champ" value="%s"><input type="hidden" name="statut" value="%s">' % (e(r['id'], quote=True), s)), t)
        for s, t in (('confirme', 'Confirmer'), ('ecarte', 'Écarter')) if r['status'] in ('propose', 'contradictoire'))) for r in rows)
    return ('<section class="ax-card"><h3>Données extraites</h3>%s<form class="m5-form m5-inline" data-api="m5683/excel" data-reload="1">%s<label>Tableau Excel du dossier<input name="chemin" '
            'placeholder="/Clients/…/Détention.xlsx" required></label><button class="ax-btn ghost" type="submit">Lire le tableau</button></form></section>') % (
        ('<div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Donnée</th><th>Valeur</th><th>Source</th><th>État</th><th></th></tr></thead><tbody>%s</tbody></table></div>' % lines) if lines else
        '<p class="ax-muted">Aucune donnée extraite.</p>', _hidden(dos))


def _steps_view(desk, dos):
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(s['label']), e(STEP_STATES.get(s['state'], s['state'])), e(AUTH[s['authorization']]), e(', '.join(STEP_LABEL[x] for x in s['depends']) or '—'), e(s['note'] or s['owner'] or '')) for s in steps_of(desk, dos['id']))
    dates = dos['data'].get('dates') or {}
    tl = ''.join('<li>%s : %s</li>' % (e(label), e(dates.get(key) or 'non renseignée')) for key, label in DATES)
    events = ''.join('<li>%s — %s</li>' % (e(ev['day'] or ev['at']), e(ev['kind'])) for ev in events_of(desk, dos['id'])[:40])
    return ('<section class="ax-card"><h3>Étapes</h3><div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Étape</th><th>État</th><th>Autorisation</th><th>Dépend de</th><th>Note</th></tr></thead>'
            '<tbody>%s</tbody></table></div></section><section class="ax-card"><h3>Dates distinctes</h3><ul>%s</ul>%s%s</section>%s') % (
        rows, tl, _proof_form(dos), _realize_form(dos), ('<section class="ax-card"><h3>Journal</h3><ul>%s</ul></section>' % events) if events else '')


def _proof_form(dos):
    if dos['state'] != 'en_cours':
        return ''
    options = (('signature_constatee', 'Signature'), ('levee_conditions', 'Levée des conditions'), ('ordre_signe', 'Ordre signé'), ('inscription', 'Inscription en compte'), ('enregistrement', 'Enregistrement fiscal'))
    return ('<details><summary>Ajouter un justificatif</summary><form class="m5-form" data-api="m5683/justificatif" data-reload="1">%s%s<label>Date<input type="date" name="date" required></label>'
            '<label>Pièce dans le dossier<input name="piece" required placeholder="/Clients/…/Acte signé.pdf"></label><button class="ax-btn ghost" type="submit">Ajouter un justificatif</button></form></details>') % (
        _hidden(dos), _select('type', 'Nature', options, 'signature_constatee'))


def _realize_form(dos):
    if dos['state'] != 'en_cours' or not dos['operation']:
        return ''
    keep = dos['data'].get('dates') or {}
    keys = ('date_signature', 'date_ordre', 'date_inscription', 'date_jouissance') if dos['branch'] == 'actions' else ('date_signature', 'date_realisation', 'date_jouissance')
    fields = ''.join('<label>%s<input type="date" name="%s" value="%s"></label>' % (e(dict(DATES)[k]), k, e(keep.get(k, ''), quote=True)) for k in keys)
    return ('<details><summary>Constater la réalisation</summary><form class="m5-form" data-api="m5683/realiser" data-reload="1" data-confirm="Inscrire la réalisation dans les registres ?">%s%s'
            '<label>Justificatif<input name="justificatif" required placeholder="Acte signé le…"></label><label class="e5636-switch"><input type="checkbox" name="actes_externes" value="1"> Actes établis hors '
            'd’AxiorHub</label><label>Pièce signée dans le dossier<input name="piece" placeholder="/Clients/…/Acte signé.pdf"></label><button class="ax-btn" type="submit">Enregistrer la réalisation</button>'
            '</form></details>') % (_hidden(dos), fields)


def _documents_view(desk, prefix, dos):
    sync_deliverables(desk, dos)
    rows = desk.db.execute('SELECT * FROM deliverables5683 WHERE dossier=? ORDER BY doc, version DESC', (dos['id'],)).fetchall()
    labels = {'projet': 'Projet', 'perime': 'Périmé : à régénérer', 'signe': 'Signé'}
    lines = ''.join('<tr><td>%s</td><td>v%d</td><td>%s</td><td><code>%s</code></td><td>%s</td></tr>' % (
        e(_doc_label(r['doc'])), r['version'], e(labels.get(r['state'], r['state'])), e(r['path'].rsplit('/', 1)[-1]),
        '' if r['state'] == 'signe' else '<form class="m5-form m5-inline" data-api="m5683/signe" data-reload="1">%s<button class="ax-btn ghost" type="submit">Marquer signé</button></form>' % _hidden(
            dos, '<input type="hidden" name="document" value="%s">' % e(r['id'], quote=True))) for r in rows)
    planned = ', '.join(_doc_label(x) for x in documents_for(desk, dos))
    return ('<section class="ax-card"><h3>Documents</h3><p class="ax-muted">Prévus pour cette branche : %s. Modèles génériques à faire valider par un avocat.</p>%s</section>') % (
        e(planned), ('<div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Document</th><th>Version</th><th>État</th><th>Fichier</th><th></th></tr></thead><tbody>%s</tbody></table></div>' % lines)
        if lines else '<p class="ax-muted">Aucun document généré : les registres ne changent pas à cette étape.</p>')


def _filings_view(desk, dos):
    rows = desk.db.execute('SELECT * FROM filings5683 WHERE dossier=? ORDER BY rowid', (dos['id'],)).fetchall()
    cards = ''
    for r in rows:
        moves = ''.join('<form class="m5-form m5-inline" data-api="m5683/formalite" data-reload="1">%s%s<button class="ax-btn ghost" type="submit">%s</button></form>' % (
            _hidden(dos, '<input type="hidden" name="formalite" value="%s"><input type="hidden" name="statut" value="%s">' % (e(r['id'], quote=True), t)),
            '<label>Récépissé dans le dossier<input name="piece" required></label>' if t in FILING_NEEDS_PIECE else '', e(FILING_LABEL[t])) for t in FILING_NEXT.get(r['statut'], ()))
        cards += '<li class="o83-filing"><strong>%s</strong> <span class="ax-muted">%s — %s%s</span>%s</li>' % (
            e(r['label']), e(r['destinataire']), e(FILING_LABEL[r['statut']]), (' — récépissé : ' + e(r['recepisse'].rsplit('/', 1)[-1])) if r['recepisse'] else '', moves)
    return ('<section class="ax-card"><h3>Formalités</h3><p class="ax-muted">Transmission à la main avec rattachement du récépissé : aucun dépôt automatique n’est annoncé.</p>%s</section>' % (
        ('<ul class="o83-filings">%s</ul>' % cards) if cards else '<p class="ax-muted">Les formalités s’ouvrent après la réalisation justifiée.</p>'))


def page_html(desk, prefix, c, args):
    schema(desk)
    branch, perimetre = branch_of(c['forme'])
    rows = of_company(desk, c['id'])
    did = str(args.get('dossier') or (rows[0]['id'] if rows else ''))
    link = prefix + '/corporate?societe=%s&vue=cessions' % c['id']
    lines = ''.join('<li><a%s href="%s">%s → %s <small>%s</small></a></li>' % (
        ' aria-current="page"' if r['id'] == did else '', e(link + '&dossier=' + r['id'], quote=True), e(r['data'].get('cedant') or 'Cédant à choisir'), e(r['data'].get('cessionnaire_nom') or 'acquéreur à préciser'),
        e({'en_cours': 'en cours', 'realisee': 'réalisée', 'abandonnee': 'abandonnée'}.get(r['state'], r['state']))) for r in rows)
    scope = ('' if perimetre == 'livre' else '<p class="c5637-alert o83-scope" role="status"><strong>Périmètre limité.</strong> Les règles propres à la forme %s ne sont pas livrées : préparation manuelle '
             'assistée, sans conformité annoncée.</p>' % e(c['forme']))
    head = ('<section class="ax-card"><h3>Cessions de %s</h3>%s<ul class="o83-list">%s</ul><form class="m5-form m5-inline" data-api="m5683/ouvrir" data-reload="1"><input type="hidden" name="company" value="%s">'
            '<input type="hidden" name="idem" value="%s"><button class="ax-btn" type="submit">Nouvelle opération</button></form></section>') % (
        'parts sociales' if branch == 'parts' else 'actions', scope, lines or '<li class="ax-muted">Aucune opération.</li>', e(c['id'], quote=True), uuid.uuid4().hex)
    if not did:
        return head
    dos = _row(desk, did)
    if dos['company'] != c['id']:
        raise Stop('operation_inconnue')
    sync_deliverables(desk, dos)
    sub = str(args.get('sous') or 'dossier')
    sub = sub if sub in dict(SUBVIEWS) else 'dossier'
    step = next_step(desk, dos['id'])
    tabs = ''.join('<a href="%s"%s>%s</a>' % (e(link + '&dossier=%s&sous=%s' % (dos['id'], k), quote=True), ' aria-current="page"' if k == sub else '', e(v)) for k, v in SUBVIEWS)
    body = _dossier_view(desk, dos) if sub == 'dossier' else _steps_view(desk, dos) if sub == 'etapes' else _documents_view(desk, prefix, dos) if sub == 'documents' else _filings_view(desk, dos)
    title = '<h3 class="o83-title">%s → %s <small>%s</small></h3>' % (e(dos['data'].get('cedant') or 'Cédant à choisir'), e(dos['data'].get('cessionnaire_nom') or 'acquéreur à préciser'), e(c['denomination']))
    end = ('<form class="m5-form m5-inline" data-api="m5683/abandonner" data-reload="1" data-confirm="Abandonner cette opération ? Aucun registre ne sera modifié.">%s<button class="ax-btn ghost" type="submit">Abandonner</button></form>' % _hidden(dos)
           if dos['state'] == 'en_cours' else '')
    return head + title + _synthesis(desk, dos, step) + '<nav class="o83-tabs" aria-label="Vues de l’opération">%s</nav>' % tabs + body + end


def api_post(desk, route, data, owner, role, client=None):
    if route == 'ouvrir':
        return open_cession(desk, data, owner)
    if route == 'fiche':
        return update_fiche(desk, data, owner)
    if route == 'donnee':
        return set_evidence(desk, data, owner)
    if route == 'excel':
        return read_excel(desk, data, owner, client)
    if route == 'preparer':
        return prepare_next(desk, data, owner, role, client)
    if route == 'justificatif':
        return add_proof(desk, data, owner, client)
    if route == 'realiser':
        return realize(desk, data, owner, client)
    if route == 'rectifier':
        return rectify(desk, data, owner)
    if route == 'formalite':
        return filing_update(desk, data, owner, role, client)
    if route == 'signe':
        return mark_signed(desk, data, owner)
    if route == 'abandonner':
        return abandon(desk, data, owner)
    raise Stop('operation_absente')
