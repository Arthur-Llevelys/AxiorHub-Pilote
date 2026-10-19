"""5.6.51 (audit 5.6.50, Dossier-D04 à D09) : modèle d'information du dossier.

Entités séparées, toutes rattachées à leur dossier, versionnées, avec leur provenance :
- **parties** canoniques et leurs **alias** (mentions d'origine conservées) ; rapprochement automatique seulement d'une même identité
  (« Mme Alice DUPONT » / « Madame Alice Dupont ») ; candidats à confirmer sinon (prénom manquant, forme sociale différente) ; jamais
  deux civilités différentes, jamais une personne et une société ; fusion réversible et décisions de séparation durables ;
- **procédures** (juridiction, chambre, RG, nature, stade, procédure de référence, statut active / historique / citée) et participations ;
- **documents logiques** : nature (assignation, conclusions, jugement, contrat, facture, courriel, jurisprudence…), partie émettrice,
  procédure, état de lecture et catégorie d'erreur, usage comme pièce **indépendant** de la nature ;
- **informations** (faits, demandes, montants, arguments) : proposition complète, auteur de l'affirmation, date et précision, sources
  (pièce, version, page, extrait), pertinence, **qualification probatoire** et **état de revue** séparés ;
- **actes de procédure** (dates de l'acte, de signification, de dépôt, de réception séparées) et **décisions judiciaires** (original
  examiné ou seulement mentionnée).

La **reconstruction** (``rebuild``) lit les registres existants — index des pièces, relevés de pièces contrôlés (5.6.32/5.6.34), fiche de
travail (5.6.27), corrections de l'avocat (5.6.44), mémoire du dossier — sans appel au modèle ; elle conserve toute décision humaine
(classement, fusion, enregistrement, refus) et ne déplace, ne renomme ni ne supprime aucun original.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re

from .common import Stop, fold, load_matters

SCHEMA = '''
CREATE TABLE IF NOT EXISTS d51_parties(id TEXT PRIMARY KEY, matter TEXT NOT NULL, kind TEXT NOT NULL, display_name TEXT NOT NULL,
  gender TEXT NOT NULL DEFAULT '', forme TEXT NOT NULL DEFAULT '', core TEXT NOT NULL, client INTEGER NOT NULL DEFAULT 0,
  counsel TEXT NOT NULL DEFAULT '', identifiers TEXT NOT NULL DEFAULT '{}', origin TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  merged_into TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS d51_parties_matter ON d51_parties(matter, status);
CREATE TABLE IF NOT EXISTS d51_aliases(party_id TEXT NOT NULL, matter TEXT NOT NULL, alias TEXT NOT NULL, normalized TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', via TEXT NOT NULL DEFAULT '', PRIMARY KEY(party_id, normalized, source));
CREATE TABLE IF NOT EXISTS d51_party_candidates(matter TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL, reason TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'propose', updated TEXT NOT NULL, PRIMARY KEY(matter, a, b));
CREATE TABLE IF NOT EXISTS d51_party_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, matter TEXT NOT NULL, kind TEXT NOT NULL,
  a TEXT NOT NULL, b TEXT NOT NULL, author TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, reverted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS d51_procedures(id TEXT PRIMARY KEY, matter TEXT NOT NULL, juridiction TEXT NOT NULL DEFAULT '',
  chambre TEXT NOT NULL DEFAULT '', rg TEXT NOT NULL DEFAULT '', nature TEXT NOT NULL DEFAULT '', stade TEXT NOT NULL DEFAULT '',
  parent_id TEXT NOT NULL DEFAULT '', statut TEXT NOT NULL DEFAULT 'active', reference INTEGER NOT NULL DEFAULT 0,
  origin TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]', review TEXT NOT NULL DEFAULT 'propose', version INTEGER NOT NULL DEFAULT 1,
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d51_participations(procedure_id TEXT NOT NULL, party_id TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
  counsel TEXT NOT NULL DEFAULT '', PRIMARY KEY(procedure_id, party_id));
CREATE TABLE IF NOT EXISTS d51_documents(matter TEXT NOT NULL, path TEXT NOT NULL, etag TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
  nature TEXT NOT NULL DEFAULT 'a_classer', nature_source TEXT NOT NULL DEFAULT 'regle', emitter TEXT NOT NULL DEFAULT '',
  procedure_id TEXT NOT NULL DEFAULT '', usage_piece TEXT NOT NULL DEFAULT 'inconnu', piece_party TEXT NOT NULL DEFAULT '',
  piece_number TEXT NOT NULL DEFAULT '', reading TEXT NOT NULL DEFAULT 'inconnu', error_category TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '', doc_date TEXT NOT NULL DEFAULT '', modified TEXT NOT NULL DEFAULT '', text_sha TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  updated TEXT NOT NULL, PRIMARY KEY(matter, path));
CREATE TABLE IF NOT EXISTS d51_assertions(id TEXT PRIMARY KEY, matter TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT 'document', author_label TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
  date_value TEXT NOT NULL DEFAULT '', date_precision TEXT NOT NULL DEFAULT 'inconnue', amount TEXT NOT NULL DEFAULT '',
  currency TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]', relevance TEXT NOT NULL DEFAULT 'a_examiner',
  proof TEXT NOT NULL DEFAULT 'allegue', review TEXT NOT NULL DEFAULT 'propose', review_by TEXT NOT NULL DEFAULT '',
  review_at TEXT NOT NULL DEFAULT '', review_motif TEXT NOT NULL DEFAULT '', signature TEXT NOT NULL, origin TEXT NOT NULL,
  procedure_id TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS d51_assertions_matter ON d51_assertions(matter, kind, review);
CREATE UNIQUE INDEX IF NOT EXISTS d51_assertions_signature ON d51_assertions(matter, signature);
CREATE TABLE IF NOT EXISTS d51_events(id TEXT PRIMARY KEY, matter TEXT NOT NULL, procedure_id TEXT NOT NULL DEFAULT '',
  nature TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
  dates TEXT NOT NULL DEFAULT '{}', date_precision TEXT NOT NULL DEFAULT 'inconnue', status TEXT NOT NULL DEFAULT 'rapporte',
  sources TEXT NOT NULL DEFAULT '[]', review TEXT NOT NULL DEFAULT 'propose', signature TEXT NOT NULL, origin TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS d51_events_signature ON d51_events(matter, signature);
CREATE TABLE IF NOT EXISTS d51_judgments(id TEXT PRIMARY KEY, matter TEXT NOT NULL, procedure_id TEXT NOT NULL DEFAULT '',
  juridiction TEXT NOT NULL DEFAULT '', date TEXT NOT NULL DEFAULT '', nature TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
  examined INTEGER NOT NULL DEFAULT 0, portee TEXT NOT NULL DEFAULT '', reserves TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]',
  review TEXT NOT NULL DEFAULT 'propose', signature TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS d51_judgments_signature ON d51_judgments(matter, signature);
CREATE TABLE IF NOT EXISTS d51_review_log(id INTEGER PRIMARY KEY AUTOINCREMENT, matter TEXT NOT NULL, object_kind TEXT NOT NULL,
  object_id TEXT NOT NULL, action TEXT NOT NULL, author TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', before TEXT NOT NULL DEFAULT '',
  after TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d51_rebuilds(matter TEXT PRIMARY KEY, at TEXT NOT NULL, counters TEXT NOT NULL DEFAULT '{}', revision TEXT NOT NULL DEFAULT '');
'''

NATURES = {
    'assignation': 'Assignation / acte introductif', 'requete': 'Requête', 'conclusions': 'Conclusions', 'jugement': 'Jugement',
    'ordonnance': 'Ordonnance', 'arret': 'Arrêt', 'rpva': 'Message RPVA / e-Barreau', 'signification': 'Acte de signification',
    'contrat': 'Contrat / bail / statuts', 'facture': 'Facture / relevé', 'constat': 'Constat / procès-verbal', 'expertise': 'Rapport d’expertise',
    'courrier': 'Courrier / lettre', 'mise_en_demeure': 'Mise en demeure', 'courriel': 'Courriel', 'photographie': 'Photographie',
    'attestation': 'Attestation', 'jurisprudence': 'Jurisprudence / doctrine', 'bordereau': 'Bordereau de pièces', 'note_interne': 'Note / projet interne',
    'archive': 'Archive (ZIP)', 'gestion': 'Gestion du cabinet (honoraires, comptabilité)', 'a_classer': 'À classer'}
PROCEDURE_NATURES = {'assignation', 'requete', 'conclusions', 'jugement', 'ordonnance', 'arret', 'rpva', 'signification'}
DECISION_NATURES = {'jugement', 'ordonnance', 'arret'}
EVIDENCE_NATURES = {'contrat', 'facture', 'constat', 'expertise', 'courrier', 'mise_en_demeure', 'photographie', 'attestation'}
# Règles de nature : nom de fichier puis début du texte ; le nom n'est qu'un indice, une correction de l'avocat prime toujours.
NATURE_RULES = (
    ('bordereau', r'bordereau'), ('jurisprudence', r'(^|[\s_-])(jp|jurisprudence|doctrine)([\s_.-]|$)|cass(ation)?[\s_.-]|legifrance|judilibre'),
    ('rpva', r'rpva|e.?barreau|accuse de reception electronique|avis de reception rpva'),
    ('signification', r'signification|proces.verbal de signification|acte de commissaire de justice'),
    ('assignation', r'assignation|acte introductif|citation a comparaitre'), ('requete', r'\brequete\b'),
    ('conclusions', r'conclusions?'), ('arret', r'\barret\b'), ('ordonnance', r'\bordonnance\b'), ('jugement', r'\bjugement\b'),
    ('mise_en_demeure', r'mise en demeure|sommation'), ('constat', r'\bconstat|proces.verbal'), ('expertise', r'expertise|rapport d.expert'),
    ('attestation', r'attestation'), ('facture', r'facture|releve de compte|decompte|avoir\b|devis'),
    ('contrat', r'contrat|\bbail\b|statuts|protocole|convention|conditions generales|cgv|avenant'),
    ('courriel', r'\.eml$|courriel|e.?mail'), ('courrier', r'courrier|lettre'), ('photographie', r'\.(jpe?g|png|heic)$|photo'),
    ('note_interne', r'\b(note|projet|brouillon|memo|draft)\b'), ('archive', r'\.(zip|7z|rar)$'))
CIVILITES = {'madame': 'f', 'mme': 'f', 'mademoiselle': 'f', 'mlle': 'f', 'monsieur': 'm', 'm': 'm', 'mr': 'm', 'messieurs': 'm',
             'maitre': '', 'me': '', 'dr': '', 'docteur': '', 'pr': ''}
FORMES = ('sa', 'sas', 'sasu', 'sarl', 'eurl', 'sci', 'snc', 'selarl', 'selas', 'scp', 'gie', 'scm', 'scop', 'association', 'fondation',
          'gaec', 'earl', 'gmbh', 'ltd', 'llc', 'inc', 'sasu', 'sem', 'spl')
COMPANY_WORDS = ('societe', 'ste', 'compagnie', 'cie', 'groupe', 'syndicat', 'copropriete', 'banque', 'mutuelle', 'office', 'commune', 'ville')
NOT_PARTY = re.compile(r'\b(juge|president|greffi|commissaire de justice|huissier|expert judiciaire|conseiller|procureur|tribunal|cour)\b')
REVIEW = {'propose': 'Proposée', 'enregistre': 'Enregistrée', 'refuse': 'Refusée', 'hors_sujet': 'Hors sujet pour ce dossier',
          'a_verifier': 'À vérifier', 'fusionne': 'Fusionnée'}
PROOF = {'allegue': 'Allégué', 'conteste': 'Contesté', 'corrobore': 'Corroboré', 'constate': 'Constaté dans une pièce',
         'retenu_decision': 'Retenu par une décision identifiée', 'a_verifier': 'À vérifier'}
KINDS = {'fait': 'Fait', 'demande': 'Demande / prétention', 'argument': 'Argument', 'montant': 'Montant'}
METADATA = re.compile(r'^\W*(rg|n[°o]\s*rg|numero de rg|portalis|tel|telephone|t[ée]l\.?|fax|adresse|siret|siren|rcs|courriel|e-?mail)\b|@[\w-]+\.\w|'
                      r'^\W*(?:n[°o]\s*)?\d{2}/\d{4,6}\W*$')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:24]


MIGRATIONS = (('d51_assertions', 'validity', "TEXT NOT NULL DEFAULT 'courante'"),
              ('d51_assertions', 'validity_note', "TEXT NOT NULL DEFAULT ''"),
              ('d51_rebuilds', 'state', "TEXT NOT NULL DEFAULT 'ok'"),
              ('d51_rebuilds', 'error', "TEXT NOT NULL DEFAULT ''"))
VALIDITY = {'courante': 'Courante', 'historique': 'Historique (source remplacée ou retirée)',
            'a_recontroler': 'Enregistrée, source remplacée : à recontrôler'}
def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    changed = False
    for table, column, decl in MIGRATIONS:   # 5.6.52 : colonnes ajoutées aux bases 5.6.51
        cols = {r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % table)}
        if column not in cols:
            desk.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, column, decl))
            changed = True
    if changed:
        desk.db.commit()


def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
    if not m:
        raise Stop('dossier_absent')
    return m


def _changed(desk, mid, object_kind, ident, version, title, detail=None, dependents=True):
    """A03 / D07 : toute décision humaine produit un événement métier ; les projets qui en dépendent sont signalés à recontrôler."""
    from . import memoire5651
    memoire5651.record_change(desk, mid, 'correction' if object_kind != 'document' else 'classement', object_kind, ident, version, title, detail, commit=False)
    if dependents:
        memoire5651.mark_dependents(desk, mid, title, paths=[ident] if object_kind == 'document' else None, commit=False)


def _log(desk, mid, kind, ident, action, author, motif='', before=None, after=None):
    desk.db.execute('INSERT INTO d51_review_log(matter,object_kind,object_id,action,author,motif,before,after,at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (mid, kind, str(ident), action, author, str(motif or '')[:500], json.dumps(before or {}, ensure_ascii=False, default=str)[:4000],
                     json.dumps(after or {}, ensure_ascii=False, default=str)[:4000], _now()))


# ------------------------------------------------------------------------------------------------ identités (D05)
def identity(name):
    """Clé d'identité d'une mention : nature (personne / société), civilité (genre), forme sociale, noyau normalisé."""
    raw = re.sub(r'\s+', ' ', str(name or '')).strip()
    head = re.split(r'\s+[(\[]|,\s*(?=\d)|,\s*(?:demeurant|domicili|sis|si[eè]ge|n[ée]e?\s)', raw, maxsplit=1)[0]
    tokens = [t for t in re.split(r'[^\w]+', fold(head)) if t]
    gender, forme, company, counsel = '', '', False, False
    while tokens and tokens[0] in CIVILITES:
        counsel = counsel or tokens[0] in ('maitre', 'me')
        gender = CIVILITES[tokens.pop(0)] or gender
    kept = []
    for t in tokens:
        if t in FORMES:
            forme, company = forme or t, True
        elif t in COMPANY_WORDS:
            company = True
        else:
            kept.append(t)
    if any(t in ('les', 'des', 'et', 'de', 'du') for t in kept) and len(kept) > 3:
        company = company or not gender
    # sans civilité ni marque de société, la nature reste inconnue et l'ordre des mots compte (« Alpha Fictive » / « Fictive Alpha »)
    kind = 'societe' if company else ('personne' if gender else 'inconnu')
    core = ' '.join(sorted(kept)) if kind == 'personne' else ' '.join(kept)
    return {'kind': kind, 'gender': gender, 'forme': forme, 'core': core, 'tokens': kept, 'display': head.strip(' ,;'), 'counsel': counsel}


def compare(a, b):
    """'meme' (même identité : rapprochement automatique), 'candidat' (à confirmer, avec motif) ou ''."""
    if not a['core'] or not b['core']:
        return '', ''
    if 'personne' in (a['kind'], b['kind']) and 'societe' in (a['kind'], b['kind']):
        return '', ''                                                   # jamais une personne et une société
    if a['gender'] and b['gender'] and a['gender'] != b['gender']:
        return '', ''                                                   # jamais « Monsieur » et « Madame »
    same_words = sorted(a['tokens']) == sorted(b['tokens'])
    if a['kind'] == 'societe' or b['kind'] == 'societe':
        if a['core'] == b['core'] and a['kind'] == b['kind']:
            if a['forme'] == b['forme']:
                return 'meme', 'même dénomination et même forme sociale'
            return 'candidat', 'même dénomination, forme sociale différente'
        if same_words:   # jamais réunis d'office
            if a['tokens'] == b['tokens']:
                return 'candidat', 'même nom, forme sociale absente de l’une des mentions'
            return 'candidat', 'mêmes mots dans un ordre différent'
        return '', ''
    if a['kind'] == b['kind'] == 'personne' and a['core'] == b['core']:
        return 'meme', 'même nom et même prénom (civilité abrégée ou casse différente)'
    if 'inconnu' in (a['kind'], b['kind']) and same_words:
        if a['kind'] == b['kind'] == 'inconnu' and a['tokens'] == b['tokens']:
            return 'meme', 'même mention (casse ou ponctuation différente)'
        if a['kind'] != b['kind'] and ' '.join(a['tokens']) == ' '.join(b['tokens']):
            return 'meme', 'même nom, civilité absente de l’une des mentions'
        return 'candidat', 'mêmes mots dans un ordre différent'
    small, big = sorted((set(a['tokens']), set(b['tokens'])), key=len)
    if small and small < big and len(small) >= 1 and len(big) - len(small) <= 2:
        return 'candidat', 'prénom ou nom manquant dans l’une des mentions'
    return '', ''


def _norm_alias(name):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w]+', ' ', fold(str(name or '')))).strip()


def _add_alias(desk, pid, mid, alias, source, via=''):
    desk.db.execute('INSERT OR IGNORE INTO d51_aliases(party_id,matter,alias,normalized,source,via) VALUES(?,?,?,?,?,?)',
                    (pid, mid, str(alias)[:200], _norm_alias(alias), str(source)[:200], via))


def parties(desk, mid, include_merged=False):
    ensure_schema(desk)
    rows = desk.db.execute('SELECT * FROM d51_parties WHERE matter=?' + ('' if include_merged else " AND status='active'") +
                           ' ORDER BY client DESC, display_name', (mid,)).fetchall()
    out = []
    for r in rows:
        p = dict(r)
        p['aliases'] = [dict(a) for a in desk.db.execute('SELECT alias,source,via FROM d51_aliases WHERE party_id=? ORDER BY alias', (r['id'],))]
        p['identifiers'] = json.loads(p['identifiers'] or '{}')
        p['roles'] = [dict(x) for x in desk.db.execute('SELECT p.procedure_id,p.role,p.counsel FROM d51_participations p WHERE p.party_id=?', (r['id'],))]
        out.append(p)
    return out


def _party_for(desk, mid, name, origin, source, client=False, counsel='', role='', procedure_id=''):
    """Rattache une mention à une partie existante (même identité) ou crée la partie ; propose les rapprochements incertains."""
    ident = identity(name)
    if not ident['core'] or ident['counsel'] or NOT_PARTY.search(fold(name)):
        return None
    rows = desk.db.execute('SELECT * FROM d51_parties WHERE matter=?', (mid,)).fetchall()
    for r in rows:
        verdict, _ = compare(ident, {'kind': r['kind'], 'gender': r['gender'], 'forme': r['forme'], 'core': r['core'], 'tokens': r['core'].split()})
        if verdict != 'meme':
            continue
        target = r
        while target['status'] == 'fusionnee' and target['merged_into']:
            target = desk.db.execute('SELECT * FROM d51_parties WHERE id=?', (target['merged_into'],)).fetchone() or target
            if target['status'] != 'fusionnee':
                break
        if target['status'] == 'retiree':
            return target['id']                                            # partie retirée par l'avocat : jamais recréée
        _add_alias(desk, target['id'], mid, name, source)
        updates = []
        if client and not target['client']:
            updates.append(('client', 1))
        if counsel and not target['counsel']:
            updates.append(('counsel', counsel[:200]))
        for col, val in updates:
            desk.db.execute('UPDATE d51_parties SET %s=?,updated=? WHERE id=?' % col, (val, _now(), target['id']))
        if procedure_id and role:
            desk.db.execute('INSERT OR IGNORE INTO d51_participations(procedure_id,party_id,role,counsel) VALUES(?,?,?,?)',
                            (procedure_id, target['id'], role, counsel[:200]))
        return target['id']
    pid = _sha(mid, 'partie', ident['kind'], ident['core'], ident['forme'], ident['gender'])
    now = _now()
    desk.db.execute('INSERT OR IGNORE INTO d51_parties(id,matter,kind,display_name,gender,forme,core,client,counsel,origin,created,updated) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (pid, mid, ident['kind'], ident['display'][:200] or str(name)[:200], ident['gender'],
                                                      ident['forme'], ident['core'], 1 if client else 0, counsel[:200], origin, now, now))
    _add_alias(desk, pid, mid, name, source)
    if procedure_id and role:
        desk.db.execute('INSERT OR IGNORE INTO d51_participations(procedure_id,party_id,role,counsel) VALUES(?,?,?,?)', (procedure_id, pid, role, counsel[:200]))
    for r in rows:
        verdict, reason = compare(ident, {'kind': r['kind'], 'gender': r['gender'], 'forme': r['forme'], 'core': r['core'], 'tokens': r['core'].split()})
        if verdict == 'candidat' and r['status'] == 'active':
            a, b = sorted((pid, r['id']))
            separated = desk.db.execute("SELECT 1 FROM d51_party_decisions WHERE matter=? AND kind='separation' AND a=? AND b=? AND reverted=0",
                                        (mid, a, b)).fetchone()
            if not separated:
                desk.db.execute('INSERT OR IGNORE INTO d51_party_candidates(matter,a,b,reason,state,updated) VALUES(?,?,?,?,?,?)',
                                (mid, a, b, reason, 'propose', now))
    return pid


def candidates(desk, mid):
    ensure_schema(desk)
    out = []
    for r in desk.db.execute("SELECT * FROM d51_party_candidates WHERE matter=? AND state='propose'", (mid,)):
        a = desk.db.execute("SELECT * FROM d51_parties WHERE id=? AND status='active'", (r['a'],)).fetchone()
        b = desk.db.execute("SELECT * FROM d51_parties WHERE id=? AND status='active'", (r['b'],)).fetchone()
        if a and b:
            out.append({'a': dict(a), 'b': dict(b), 'reason': r['reason']})
    return out


def merge(desk, mid, keep, other, author, motif=''):
    """Fusion réversible : l'autre partie devient un alias de la partie gardée ; liens et sources suivent ; décision journalisée."""
    ensure_schema(desk)
    a = desk.db.execute("SELECT * FROM d51_parties WHERE id=? AND matter=? AND status='active'", (keep, mid)).fetchone()
    b = desk.db.execute("SELECT * FROM d51_parties WHERE id=? AND matter=? AND status='active'", (other, mid)).fetchone()
    if not a or not b or keep == other:
        raise Stop('partie_absente')
    if 'personne' in (a['kind'], b['kind']) and 'societe' in (a['kind'], b['kind']):
        raise Stop('fusion_personne_societe_refusee')
    cur = desk.db.execute("INSERT INTO d51_party_decisions(matter,kind,a,b,author,motif,at) VALUES(?,?,?,?,?,?,?)", (mid, 'fusion', keep, other, author, motif, _now()))
    via = 'fusion:%d' % cur.lastrowid
    for al in desk.db.execute('SELECT alias,source FROM d51_aliases WHERE party_id=?', (other,)).fetchall():
        _add_alias(desk, keep, mid, al['alias'], al['source'], via)
    _add_alias(desk, keep, mid, b['display_name'], 'fusion', via)
    for p in desk.db.execute('SELECT * FROM d51_participations WHERE party_id=?', (other,)).fetchall():
        desk.db.execute('INSERT OR IGNORE INTO d51_participations(procedure_id,party_id,role,counsel) VALUES(?,?,?,?)', (p['procedure_id'], keep, p['role'], p['counsel']))
    desk.db.execute("UPDATE d51_parties SET status='fusionnee',merged_into=?,version=version+1,updated=? WHERE id=?", (keep, _now(), other))
    desk.db.execute("UPDATE d51_parties SET client=MAX(client,?),version=version+1,updated=? WHERE id=?", (b['client'], _now(), keep))
    desk.db.execute("UPDATE d51_assertions SET author=?,updated=? WHERE matter=? AND author=?", (keep, _now(), mid, other))
    desk.db.execute("UPDATE d51_party_candidates SET state='fusionne',updated=? WHERE matter=? AND ((a=? AND b=?) OR (a=? AND b=?))",
                    (_now(), mid, keep, other, other, keep))
    _log(desk, mid, 'partie', other, 'fusion', author, motif, {'dans': keep})
    _changed(desk, mid, 'partie', other, 'fusion:%d' % cur.lastrowid, 'Parties réunies : « %s » rattachée à « %s »' % (b['display_name'], a['display_name']))
    desk.db.commit()
    return {'decision': cur.lastrowid}


