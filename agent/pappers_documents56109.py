"""Plan de collecte proposé, sans réseau, coffre ou écriture de fichier.

Les entités, métadonnées et permissions proviennent des services de confiance,
jamais de booléens faisant foi fournis par le modèle.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from .common import Stop

TYPES=frozenset({'statuts','acte','comptes'})


@dataclass(frozen=True)
class Societe:
    siren: str
    role: str
    registre_version: str


@dataclass(frozen=True)
class Document:
    siren: str
    identifiant: str
    revision: str
    type: str
    token: str


def _siren(siren):
    # La correspondance société/dossier est vérifiée au registre, pas par le LLM.
    if not isinstance(siren,str) or not re.fullmatch(r'[0-9]{9}',siren):
        raise Stop('siren_societe_a_verifier')
    return siren


def _decimal(value):
    if isinstance(value,bool):raise Stop('credits_pappers_invalides')
    try:d=Decimal(str(value))
    except (InvalidOperation,ValueError):raise Stop('credits_pappers_invalides') from None
    if not d.is_finite() or d<0:raise Stop('credits_pappers_invalides')
    return d


def requete_statuts(siren):
    return {'method':'GET','path':'/v2/document/statuts','params':{'siren':_siren(siren)}}


def requete_document(token):
    if not isinstance(token,str) or not 1<=len(token)<=4096 or re.search(r'[\x00-\x1f\x7f]',token):
        raise Stop('identifiant_document_pappers_invalide')
    return {'method':'GET','path':'/v2/document/telechargement','params':{'token':token}}


def planifier(*, dossier, societes, documents, deja_connus, mode, declenchement,
              autorisation_collecte, max_documents, credits_par_document, credits_disponibles):
    """Plans fermés et coût explicite ; ne réserve pas lui-même le budget.

    Le prix par document est un instantané de tarif qualifié ; les recherches et
    fiches ont leur propre coût à réserver séparément par le service appelant.
    Une sélection doit être bornée avant cet appel, pas issue d'un flux infini.
    """
    if not dossier or mode not in ('demande','proposer','automatique') or declenchement not in ('utilisateur','agent'):
        raise Stop('politique_collecte_invalide')
    if type(max_documents) is not int or not 1<=max_documents<=100:
        raise Stop('limite_documents_invalide')
    if len(societes)>20 or len(documents)>1000:
        raise Stop('selection_collecte_trop_large')
    entites={}
    for s in societes:
        _siren(s.siren)
        if s.role not in ('client','adversaire') or not s.registre_version:
            raise Stop('societe_ou_role_a_verifier')
        if s.siren in entites and entites[s.siren] != s:
            raise Stop('societe_ou_role_en_conflit')
        entites[s.siren]=s
    known=set(deja_connus);selection={}
    for d in documents:
        if d.siren not in entites or d.type not in TYPES or not d.identifiant or not d.revision:
            raise Stop('document_hors_perimetre')
        key=(d.siren,d.identifiant,d.revision)
        if key in selection and selection[key]!=d:
            raise Stop('metadonnees_document_en_conflit')
        if key not in known:
            requete_document(d.token)
            selection[key]=d
    chosen=list(selection.values())
    if len(chosen)>max_documents:raise Stop('selection_documents_a_reduire')
    cost=None if credits_par_document is None else _decimal(credits_par_document)*len(chosen)
    # Absence de nouvelle pièce : aucun téléchargement, indépendamment d'un tarif manquant.
    if not chosen:return {'etat':'aucun_nouveau_document_selectionne','documents':[],'credits_documents':'0'}
    if cost is None:return {'etat':'cout_a_preciser','documents':chosen,'credits_documents':None}
    if cost>_decimal(credits_disponibles):raise Stop('budget_pappers_depasse')
    autorise=(autorisation_collecte is True and (mode=='automatique' or declenchement=='utilisateur'))
    result=[]
    for d in chosen:
        canonical=json.dumps([dossier,d.siren,d.identifiant,d.revision],ensure_ascii=False,separators=(',',':'))
        result.append({'document':d,'role':entites[d.siren].role,'categorie':'documents_publics_societes',
                       'cle':hashlib.sha256(canonical.encode()).hexdigest(),
                       'requete':requete_document(d.token) if autorise else None})
    return {'etat':'a_telecharger' if autorise else 'a_autoriser','documents':result,'credits_documents':str(cost)}


def verifier_pdf(raw, *, max_octets=32*1024*1024):
    """Validation de transport uniquement ; contrôle PDF métier séparé."""
    if type(raw) is not bytes or not 0<len(raw)<=max_octets or not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-2048:]:
        raise Stop('document_pdf_non_recu')
    return {'sha256':hashlib.sha256(raw).hexdigest(),'taille':len(raw)}



# ======================================================================================== 5.6.109 (lot G4, G17) : raccordement au produit
# Capacité « API Pappers Entreprises » dans les connexions existantes (``connexions5652`` : même registre, même coffre, mêmes états et preuves),
# DISTINCTE de Pappers MCP (``sources5632``) et de Pappers Justice (``adaptateurs5652.PappersJustice``) : leur présence n'atteste pas la collecte
# binaire. Le site public Pappers n'est JAMAIS navigué automatiquement (CGU) : ce module n'importe pas le navigateur supervisé et ne connaît que
# les routes fermées de l'API. Documents rangés dans le registre des documents publics (``registre_docs5688``, colonnes du lot S) et dans le
# sous-dossier « Documents publics des sociétés » : un document public de l'adversaire n'est JAMAIS une « pièce communiquée »
# (``piece_communiquee`` = 0, jamais dans PIECES ADVERSES ni numéroté). Crédits : enveloppe du cabinet (plafond mensuel explicite), du dossier et
# de l'autorisation, réservés AVANT chaque appel (recherche et document distingués) et remontés au registre de consommation (lot M,
# ``budget_ledger5689.noter_quantite`` : unité « credit_pappers », jamais une fausse devise).
from datetime import datetime as _dt, timedelta as _td, timezone as _tz
import uuid as _uuid
from urllib.parse import urlencode as _urlencode

from .adaptateurs5652 import Adapter as _Adapter

PROVIDER = 'pappers_entreprises'
BASE_API = 'https://api.pappers.fr'           # domaine fermé ; aucune adresse proposée par le modèle n'est exécutée
SITE_PUBLIC = 'www.pappers.fr'               # jamais navigué automatiquement
CAP_LISTE = 'entreprises.documents'
CAP_FICHIER = 'entreprises.telecharger'
MODES = {'demande': 'Sur demande uniquement', 'proposer': 'Proposer les documents utiles', 'automatique': 'Collecter automatiquement'}
ORDRE_MODES = ('demande', 'proposer', 'automatique')
POLITIQUE = 'pappers56109:politique'
DOSSIER_DOCUMENTS = 'Documents publics des sociétés'
MAX_DOCUMENT = 32 * 1024 * 1024
INCERTAINS = ('delai_http_depasse', 'connexion_http_indisponible', 'http_500', 'http_502', 'http_503', 'http_504')
LIBELLES_TYPES = {'statuts': 'Statuts', 'acte': 'Acte déposé', 'comptes': 'Comptes publiés'}
ETATS_COLLECTE = {'terminee': 'Collecte terminée', 'partielle': 'Collecte partielle', 'a_autoriser': 'À autoriser : rien n’a été téléchargé',
                  'cout_a_preciser': 'Tarif en crédits à préciser : rien n’a été téléchargé', 'plafond_credits_a_definir': 'Plafond de crédits du cabinet à définir',
                  'budget_pappers_depasse': 'Budget de crédits atteint : rien de plus n’a été téléchargé',
                  'capacite_a_qualifier': 'Connexion API Pappers Entreprises à vérifier (Paramètres › Connexions API)',
                  'reseau_non_autorise': 'Bloquée par la politique de données (local strict)', 'suspendue': 'IA en pause : collecte d’initiative suspendue',
                  'aucune_societe_confirmee': 'Aucune société confirmée (SIREN et rôle) dans le dossier', 'aucun_nouveau_document_selectionne': 'Aucun nouveau document',
                  'selection_documents_a_reduire': 'Trop de documents pour la limite fixée : réduisez la sélection'}
SPEC = {'label': 'Pappers Entreprises (API)', 'family': 'droit', 'auth': 'Clé API Pappers (en-tête api-key), offre « API Pappers Entreprises »',
        'access': 'https://www.pappers.fr/api', 'docs': 'https://www.pappers.fr/api/documentation',
        'fields': [{'name': 'api_key', 'label': 'Clé API', 'secret': True, 'required': True, 'help': ''}],
        'caps': {CAP_LISTE: ('Lister statuts, actes et comptes publics d’une société (crédits)', 'lecture', None),
                 CAP_FICHIER: ('Télécharger ces documents dans le dossier (crédits)', 'lecture', None)},
        'usage': 'Statuts, actes et comptes publiés du client et des sociétés adverses, rangés dans « Documents publics des sociétés ». Distinct de '
                 'Pappers MCP et de Pappers Justice : chaque capacité se vérifie à part.',
        'note': 'Chaque appel consomme des crédits selon l’offre du compte : plafond du cabinet obligatoire. Le site public Pappers n’est jamais '
                'consulté automatiquement (conditions d’utilisation).'}
SCHEMA_G17 = '''
CREATE TABLE IF NOT EXISTS pappers_appels56109(call_id TEXT PRIMARY KEY, dossier TEXT NOT NULL, siren TEXT NOT NULL, nature TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '',
  credits_reserves TEXT NOT NULL, credits_declares TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, autorisation TEXT NOT NULL DEFAULT '', mois TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pappers_appels56109_mois ON pappers_appels56109(mois, etat);
CREATE TABLE IF NOT EXISTS pappers_dossiers56109(dossier TEXT PRIMARY KEY, mode TEXT NOT NULL, credits_max TEXT NOT NULL DEFAULT '', maj_par TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pappers_autorisations56109(id TEXT PRIMARY KEY, dossier TEXT NOT NULL, sirens_json TEXT NOT NULL, types_json TEXT NOT NULL,
  max_documents INTEGER NOT NULL, credits_max TEXT NOT NULL, expire_le TEXT NOT NULL, par TEXT NOT NULL, cree_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pappers_ops56109(acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}',
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''


def ensure_schema(desk):
    """Démarrage (``Desk._prepare``) : tables du lot ; le registre des documents (``registre_docs5688``) et ses colonnes sont ceux des lots INPI et S."""
    if desk.db.in_transaction:
        desk.db.commit()
    from . import international56109
    international56109.ensure_schema(desk)          # colonnes du registre des documents publics (lot S), idempotent
    desk.db.executescript(SCHEMA_G17)
    desk.db.commit()


def _now():
    return _dt.now(_tz.utc).isoformat(timespec='seconds')


class PappersEntreprises(_Adapter):
    """Adaptateur de l'API Pappers Entreprises : routes fermées, clé injectée au dernier moment dans l'en-tête ``api-key`` (jamais dans
    l'adresse, le modèle, les journaux ou les fichiers). Formats relus dans la documentation publique ; à qualifier sur un compte réel."""
    provider = PROVIDER
    version = '1'

    @property
    def base(self):
        return BASE_API

    def headers(self):
        return {'api-key': self.ctx.secret('api_key')}

    def identity(self):
        out = self.request('GET', '/v2/suivi-jetons')
        restant = out.get('jetons_restants') if isinstance(out, dict) else None
        return {'compte': 'clé API valide', **({'jetons_restants': restant} if isinstance(restant, int) else {})}

    def cap_entreprises_documents(self, p):
        siren = _siren(p.get('siren'))
        out = self.request('GET', '/v2/entreprise?' + _urlencode({'siren': siren}))
        out = out if isinstance(out, dict) else {}
        docs = []
        st = out.get('derniers_statuts')
        if isinstance(st, dict) and st.get('token'):
            docs.append({'type': 'statuts', 'reference': 'statuts', 'revision': str(st.get('date_depot') or st['token'][:16]), 'token': st['token'],
                         'date_depot': str(st.get('date_depot') or ''), 'date_document': str(st.get('date_acte') or '')})
        for a in (out.get('actes') or [])[:50]:
            if isinstance(a, dict) and a.get('token'):
                types = ' ; '.join(str(x.get('type') or '') for x in (a.get('actes') or []) if isinstance(x, dict))[:200]
                docs.append({'type': 'acte', 'reference': 'acte:%s:%s' % (a.get('date_depot') or '', types), 'revision': str(a.get('date_depot') or ''),
                             'token': a['token'], 'date_depot': str(a.get('date_depot') or ''), 'date_document': '', 'libelle': types})
        for c in (out.get('comptes') or [])[:20]:
            if isinstance(c, dict) and c.get('token'):
                docs.append({'type': 'comptes', 'reference': 'comptes:%s' % (c.get('date_cloture') or ''), 'revision': str(c.get('date_depot') or ''),
                             'token': c['token'], 'date_depot': str(c.get('date_depot') or ''), 'date_document': str(c.get('date_cloture') or '')})
        return {'documents': docs, 'denomination': str(out.get('nom_entreprise') or out.get('denomination') or '')[:200]}

    def cap_entreprises_telecharger(self, p):
        req = requete_document(p.get('token'))
        raw = self.request('GET', req['path'] + '?' + _urlencode(req['params']), raw=True, accept='application/pdf', limit=MAX_DOCUMENT + 1)
        return {'fichier': raw if isinstance(raw, bytes) else bytes(raw or b'')}


# ------------------------------------------------------------------------------------------------ politique, autorisations, budget
def politique(desk):
    raw = desk.settings(POLITIQUE, None) or {}
    out = {'mode': raw.get('mode') if raw.get('mode') in MODES else 'demande', 'max_documents': int(raw.get('max_documents') or 10),
           'credits_document': raw.get('credits_document'), 'credits_recherche': raw.get('credits_recherche'), 'credits_mois': raw.get('credits_mois')}
    out['max_documents'] = max(1, min(out['max_documents'], 100))
    return out


def enregistrer_politique(desk, acteur, role, data):
    """Administrateur : mode du cabinet, nombre de documents, tarif qualifié (crédits par recherche et par document, saisis d'après l'offre
    du compte — jamais figés dans le code) et plafond mensuel de crédits (obligatoire avant toute collecte)."""
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    out = politique(desk)
    if data.get('mode') not in (None, ''):
        if data['mode'] not in MODES:
            raise Stop('politique_collecte_invalide')
        out['mode'] = data['mode']
    if data.get('max_documents') not in (None, ''):
        try:
            n = int(data['max_documents'])
        except (TypeError, ValueError):
            raise Stop('limite_documents_invalide') from None
        if not 1 <= n <= 100:
            raise Stop('limite_documents_invalide')
        out['max_documents'] = n
    for k in ('credits_document', 'credits_recherche', 'credits_mois'):
        if k in data:
            v = str(data.get(k) if data.get(k) is not None else '').strip().replace(',', '.')
            out[k] = None if v == '' else str(_decimal(v))
    desk.setting(POLITIQUE, out)
    desk.audit('pappers56109_politique', {'mode': out['mode'], 'par': acteur})
    return out


def politique_dossier(desk, dossier):
    """Le dossier peut RESTREINDRE la règle du cabinet (mode plus prudent, plafond propre), jamais l'élargir."""
    p = politique(desk)
    row = desk.db.execute('SELECT * FROM pappers_dossiers56109 WHERE dossier=?', (dossier,)).fetchone()
    if row:
        if ORDRE_MODES.index(row['mode']) < ORDRE_MODES.index(p['mode']):
            p['mode'] = row['mode']
        if row['credits_max'] not in ('', None):
            p['credits_dossier'] = row['credits_max']
    return p


def restreindre_dossier(desk, acteur, dossier, mode, credits_max=''):
    from . import acces5697
    a = acces5697.exiger(desk, acteur, dossier_id=dossier)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    if mode not in MODES:
        raise Stop('politique_collecte_invalide')
    c = str(credits_max or '').strip().replace(',', '.')
    desk.db.execute('INSERT OR REPLACE INTO pappers_dossiers56109(dossier,mode,credits_max,maj_par,maj_le) VALUES(?,?,?,?,?)',
                    (dossier, mode, str(_decimal(c)) if c else '', a.compte, _now()))
    desk.db.commit()
    return politique_dossier(desk, dossier)


def autoriser(desk, acteur, dossier, sirens, types, max_documents, credits_max, jours=30):
    """Autorisation bornée d'une collecte d'initiative : sociétés, types, nombre de documents, crédits maximum, durée. Aucune confirmation par
    fichier ensuite ; l'agent ne fournit jamais lui-même cette autorisation (elle est relue en base par son identifiant)."""
    from . import acces5697
    a = acces5697.exiger(desk, acteur, dossier_id=dossier)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    sirens = sorted({_siren(s) for s in (sirens or [])})
    types = sorted({t for t in (types or []) if t in TYPES})
    if not sirens or not types or type(max_documents) is not int or not 1 <= max_documents <= 100:
        raise Stop('autorisation_invalide')
    ident = 'pa-' + _uuid.uuid4().hex[:20]
    desk.db.execute('INSERT INTO pappers_autorisations56109(id,dossier,sirens_json,types_json,max_documents,credits_max,expire_le,par,cree_le) VALUES(?,?,?,?,?,?,?,?,?)',
                    (ident, dossier, json.dumps(sirens), json.dumps(types), max_documents, str(_decimal(credits_max)),
                     (_dt.now(_tz.utc) + _td(days=max(1, min(int(jours), 365)))).isoformat(timespec='seconds'), a.compte, _now()))
    desk.db.commit()
    return ident


def _autorisation(desk, ident, dossier):
    row = desk.db.execute('SELECT * FROM pappers_autorisations56109 WHERE id=? AND dossier=?', (str(ident or ''), dossier)).fetchone()
    if not row or row['expire_le'] < _now():
        return None
    return row


def _mois(desk):
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo((desk.c.get('calendar') or {}).get('timezone') or 'Europe/Paris')
    except Exception:   # noqa: BLE001
        zone = ZoneInfo('Europe/Paris')
    return _dt.now(zone).strftime('%Y-%m')


def credits_engages(desk, mois=None, dossier=None, autorisation=None):
    """Crédits réservés, déclarés ou incertains (un appel parti sans réponse reste compté) ; un appel annulé avant l'envoi ne compte pas."""
    sql, params = "SELECT credits_reserves, credits_declares, etat FROM pappers_appels56109 WHERE etat IN ('reserve','declare','incertain')", []
    for col, val in (('mois', mois), ('dossier', dossier), ('autorisation', autorisation)):
        if val:
            sql += ' AND %s=?' % col
            params.append(val)
    total = Decimal(0)
    for r in desk.db.execute(sql, params):
        total += Decimal(r['credits_declares'] if r['etat'] == 'declare' and r['credits_declares'] else r['credits_reserves'])
    return total


def reserver(desk, dossier, siren, nature, credits, reference='', autorisation=''):
    """Réservation ATOMIQUE avant l'appel : plafond mensuel du cabinet, plafond du dossier, plafond de l'autorisation. Refus : rien n'est appelé."""
    p = politique_dossier(desk, dossier)
    if p.get('credits_mois') in (None, ''):
        raise Stop('plafond_credits_a_definir')
    cout = _decimal(credits)
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        mois = _mois(desk)
        if credits_engages(desk, mois=mois) + cout > Decimal(p['credits_mois']):
            raise Stop('budget_pappers_depasse')
        if p.get('credits_dossier') and credits_engages(desk, dossier=dossier) + cout > Decimal(p['credits_dossier']):
            raise Stop('budget_pappers_depasse')
        if autorisation:
            row = desk.db.execute('SELECT credits_max FROM pappers_autorisations56109 WHERE id=?', (autorisation,)).fetchone()
            if not row or credits_engages(desk, autorisation=autorisation) + cout > Decimal(row['credits_max']):
                raise Stop('budget_pappers_depasse')
        call_id = 'pap-' + _uuid.uuid4().hex
        desk.db.execute('INSERT INTO pappers_appels56109(call_id,dossier,siren,nature,reference,credits_reserves,etat,autorisation,mois,at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (call_id, dossier, siren, nature, str(reference)[:200], str(cout), 'reserve', autorisation or '', mois, _now()))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    return call_id


def _regler(desk, call_id, etat, credits=None):
    """Après l'appel : déclaré (crédits du tarif retenu), incertain (réponse perdue : compté, jamais rejoué) ou annulé (rien n'est parti).
    La quantité rejoint le registre de consommation (lot M) sous l'unité « credit_pappers »."""
    desk.db.execute('UPDATE pappers_appels56109 SET etat=?, credits_declares=? WHERE call_id=?', (etat, '' if credits is None else str(credits), call_id))
    desk.db.commit()
    if etat == 'annule':
        return
    row = desk.db.execute('SELECT * FROM pappers_appels56109 WHERE call_id=?', (call_id,)).fetchone()
    try:
        from .budget_ledger5689 import noter_quantite
        noter_quantite(desk.c['state_dir'], call_id, 'pappers', 'credit_pappers', row['credits_declares'] or row['credits_reserves'],
                       etat='declaree' if etat == 'declare' else 'estimee', operation='pappers_' + row['nature'], mission=row['dossier'],
                       source='pappers_documents56109', config=desk.c)
    except Exception as ex:   # noqa: BLE001 — le relevé de consommation ne défait jamais la réservation locale (elle reste comptée)
        desk.audit('pappers56109_releve_conso_impossible', {'appel': call_id, 'cause': type(ex).__name__})


# ------------------------------------------------------------------------------------------------ sociétés du registre du dossier
def societes_confirmees(desk, dossier):
    """Sociétés liées au dossier dans le registre (fiches de contact « société » liées comme client ou partie adverse, fiche client de
    facturation) avec un SIREN valide. Un nom ou une ville ne suffisent pas. SIREN aux rôles contradictoires : question, sans bloquer les autres."""
    questions = []
    roles = {'client': 'client', 'partie_adverse': 'adversaire'}
    try:
        rows = desk.db.execute("SELECT c.id, c.name, c.siren, c.kind, c.updated, l.role FROM contacts5636 c JOIN contact_links5636 l ON l.contact=c.id "
                               "WHERE l.matter=? AND l.role IN ('client','partie_adverse') AND c.archived=0", (dossier,)).fetchall()
    except Exception:   # noqa: BLE001 — annuaire absent
        rows = []
    candidats = []
    for r in rows:
        siren = re.sub(r'\s', '', str(r['siren'] or ''))
        if r['kind'] != 'societe':
            continue
        if not re.fullmatch(r'[0-9]{9}', siren):
            questions.append({'societe': r['name'], 'question': 'SIREN à confirmer pour cette société.'})
            continue
        candidats.append((siren, roles[r['role']], 'contact:%s:%s' % (r['id'], r['updated']), r['name']))
    try:
        row = desk.db.execute('SELECT data_json, version FROM b53_clients WHERE matter=?', (dossier,)).fetchone()
        if row:
            d = json.loads(row['data_json'] or '{}')
            siren = re.sub(r'\s', '', str(d.get('siren') or ''))
            if re.fullmatch(r'[0-9]{9}', siren) and d.get('kind') != 'consommateur':
                candidats.append((siren, 'client', 'facturation:%s' % row['version'], d.get('nom') or ''))
    except Exception:   # noqa: BLE001
        pass
    par_siren = {}
    for siren, role, version, nom in candidats:
        par_siren.setdefault(siren, []).append((role, version, nom))
    trouvees = {}
    for siren, vals in par_siren.items():
        if len({v[0] for v in vals}) > 1:
            questions.append({'siren': siren, 'question': 'Ce SIREN est enregistré à la fois comme client et comme partie adverse : précisez le rôle.'})
            continue
        trouvees[siren] = (Societe(siren, vals[0][0], vals[0][1]), vals[0][2])
    return trouvees, questions


def _connexion(desk):
    from .connexions5652 import _connections_for
    lignes = [r for r in _connections_for(desk, CAP_LISTE, 'cabinet') if r['provider'] == PROVIDER]
    lignes = [r for r in lignes if json.loads(r['caps_json'] or '{}').get(CAP_FICHIER, {}).get('enabled')]
    return lignes[0] if lignes else None


def _local_strict(desk):
    from .selection_sources5697 import local_strict
    return local_strict(desk)


def _pause(desk):
    from .sources_publiques56109 import _pause as pause
    return pause(desk)


# ------------------------------------------------------------------------------------------------ collecte
def collecter(desk, acteur, dossier, cle, declenchement='utilisateur', autorisation='', types=None):
    """Documents publics des sociétés confirmées du dossier ; idempotent par (acteur, clé). ``declenchement`` : « utilisateur » (ordre de
    l'avocat dans la politique) ou « agent » (initiative : mode automatique ET autorisation bornée valide exigés)."""
    from . import acces5697
    ensure_schema(desk)
    a = acces5697.exiger(desk, acteur, dossier_id=str(dossier or ''))
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    if declenchement not in ('utilisateur', 'agent'):
        raise Stop('politique_collecte_invalide')
    cle = str(cle or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}', cle):
        raise Stop('cle_requise')
    types = sorted({t for t in (types or TYPES) if t in TYPES}) or sorted(TYPES)
    empreinte = hashlib.sha256(json.dumps([dossier, declenchement, autorisation, types]).encode()).hexdigest()
    row = desk.db.execute('SELECT * FROM pappers_ops56109 WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()
    if row:
        if row['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        if row['etat'] == 'fait':
            return {**json.loads(row['resultat']), 'rejoue': True}
        raise Stop('operation_en_cours')
    desk.db.execute('INSERT INTO pappers_ops56109(acteur,cle,empreinte,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?)', (a.compte, cle, empreinte, 'en_cours', _now(), _now()))
    desk.db.commit()
    try:
        out = _collecter(desk, a, dossier, declenchement, autorisation, types, cle)
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        desk.db.execute('DELETE FROM pappers_ops56109 WHERE acteur=? AND cle=?', (a.compte, cle))   # erreur : réessayable ; les crédits engagés restent comptés
        desk.db.commit()
        raise
    desk.db.execute("UPDATE pappers_ops56109 SET etat='fait', resultat=?, maj_le=? WHERE acteur=? AND cle=?",
                    (json.dumps(out, ensure_ascii=False, default=str), _now(), a.compte, cle))
    desk.db.commit()
    return out


def _bilan(etat, **plus):
    return {'etat': etat, 'etat_libelle': ETATS_COLLECTE.get(etat, etat), 'classes': [], 'deja_la': 0, 'versions_nouvelles': [], 'a_verifier': [],
            'erreurs': [], 'questions': [], 'proposes': [], 'credits_engages': '0', 'piece_communiquee': False, **plus}


def _collecter(desk, a, dossier, declenchement, autorisation, types, cle):
    from .pause5629 import checkpoint
    if _local_strict(desk):
        return _bilan('reseau_non_autorise')
    if declenchement == 'agent' and _pause(desk):
        return _bilan('suspendue')
    p = politique_dossier(desk, dossier)
    if p.get('credits_mois') in (None, ''):
        return _bilan('plafond_credits_a_definir')
    soc, questions = societes_confirmees(desk, dossier)
    aut = _autorisation(desk, autorisation, dossier) if autorisation else None
    if declenchement == 'agent':
        if aut is None:
            return _bilan('a_autoriser', questions=questions)
        permis = set(json.loads(aut['sirens_json']))
        soc = {s: v for s, v in soc.items() if s in permis}
        types = [t for t in types if t in json.loads(aut['types_json'])]
    if not soc:
        return _bilan('aucune_societe_confirmee', questions=questions)
    row = _connexion(desk)
    if row is None:
        return _bilan('capacite_a_qualifier', questions=questions)
    if p.get('credits_recherche') in (None, ''):
        return _bilan('cout_a_preciser', questions=questions)     # une recherche de métadonnées consomme aussi des crédits
    from .connexions5652 import adapter
    ad = adapter(desk, row)
    bilan = _bilan('partielle', questions=questions)
    metadonnees, meta, denominations = [], {}, {}
    for siren in sorted(soc):
        checkpoint(desk)
        try:
            call = reserver(desk, dossier, siren, 'recherche', p['credits_recherche'], autorisation=aut['id'] if aut else '')
        except Stop as ex:
            return {**bilan, 'etat': str(ex).split(':')[0], 'etat_libelle': ETATS_COLLECTE.get(str(ex).split(':')[0], str(ex))}
        try:
            out = ad.call(CAP_LISTE, {'siren': siren})
        except Stop as ex:
            code = str(ex).split(':')[0]
            _regler(desk, call, 'incertain' if code in INCERTAINS else 'annule')
            bilan['erreurs'].append({'siren': siren, 'code': code})      # une panne ou un refus ne prouve pas l'absence de documents
            continue
        _regler(desk, call, 'declare', p['credits_recherche'])
        denominations[siren] = out.get('denomination') or soc[siren][1]
        for d in out.get('documents') or []:
            if d.get('type') in types:
                doc = Document(siren, str(d['reference'])[:200], str(d['revision'])[:60], d['type'], str(d['token']))
                metadonnees.append(doc)
                meta[(doc.siren, doc.identifiant, doc.revision)] = d
    connus = {(r['siren'], r['reference'], r['revision']): r for r in desk.db.execute(
        "SELECT siren, reference, revision, sha256, chemin FROM registre_docs5688 WHERE matter=? AND service=? AND etat IN ('classe','a_verifier')", (dossier, PROVIDER))}
    maximum = min(p['max_documents'], aut['max_documents'] if aut else 100)
    try:
        plan = planifier(dossier=dossier, societes=[v[0] for v in soc.values()], documents=metadonnees, deja_connus=list(connus),
                         mode=p['mode'], declenchement=declenchement, autorisation_collecte=(declenchement == 'utilisateur' or aut is not None),
                         max_documents=maximum, credits_par_document=p.get('credits_document'), credits_disponibles=_disponible(desk, dossier, p, aut))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return {**bilan, 'etat': code, 'etat_libelle': ETATS_COLLECTE.get(code, code)}
    bilan['deja_la'] = len([k for k in meta if k in connus])
    if plan['etat'] != 'a_telecharger':
        proposes = [x['document'] if isinstance(x, dict) else x for x in plan['documents']]
        return {**bilan, 'etat': plan['etat'], 'etat_libelle': ETATS_COLLECTE.get(plan['etat'], plan['etat']),
                'proposes': [{'siren': d.siren, 'type': d.type, 'reference': d.identifiant, 'revision': d.revision} for d in proposes]}
    racine = _racine_dossier(desk, dossier)
    from .common import clean_path, under
    from .fichiers5670 import client
    dav = client(desk)
    for el in plan['documents']:
        checkpoint(desk)
        if declenchement == 'agent' and _pause(desk):
            bilan['etat'] = 'suspendue'
            break
        d = el['document']
        try:
            call = reserver(desk, dossier, d.siren, 'document', p['credits_document'], d.identifiant, aut['id'] if aut else '')
        except Stop as ex:
            bilan['erreurs'].append({'siren': d.siren, 'reference': d.identifiant, 'code': str(ex).split(':')[0]})
            bilan['etat'] = str(ex).split(':')[0]
            break
        try:
            got = ad.call(CAP_FICHIER, {'token': d.token})
        except Stop as ex:
            code = str(ex).split(':')[0]
            incertain = code in INCERTAINS
            _regler(desk, call, 'incertain' if incertain else 'annule')
            (bilan['a_verifier'] if incertain else bilan['erreurs']).append({'siren': d.siren, 'reference': d.identifiant, 'code': code})
            continue
        _regler(desk, call, 'declare', p['credits_document'])               # le crédit est consommé même si le binaire n'est pas un PDF
        try:
            info = verifier_pdf(got.get('fichier'), max_octets=MAX_DOCUMENT)
        except Stop:
            bilan['a_verifier'].append({'siren': d.siren, 'reference': d.identifiant, 'code': 'document_pdf_non_recu'})
            continue
        m = meta.get((d.siren, d.identifiant, d.revision)) or {}
        nom_soc = re.sub(r'[^\w .,()\'’-]+', ' ', str(denominations.get(d.siren) or d.siren)).strip()[:60] or d.siren
        dossier_cible = clean_path('%s/%s/%s (%s)' % (racine, DOSSIER_DOCUMENTS, nom_soc, d.siren))
        if not under(dossier_cible, racine):
            raise Stop('destination_hors_dossier')
        dav.ensure_folder(dossier_cible, racine)
        nom = re.sub(r'\s+', ' ', '%s %s (dépôt %s).pdf' % (LIBELLES_TYPES[d.type], (m.get('date_document') or '')[:10], (m.get('date_depot') or d.revision)[:10]))
        chemin = _deposer(dav, clean_path(dossier_cible + '/' + nom), got['fichier'])
        relu = dav.read_archive(chemin, MAX_DOCUMENT + 1)
        etat = 'classe' if hashlib.sha256(relu).hexdigest() == info['sha256'] else 'a_verifier'
        anciennes = [k for k in connus if k[0] == d.siren and k[1] == d.identifiant and k[2] != d.revision]
        desk.db.execute('INSERT OR IGNORE INTO registre_docs5688(inpi_id,matter,siren,nature,libelle,date_depot,date_cloture,chemin,sha256,taille,texte_etat,extrait,'
                        'recupere_le,service,societe_cle,reference,revision,nature_document,format,devise,cout,credits,role_societe,etat,cle_idem,piece_communiquee) '
                        "VALUES(?,?,?,?,?,?,?,?,?,?,'non_lu','',?,?,?,?,?,'ordinaire','application/pdf','','',?,?,?,?,0)",
                        (el['cle'], dossier, d.siren, 'document_public_societe', ('%s %s' % (LIBELLES_TYPES[d.type], m.get('libelle') or '')).strip()[:200],
                         (m.get('date_depot') or '')[:10], (m.get('date_document') or '')[:10] if d.type == 'comptes' else '', chemin, info['sha256'], info['taille'],
                         _now(), PROVIDER, d.siren, d.identifiant, d.revision, str(p['credits_document']), el['role'], etat, cle))
        desk.db.commit()
        (bilan['classes'] if etat == 'classe' else bilan['a_verifier']).append({'siren': d.siren, 'role': el['role'], 'type': d.type, 'reference': d.identifiant,
                                                                                'revision': d.revision, 'chemin': chemin, 'categorie': el['categorie']})
        if anciennes:
            bilan['versions_nouvelles'].append({'siren': d.siren, 'reference': d.identifiant, 'ancienne': anciennes[0][2], 'nouvelle': d.revision})
    if bilan['etat'] == 'partielle' and not bilan['erreurs'] and not bilan['a_verifier']:
        bilan['etat'] = 'terminee'
    bilan['etat_libelle'] = ETATS_COLLECTE.get(bilan['etat'], bilan['etat'])
    bilan['credits_engages'] = str(credits_engages(desk, dossier=dossier))
    desk.audit('pappers56109_collecte', {'dossier': dossier, 'classes': len(bilan['classes']), 'etat': bilan['etat'], 'declenchement': declenchement})
    return bilan


def _disponible(desk, dossier, p, aut):
    restes = [Decimal(p['credits_mois']) - credits_engages(desk, mois=_mois(desk))]
    if p.get('credits_dossier'):
        restes.append(Decimal(p['credits_dossier']) - credits_engages(desk, dossier=dossier))
    if aut is not None:
        restes.append(Decimal(aut['credits_max']) - credits_engages(desk, autorisation=aut['id']))
    return str(max(Decimal(0), min(restes)))


def _racine_dossier(desk, dossier):
    from .common import load_matters
    try:
        m = next((x for x in load_matters(desk.c) if str(x['id']) == dossier), None)
    except Stop:
        m = None
    if m and m.get('path'):
        return m['path']
    r = desk.db.execute('SELECT chemin FROM dossiers5697 WHERE id=?', (dossier,)).fetchone()
    if r and r['chemin']:
        return r['chemin']
    raise Stop('introuvable')


def _deposer(dav, chemin, data):
    base, ext = chemin.rsplit('.', 1)
    for n in range(1, 6):
        cible = chemin if n == 1 else '%s (%d).%s' % (base, n, ext)
        try:
            return dav.put_archive(cible, data, MAX_DOCUMENT, 'application/pdf')
        except Stop as ex:
            if str(ex).split(':')[0] != 'http_412' or n >= 5:
                raise
    raise Stop('depot_impossible')


def documents(desk, acteur, dossier):
    """Documents publics des sociétés classés pour un dossier visible : versions conservées, rôle, dates distinctes ; jamais « pièce communiquée »."""
    from . import acces5697
    acces5697.exiger(desk, acteur, dossier_id=dossier)
    return [dict(r) for r in desk.db.execute("SELECT siren, role_societe, libelle, reference, revision, date_depot, date_cloture, recupere_le, chemin, sha256, taille, "
                                             "credits, etat, piece_communiquee FROM registre_docs5688 WHERE matter=? AND service=? ORDER BY siren, reference, revision",
                                             (dossier, PROVIDER))]


def carte_html(desk, prefix, role):
    from html import escape as e
    p = politique(desk)
    pol = ''
    if role == 'administrateur':
        pol = ('<details class="r52-edit"><summary class="ax-btn ghost">Politique et crédits</summary><form class="m5-form" data-api="m56109/parcours/pappers-politique" '
               'data-reload><label>Mode<select name="mode">%s</select></label><label>Documents au plus par collecte<input name="max_documents" type="number" min="1" '
               'max="100" value="%d"></label><label>Crédits par recherche (selon votre offre)<input name="credits_recherche" value="%s"></label>'
               '<label>Crédits par document (selon votre offre)<input name="credits_document" value="%s"></label><label>Plafond mensuel de crédits'
               '<input name="credits_mois" value="%s"></label><button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details>') % (
            ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == p['mode'] else '', e(v)) for k, v in MODES.items()), p['max_documents'],
            e(p['credits_recherche'] or '', quote=True), e(p['credits_document'] or '', quote=True), e(p['credits_mois'] or '', quote=True))
    collecte = ''
    if role in ('administrateur', 'avocat'):
        collecte = ('<form class="m5-form" data-api="m56109/parcours/pappers-collecter" data-reload><input type="hidden" name="cle" value="%s">'
                    '<label>Dossier<input name="d" maxlength="200" required></label><button class="ax-btn ghost" type="submit">Obtenir les documents publics</button>'
                    '<p role="status" class="m5-status"></p></form>') % _uuid.uuid4().hex
    return ('<div class="c5614-routine r52-card g4-carte" id="g4-pappers"><div class="r52-head"><strong>Documents publics des sociétés (API Pappers Entreprises)</strong></div>'
            '<span class="r52-meta">Mode : %s ; plafond mensuel : %s crédit(s). Statuts, actes et comptes du client et des sociétés adverses, rangés dans « %s ». '
            'Un document public de l’adversaire n’est pas une pièce communiquée. Le site public Pappers n’est jamais consulté automatiquement.</span>%s%s</div>') % (
        e(MODES[p['mode']]), e(p['credits_mois'] or 'à définir'), e(DOSSIER_DOCUMENTS), pol, collecte)
