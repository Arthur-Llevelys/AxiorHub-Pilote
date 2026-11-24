"""5.6.109 (lot G3, G05) : « Suites et exécution » dans la fiche contentieux (onglet Juridiction), sans second agenda ni second registre de délais.

Une suite part d'un acte EXACT : décision ou accord (protocole transactionnel), désigné par son chemin et sa version (empreinte), avec sa date
et, si elle est connue, la preuve de signification ou de notification. Puis :

- **Obligations sourcées** (``suites_obligations56109``) : chaque proposition porte le passage exact de l'acte et son type (dispositif, clause,
  motifs, prétention citée, antécédent). Le découpage local (``decouper``, sans modèle : IA en pause, tout reste utilisable) repère le
  dispositif après « PAR CES MOTIFS » ; une proposition venue d'un modèle (``proposer_lignes``) n'est retenue que si son passage figure
  mot pour mot dans l'acte, et son type est RECALCULÉ d'après sa place (un motif présenté comme dispositif reste un motif). Seul un passage du
  dispositif ou d'une clause devient une obligation confirmée, par l'avocat (débiteur, créancier, bénéficiaire, montant, condition de départ).
- **Aucune date inventée ni calculée** : une échéance est soit saisie par l'avocat AVEC sa source (« protocole, art. 3 »), soit le lien vers
  une échéance existante du moteur de délais (``limitation500``, avec une ligne de journal ``limitation500_journal``, ou ``deadlines450``).
  Sans point de départ, règle ou notification établis : « À vérifier ». Un calendrier conventionnel n'est pas un délai de recours.
- **Actions** (``suites_actions56109``) reliées aux tâches et événements EXISTANTS : « Créer la tâche » passe par ``agenda5697.creer``
  (registre commun ``tasks``, clé d'idempotence stable) ; un événement existant peut être relié. Une action préparée ne devient accomplie
  qu'avec sa preuve (une transmission faite à la main est enregistrée avec son justificatif ; aucune API RPVA ni intégration CARPA).
- **Échéancier** (``suites_echeancier56109``) : lignes datées (date conventionnelle et sa source), montant en unités mineures, devise ;
  statut attendu / reçu / partiel / en retard CALCULÉ à la lecture depuis le mouvement ou la facture liés, par les fonctions du lot G1
  (``gestion56109.paiement_partiel`` : chaque règlement compté une fois ; ``mouvements56109``). Un même mouvement ou une même facture ne
  se lie qu'à une seule ligne (index unique) : un paiement partiel n'est jamais compté deux fois. Aucun paiement n'est exécuté.

Aucune écriture pendant une lecture ; tables créées au démarrage ; routes ``m56109/operations/suites/…`` (``closing56109.api``).
"""
from datetime import date
from html import escape as e
import json
import re
import sqlite3
import uuid

from .common import Stop
from .closing56109 import (Refus, acteur_de, dossier_visible, humain, jour, maintenant, sha, texte, transaction, version_attendue)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS suites56109(id TEXT PRIMARY KEY, matter TEXT NOT NULL, source_kind TEXT NOT NULL CHECK(source_kind IN ('decision','accord')),
  document TEXT NOT NULL, document_version TEXT NOT NULL, libelle TEXT NOT NULL DEFAULT '', date_acte TEXT NOT NULL DEFAULT '',
  notification_date TEXT NOT NULL DEFAULT '', notification_preuve TEXT NOT NULL DEFAULT '', texte_empreinte TEXT NOT NULL DEFAULT '',
  etat TEXT NOT NULL DEFAULT 'ouverte' CHECK(etat IN ('ouverte','close')), version INTEGER NOT NULL DEFAULT 1, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL,
  modifie_le TEXT NOT NULL, UNIQUE(matter, document, document_version), CHECK(notification_date='' OR notification_preuve<>''));
CREATE TABLE IF NOT EXISTS suites_obligations56109(id TEXT PRIMARY KEY, suite TEXT NOT NULL, passage TEXT NOT NULL, passage_type TEXT NOT NULL
  CHECK(passage_type IN ('dispositif','clause','motifs','pretention','antecedent','autre')), position INTEGER NOT NULL DEFAULT 0,
  nature TEXT NOT NULL DEFAULT 'autre' CHECK(nature IN ('payer','faire','ne_pas_faire','informer','autre')), debiteur TEXT NOT NULL DEFAULT '',
  creancier TEXT NOT NULL DEFAULT '', beneficiaire TEXT NOT NULL DEFAULT '', montant_minor INTEGER CHECK(montant_minor IS NULL OR montant_minor>=0),
  devise TEXT NOT NULL DEFAULT 'EUR', condition_depart TEXT NOT NULL DEFAULT '', fait_generateur TEXT NOT NULL DEFAULT '',
  echeance TEXT NOT NULL DEFAULT '', echeance_source TEXT NOT NULL DEFAULT '', echeance_lien_kind TEXT NOT NULL DEFAULT '' CHECK(echeance_lien_kind IN ('','limitation500','deadlines450')),
  echeance_lien_id TEXT NOT NULL DEFAULT '', echeance_statut TEXT NOT NULL DEFAULT 'a_verifier' CHECK(echeance_statut IN ('a_verifier','confirmee')),
  statut TEXT NOT NULL DEFAULT 'proposee' CHECK(statut IN ('proposee','confirmee','ecartee')), origine TEXT NOT NULL CHECK(origine IN ('extraction_locale','modele','avocat')),
  confirme_par TEXT NOT NULL DEFAULT '', confirme_le TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL,
  CHECK(statut<>'confirmee' OR passage_type IN ('dispositif','clause')), CHECK(echeance='' OR echeance_source<>''),
  CHECK(echeance_statut<>'confirmee' OR (echeance<>'' AND echeance_source<>'') OR echeance_lien_id<>''));