def separate(desk, mid, a, b, author, motif=''):
    """Refus durable d'un rapprochement (homonymes, époux, société et dirigeant) : jamais reproposé."""
    ensure_schema(desk)
    x, y = sorted((a, b))
    desk.db.execute("INSERT INTO d51_party_decisions(matter,kind,a,b,author,motif,at) VALUES(?,?,?,?,?,?,?)", (mid, 'separation', x, y, author, motif, _now()))
    desk.db.execute("UPDATE d51_party_candidates SET state='separe',updated=? WHERE matter=? AND a=? AND b=?", (_now(), mid, x, y))
    _log(desk, mid, 'partie', x + '|' + y, 'separation', author, motif)
    desk.db.commit()


def undo_merge(desk, mid, decision_id, author):
    ensure_schema(desk)
    d = desk.db.execute("SELECT * FROM d51_party_decisions WHERE id=? AND matter=? AND kind='fusion' AND reverted=0", (int(decision_id), mid)).fetchone()
    if not d:
        raise Stop('decision_absente')
    desk.db.execute("DELETE FROM d51_aliases WHERE party_id=? AND via=?", (d['a'], 'fusion:%d' % d['id']))
    desk.db.execute("UPDATE d51_parties SET status='active',merged_into='',version=version+1,updated=? WHERE id=?", (_now(), d['b']))
    desk.db.execute("UPDATE d51_party_decisions SET reverted=1 WHERE id=?", (d['id'],))
    _log(desk, mid, 'partie', d['b'], 'annulation_fusion', author)
    _changed(desk, mid, 'partie', d['b'], 'annulation:%d' % d['id'], 'Fusion de parties annulée')
    desk.db.commit()


def set_party(desk, mid, pid, author, data):
    """Correction de l'avocat : nom affiché, client du cabinet, conseil, retrait (la mention d'origine reste en alias)."""
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM d51_parties WHERE id=? AND matter=?', (pid, mid)).fetchone()
    if not row:
        raise Stop('partie_absente')
    if 'version' in data and int(data['version']) != row['version']:
        raise Stop('version_en_conflit')
    changes = {}
    if data.get('display_name'):
        name = re.sub(r'\s+', ' ', str(data['display_name'])).strip()[:200]
        if not name:
            raise Stop('nom_partie_requis')
        _add_alias(desk, pid, mid, row['display_name'], 'avant correction')
        changes['display_name'] = name
    if 'client' in data:
        changes['client'] = 1 if data['client'] is True else 0
    if 'counsel' in data:
        changes['counsel'] = str(data['counsel'] or '')[:200]
    if data.get('retirer') is True:
        changes['status'] = 'retiree'
    if not changes:
        return dict(row)
    sets = ','.join('%s=?' % k for k in changes)
    desk.db.execute('UPDATE d51_parties SET %s,origin=?,version=version+1,updated=? WHERE id=?' % sets, (*changes.values(), 'avocat', _now(), pid))
    _log(desk, mid, 'partie', pid, 'correction', author, '', {k: row[k] for k in changes}, changes)
    _changed(desk, mid, 'partie', pid, row['version'] + 1, 'Partie corrigée : ' + str(changes.get('display_name') or row['display_name']))
    desk.db.commit()
    return dict(desk.db.execute('SELECT * FROM d51_parties WHERE id=?', (pid,)).fetchone())