CREATE INDEX IF NOT EXISTS suites_obligations56109_s ON suites_obligations56109(suite, position);
CREATE TABLE IF NOT EXISTS suites_actions56109(id TEXT PRIMARY KEY, suite TEXT NOT NULL, obligation TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL
  CHECK(kind IN ('rendre_compte','demander_piece','transmettre_commissaire','suivre_paiement','suivre_formalite','autre')), libelle TEXT NOT NULL,
  task_id TEXT NOT NULL DEFAULT '', event_id TEXT NOT NULL DEFAULT '', responsable TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL DEFAULT 'preparee'
  CHECK(etat IN ('preparee','accomplie')), preuve TEXT NOT NULL DEFAULT '', accomplie_par TEXT NOT NULL DEFAULT '', accomplie_le TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL, CHECK(etat<>'accomplie' OR preuve<>''));
CREATE TABLE IF NOT EXISTS suites_echeancier56109(id TEXT PRIMARY KEY, suite TEXT NOT NULL, obligation TEXT NOT NULL DEFAULT '', jour TEXT NOT NULL,
  jour_source TEXT NOT NULL CHECK(jour_source<>''), montant_minor INTEGER NOT NULL CHECK(montant_minor>0), devise TEXT NOT NULL DEFAULT 'EUR',
  lien_kind TEXT NOT NULL DEFAULT '' CHECK(lien_kind IN ('','mouvement','facture')), lien_id TEXT NOT NULL DEFAULT '', justificatif TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS suites_echeancier56109_lien ON suites_echeancier56109(lien_kind, lien_id) WHERE lien_kind<>'';
CREATE TABLE IF NOT EXISTS suites_journal56109(id INTEGER PRIMARY KEY AUTOINCREMENT, suite TEXT NOT NULL, objet TEXT NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL,
  auteur TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
CREATE TRIGGER IF NOT EXISTS suites_journal56109_fige_maj BEFORE UPDATE ON suites_journal56109 BEGIN SELECT RAISE(ABORT, 'journal_immuable'); END;
CREATE TRIGGER IF NOT EXISTS suites_journal56109_fige_sup BEFORE DELETE ON suites_journal56109 BEGIN SELECT RAISE(ABORT, 'journal_immuable'); END;
'''
TABLES = ('suites56109', 'suites_obligations56109', 'suites_actions56109', 'suites_echeancier56109', 'suites_journal56109')
SOURCES = {'decision': 'Décision', 'accord': 'Accord ou protocole'}
TYPES = {'dispositif': 'Dispositif', 'clause': 'Clause de l’accord', 'motifs': 'Motifs (non exécutoire)', 'pretention': 'Prétention citée',
         'antecedent': 'Antécédent ou jurisprudence citée', 'autre': 'Autre passage'}
EXECUTOIRES = ('dispositif', 'clause')
NATURES = {'payer': 'Payer', 'faire': 'Faire', 'ne_pas_faire': 'Ne pas faire', 'informer': 'Informer', 'autre': 'Autre'}
ACTIONS = {'rendre_compte': 'Rendre compte au client', 'demander_piece': 'Demander une pièce', 'transmettre_commissaire': 'Préparer la transmission au commissaire de justice',
           'suivre_paiement': 'Suivre un paiement', 'suivre_formalite': 'Suivre une formalité', 'autre': 'Autre action'}
STATUTS_LIGNE = {'attendu': 'Attendu', 'recu': 'Reçu', 'partiel': 'Partiel', 'en_retard': 'En retard', 'a_verifier': 'À vérifier'}
DEVISE = re.compile(r'^[A-Z]{3}$')
MAX_TEXTE = 200_000
MAX_PROPOSITIONS = 60

DISPOSITIF = re.compile(r'\bPAR\s+CES\s+MOTIFS\b', re.I)
CHEF = re.compile(r'^\s*(?:[-–•]\s*)?(Condamne|Ordonne|Dit|Déboute|Rejette|Fixe|Constate|Prononce|Autorise|Accorde|Enjoint|Alloue|Laisse|Donne acte|Homologue|Renvoie)\b',
                  re.M)
CLAUSE = re.compile(r'(?im)^\s*(?:article|art\.)\s*\d+[^\n]*')
ENGAGEMENT = re.compile(r"(?i)\b(s['’]engage|versera|paiera|remettra|restituera|s['’]oblige|renonce)")
ANTECEDENT = re.compile(r'(?i)\b(Cass\.|Cour de cassation|CE,|Conseil d[’\']État|arrêt du|jurisprudence)\b')
PRETENTION = re.compile(r'(?i)\b(sollicite|demande que|conclut à|prie le tribunal|réclame)\b')
MONTANT = re.compile(r'(\d{1,3}(?:[  .]\d{3})*|\d+)(?:,(\d{2}))?\s*(?:€|euros?\b)', re.I)


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _journal(desk, suite, objet, action, auteur, detail=None):
    desk.db.execute('INSERT INTO suites_journal56109(suite,objet,at,action,auteur,detail) VALUES(?,?,?,?,?,?)',
                    (suite, objet, maintenant(), action, auteur, json.dumps(detail or {}, ensure_ascii=False, default=str)))


def _aujourdhui(desk):
    from .acces5683 import today_of
    return today_of(desk)[0]


def _montant(valeur, devise='EUR'):
    """Montant texte → unités mineures (centimes), sans flottant ; vide → None (inconnu, jamais zéro)."""
    if valeur is None or str(valeur).strip() == '':
        return None
    if isinstance(valeur, float) or isinstance(valeur, bool):
        raise Refus('champs_invalides', {'montant': 'Montant en texte (ex. 1 500,00), jamais un nombre flottant.'})
    try:
        from .gestion56109 import montant_minor
        return montant_minor(str(valeur), devise)
    except ImportError:          # lot G1 absent : même règle, centimes EUR seulement
        m = re.fullmatch(r'(\d{1,12})(?:[,.](\d{1,2}))?', re.sub(r'[\s ]', '', str(valeur)))
        if not m or devise != 'EUR':
            raise Refus('champs_invalides', {'montant': 'Montant illisible.'}) from None
        return int(m.group(1)) * 100 + int((m.group(2) or '0').ljust(2, '0'))
    except ValueError:
        raise Refus('champs_invalides', {'montant': 'Montant positif, selon les décimales de la devise.'}) from None


# ------------------------------------------------------------------------------------------------ découpage local (sans modèle)
def decouper(texte_acte, source_kind):
    """Passages candidats, chacun avec son type et sa position. Aucune date n'est extraite comme échéance ; aucun montant n'est retenu sans
    confirmation. Décision : dispositif après « PAR CES MOTIFS » (chefs commençant par Condamne, Ordonne, Dit…) ; les motifs qui parlent de
    condamnation, les prétentions citées et la jurisprudence sont montrés comme NON exécutoires. Accord : clauses « Article n » d'engagement."""
    t = str(texte_acte or '')[:MAX_TEXTE]
    out = []
    if source_kind == 'decision':
        m = DISPOSITIF.search(t)
        debut = m.end() if m else len(t)
        dispo = t[debut:]
        reperes = [x.start() for x in CHEF.finditer(dispo)]
        for i, pos in enumerate(reperes):
            fin = reperes[i + 1] if i + 1 < len(reperes) else len(dispo)
            passage = re.sub(r'\s+', ' ', dispo[pos:fin]).strip()
            if passage:
                out.append({'passage': passage[:1200], 'type': 'dispositif', 'position': debut + pos})
        for s in re.finditer(r'[^.\n]*(?:condamn|payer|verser)[^.\n]*\.', t[:debut], re.I):
            phrase = s.group(0).strip()
            typ = 'antecedent' if ANTECEDENT.search(phrase) else 'pretention' if PRETENTION.search(phrase) else 'motifs'
            out.append({'passage': phrase[:1200], 'type': typ, 'position': s.start()})
    else:
        reperes = [x.start() for x in CLAUSE.finditer(t)]
        for i, pos in enumerate(reperes):
            fin = reperes[i + 1] if i + 1 < len(reperes) else len(t)
            passage = re.sub(r'\s+', ' ', t[pos:fin]).strip()
            if passage and ENGAGEMENT.search(passage):
                out.append({'passage': passage[:1200], 'type': 'clause', 'position': pos})
    for x in out:
        x['nature'] = ('payer' if re.search(r'(?i)\b(condamne|payer|verser|versera|paiera)\b', x['passage']) and MONTANT.search(x['passage'])
                       else 'faire' if re.search(r'(?i)\b(ordonne|remettra|restituera|enjoint)\b', x['passage']) else 'autre')
        mt = MONTANT.search(x['passage'])
        x['montant_propose'] = ('%s,%s' % (re.sub(r'[\s .]', '', mt.group(1)), mt.group(2) or '00')) if mt else ''
    out.sort(key=lambda x: x['position'])
    return out[:MAX_PROPOSITIONS]


def type_par_position(texte_acte, source_kind, passage):
    """Type d'un passage d'après sa PLACE dans l'acte (jamais d'après l'étiquette d'un modèle) ; ``None`` s'il n'y figure pas mot pour mot."""
    t = str(texte_acte or '')
    norm = lambda s: re.sub(r'\s+', ' ', s).strip()
    tn, pn = norm(t), norm(str(passage or ''))
    if len(pn) < 8:
        return None
    pos = tn.find(pn)
    if pos < 0:
        return None
    if source_kind == 'accord':
        return 'clause' if ENGAGEMENT.search(pn) else 'autre'
    m = DISPOSITIF.search(tn)
    if m and pos >= m.end():
        return 'dispositif'
    return 'antecedent' if ANTECEDENT.search(pn) else 'pretention' if PRETENTION.search(pn) else 'motifs'


# ------------------------------------------------------------------------------------------------ services
def _suite(desk, acteur, sid):
    s = desk.db.execute('SELECT * FROM suites56109 WHERE id=?', (str(sid or ''),)).fetchone()
    if s is None:
        raise Stop('introuvable')
    dossier_visible(desk, acteur, s['matter'])
    return dict(s)


def creer(desk, acteur, data):
    """Suite d'un acte exact (chemin + version) du dossier ; même acte, même version : la même suite (aucun doublon)."""
    a = humain(acteur)
    mid = dossier_visible(desk, acteur, data.get('dossier'))
    err = {}
    kind = str(data.get('source_kind') or '')
    if kind not in SOURCES:
        err['source_kind'] = 'Décision ou accord.'
    doc, ver = texte(data.get('document'), 400), texte(data.get('document_version'), 120)
    if not doc:
        err['document'] = 'Désignez l’acte exact (pièce du dossier).'
    if not ver:
        err['document_version'] = 'Version ou empreinte de l’acte requise.'
    try:
        date_acte = jour(data.get('date_acte'))
        notif = jour(data.get('notification_date'))
    except Stop:
        err['date_acte'] = 'Date invalide.'
        date_acte = notif = ''
    preuve = texte(data.get('notification_preuve'), 300)
    if notif and not preuve:
        err['notification_preuve'] = 'Une date de notification n’est retenue qu’avec sa preuve (acte de signification, accusé…).'
    if err:
        raise Refus('champs_invalides', err)
    with transaction(desk):
        old = desk.db.execute('SELECT id FROM suites56109 WHERE matter=? AND document=? AND document_version=?', (mid, doc, ver)).fetchone()
        if old:
            return {'suite': old['id'], 'deja': True, 'message': 'Suite déjà ouverte pour cet acte.'}
        sid = 'su-' + uuid.uuid4().hex[:20]
        desk.db.execute('INSERT INTO suites56109(id,matter,source_kind,document,document_version,libelle,date_acte,notification_date,notification_preuve,cree_par,cree_le,modifie_le) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (sid, mid, kind, doc, ver, texte(data.get('libelle'), 200) or doc.rsplit('/', 1)[-1], date_acte, notif, preuve,
                                                            a.compte, maintenant(), maintenant()))
        _journal(desk, sid, sid, 'creation', a.compte, {'document': doc, 'version': ver})
    return {'suite': sid, 'deja': False, 'message': 'Suite ouverte : désignez les passages du dispositif ou des clauses.'}


def proposer(desk, acteur, data):
    """Découpage local de l'acte (texte fourni par l'avocat ou lu dans la pièce) : propositions sourcées, aucune confirmée."""
    a = humain(acteur)
    s = _suite(desk, acteur, data.get('suite'))
    t = str(data.get('texte') or '')
    if not t.strip() or len(t) > MAX_TEXTE:
        raise Refus('champs_invalides', {'texte': 'Texte de l’acte requis (borné).'})
    return _inscrire(desk, a.compte, s, t, [(x, 'extraction_locale') for x in decouper(t, s['source_kind'])])


def proposer_lignes(desk, acteur, sid, texte_acte, propositions):
    """Propositions d'un modèle (mission) : un passage absent de l'acte est rejeté ; le type est recalculé d'après la place du passage.
    Rien n'est confirmé : seule la route de l'avocat confirme."""
    a = acteur_de(acteur)
    s = _suite(desk, acteur, sid)
    lignes, rejets = [], 0
    for p in list(propositions or ())[:MAX_PROPOSITIONS]:
        passage = texte((p or {}).get('passage'), 1200)
        typ = type_par_position(texte_acte, s['source_kind'], passage)
        if typ is None:
            rejets += 1
            continue
        lignes.append(({'passage': passage, 'type': typ, 'position': 0, 'nature': str(p.get('nature') or 'autre') if p.get('nature') in NATURES else 'autre',
                        'montant_propose': ''}, 'modele'))
    out = _inscrire(desk, a.compte, s, texte_acte, lignes)
    out['rejetees_hors_acte'] = rejets
    return out


def _inscrire(desk, auteur, s, t, lignes):
    n = 0
    with transaction(desk):
        desk.db.execute('UPDATE suites56109 SET texte_empreinte=?, modifie_le=? WHERE id=?', (sha(t), maintenant(), s['id']))
        connus = {r['passage'] for r in desk.db.execute('SELECT passage FROM suites_obligations56109 WHERE suite=?', (s['id'],))}
        for x, origine in lignes:
            if x['passage'] in connus:
                continue
            connus.add(x['passage'])
            oid = 'ob-' + uuid.uuid4().hex[:20]
            desk.db.execute('INSERT INTO suites_obligations56109(id,suite,passage,passage_type,position,nature,origine,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?)',
                            (oid, s['id'], x['passage'], x['type'], int(x.get('position') or 0), x.get('nature') or 'autre', origine, maintenant(), maintenant()))
            n += 1
        _journal(desk, s['id'], s['id'], 'propositions', auteur, {'ajoutees': n})
    return {'suite': s['id'], 'ajoutees': n, 'message': '%d passage(s) proposé(s) : seuls le dispositif et les clauses peuvent devenir des obligations, après votre confirmation.' % n}


def _obligation(desk, acteur, oid):
    o = desk.db.execute('SELECT * FROM suites_obligations56109 WHERE id=?', (str(oid or ''),)).fetchone()
    if o is None:
        raise Stop('introuvable')
    s = _suite(desk, acteur, o['suite'])
    return dict(o), s


def _lien_echeance(desk, s, kind, ident):
    """Échéance d'un moteur EXISTANT, dans le même dossier ; aucune date n'est recopiée ni recalculée ici."""
    if kind == 'limitation500':
        try:
            row = desk.db.execute('SELECT id, matter FROM limitation500 WHERE id=?', (ident,)).fetchone()
        except sqlite3.Error:
            row = None
    elif kind == 'deadlines450':
        try:
            row = desk.db.execute('SELECT id, matter FROM deadlines450 WHERE id=?', (ident,)).fetchone()
        except sqlite3.Error:
            row = None
    else:
        raise Refus('champs_invalides', {'echeance_lien_kind': 'Moteur inconnu.'})
    if row is None or row['matter'] != s['matter']:
        raise Refus('champs_invalides', {'echeance_lien_id': 'Échéance introuvable dans ce dossier.'})
    return row['id']


def confirmer(desk, acteur, data):
    """Confirmation par l'avocat d'une obligation issue du DISPOSITIF ou d'une CLAUSE. L'échéance n'est retenue qu'avec sa source (saisie de
    l'avocat) ou un lien vers le moteur de délais existant ; sinon elle reste « À vérifier »."""
    a = humain(acteur, valideur=True)
    o, s = _obligation(desk, acteur, data.get('obligation'))
    version_attendue(o, data.get('version'))
    if str(data.get('decision') or 'confirmer') == 'ecarter':
        with transaction(desk):
            desk.db.execute("UPDATE suites_obligations56109 SET statut='ecartee', confirme_par=?, confirme_le=?, version=version+1, modifie_le=? WHERE id=? AND version=?",
                            (a.compte, maintenant(), maintenant(), o['id'], o['version']))
            _journal(desk, s['id'], o['id'], 'ecartee', a.compte, {'motif': texte(data.get('motif'), 300)})
        return {'obligation': o['id'], 'statut': 'ecartee', 'message': 'Passage écarté.'}
    if o['passage_type'] not in EXECUTOIRES:
        raise Refus('passage_non_executoire', {'passage': 'Ce passage relève des %s : il ne crée aucune obligation. Désignez le dispositif ou la clause.' % TYPES[o['passage_type']].lower()})
    err = {}
    nature = str(data.get('nature') or o['nature'])
    if nature not in NATURES:
        err['nature'] = 'Nature inconnue.'
    devise = str(data.get('devise') or 'EUR').upper()
    if not DEVISE.match(devise):
        err['devise'] = 'Devise en trois lettres.'
    montant = None
    try:
        montant = _montant(data.get('montant'), devise) if not err.get('devise') else None
    except Refus as ex:
        err.update(ex.detail)
    echeance, source, lien_kind, lien_id = '', '', '', ''
    try:
        echeance = jour(data.get('echeance'))
    except Stop:
        err['echeance'] = 'Date invalide.'
    if echeance:
        source = texte(data.get('echeance_source'), 300)
        if len(source) < 5:
            err['echeance_source'] = 'Indiquez la source de cette date (clause, page de la décision, acte de signification) : aucune date sans source.'
    if data.get('echeance_lien_id'):
        lien_kind = str(data.get('echeance_lien_kind') or 'limitation500')
        try:
            lien_id = _lien_echeance(desk, s, lien_kind, str(data['echeance_lien_id']))
        except Refus as ex:
            err.update(ex.detail)
    if err:
        raise Refus('champs_invalides', err)
    statut_ech = 'confirmee' if ((echeance and source) or lien_id) else 'a_verifier'
    maj = {'nature': nature, 'debiteur': texte(data.get('debiteur'), 200), 'creancier': texte(data.get('creancier'), 200),
           'beneficiaire': texte(data.get('beneficiaire'), 200), 'montant_minor': montant, 'devise': devise,
           'condition_depart': texte(data.get('condition_depart'), 300), 'fait_generateur': texte(data.get('fait_generateur'), 300),
           'echeance': echeance, 'echeance_source': source, 'echeance_lien_kind': lien_kind, 'echeance_lien_id': lien_id, 'echeance_statut': statut_ech,
           'statut': 'confirmee', 'confirme_par': a.compte, 'confirme_le': maintenant()}
    with transaction(desk):
        cur = desk.db.execute('UPDATE suites_obligations56109 SET %s, version=version+1, modifie_le=? WHERE id=? AND version=?' % ', '.join('%s=?' % k for k in maj),
                              tuple(maj.values()) + (maintenant(), o['id'], o['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        if lien_kind == 'limitation500':     # le journal du moteur de délais existant garde la trace du lien (aucune date modifiée)
            desk.db.execute('INSERT INTO limitation500_journal(limitation_id,at,action,reason,before,after) VALUES(?,?,?,?,?,?)',
                            (lien_id, maintenant(), 'lien_suite56109', 'Obligation %s (suite %s)' % (o['id'], s['id']), '{}', json.dumps({'obligation': o['id']})))
        _journal(desk, s['id'], o['id'], 'confirmee', a.compte, {k: maj[k] for k in ('nature', 'montant_minor', 'devise', 'echeance', 'echeance_source', 'echeance_lien_id')})
    msg = 'Obligation confirmée.' + ('' if statut_ech == 'confirmee' else ' Échéance À vérifier : point de départ, règle ou notification non établis.')
    return {'obligation': o['id'], 'statut': 'confirmee', 'echeance_statut': statut_ech, 'message': msg}


def action(desk, acteur, data, auth=None):
    """Action reliée aux tâches et événements EXISTANTS. « Créer la tâche » écrit une tâche du registre commun par ``agenda5697.creer``
    (clé stable : un double clic ne crée qu'une tâche) ; l'échéance de la tâche n'est que celle, confirmée, de l'obligation."""
    a = humain(acteur)
    s = _suite(desk, acteur, data.get('suite'))
    kind = str(data.get('kind') or '')
    if kind not in ACTIONS:
        raise Refus('champs_invalides', {'kind': 'Action inconnue.'})
    o = None
    if data.get('obligation'):
        o, s2 = _obligation(desk, acteur, data['obligation'])
        if s2['id'] != s['id']:
            raise Stop('introuvable')
    libelle = texte(data.get('libelle'), 200) or ACTIONS[kind]
    event_id = texte(data.get('event_id'), 120)
    if event_id:
        try:
            ev = desk.db.execute('SELECT id, matter FROM agenda5634 WHERE id=?', (event_id,)).fetchone()
        except sqlite3.Error:
            ev = None
        if ev is None or (ev['matter'] and ev['matter'] != s['matter']):
            raise Refus('champs_invalides', {'event_id': 'Événement introuvable dans ce dossier.'})
    aid = 'ac-' + sha([s['id'], kind, libelle, o['id'] if o else ''])[:20]
    if desk.db.execute('SELECT 1 FROM suites_actions56109 WHERE id=?', (aid,)).fetchone():
        return {'action': aid, 'deja': True, 'message': 'Action déjà préparée.'}
    task_id = ''
    if str(data.get('creer_tache') or '') in ('1', 'true', 'on', 'True'):
        echeance = o['echeance'] if (o and o['echeance_statut'] == 'confirmee' and o['echeance']) else ''
        from . import agenda5697
        out = agenda5697.creer(desk, a, {'objet': 'tache', 'titre': libelle, 'dossier': s['matter'], 'cle': ('suite56109-' + aid)[:80],
                                         'tache': {'titre': libelle, 'responsable': texte(data.get('responsable'), 160), 'echeance': echeance}}, auth, executer=False)
        task_id = out.get('tache_id') or ''
    with transaction(desk):
        desk.db.execute('INSERT INTO suites_actions56109(id,suite,obligation,kind,libelle,task_id,event_id,responsable,cree_par,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (aid, s['id'], o['id'] if o else '', kind, libelle, task_id, event_id, texte(data.get('responsable'), 160), a.compte, maintenant(), maintenant()))
        _journal(desk, s['id'], aid, 'action_preparee', a.compte, {'kind': kind, 'tache': task_id, 'evenement': event_id})
    return {'action': aid, 'tache': task_id, 'message': 'Action préparée%s : elle sera accomplie sur preuve.' % (' et tâche créée' if task_id else '')}


def accomplir(desk, acteur, data):
    """Accomplissement sur PREUVE (transmission manuelle, accusé, courriel envoyé) ; jamais déduit d'une tâche cochée ou d'une date passée."""
    a = humain(acteur)
    r = desk.db.execute('SELECT * FROM suites_actions56109 WHERE id=?', (str(data.get('action') or ''),)).fetchone()
    if r is None:
        raise Stop('introuvable')
    s = _suite(desk, acteur, r['suite'])
    version_attendue(r, data.get('version'))
    preuve = texte(data.get('preuve'), 400)
    if len(preuve) < 3:
        raise Refus('champs_invalides', {'preuve': 'Preuve de l’accomplissement requise (pièce, accusé, référence).'})
    with transaction(desk):
        cur = desk.db.execute("UPDATE suites_actions56109 SET etat='accomplie', preuve=?, accomplie_par=?, accomplie_le=?, version=version+1, modifie_le=? WHERE id=? AND version=?",
                              (preuve, a.compte, maintenant(), maintenant(), r['id'], r['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, s['id'], r['id'], 'accomplie', a.compte, {'preuve': preuve})
    return {'action': r['id'], 'etat': 'accomplie', 'message': 'Action accomplie (preuve enregistrée).'}


def echeance(desk, acteur, data):
    """Ligne d'échéancier : date CONVENTIONNELLE avec sa source (jamais calculée), montant en unités mineures, devise."""
    a = humain(acteur)
    s = _suite(desk, acteur, data.get('suite'))
    err = {}
    devise = str(data.get('devise') or 'EUR').upper()
    if not DEVISE.match(devise):
        err['devise'] = 'Devise en trois lettres.'
    try:
        j = jour(data.get('jour'), requis=True)
    except Stop:
        err['jour'] = 'Date de l’échéance (telle qu’écrite dans l’acte) requise.'
        j = ''
    source = texte(data.get('jour_source'), 300)
    if len(source) < 5:
        err['jour_source'] = 'Source de la date requise (clause, page).'
    montant = None
    try:
        montant = _montant(data.get('montant'), devise) if 'devise' not in err else None
    except Refus as ex:
        err.update(ex.detail)
    if not montant:
        err.setdefault('montant', 'Montant positif requis.')
    oid = str(data.get('obligation') or '')
    if oid:
        o, s2 = _obligation(desk, acteur, oid)
        if s2['id'] != s['id']:
            raise Stop('introuvable')
    if err:
        raise Refus('champs_invalides', err)
    lid = 'ec-' + uuid.uuid4().hex[:20]
    with transaction(desk):
        desk.db.execute('INSERT INTO suites_echeancier56109(id,suite,obligation,jour,jour_source,montant_minor,devise,cree_par,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (lid, s['id'], oid, j, source, montant, devise, a.compte, maintenant(), maintenant()))
        _journal(desk, s['id'], lid, 'echeance', a.compte, {'jour': j, 'source': source, 'montant_minor': montant, 'devise': devise})
    return {'ligne': lid, 'message': 'Échéance inscrite.'}


def lier(desk, acteur, data):
    """Lie une ligne à UN mouvement ou UNE facture existants (registres du lot G1) : jamais copiés ; un même mouvement ou une même facture ne
    se lie qu'une fois (paiement partiel compté une fois)."""
    a = humain(acteur)
    r = desk.db.execute('SELECT * FROM suites_echeancier56109 WHERE id=?', (str(data.get('ligne') or ''),)).fetchone()
    if r is None:
        raise Stop('introuvable')
    s = _suite(desk, acteur, r['suite'])
    version_attendue(r, data.get('version'))
    kind, ident = str(data.get('lien_kind') or ''), texte(data.get('lien_id'), 120)
    if kind == 'mouvement':
        m = desk.db.execute('SELECT matter, devise FROM mouvements56109 WHERE id=?', (ident,)).fetchone()
        if m is None or m['matter'] != s['matter']:
            raise Refus('champs_invalides', {'lien_id': 'Mouvement introuvable dans ce dossier.'})
        if m['devise'] != r['devise']:
            raise Refus('champs_invalides', {'lien_id': 'Devise différente : aucune conversion.'})
    elif kind == 'facture':
        from .gestion56109 import paiement_partiel
        paiement_partiel(desk, acteur, ident)          # droits « voir les honoraires » et dossier relus par le lot G1
        if r['devise'] != 'EUR':
            raise Refus('champs_invalides', {'lien_id': 'Facture en euros seulement : aucune conversion.'})
    else:
        raise Refus('champs_invalides', {'lien_kind': 'Mouvement ou facture.'})
    with transaction(desk):
        if desk.db.execute('SELECT 1 FROM suites_echeancier56109 WHERE lien_kind=? AND lien_id=? AND id<>?', (kind, ident, r['id'])).fetchone():
            raise Stop('paiement_deja_compte')
        cur = desk.db.execute('UPDATE suites_echeancier56109 SET lien_kind=?, lien_id=?, justificatif=?, version=version+1, modifie_le=? WHERE id=? AND version=?',
                              (kind, ident, texte(data.get('justificatif'), 300), maintenant(), r['id'], r['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, s['id'], r['id'], 'lien', a.compte, {'kind': kind, 'id': ident})
    return {'ligne': r['id'], 'message': 'Ligne reliée.'}


def statut_ligne(desk, acteur, r, aujourdhui):
    """Statut CALCULÉ à la lecture (aucune écriture) depuis le mouvement constaté ou les règlements vérifiés de la facture (lot G1)."""
    recu = None
    if r['lien_kind'] == 'mouvement':
        m = desk.db.execute('SELECT etat, sens, montant_minor FROM mouvements56109 WHERE id=?', (r['lien_id'],)).fetchone()
        recu = int(m['montant_minor']) if (m and m['etat'] == 'constate' and m['sens'] == 'entree') else 0
    elif r['lien_kind'] == 'facture':
        try:
            from .gestion56109 import paiement_partiel
            recu = int(paiement_partiel(desk, acteur, r['lien_id'])['regle_cents'])
        except (ImportError, Stop):
            return 'a_verifier', None
    recu = recu or 0
    if recu >= r['montant_minor']:
        return 'recu', recu
    if r['jour'] and r['jour'] < aujourdhui.isoformat():
        return 'en_retard', recu
    return ('partiel' if recu else 'attendu'), recu


def _echeance_liee(desk, kind, ident):
    try:
        if kind == 'limitation500':
            row = desk.db.execute('SELECT due, status, rule_id FROM limitation500 WHERE id=?', (ident,)).fetchone()
        else:
            row = desk.db.execute('SELECT due, status, rule_id FROM deadlines450 WHERE id=?', (ident,)).fetchone()
    except sqlite3.Error:
        row = None
    return {'date': row['due'], 'statut': row['status'], 'regle': row['rule_id'], 'moteur': kind} if row else {'date': '', 'statut': 'introuvable', 'moteur': kind}


def vue(desk, acteur, matter):
    """Suites du dossier (lecture seule) : obligations, actions (état de la tâche liée), échéancier (statut calculé)."""
    mid = dossier_visible(desk, acteur, matter)
    aujourdhui = _aujourdhui(desk)
    out = []
    for s in desk.db.execute('SELECT * FROM suites56109 WHERE matter=? ORDER BY cree_le DESC LIMIT 20', (mid,)):
        obs = []
        for o in desk.db.execute('SELECT * FROM suites_obligations56109 WHERE suite=? ORDER BY position, cree_le', (s['id'],)):
            x = dict(o)
            x['type_libelle'] = TYPES[o['passage_type']]
            x['executoire'] = o['passage_type'] in EXECUTOIRES
            if o['echeance_lien_id']:
                x['echeance_liee'] = _echeance_liee(desk, o['echeance_lien_kind'], o['echeance_lien_id'])
            obs.append(x)
        acts = []
        for r in desk.db.execute('SELECT * FROM suites_actions56109 WHERE suite=? ORDER BY cree_le', (s['id'],)):
            x = dict(r)
            if r['task_id']:
                t = desk.db.execute('SELECT status, due FROM tasks WHERE id=?', (r['task_id'],)).fetchone()
                x['tache'] = {'statut': t['status'], 'echeance': t['due']} if t else {'statut': 'introuvable'}
            acts.append(x)
        lignes = []
        for r in desk.db.execute('SELECT * FROM suites_echeancier56109 WHERE suite=? ORDER BY jour', (s['id'],)):
            st, recu = statut_ligne(desk, acteur, r, aujourdhui)
            lignes.append({**dict(r), 'statut': st, 'statut_libelle': STATUTS_LIGNE[st], 'recu_minor': recu})
        out.append({**dict(s), 'source_libelle': SOURCES[s['source_kind']], 'obligations': obs, 'actions': acts, 'echeancier': lignes})
    return {'dossier': mid, 'suites': out, 'mentions': ['Aucune date n’est calculée ici : une échéance vient de l’acte (avec sa source) ou du moteur de délais.',
                                                        'Un échéancier conventionnel n’est pas un délai de recours.',
                                                        'Aucun paiement, aucune transmission RPVA ni opération CARPA n’est exécuté depuis cet onglet.']}


ROUTES = {'creer': creer, 'proposer': proposer, 'confirmer': confirmer, 'action': action, 'accomplir': accomplir, 'echeance': echeance, 'lier': lier}


# ------------------------------------------------------------------------------------------------ bloc « Suites et exécution » de la fiche
def _form(route, corps, bouton, cls=''):
    from .closing56109 import _form as f
    return f('suites/' + route, corps, bouton, cls)


def _cache(**kw):
    from .closing56109 import _cache as c
    return c(**kw)


def montage_fiche(desk, acteur, prefix, m):
    """Point de montage unique dans la fiche (onglet Juridiction) : rendu en lecture seule ; ne lève jamais (la fiche reste affichée)."""
    try:
        v = vue(desk, acteur, m['id'])
    except Exception:   # noqa: BLE001 — dossier hors registre, droits, table absente : la fiche existante s'affiche sans le bloc
        return ''
    blocs = ''
    for s in v['suites']:
        obs = ''
        for o in s['obligations']:
            if o['statut'] == 'ecartee':
                continue
            ech = ''
            if o['statut'] == 'confirmee':
                if o.get('echeance_liee'):
                    ech = 'échéance du moteur de délais : %s (%s)' % (o['echeance_liee']['date'] or '—', o['echeance_liee']['statut'])
                elif o['echeance_statut'] == 'confirmee':
                    ech = 'échéance %s — source : %s' % (o['echeance'], o['echeance_source'])
                else:
                    ech = 'échéance À vérifier'
            gestes = ''
            if o['statut'] == 'proposee' and o['executoire']:
                gestes = '<details><summary>Confirmer</summary>%s</details>' % _form('confirmer', _cache(obligation=o['id'], version=o['version']) +
                    '<label>Débiteur<input name="debiteur"></label><label>Créancier<input name="creancier"></label><label>Bénéficiaire<input name="beneficiaire"></label>'
                    '<label>Montant<input name="montant" inputmode="decimal"></label><label>Devise<input name="devise" value="EUR" maxlength="3"></label>'
                    '<label>Fait générateur<input name="fait_generateur"></label><label>Condition de départ<input name="condition_depart"></label>'
                    '<label>Échéance écrite dans l’acte<input type="date" name="echeance"></label><label>Source de cette date<input name="echeance_source"></label>',
                    'Confirmer l’obligation', 'ghost')
            obs += ('<li class="g3-ob g3-ob-%s"><span class="g3-badge">%s</span> <q>%s</q> <small>%s%s</small>%s</li>') % (
                e(o['passage_type']), e(o['type_libelle']), e(o['passage'][:400]), e({'proposee': 'proposé', 'confirmee': 'confirmé'}.get(o['statut'], o['statut'])),
                (' · ' + e(ech)) if ech else '', gestes)
        acts = ''.join('<li>%s — %s%s%s</li>' % (e(x['libelle']), e({'preparee': 'préparée', 'accomplie': 'accomplie'}[x['etat']]),
                                                 (' · tâche : ' + e(x['tache']['statut'])) if x.get('tache') else '',
                                                 (' · preuve : ' + e(x['preuve'])) if x['preuve'] else _form('accomplir', _cache(action=x['id'], version=x['version']) +
                                                                                                             '<label>Preuve<input name="preuve" required></label>', 'Marquer accomplie', 'ghost'))
                       for x in s['actions'])
        lignes = ''.join('<li>%s — %s %s · <strong>%s</strong> <small>(source : %s)</small></li>' % (
            e(x['jour']), e(str(x['montant_minor'])), e(x['devise']), e(x['statut_libelle']), e(x['jour_source'])) for x in s['echeancier'])
        blocs += ('<div class="g3-suite"><h4>%s — %s</h4><p class="ax-muted">Acte : %s (version %s)%s</p><ol class="g3-obligations">%s</ol>'
                  '<h5>Actions</h5><ul>%s</ul>%s<h5>Échéancier</h5><ul>%s</ul>%s<details><summary>Proposer les passages du dispositif ou des clauses</summary>%s</details></div>') % (
            e(s['source_libelle']), e(s['libelle']), e(s['document']), e(s['document_version'][:16]),
            (' · notifiée le %s (preuve : %s)' % (e(s['notification_date']), e(s['notification_preuve']))) if s['notification_date'] else ' · notification : à vérifier',
            obs or '<li class="ax-muted">Aucun passage désigné.</li>', acts or '<li class="ax-muted">Aucune action.</li>',
            _form('action', _cache(suite=s['id']) + '<label>Action<select name="kind">%s</select></label><label>Libellé<input name="libelle"></label>'
                  '<label class="g3-case"><input type="checkbox" name="creer_tache" value="1"> Créer la tâche</label>' % ''.join(
                      '<option value="%s">%s</option>' % (k, e(v)) for k, v in ACTIONS.items()), 'Préparer l’action', 'ghost'),
            lignes or '<li class="ax-muted">Aucune échéance.</li>',
            _form('echeance', _cache(suite=s['id']) + '<label>Date (telle qu’écrite)<input type="date" name="jour" required></label><label>Source<input name="jour_source" required>'
                  '</label><label>Montant<input name="montant" inputmode="decimal" required></label><label>Devise<input name="devise" value="EUR" maxlength="3"></label>',
                  'Ajouter l’échéance', 'ghost'),
            _form('proposer', _cache(suite=s['id']) + '<label>Texte de l’acte<textarea name="texte" rows="4" required></textarea></label>', 'Proposer', 'ghost'))
    ouvrir = _form('creer', _cache(dossier=m['id']) + '<label>Acte<select name="source_kind"><option value="decision">Décision</option><option value="accord">Accord ou protocole'
                   '</option></select></label><label>Pièce (chemin)<input name="document" required></label><label>Version ou empreinte<input name="document_version" required></label>'
                   '<label>Date de l’acte<input type="date" name="date_acte"></label><label>Notifié le<input type="date" name="notification_date"></label>'
                   '<label>Preuve de la notification<input name="notification_preuve"></label>', 'Ouvrir la suite', 'ghost')
    return ('<section class="d79-card g3-suites" id="g3-suites"><div class="d79-card-head"><h2>Suites et exécution</h2></div>%s<ul class="ax-muted g3-mentions">%s</ul>'
            '<details><summary>Ouvrir une suite (décision ou accord)</summary>%s</details></section>') % (
        blocs, ''.join('<li>%s</li>' % e(x) for x in v['mentions']), ouvrir)