def add_party(desk, mid, author, name, client=False, counsel=''):
    ensure_schema(desk)
    pid = _party_for(desk, mid, name, 'avocat', 'saisie de l’avocat', client=client, counsel=counsel)
    if not pid:
        raise Stop('nom_partie_invalide')
    desk.db.execute("UPDATE d51_parties SET status='active',origin='avocat' WHERE id=?", (pid,))
    _log(desk, mid, 'partie', pid, 'ajout', author)
    desk.db.commit()
    return pid


# ------------------------------------------------------------------------------------------------ documents (D08, D11)
ERROR_CATEGORIES = (('scan', r'scan|ocr|sans_texte|page_pdf'), ('protege', r'chiffr|protege|encrypt|password|mot de passe'),
                    ('format', r'format|non_pris|unsupported|extension|office_non_lisible'), ('archive', r'archive|zip|decompress'),
                    ('volume', r'trop_long|trop_volumineu|trop_grosse'), ('outil', r'extracteur|delai|temps_extraction|poppler|tesseract'),
                    ('acces', r'http|fichier_|introuvable|acces|connexion'))
ERROR_LABELS = {'scan': 'PDF scanné / OCR insuffisant', 'protege': 'Fichier chiffré ou protégé', 'format': 'Format non pris en charge',
                'archive': 'Archive non extraite', 'volume': 'Document trop volumineux', 'outil': 'Outil d’extraction absent ou délai dépassé',
                'acces': 'Erreur d’accès au fichier', 'autre': 'Autre erreur de lecture'}
READING = {'lisible': 'Lu', 'partielle': 'Lu partiellement', 'erreur': 'Erreur de lecture', 'vide': 'Sans texte', 'protegee': 'Protégé',
           'non_prise_en_charge': 'Format non pris en charge', 'inconnu': 'Pas encore lu'}


def classify_name(path, text=''):
    """Nature par règles (nom puis début du texte) ; « a_classer » si rien de sûr."""
    name = fold(PurePosixPath(path).name)
    folders = fold(str(PurePosixPath(path).parent))
    if re.search(r'comptabilit|honoraires|facturation', folders) or re.search(r'honoraires|^facture|^avoir|^devis', name):
        return 'gestion'                                                   # gestion du cabinet : jamais une pièce par défaut
    for nature, pattern in NATURE_RULES:
        if re.search(pattern, name):
            return nature
    head = fold(str(text or '')[:1500])
    for nature, pattern in NATURE_RULES:
        if nature in ('courriel', 'photographie', 'archive', 'note_interne'):
            continue
        if re.search(r'^\W*(?:\S+\s+){0,12}' + pattern, head):
            return nature
    return 'a_classer'


def error_category(error):
    err = fold(str(error or ''))
    if not err:
        return ''
    for key, pattern in ERROR_CATEGORIES:
        if re.search(pattern, err):
            return key
    return 'autre'


def documents(desk, mid, nature=None, usage=None, limit=None, offset=0):
    ensure_schema(desk)
    sql, params = 'SELECT * FROM d51_documents WHERE matter=?', [mid]
    if nature:
        sql += ' AND nature=?'
        params.append(nature)
    if usage:
        sql += ' AND usage_piece=?'
        params.append(usage)
    sql += ' ORDER BY doc_date DESC, name'
    if limit:
        sql += ' LIMIT ? OFFSET ?'
        params += [int(limit), int(offset)]
    return [dict(r) for r in desk.db.execute(sql, params)]


def classify(desk, mid, path, author, nature=None, piece=None, piece_party=None, piece_number=None, procedure_id=None, version=None):
    """Classement par l'avocat (prioritaire sur les règles et les rescans) : nature, usage comme pièce, partie productrice, numéro."""
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM d51_documents WHERE matter=? AND path=?', (mid, path)).fetchone()
    if not row:
        raise Stop('document_absent')
    if version is not None and int(version) != row['version']:
        raise Stop('version_en_conflit')
    changes = {}
    if nature is not None:
        if nature not in NATURES:
            raise Stop('nature_invalide')
        changes.update(nature=nature, nature_source='avocat')
    if piece is not None:
        changes['usage_piece'] = 'oui' if piece else 'non'
    if piece_party is not None:
        changes['piece_party'] = str(piece_party)[:60]
    if piece_number is not None:
        changes['piece_number'] = str(piece_number)[:20]
    if procedure_id is not None:
        changes['procedure_id'] = str(procedure_id)[:40]
    if not changes:
        return dict(row)
    sets = ','.join('%s=?' % k for k in changes)
    desk.db.execute('UPDATE d51_documents SET %s,version=version+1,updated=? WHERE matter=? AND path=?' % sets, (*changes.values(), _now(), mid, path))
    _log(desk, mid, 'document', path, 'classement', author, '', {k: row[k] for k in changes}, changes)
    _changed(desk, mid, 'document', path, row['version'] + 1, 'Document classé : %s (%s)' % (row['name'], NATURES.get(changes.get('nature', row['nature']), '')),
             dependents=False)   # D07 : un classement ne déclare aucune communication et ne renumérote rien
    desk.db.commit()
    return dict(desk.db.execute('SELECT * FROM d51_documents WHERE matter=? AND path=?', (mid, path)).fetchone())


def pieces(desk, mid):
    """Pièces : seulement les documents dont l'usage comme pièce est établi (bordereau, communication, choix de l'avocat) ou probable
    (pièce probatoire non encore numérotée, « à numéroter »). Les actes, conclusions, notes et jurisprudences n'y sont pas par défaut."""
    ensure_schema(desk)
    out = {'nos_pieces': [], 'adverses': {}, 'a_numeroter': [], 'a_classer': []}
    for d in documents(desk, mid):
        if d['usage_piece'] == 'non':
            continue
        if d['usage_piece'] == 'oui' or d['piece_number']:
            if d['piece_party'] and d['piece_party'] != 'cabinet':
                out['adverses'].setdefault(d['piece_party'], []).append(d)
            else:
                out['nos_pieces'].append(d)
        elif d['nature'] in EVIDENCE_NATURES:
            out['a_numeroter'].append(d)
        elif d['nature'] == 'a_classer':
            out['a_classer'].append(d)
    key = lambda d: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', d['piece_number'] or '~' + d['name'])]
    out['nos_pieces'].sort(key=key)
    for v in out['adverses'].values():
        v.sort(key=key)
    return out


# ------------------------------------------------------------------------------------------------ procédures (D09)
def procedures(desk, mid):
    ensure_schema(desk)
    rows = [dict(r) for r in desk.db.execute("SELECT * FROM d51_procedures WHERE matter=? ORDER BY reference DESC, statut='active' DESC, updated DESC", (mid,))]
    for p in rows:
        p['sources'] = json.loads(p['sources'] or '[]')
        p['parties'] = [dict(x) for x in desk.db.execute('SELECT pa.role,pa.counsel,p.display_name,p.id FROM d51_participations pa JOIN d51_parties p '
                                                         "ON p.id=pa.party_id WHERE pa.procedure_id=? AND p.status='active'", (p['id'],))]
    return rows


def reference_procedure(desk, mid):
    rows = procedures(desk, mid)
    return next((p for p in rows if p['reference']), next((p for p in rows if p['statut'] == 'active'), None))


def save_procedure(desk, mid, author, data):
    """Création ou correction par l'avocat ; « reference » désigne la procédure de référence (une seule)."""
    ensure_schema(desk)
    fields = {k: re.sub(r'\s+', ' ', str(data.get(k) or '')).strip()[:200] for k in ('juridiction', 'chambre', 'rg', 'nature', 'stade')}
    statut = str(data.get('statut') or 'active')
    if statut not in ('active', 'historique', 'citee'):
        raise Stop('statut_procedure_invalide')
    pid = str(data.get('id') or '')
    now = _now()
    if pid:
        row = desk.db.execute('SELECT * FROM d51_procedures WHERE id=? AND matter=?', (pid, mid)).fetchone()
        if not row:
            raise Stop('procedure_absente')
        if 'version' in data and int(data['version']) != row['version']:
            raise Stop('version_en_conflit')
        desk.db.execute('UPDATE d51_procedures SET juridiction=?,chambre=?,rg=?,nature=?,stade=?,statut=?,origin=?,review=?,version=version+1,updated=? WHERE id=?',
                        (*fields.values(), statut, 'avocat', 'enregistre', now, pid))
    else:
        if not (fields['juridiction'] or fields['rg']):
            raise Stop('procedure_vide')
        pid = _sha(mid, 'procedure', fold(fields['juridiction']), fields['rg'], now)
        desk.db.execute('INSERT INTO d51_procedures(id,matter,juridiction,chambre,rg,nature,stade,statut,origin,review,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (pid, mid, *fields.values(), statut, 'avocat', 'enregistre', now, now))
    if data.get('reference') is True:
        desk.db.execute('UPDATE d51_procedures SET reference=CASE WHEN id=? THEN 1 ELSE 0 END WHERE matter=?', (pid, mid))
    _log(desk, mid, 'procedure', pid, 'enregistrement', author, '', {}, {**fields, 'statut': statut})
    _changed(desk, mid, 'procedure', pid, now, 'Procédure enregistrée : %s %s' % (fields['juridiction'], fields['rg']))
    desk.db.commit()
    return pid


# ------------------------------------------------------------------------------------------------ informations (D06, D07)
def assertions(desk, mid, kind=None, reviews=None, limit=None, current_only=False):
    ensure_schema(desk)
    sql, params = 'SELECT * FROM d51_assertions WHERE matter=?', [mid]
    if current_only:
        sql += " AND validity<>'historique'"
    if kind:
        sql += ' AND kind=?'
        params.append(kind)
    if reviews:
        sql += ' AND review IN (%s)' % ','.join('?' * len(reviews))
        params += list(reviews)
    sql += " ORDER BY CASE WHEN date_value='' THEN 1 ELSE 0 END, date_value, created"
    if limit:
        sql += ' LIMIT ?'
        params.append(int(limit))
    rows = [dict(r) for r in desk.db.execute(sql, params)]
    for r in rows:
        r['sources'] = json.loads(r['sources'] or '[]')
    return rows


def review(desk, mid, ident, author, action, data=None):
    """Décision typée sur une information (D07) : enregistrer, corriger, hors sujet, à vérifier, refuser, fusionner avec ; la version
    attendue est contrôlée ; l'enregistrement ne rend pas un fait « établi » : la qualification probatoire est un choix distinct."""
    ensure_schema(desk)
    data = data or {}
    row = desk.db.execute('SELECT * FROM d51_assertions WHERE id=? AND matter=?', (ident, mid)).fetchone()
    if not row:
        raise Stop('information_absente')
    if 'version' in data and data['version'] not in (None, '') and int(data['version']) != row['version']:
        raise Stop('version_en_conflit')
    changes = {'review_by': author, 'review_at': _now(), 'review_motif': str(data.get('motif') or '')[:500]}
    if action == 'enregistrer':
        changes['review'] = 'enregistre'
        changes['relevance'] = 'pertinent'
    elif action == 'hors_sujet':
        changes.update(review='hors_sujet', relevance='hors_sujet')
    elif action == 'a_verifier':
        changes['review'] = 'a_verifier'
    elif action == 'refuser':
        changes['review'] = 'refuse'
    elif action == 'corriger':
        text = re.sub(r'\s+', ' ', str(data.get('texte') or '')).strip()
        if not 3 <= len(text) <= 2000:
            raise Stop('texte_information_invalide')
        changes.update(text=text, review='enregistre', relevance='pertinent', origin='avocat')
    elif action == 'fusionner':
        other = str(data.get('avec') or '')
        target = desk.db.execute('SELECT * FROM d51_assertions WHERE id=? AND matter=?', (other, mid)).fetchone()
        if not target or other == ident:
            raise Stop('information_absente')
        merged = json.loads(target['sources'] or '[]') + [s for s in json.loads(row['sources'] or '[]') if s not in json.loads(target['sources'] or '[]')]
        desk.db.execute('UPDATE d51_assertions SET sources=?,version=version+1,updated=? WHERE id=?', (json.dumps(merged, ensure_ascii=False), _now(), other))
        changes['review'] = 'fusionne'
    else:
        raise Stop('action_information_invalide')
    if data.get('proof') is not None:
        if data['proof'] not in PROOF:
            raise Stop('qualification_invalide')
        changes['proof'] = data['proof']
    if data.get('date') is not None:
        from .preuve5634 import normalize_date
        raw = str(data['date']).strip()
        if raw:
            d = normalize_date(raw)
            if not d['valide']:
                raise Stop('date_information_invalide')
            changes.update(date_value=d['iso'], date_precision=d['precision'] or 'jour')
        else:
            changes.update(date_value='', date_precision='inconnue')
    sets = ','.join('%s=?' % k for k in changes)
    desk.db.execute('UPDATE d51_assertions SET %s,version=version+1,updated=? WHERE id=?' % sets, (*changes.values(), _now(), ident))
    _log(desk, mid, 'information', ident, action, author, data.get('motif') or '', {'review': row['review'], 'text': row['text']},
         {k: v for k, v in changes.items() if k not in ('review_by', 'review_at')})
    labels = {'enregistrer': 'Information enregistrée', 'corriger': 'Information corrigée', 'hors_sujet': 'Information hors sujet',
              'a_verifier': 'Information à vérifier', 'refuser': 'Information refusée', 'fusionner': 'Informations réunies'}
    _changed(desk, mid, 'information', ident, row['version'] + 1, '%s : %s' % (labels[action], (changes.get('text') or row['text'])[:120]))
    desk.db.commit()
    return dict(desk.db.execute('SELECT * FROM d51_assertions WHERE id=?', (ident,)).fetchone())


def add_assertion(desk, mid, author, data):
    """Saisie de l'avocat : identifiée comme telle (origine « avocat ») ; sans source, elle reste une saisie, pas une preuve."""
    ensure_schema(desk)
    kind = str(data.get('kind') or 'fait')
    if kind not in KINDS:
        raise Stop('type_information_invalide')
    text = re.sub(r'\s+', ' ', str(data.get('texte') or '')).strip()
    if not 3 <= len(text) <= 2000:
        raise Stop('texte_information_invalide')
    from .preuve5634 import normalize_date
    d = normalize_date(str(data.get('date') or '')) if data.get('date') else {'valide': True, 'iso': '', 'precision': ''}
    if not d['valide']:
        raise Stop('date_information_invalide')
    proof = str(data.get('proof') or 'a_verifier')
    if proof not in PROOF:
        raise Stop('qualification_invalide')
    now = _now()
    ident = _sha(mid, 'avocat', kind, text, now)
    desk.db.execute('INSERT INTO d51_assertions(id,matter,kind,text,author,author_label,date_value,date_precision,sources,relevance,proof,review,review_by,'
                    'review_at,signature,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, mid, kind, text, str(data.get('author') or 'cabinet'), str(data.get('author_label') or 'Saisie de l’avocat'), d['iso'],
                     d['precision'] or ('jour' if d['iso'] else 'inconnue'), '[]', 'pertinent', proof, 'enregistre', author, now, ident, 'avocat', now, now))
    _log(desk, mid, 'information', ident, 'ajout', author)
    desk.db.commit()
    return ident


# ------------------------------------------------------------------------------------------------ reconstruction (D12)
def _author_of(desk, mid, path, nature, emitters):
    """Auteur d'une affirmation : la partie émettrice du document (conclusions, assignation) si elle est connue, sinon le document."""
    pid = emitters.get(path)
    if pid:
        row = desk.db.execute('SELECT display_name FROM d51_parties WHERE id=?', (pid,)).fetchone()
        return pid, (row['display_name'] if row else '')
    return 'document', NATURES.get(nature, 'Document')


def _upsert_assertion(desk, mid, kind, text, origin, sources, author='document', author_label='', date='', precision='', amount='', currency='',
                      relevance='a_examiner', proof='allegue', review_state='propose', topic='', procedure_id=''):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()[:2000]
    if len(text) < 4:
        return None
    sig = _sha(kind, re.sub(r'[^\w]+', ' ', fold(text)).strip(), date or '', amount or '')
    row = desk.db.execute('SELECT * FROM d51_assertions WHERE matter=? AND signature=?', (mid, sig)).fetchone()
    now = _now()
    if row:
        if row['review'] in ('refuse', 'hors_sujet'):
            return row['id']                                               # refus durable : jamais reproposé
        known = json.loads(row['sources'] or '[]')
        added = [s for s in sources if s not in known]
        if added:
            desk.db.execute('UPDATE d51_assertions SET sources=?,updated=? WHERE id=?', (json.dumps(known + added, ensure_ascii=False), now, row['id']))
        return row['id']
    ident = _sha(mid, 'information', sig)
    desk.db.execute('INSERT OR IGNORE INTO d51_assertions(id,matter,kind,text,author,author_label,topic,date_value,date_precision,amount,currency,sources,'
                    'relevance,proof,review,signature,origin,procedure_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, mid, kind, text, author, author_label, topic, date or '', precision or ('jour' if date else 'inconnue'), amount, currency,
                     json.dumps(sources, ensure_ascii=False), relevance, proof, review_state, sig, origin, procedure_id, now, now))
    return ident


def _upsert_event(desk, mid, nature, title, origin, sources, dates=None, precision='', author='', description='', status='rapporte', procedure_id=''):
    sig = _sha(nature, re.sub(r'[^\w]+', ' ', fold(title)).strip(), json.dumps(dates or {}, sort_keys=True), (sources[0].get('path') if sources else ''))
    row = desk.db.execute('SELECT id FROM d51_events WHERE matter=? AND signature=?', (mid, sig)).fetchone()
    if row:
        return row['id']
    ident = _sha(mid, 'acte', sig)
    now = _now()
    desk.db.execute('INSERT OR IGNORE INTO d51_events(id,matter,procedure_id,nature,title,description,author,dates,date_precision,status,sources,signature,origin,created,updated) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, mid, procedure_id, nature, title[:300], description[:1000], author,
                                                              json.dumps(dates or {}, ensure_ascii=False), precision or 'inconnue', status,
                                                              json.dumps(sources, ensure_ascii=False), sig, origin, now, now))
    return ident


EVENT_WORDS = (('signification', r'signifi'), ('audience', r'audience|renvoi|plaidoirie'), ('depot', r'depos|deposees|remis au greffe|enrol'),
               ('jugement', r'jugement|ordonnance|arret|decision rendue|a condamne|a deboute'), ('assignation', r'assign|cite a comparaitre'),
               ('conclusions', r'conclusions'), ('appel', r'interjete appel|declaration d.appel'), ('mise_en_demeure', r'mis[e]? en demeure|sommation'))


def _revision(desk, mid, inv, digests):
    """Empreinte de tout ce que lit la reconstruction : un dossier inchangé n'est jamais réanalysé (A03, pas de boucle)."""
    parts = sorted(d['path'] + d['etag'] + d['sha256'] + d['state'] for d in inv) + sorted(k + v['sha'] + v['status'] for k, v in digests.items())
    for sql, empty in (("SELECT COALESCE(MAX(version),0) FROM fiche5627 WHERE matter=?", [0]),
                       ("SELECT COUNT(*),COALESCE(MAX(id),0) FROM corrections5644 WHERE matter=?", [0, 0]),
                       ("SELECT COUNT(*),COALESCE(MAX(updated),'') FROM legal_memory_records WHERE matter=?", [0, '']),
                       ("SELECT COUNT(*) FROM pieces520_comms WHERE matter=?", [0]),
                       ("SELECT COUNT(*),COALESCE(MAX(updated),'') FROM pieces510_drafts WHERE matter=?", [0, ''])):
        try:
            parts.append(json.dumps(list(desk.db.execute(sql, (mid,)).fetchone())))
        except Exception:   # noqa: BLE001 — table absente : même empreinte qu'une table vide
            parts.append(json.dumps(empty))
    return _sha(*parts)


def rebuild(desk, mid, force=False):
    """Reconstruction idempotente à partir des registres existants (aucun appel au modèle) ; décisions humaines conservées."""
    m = _matter(desk, mid)
    ensure_schema(desk)
    from . import memoire5651
    memoire5651.ensure_schema(desk)
    counters = {'documents': 0, 'parties': 0, 'procedures': 0, 'informations': 0, 'actes': 0, 'decisions': 0}
    now = _now()
    previous = last_rebuild(desk, mid)
    from . import synthese5632
    try:
        inv = synthese5632.inventory(desk, mid)
    except Stop:
        inv = []
    digests = {}
    try:
        synthese5632.schema(desk)
        current_etag = {d['path']: d['etag'] for d in inv}
        for r in desk.db.execute("SELECT path,text_sha,file_version,digest,status FROM piece_digests5634 WHERE matter=? ORDER BY created, rowid", (mid,)):
            # 5.6.52 (F03) : relevé courant = celui de la version indexée ; à défaut (version non renseignée), le plus récent
            known = digests.get(r['path'])
            if known and known['etag'] == current_etag.get(r['path']) and r['file_version'] != current_etag.get(r['path']):
                continue
            digests[r['path']] = {'sha': r['text_sha'], 'etag': r['file_version'], 'data': json.loads(r['digest'] or '{}'), 'status': r['status']}
    except Exception:   # noqa: BLE001
        pass
    revision = _revision(desk, mid, inv, digests)
    if previous and previous['revision'] == revision and not force:
        return {**previous['counters'], 'inchange': True}
    first = previous is None
    # 1. Documents : nature (règles, relevé, décision de l'avocat), état de lecture, catégorie d'erreur
    from .memoire5651 import not_a_document
    junk = [d['path'] for d in inv if not_a_document(PurePosixPath(d['path']).name)]
    if junk:   # 5.6.64 : fichiers temporaires, verrous, raccourcis : retirés de l'inventaire du dossier (ils comptaient « en erreur »)
        desk.db.executemany('DELETE FROM d51_documents WHERE matter=? AND path=?', [(mid, p) for p in junk])
    inv = [d for d in inv if d['path'] not in set(junk)]
    for d in inv:
        nature = classify_name(d['path'], d['text'])
        dig = (digests.get(d['path']) or {}).get('data') or {}
        if nature == 'a_classer' and dig.get('nature'):
            nature = classify_name('x', dig['nature']) if classify_name('x', dig['nature']) != 'a_classer' else 'a_classer'
        reading = {'extrait': 'lisible', 'vide': 'vide', 'protegee': 'protegee', 'non_prise_en_charge': 'non_prise_en_charge'}.get(d['state'], 'erreur')
        if reading == 'lisible' and digests.get(d['path']) and digests[d['path']]['status'] != 'complet':
            reading = 'partielle'
        date = ''
        if dig.get('date_document'):
            from .preuve5634 import normalize_date
            nd = normalize_date(dig['date_document'])
            date = nd['iso'] if nd['valide'] else ''
        row = desk.db.execute('SELECT * FROM d51_documents WHERE matter=? AND path=?', (mid, d['path'])).fetchone()
        name = PurePosixPath(d['path']).name
        if row and row['etag'] != d['etag']:
            # A03 : nouvelle version ; une version au texte identique (mise en forme, métadonnées) reste une opération technique
            same_text = row['text_sha'] == d['sha256']
            memoire5651.record_change(desk, mid, 'nouvelle_version', 'document', d['path'], d['etag'] + ':' + d['sha256'][:12],
                                      ('Nouvelle version sans changement de texte : ' if same_text else 'Nouvelle version : ') + name,
                                      {'path': d['path'], 'avant': row['etag'], 'apres': d['etag']}, technical=same_text, commit=False)
            if not same_text:
                memoire5651.mark_dependents(desk, mid, 'nouvelle version de « %s »' % name, paths=[d['path']], commit=False)
        elif not row:
            memoire5651.record_change(desk, mid, 'nouveau_document', 'document', d['path'], d['etag'], 'Nouveau document : ' + name,
                                      {'path': d['path']}, technical=first, commit=False)   # import initial : journal technique seulement
        if row:
            sets = {'etag': d['etag'], 'reading': reading, 'error_category': error_category(d['error']) if reading == 'erreur' or d['error'] else '',
                    'error': d['error'], 'modified': d['modified'], 'doc_date': row['doc_date'] or date, 'text_sha': d['sha256']}
            if row['nature_source'] != 'avocat':
                sets['nature'] = nature
            desk.db.execute('UPDATE d51_documents SET %s,updated=? WHERE matter=? AND path=?' % ','.join('%s=?' % k for k in sets), (*sets.values(), now, mid, d['path']))
        else:
            desk.db.execute('INSERT INTO d51_documents(matter,path,etag,name,nature,nature_source,reading,error_category,error,doc_date,modified,text_sha,updated) '
                            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (mid, d['path'], d['etag'], name, nature, 'regle', reading,
                                                                 error_category(d['error']) if d['error'] else '', d['error'], date, d['modified'], d['sha256'], now))
        counters['documents'] += 1
        if counters['documents'] % COMMIT_EVERY == 0:
            desk.db.commit()                                         # 5.6.55 : verrou d'écriture relâché par lots
    # usage comme pièce : bordereaux et communications existants (5.1.0 / 5.2.0) ; jamais déduit du seul nom de dossier
    try:
        from . import pieces510, pieces520
        pieces510.ensure_schema(desk)
        pieces520.ensure_schema(desk)
        for c in desk.db.execute('SELECT path,number FROM pieces520_comms WHERE matter=?', (mid,)):
            desk.db.execute("UPDATE d51_documents SET usage_piece='oui',piece_party=CASE WHEN piece_party='' THEN 'cabinet' ELSE piece_party END,"
                            "piece_number=CASE WHEN piece_number='' THEN ? ELSE piece_number END WHERE matter=? AND path=? AND usage_piece<>'non'",
                            (str(c['number']), mid, c['path']))
        for dr in desk.db.execute("SELECT data FROM pieces510_drafts WHERE matter=? AND status='cree' ORDER BY updated", (mid,)):
            data = json.loads(dr['data'] or '{}')
            included = [r for r in data.get('rows') or [] if r.get('include') and r.get('path')]
            for n, r in enumerate(included, 1):
                r.setdefault('number', n)
                if True:
                    desk.db.execute("UPDATE d51_documents SET usage_piece=CASE WHEN usage_piece='non' THEN 'non' ELSE 'oui' END,"
                                    "piece_party=CASE WHEN piece_party='' THEN 'cabinet' ELSE piece_party END,"
                                    "piece_number=CASE WHEN piece_number='' THEN ? ELSE piece_number END WHERE matter=? AND path=?",
                                    (str(r.get('number') or ''), mid, r['path']))
    except Exception:   # noqa: BLE001 — bordereaux absents : aucun usage déduit
        pass
    # 2. Procédure de référence : fiche de travail, corrections de l'avocat (RG, juridiction), mémoire validée
    from . import dossier5627, dossier5644
    fiche = dossier5627.latest(desk, mid)
    fdata = (fiche or {}).get('data') or {}
    proc = fdata.get('procedure') or {}
    juri = dossier5644.single(desk, mid, 'jurisdiction', {'value': proc.get('juridiction'), 'title': proc.get('juridiction'), 'status': 'derived'}
                              if proc.get('juridiction') else None)
    rg = dossier5644.single(desk, mid, 'case_number', {'value': proc.get('numero_rg'), 'title': proc.get('numero_rg'), 'status': 'derived'}
                            if proc.get('numero_rg') else None)
    ref_id = ''
    if juri or rg:
        existing = desk.db.execute("SELECT * FROM d51_procedures WHERE matter=? AND origin<>'avocat' AND reference=1", (mid,)).fetchone()
        avocat = desk.db.execute("SELECT id FROM d51_procedures WHERE matter=? AND origin='avocat' AND reference=1", (mid,)).fetchone()
        values = ((juri or {}).get('value') or '', (rg or {}).get('value') or '', proc.get('stade') or '')
        if avocat:
            ref_id = avocat['id']                                          # procédure de référence choisie par l'avocat : jamais écrasée
        elif existing:
            ref_id = existing['id']
            desk.db.execute('UPDATE d51_procedures SET juridiction=?,rg=?,stade=?,updated=? WHERE id=?', (*values, now, ref_id))
        else:
            ref_id = _sha(mid, 'procedure', 'reference')
            desk.db.execute("INSERT OR IGNORE INTO d51_procedures(id,matter,juridiction,rg,stade,statut,reference,origin,review,sources,created,updated) "
                            "VALUES(?,?,?,?,?,'active',1,'fiche',?,?,?,?)", (ref_id, mid, *values, 'enregistre' if (juri or {}).get('validated') or (rg or {}).get('validated') else 'propose',
                                                                            json.dumps([{'label': 'Fiche de travail' if fiche else 'Correction de l’avocat'}], ensure_ascii=False), now, now))
            counters['procedures'] += 1
    # 3. Parties : configuration, fiche de travail, corrections, relevés (alias seulement)
    if m.get('client_name'):
        _party_for(desk, mid, m['client_name'], 'configuration', 'configuration du dossier', client=True)
    for p in fdata.get('parties') or []:
        role = p.get('qualite') or ''
        _party_for(desk, mid, p.get('nom', ''), 'fiche', 'fiche de travail v%s' % (fiche or {}).get('version', ''), client=bool(p.get('client_du_cabinet')),
                   counsel=str(p.get('conseil') or ''), role=role if role and role != 'autre' else '', procedure_id=ref_id)
    for c in dossier5644.corrections(desk, mid, 'parties'):
        if c['action'] in ('add', 'replace'):
            pid = _party_for(desk, mid, c['value'], 'avocat', 'correction de l’avocat')
            if pid:
                desk.db.execute("UPDATE d51_parties SET origin='avocat' WHERE id=?", (pid,))
        if c['action'] in ('hide', 'replace'):
            ident = identity(c['label'])
            for r in desk.db.execute("SELECT id,core,kind FROM d51_parties WHERE matter=? AND status='active' AND origin<>'avocat'", (mid,)).fetchall():
                if r['core'] == ident['core'] and r['kind'] == ident['kind']:
                    desk.db.execute("UPDATE d51_parties SET status='retiree',updated=? WHERE id=?", (now, r['id']))
    natures = {d['path']: d['nature'] for d in desk.db.execute('SELECT path,nature FROM d51_documents WHERE matter=?', (mid,))}
    for path, dg in digests.items():
        nature = natures.get(path, 'a_classer')
        for name in (dg['data'].get('parties') or [])[:20]:
            if nature in PROCEDURE_NATURES or nature == 'contrat':
                _party_for(desk, mid, name, 'releve', PurePosixPath(path).name)          # parties d'un acte ou d'un contrat
                continue
            ident = identity(name)                                                         # ailleurs : simple mention d'une partie connue
            for r in desk.db.execute("SELECT id,core,kind,gender,forme FROM d51_parties WHERE matter=? AND status='active'", (mid,)).fetchall():
                if compare(ident, {'kind': r['kind'], 'gender': r['gender'], 'forme': r['forme'], 'core': r['core'], 'tokens': r['core'].split()})[0] == 'meme':
                    _add_alias(desk, r['id'], mid, name, PurePosixPath(path).name)
    existing_parties = desk.db.execute("SELECT id,core,kind,gender,forme FROM d51_parties WHERE matter=? AND status='active'", (mid,)).fetchall()
    counters['parties'] = desk.db.execute("SELECT COUNT(*) FROM d51_parties WHERE matter=? AND status='active'", (mid,)).fetchone()[0]
    # émetteurs : conclusions / assignation dont le nom mentionne une partie connue
    emitters = {}
    for d in desk.db.execute("SELECT path,name,nature,emitter FROM d51_documents WHERE matter=?", (mid,)).fetchall():
        if d['emitter']:
            emitters[d['path']] = d['emitter']
            continue
        if d['nature'] in ('conclusions', 'assignation', 'requete', 'courrier', 'mise_en_demeure'):
            # émetteur : la partie dont le nom figure dans le titre du document — dénomination complète, ou nom propre propre à une
            # seule partie du dossier (deux homonymes : aucune attribution)
            fname = ' ' + re.sub(r'[^\w]+', ' ', fold(d['name'])) + ' '
            counts = {}
            for r in existing_parties:
                for tok in set(r['core'].split()):
                    counts[tok] = counts.get(tok, 0) + 1
            found = [r for r in existing_parties if r['core'] and all(' ' + tok + ' ' in fname for tok in r['core'].split())]
            if not found:
                found = [r for r in existing_parties if r['kind'] == 'personne' and
                         any(len(tok) >= 4 and counts.get(tok) == 1 and ' ' + tok + ' ' in fname for tok in r['core'].split())]
            if len(found) == 1:
                emitters[d['path']] = found[0]['id']
                desk.db.execute('UPDATE d51_documents SET emitter=? WHERE matter=? AND path=?', (found[0]['id'], mid, d['path']))
    # 4. Informations des relevés contrôlés (attribuées à l'émetteur du document, sources localisées)
    for path, dg in digests.items():
        nature = natures.get(path, 'a_classer')
        if nature in ('jurisprudence', 'note_interne'):
            continue                                                          # une jurisprudence citée n'est pas un fait du dossier
        author, label = _author_of(desk, mid, path, nature, emitters)
        def src(item):
            return [{k: v for k, v in {'path': path, 'etag': dg.get('etag', ''), 'page': item.get('page', ''), 'paragraphe': item.get('paragraphe', ''),
                                       'extrait': str(item.get('extrait') or '')[:500], 'statut': item.get('statut', '')}.items() if v}]
        for f in dg['data'].get('faits') or []:
            if not isinstance(f, dict) or METADATA.search(fold(f.get('texte', ''))):
                continue
            proof = 'constate' if nature in EVIDENCE_NATURES and f.get('statut') == 'documente' else ('allegue' if author != 'document' else 'a_verifier')
            ident = _upsert_assertion(desk, mid, 'fait', f.get('texte'), 'releve', src(f), author, label, f.get('date', ''), f.get('date_precision', ''),
                                      proof=proof, procedure_id=ref_id if nature in PROCEDURE_NATURES else '')
            counters['informations'] += 1 if ident else 0
            if nature in PROCEDURE_NATURES or nature in ('mise_en_demeure',):
                ftext = fold(f.get('texte', ''))
                for ev_nature, pattern in EVENT_WORDS:
                    if re.search(pattern, ftext):
                        dates = {'evenement': f.get('date', '')} if f.get('date') else {}
                        _upsert_event(desk, mid, ev_nature, f.get('texte', ''), 'releve', src(f), dates, f.get('date_precision', ''), author,
                                      procedure_id=ref_id)
                        counters['actes'] += 1
                        break
        for dmd in dg['data'].get('demandes') or []:
            if isinstance(dmd, dict):
                _upsert_assertion(desk, mid, 'demande', dmd.get('texte'), 'releve', src(dmd), author, label, procedure_id=ref_id)
                counters['informations'] += 1
        for mt in dg['data'].get('montants') or []:
            if isinstance(mt, dict) and mt.get('montant'):
                kind = 'demande' if str(mt.get('role') or '').startswith('demande') else 'montant'
                text = '%s : %s %s' % (mt.get('objet') or 'Montant', mt.get('montant'), mt.get('devise') or '')
                topic = str(mt.get('objet') or '')[:160]
                value = str(mt.get('valeur') or mt.get('montant'))
                if topic:   # A03 : même objet, autre montant → changement signalé et passages des projets concernés repérés
                    for old in desk.db.execute("SELECT id,amount,text FROM d51_assertions WHERE matter=? AND kind=? AND topic=? AND amount<>'' AND amount<>? "
                                               "AND review NOT IN ('refuse','hors_sujet')", (mid, kind, topic, value)).fetchall():
                        memoire5651.record_change(desk, mid, 'montant_modifie', 'information', old['id'], value,
                                                  'Montant modifié (%s) : %s → %s' % (topic, old['amount'], value),
                                                  {'ancien': old['text'], 'nouveau': text, 'source': PurePosixPath(path).name,
                                                   'consequence': 'Les passages qui citent l’ancien montant sont à recontrôler.'}, commit=False)
                        memoire5651.mark_dependents(desk, mid, 'montant modifié (%s)' % topic, amounts=[old['amount']], commit=False)
                _upsert_assertion(desk, mid, kind, text, 'releve', src(mt), author, label, amount=str(mt.get('valeur') or mt.get('montant')),
                                  currency=str(mt.get('devise') or ''), topic=str(mt.get('objet') or '')[:160])
                counters['informations'] += 1
        if nature in DECISION_NATURES:
            sig = _sha('decision', path)
            desk.db.execute('INSERT OR IGNORE INTO d51_judgments(id,matter,procedure_id,nature,path,examined,date,sources,signature,created,updated) '
                            'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (_sha(mid, sig), mid, ref_id, nature, path, 1 if dg['data'] else 0,
                                                              dg['data'].get('date_document', ''), json.dumps([{'path': path}]), sig, now, now))
            counters['decisions'] += 1
    # 5. Fiche de travail : rappel des faits (propositions sourcées) et chronologie
    stored = {s.get('id'): s for s in ((fiche or {}).get('sources') or [])} if fiche else {}
    for para in fdata.get('rappel_des_faits') or []:
        srcs = [{'path': stored.get(i, {}).get('path', ''), 'label': stored.get(i, {}).get('nom', i)} for i in para.get('source_ids', []) if i in stored]
        _upsert_assertion(desk, mid, 'fait', para.get('texte'), 'fiche', [s for s in srcs if s.get('path')] or srcs)
    for ev in fdata.get('chronologie') or []:
        srcs = [{'path': stored.get(i, {}).get('path', ''), 'label': stored.get(i, {}).get('nom', i)} for i in ev.get('source_ids', []) if i in stored]
        _upsert_assertion(desk, mid, 'fait', ev.get('evenement'), 'fiche', srcs, date=ev.get('date', ''), precision=ev.get('precision', ''))
    # 6. Mémoire du dossier : faits validés (trace de validation conservée, sans devenir « établis »), refus conservés
    try:
        from .legal_memory import memory_records
        for r in memory_records(desk, mid, None, 1000):
            rtype, status = r['record_type'], r['status']
            if rtype not in ('event', 'claim', 'amount', 'other', 'completed_action', 'deadline', 'planned_action'):
                continue                                                      # parties, RG, consignes, questions : objets propres
            src = []
            raw_sources = r['sources'] if isinstance(r['sources'], list) else []
            src = [{k: v for k, v in {'path': s.get('path', ''), 'label': s.get('label', ''),
                                      'extrait': str(s.get('excerpt') or s.get('quote') or '')[:500]}.items() if v}
                   for s in raw_sources if isinstance(s, dict)]
            review_state = {'validated': 'enregistre', 'pinned': 'enregistre', 'disputed': 'refuse'}.get(status, 'propose')
            text = (r['title'] + ' — ' + r['content']) if r['content'] and r['content'] not in r['title'] else r['title']
            if rtype in ('completed_action', 'deadline', 'planned_action'):
                _upsert_event(desk, mid, rtype, text, 'memoire', src, {'evenement': r['event_date']} if r['event_date'] else {},
                              'jour' if r['event_date'] else 'inconnue', status='realise' if rtype == 'completed_action' else 'projete', procedure_id=ref_id)
                continue
            kind = 'demande' if rtype == 'claim' else ('montant' if rtype == 'amount' else 'fait')
            _upsert_assertion(desk, mid, kind, text, 'memoire', src, date=r['event_date'] or '', precision='jour' if r['event_date'] else '',
                              review_state=review_state, proof='a_verifier', relevance='pertinent' if review_state == 'enregistre' else 'a_examiner')
    except Exception:   # noqa: BLE001 — mémoire absente
        pass
    _refresh_validity(desk, mid, {p: d['etag'] for p, d in digests.items()}, {d['path'] for d in inv})
    memoire5651.sync_gaps(desk, mid)
    try:   # 5.6.59 (A02) : bilan de chaque pièce nouvellement lue dont le texte est nouveau
        from .resume5659 import after_rebuild
        after_rebuild(desk, mid)
    except Exception:   # noqa: BLE001 — un bilan impossible n'empêche jamais l'actualisation
        pass
    desk.db.execute('INSERT OR REPLACE INTO d51_rebuilds(matter,at,counters,revision) VALUES(?,?,?,?)', (mid, now, json.dumps(counters), revision))
    desk.db.commit()
    return counters


def _refresh_validity(desk, mid, current_versions, indexed):
    """5.6.52 (audit 5.6.51, F03) : une information issue d'un relevé n'est courante que si l'une de ses sources est la version
    actuelle d'un document encore indexé. Sinon elle devient historique (consultable, jamais présentée comme actuelle) ; une information
    enregistrée par l'avocat n'est jamais retirée : elle est signalée « à recontrôler ». Un document remplacé n'est pas un fait faux."""
    now = _now()
    for row in desk.db.execute("SELECT id,origin,review,sources,validity FROM d51_assertions WHERE matter=? AND origin='releve'", (mid,)).fetchall():
        sources = json.loads(row['sources'] or '[]')
        live = [s for s in sources if s.get('path') in indexed and (not s.get('etag') or s.get('etag') == current_versions.get(s.get('path'))
                                                                     or s.get('path') not in current_versions)]
        if live:
            validity, note = 'courante', ''
        else:
            gone = [s.get('path', '').rsplit('/', 1)[-1] for s in sources]
            note = ('source remplacée ou retirée : ' + ', '.join(sorted(set(gone)))[:300])
            validity = 'a_recontroler' if row['review'] == 'enregistre' else 'historique'
        if validity != row['validity']:
            desk.db.execute('UPDATE d51_assertions SET validity=?,validity_note=?,updated=? WHERE id=?', (validity, note, now, row['id']))
            if validity == 'a_recontroler':
                from . import memoire5651
                memoire5651.record_change(desk, mid, 'correction', 'information', row['id'], 'source:' + now, 'Information enregistrée dont la source a '
                                          'changé : à recontrôler', {'note': note}, commit=False)


INLINE_MAX = 300          # 5.6.55 : au-delà, pas de reconstruction pendant une rédaction (actualisation en arrière-plan)
AUTO_MAX = 1500           # au-delà, actualisation sur demande seulement (bouton « Actualiser les données » de la page Dossier)
COMMIT_EVERY = 200        # écritures validées par lots : la base n'est jamais verrouillée le temps d'un grand dossier


def doc_count(desk, mid):
    """Nombre de documents indexés du dossier (lecture seule de l'index) ; None si l'index est illisible."""
    try:
        from .index import DocumentIndex
        index = DocumentIndex(desk.c['state_dir'])
        try:
            return index.db.execute('SELECT COUNT(*) FROM docs WHERE matter=?', (str(mid),)).fetchone()[0]
        finally:
            index.db.close()
    except Exception:   # noqa: BLE001
        return None


def mark_dirty(desk, mid):
    ensure_schema(desk)
    cur = desk.db.execute("UPDATE d51_rebuilds SET state='a_actualiser' WHERE matter=?", (str(mid),))
    if not cur.rowcount:
        desk.db.execute("INSERT OR IGNORE INTO d51_rebuilds(matter,at,counters,revision,state) VALUES(?,?,?,?,?)", (str(mid), '', '{}', '', 'a_actualiser'))
    desk.db.commit()


def _client_matter(desk, mid):
    try:
        from .facturation5655 import classify
        m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
        return bool(m) and classify(desk, m)[0] == 'client'
    except Exception:   # noqa: BLE001
        return True


def schedule_refresh(desk, mid):
    """Après une indexation ou un relevé : dossier marqué « à actualiser » et UNE actualisation de faible priorité mise en file
    (dossiers clients de taille raisonnable ; dossiers internes et très volumineux : sur demande)."""
    if not mid:
        return None
    mid = str(mid)
    mark_dirty(desk, mid)
    count = doc_count(desk, mid)
    if count is None or count > AUTO_MAX or not _client_matter(desk, mid):
        return None
    marker = '%"matter": "' + mid.replace('%', '') + '"%'
    if desk.db.execute("SELECT 1 FROM jobs WHERE kind='actualiser5655' AND status IN ('pending','running') AND args LIKE ?", (marker,)).fetchone():
        return None
    try:
        return desk.enqueue('actualiser5655', {'matter': mid}, priority=60)
    except Stop:
        return None


def refresh(desk, mid, inline_max=INLINE_MAX):
    """Avant une lecture (rédaction, routine, relecture) : petit dossier = contrôle et reconstruction immédiats (comme en 5.6.52) ;
    grand dossier = données existantes utilisées telles quelles, actualisation mise en file si elles sont marquées « à actualiser »
    (état « partiel » signalé au modèle). Jamais de reconstruction d'un grand dossier sur le chemin d'une rédaction."""
    count = doc_count(desk, mid)
    if count is not None and count <= inline_max:
        return ensure_current(desk, mid)
    ensure_schema(desk)
    row = desk.db.execute('SELECT state FROM d51_rebuilds WHERE matter=?', (str(mid),)).fetchone()
    if row and row['state'] == 'ok':
        return {'state': 'ok', 'inchange': True}
    schedule_refresh(desk, mid)
    return {'state': 'partiel', 'error': 'actualisation_en_arriere_plan'}


def ensure_current(desk, mid):
    """5.6.52 (audit 5.6.51, F02) : données structurées du dossier à jour avant toute lecture (page, rédactions, missions, relecture).
    Idempotent (empreinte de révision, aucun appel au modèle) ; un échec est enregistré et rendu explicite, jamais masqué."""
    ensure_schema(desk)
    try:
        out = rebuild(desk, mid)
        return {'state': 'ok', **out}
    except Stop as ex:
        code = str(ex).split(':')[0]
    except Exception as ex:   # noqa: BLE001 — reconstruction impossible : contexte partiel signalé
        code = 'reconstruction_impossible:' + type(ex).__name__
    try:
        desk.db.rollback()
        desk.db.execute("UPDATE d51_rebuilds SET state='partiel',error=? WHERE matter=?", (code[:200], mid))
        desk.db.commit()
    except Exception:   # noqa: BLE001
        pass
    return {'state': 'partiel', 'error': code}


def last_rebuild(desk, mid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM d51_rebuilds WHERE matter=?', (mid,)).fetchone()
    return {'at': row['at'], 'counters': json.loads(row['counters'] or '{}'), 'revision': row['revision']} if row else None


def events(desk, mid):
    ensure_schema(desk)
    rows = [dict(r) for r in desk.db.execute('SELECT * FROM d51_events WHERE matter=? ORDER BY updated', (mid,))]
    for r in rows:
        r['dates'] = json.loads(r['dates'] or '{}')
        r['sources'] = json.loads(r['sources'] or '[]')
    return rows


def judgments(desk, mid):
    ensure_schema(desk)
    rows = [dict(r) for r in desk.db.execute('SELECT * FROM d51_judgments WHERE matter=? ORDER BY date DESC', (mid,))]
    for r in rows:
        r['sources'] = json.loads(r['sources'] or '[]')
    return rows
