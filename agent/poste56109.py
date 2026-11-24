"""5.6.109 (lot O, F12 et G13) : missions sur le poste — médiateur, autorisations, reçus — et contrat du compagnon pour le serveur.

F12 — médiateur de mission (reprise produit de ``reference/poste109.py``)
- Une **autorisation** est créée côté application à partir de la session (jamais du modèle) : mission (``missions_v567``), personne, rôle,
  cabinet, poste exécuteur (identité du poste : ``liaison56103.identite``), révision, échéance, sources (dossiers lisibles), sorties,
  sites et connecteurs admis, réseau permis ou non, plafond d'étapes. Le modèle ne choisit ni son propriétaire, ni son poste, ni un chemin.
- ``executer`` : contrôle complet AVANT l'opération (identité et poste, pause globale ou de la mission, révocation, révision, échéance,
  source, sortie, site, connecteur, outil fermé), effet par les services existants seulement (``fichiers5670`` / ``dav``), contrôle APRÈS
  l'opération : un arrêt ou un retrait de droit pendant l'effet donne « À rapprocher », jamais un succès publié ni un nouvel essai.
- **Reçu durable** par étape (clé d'idempotence) : la même clé rend le même reçu, sans refaire l'effet ; un reçu resté « en cours » après une
  panne devient « À rapprocher ».
- **Pause** = arrêt des nouveaux effets (pause globale ou suspension de la mission) ; **révocation** = révision incrémentée, les étapes en
  vol échouent à leur contrôle de sortie.
- Outils fermés : ``rechercher_fichiers``, ``lire_fichiers`` (services documentaires existants), ``navigateur`` (navigateur supervisé du
  poste) ; ``recherche_juridique``, ``recherche_web``, ``preparer_document``, ``convertir_copie``, ``publier_brouillon``, ``recette_locale``
  et ``bureau_supervise`` sont **contrôlés puis signalés indisponibles** (pilote non livré) : aucun shell, aucune recette sans confinement
  qualifié, aucun contrôle graphique. Open Terminal, ATLAS et Wayland sont des études : aucun composant repris ni installé.

G13 — délégation au poste pour le serveur / l'ExApp (contrat du compagnon)
- ``navigateur5673.available`` reste limité au poste ; le serveur ne simule jamais un poste. Appairage par code court approuvé dans les deux
  sessions (le poste prouve la possession de sa clé ; le serveur confirme l'empreinte), ordres signés et expirants (cabinet, personne,
  appareil, mission, opération, domaines, actions, documents exacts par version et empreinte, budget, nonce, génération), **bail
  d'exécution** par opération (un seul poste agit), reçus signés par le poste. Un ordre ancien, dupliqué, révoqué ou destiné à un autre
  appareil est refusé ; aucune clé privée, aucun cookie, aucun port DevTools ni secret dans un ordre ou un reçu.
- Sans poste associé et joignable : état « Attente du poste » (import manuel possible). Le canal sortant authentifié entre le serveur et le
  compagnon n'est PAS livré ici : la délégation reste en attente tant qu'il n'existe pas.

Limite : ce module est un contrat testé avec des services simulés ; il n'est ni une sandbox OS, ni un pilote Windows/Wayland.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import ipaddress
import json
import re
import secrets
from time import time
from urllib.parse import urlsplit

from .common import Stop

# ======================================================================================== médiateur (reprise de la référence)
class Refus(Stop):
    pass


@dataclass(frozen=True)
class Portee:
    mission: str
    utilisateur: str
    cabinet: str
    poste: str
    revision: int
    expire: float
    sources: frozenset
    sorties: frozenset
    domaines: frozenset = frozenset()
    connecteurs: frozenset = frozenset()
    recettes: frozenset = frozenset()
    skills: frozenset = frozenset({'assistant_juridique'})
    bureau_autorise: bool = False
    confinement_recettes_verifie: bool = False
    limite_etapes: int = 40


@dataclass(frozen=True)
class Etat:
    revision: int
    pause: bool = False
    annulee: bool = False
    reseau_interdit: bool = False


@dataclass(frozen=True)
class Action:
    outil: str
    sources: tuple = ()
    sortie: str = ''
    url: str = ''
    connecteur: str = ''
    recette: str = ''
    requete: str = ''


@dataclass(frozen=True)
class Resultat:
    etat: str
    code: str = ''
    interne: object = field(default=None, repr=False)
    donnees: object = None

    def public(self):
        # Une sortie d'outil n'est jamais sérialisée aveuglément : seulement l'état, le code et des données non sensibles prévues.
        out = {'etat': self.etat, 'code': self.code, 'reessai_automatique': False}
        if self.donnees is not None:
            out['donnees'] = self.donnees
        return out


LECTURE = frozenset({'rechercher_fichiers', 'lire_fichiers'})
ECRITURE = frozenset({'preparer_document', 'convertir_copie', 'publier_brouillon'})
OUTILS = LECTURE | ECRITURE | frozenset({'recherche_web', 'navigateur', 'recherche_juridique', 'recette_locale', 'bureau_supervise'})
LIVRES = frozenset({'rechercher_fichiers', 'lire_fichiers', 'navigateur'})     # les autres : contrôlés puis « pilote indisponible »
RESEAU = frozenset({'recherche_web', 'navigateur', 'recherche_juridique'})


def domaine_public_autorise(url, domaines):
    """Validation syntaxique seulement ; DNS, adresses et redirections restent contrôlés par le navigateur et le transport réels."""
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 32 for c in url):
        raise Refus('adresse_invalide')
    try:
        value = urlsplit(url)
        host = (value.hostname or '').encode('idna').decode('ascii').lower()
        port = value.port
    except (ValueError, UnicodeError):
        raise Refus('adresse_invalide') from None
    if value.scheme != 'https' or not host or value.username is not None or value.password is not None or port not in (None, 443):
        raise Refus('adresse_invalide')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise Refus('adresse_ip_refusee')
    if host not in domaines and not any(host.endswith('.' + d) for d in domaines):
        raise Refus('site_non_autorise')
    return host


class Mediateur:
    def __init__(self, portee, lire_etat, peut_lire_source, *, horloge=time):
        self.portee = portee
        self.lire_etat = lire_etat
        self.peut_lire_source = peut_lire_source
        self.horloge = horloge
        self.etapes = 0

    def checkpoint(self):
        p, state = self.portee, self.lire_etat()
        if state.annulee:
            raise Refus('mission_annulee')
        if state.pause:
            raise Refus('ia_en_pause')
        if state.revision != p.revision:
            raise Refus('autorisation_perimee')
        if self.horloge() >= p.expire:
            raise Refus('autorisation_expiree')
        return state

    def verifier(self, action, *, utilisateur, cabinet, poste, accord_bureau=False):
        p, state = self.portee, self.checkpoint()
        if (utilisateur, cabinet, poste) != (p.utilisateur, p.cabinet, p.poste):
            raise Refus('identite_ou_poste_invalide')
        if 'assistant_juridique' not in p.skills:
            raise Refus('methode_juridique_absente')
        if action.outil not in OUTILS:
            raise Refus('outil_non_expose')
        if self.etapes >= p.limite_etapes:
            raise Refus('limite_etapes')
        if any(s not in p.sources or not self.peut_lire_source(s) for s in action.sources):
            raise Refus('source_non_autorisee')
        if action.outil in LECTURE and not action.sources:
            raise Refus('source_requise')
        if action.outil in ECRITURE and action.sortie not in p.sorties:
            raise Refus('sortie_non_autorisee')
        if action.outil in ('recherche_web', 'navigateur'):
            if state.reseau_interdit:
                raise Refus('reseau_interdit')
            domaine_public_autorise(action.url, p.domaines)
        if action.outil == 'recherche_juridique':
            if state.reseau_interdit:
                raise Refus('reseau_interdit')
            if action.connecteur not in p.connecteurs:
                raise Refus('connecteur_non_autorise')
        if action.outil == 'recette_locale':
            if not p.confinement_recettes_verifie:
                raise Refus('confinement_non_verifie')
            if action.recette not in p.recettes:
                raise Refus('recette_non_autorisee')
        if action.outil == 'bureau_supervise' and not (p.bureau_autorise and accord_bureau):
            raise Refus('accord_bureau_requis')
        return True

    def executer(self, action, executeur, *, utilisateur, cabinet, poste, accord_bureau=False):
        self.verifier(action, utilisateur=utilisateur, cabinet=cabinet, poste=poste, accord_bureau=accord_bureau)
        self.etapes += 1
        result = executeur(action)
        # Un arrêt après effet n'est ni une annulation de l'effet, ni un succès publié.
        try:
            state = self.checkpoint()
            if any(not self.peut_lire_source(s) for s in action.sources):
                raise Refus('source_non_autorisee')
            if state.reseau_interdit and action.outil in RESEAU:
                raise Refus('reseau_interdit')
        except Refus as ex:
            return Resultat('a_rapprocher', str(ex), result)
        donnees = result.get('public') if isinstance(result, dict) else None
        return Resultat('terminee', interne=result, donnees=donnees)


# ======================================================================================== schéma
SCHEMA = '''
CREATE TABLE IF NOT EXISTS poste56109_autorisations(id TEXT PRIMARY KEY, mission TEXT NOT NULL, utilisateur TEXT NOT NULL, role TEXT NOT NULL,
  cabinet TEXT NOT NULL, poste TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, etat TEXT NOT NULL DEFAULT 'active', expire TEXT NOT NULL,
  sources TEXT NOT NULL DEFAULT '[]', sorties TEXT NOT NULL DEFAULT '[]', domaines TEXT NOT NULL DEFAULT '[]', connecteurs TEXT NOT NULL DEFAULT '[]',
  reseau_interdit INTEGER NOT NULL DEFAULT 0, limite_etapes INTEGER NOT NULL DEFAULT 40, etapes INTEGER NOT NULL DEFAULT 0,
  catalogue TEXT NOT NULL DEFAULT '{}', cle TEXT NOT NULL, cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, UNIQUE(utilisateur, cle));
CREATE INDEX IF NOT EXISTS poste56109_autorisations_mission ON poste56109_autorisations(mission, etat);
CREATE TABLE IF NOT EXISTS poste56109_recus(id INTEGER PRIMARY KEY, autorisation TEXT NOT NULL, mission TEXT NOT NULL, utilisateur TEXT NOT NULL,
  outil TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, etat TEXT NOT NULL, code TEXT NOT NULL DEFAULT '', resume TEXT NOT NULL DEFAULT '{}',
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, UNIQUE(autorisation, cle));
CREATE TABLE IF NOT EXISTS poste56109_postes(id TEXT PRIMARY KEY, utilisateur TEXT NOT NULL, libelle TEXT NOT NULL DEFAULT '',
  cle_publique TEXT NOT NULL DEFAULT '', code_h TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, expire TEXT NOT NULL DEFAULT '',
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, vu_le TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS poste56109_ordres(nonce TEXT PRIMARY KEY, sens TEXT NOT NULL, poste TEXT NOT NULL, operation TEXT NOT NULL,
  epoch INTEGER NOT NULL, expire TEXT NOT NULL, empreinte TEXT NOT NULL, etat TEXT NOT NULL, recu TEXT NOT NULL DEFAULT '',
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS poste56109_ordres_operation ON poste56109_ordres(operation, etat);
CREATE TABLE IF NOT EXISTS poste56109_baux(operation TEXT PRIMARY KEY, poste TEXT NOT NULL, generation INTEGER NOT NULL, expire TEXT NOT NULL,
  etat TEXT NOT NULL, maj_le TEXT NOT NULL);
'''
TABLES = ('poste56109_autorisations', 'poste56109_recus', 'poste56109_postes', 'poste56109_ordres', 'poste56109_baux')
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
MAX_DUREE_MINUTES = 8 * 60
MAX_FICHIERS = 200


def ensure_schema(desk):
    """Démarrage (``Desk._prepare``) : tables du médiateur et du compagnon. Sans changement : aucune écriture."""
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if any(t not in existants for t in TABLES):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def _maintenant():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _maintenant()).isoformat()


def _ts(iso):
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _cabinet(desk):
    return str(desk.c.get('cabinet_id') or 'cabinet')[:80]


def identite_poste(desk, creer=True):
    """Identité de CE poste (clé Ed25519 du coffre, partagée avec la liaison entre postes) ; jamais un identifiant choisi par le modèle.
    Lecture (``creer=False``) : aucune création pendant un GET."""
    from .liaison56103 import identite
    return identite(desk.c['state_dir'], creer=creer).id


def _poste_local(auth=None):
    from .navigateur5673 import available
    return bool(available(auth))


# ======================================================================================== autorisations de mission
def _mission(desk, acteur, mission):
    from . import missions567
    compte, role = acteur
    try:
        return missions567._row(desk, str(mission or ''), compte, role == 'administrateur')
    except Stop:
        raise Stop('introuvable') from None


def _lisible(desk, acteur, mid):
    from .acces5697 import peut_lire_dossier
    return peut_lire_dossier(desk, acteur, mid)


def _liste(valeur, motif, maximum=50):
    if valeur in (None, ''):
        return []
    if isinstance(valeur, str):
        valeur = [x for x in re.split(r'[\s,;]+', valeur) if x]
    if isinstance(valeur, tuple):
        valeur = list(valeur)
    if not isinstance(valeur, list) or len(valeur) > maximum or any(not isinstance(x, str) or not 1 <= len(x) <= 200 for x in valeur):
        raise Stop(motif)
    return sorted(set(x.strip() for x in valeur))


def autoriser(desk, acteur, mission, sources, sorties=(), domaines=(), connecteurs=(), duree_minutes=120, reseau_interdit=False,
              limite_etapes=40, cle='', auth=None):
    """Autorisation de mission sur CE poste (poste exécuteur = identité du poste). Mêmes paramètres et même clé → même autorisation."""
    compte, role = acteur
    if role not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('role_insuffisant')
    if not CLE.match(str(cle or '')):
        raise Stop('cle_requise')
    if not _poste_local(auth):
        raise Stop('poste_absent')                 # sur le serveur : délégation au compagnon associé, jamais un faux poste
    _mission(desk, acteur, mission)
    sources, sorties = _liste(sources, 'sources_invalides'), _liste(sorties, 'sorties_invalides')
    domaines, connecteurs = _liste(domaines, 'domaines_invalides'), _liste(connecteurs, 'connecteurs_invalides')
    if not sources:
        raise Stop('sources_requises')
    from .common import load_matters
    connus = {str(m['id']) for m in load_matters(desk.c)}
    for mid in sources:
        if mid not in connus or not _lisible(desk, acteur, mid):
            raise Stop('introuvable')              # dossier absent ou réservé : même réponse neutre
    for s in sorties:
        if not re.fullmatch(r'brouillon:[A-Za-z0-9_.-]{1,80}', s) or s.split(':', 1)[1] not in sources:
            raise Stop('sorties_invalides')
    from . import nav5673
    for d in domaines:
        if not re.fullmatch(r'[a-z0-9.-]{3,253}', d) or '.' not in d or nav5673.host_kind(d) != 'public':
            raise Stop('domaines_invalides')
    for c in connecteurs:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', c):
            raise Stop('connecteurs_invalides')
    try:
        duree = max(5, min(int(duree_minutes), MAX_DUREE_MINUTES))
        limite = max(1, min(int(limite_etapes), 40))
    except (TypeError, ValueError):
        raise Stop('duree_invalide') from None
    poste = identite_poste(desk)
    contenu = {'mission': str(mission), 'sources': sources, 'sorties': sorties, 'domaines': domaines, 'connecteurs': connecteurs,
               'reseau_interdit': bool(reseau_interdit), 'limite': limite, 'duree': duree}
    row = desk.db.execute('SELECT * FROM poste56109_autorisations WHERE utilisateur=? AND cle=?', (compte, cle)).fetchone()
    if row:
        precedent = {'mission': row['mission'], 'sources': json.loads(row['sources']), 'sorties': json.loads(row['sorties']),
                     'domaines': json.loads(row['domaines']), 'connecteurs': json.loads(row['connecteurs']),
                     'reseau_interdit': bool(row['reseau_interdit']), 'limite': row['limite_etapes'], 'duree': contenu['duree']}
        if precedent != contenu:
            raise Stop('cle_reutilisee')
        return {**vue(row), 'rejoue': True}
    ident, quand = secrets.token_hex(16), _maintenant()
    desk.db.execute('INSERT INTO poste56109_autorisations(id,mission,utilisateur,role,cabinet,poste,revision,etat,expire,sources,sorties,domaines,'
                    'connecteurs,reseau_interdit,limite_etapes,etapes,catalogue,cle,cree_le,maj_le) VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,0,?,?,?,?)',
                    (ident, str(mission), compte, role, _cabinet(desk), poste, 'active', _iso(quand + timedelta(minutes=duree)),
                     json.dumps(sources), json.dumps(sorties), json.dumps(domaines), json.dumps(connecteurs), int(bool(reseau_interdit)),
                     limite, '{}', cle, _iso(quand), _iso(quand)))
    from . import missions567
    missions567._inscrire(desk, str(mission), 'poste_autorisation', {'autorisation': ident, 'sources': len(sources), 'reseau': not reseau_interdit})
    desk.db.commit()
    desk.audit('poste56109_autorisation', {'autorisation': ident, 'mission': str(mission), 'par': compte[:120]})
    return vue(desk.db.execute('SELECT * FROM poste56109_autorisations WHERE id=?', (ident,)).fetchone())


def vue(row):
    return {'id': row['id'], 'mission': row['mission'], 'etat': row['etat'], 'revision': row['revision'], 'expire': row['expire'],
            'sources': json.loads(row['sources']), 'sorties': json.loads(row['sorties']), 'domaines': json.loads(row['domaines']),
            'connecteurs': json.loads(row['connecteurs']), 'reseau': not row['reseau_interdit'], 'etapes': row['etapes'],
            'limite_etapes': row['limite_etapes'], 'poste': row['poste'][:8]}


def _autorisation(desk, acteur, ident):
    compte, role = acteur
    row = desk.db.execute('SELECT * FROM poste56109_autorisations WHERE id=?', (str(ident or '')[:64],)).fetchone()
    if not row or (row['utilisateur'] != compte and role != 'administrateur'):
        raise Stop('introuvable')
    return row


def changer_etat(desk, acteur, ident, etat, version=None):
    """Suspendre (pause : aucun nouvel effet), reprendre, révoquer (définitif) ; la révision change : les étapes en vol échouent."""
    if etat not in ('active', 'suspendue', 'revoquee'):
        raise Stop('etat_invalide')
    row = _autorisation(desk, acteur, ident)
    if version is not None and str(version) != str(row['revision']):
        raise Stop('version_perimee')
    if row['etat'] == 'revoquee':
        raise Stop('autorisation_revoquee')
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        desk.db.execute('UPDATE poste56109_autorisations SET etat=?, revision=revision+1, maj_le=? WHERE id=? AND revision=?',
                        (etat, _iso(), row['id'], row['revision']))
        from . import missions567
        missions567._inscrire(desk, row['mission'], 'poste_' + etat, {'autorisation': row['id']})
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('poste56109_' + etat, {'autorisation': row['id'], 'par': str(acteur[0])[:120]})
    return vue(desk.db.execute('SELECT * FROM poste56109_autorisations WHERE id=?', (row['id'],)).fetchone())


def revoquer_tout(desk, acteur):
    """« Révoquer les autorisations de ce poste » : toutes celles de la personne (ou du cabinet pour un administrateur)."""
    compte, role = acteur
    rows = desk.db.execute("SELECT id FROM poste56109_autorisations WHERE etat!='revoquee'" + ('' if role == 'administrateur' else ' AND utilisateur=?'),
                           () if role == 'administrateur' else (compte,)).fetchall()
    return [changer_etat(desk, acteur, r['id'], 'revoquee')['id'] for r in rows]


# ======================================================================================== exécution d'une étape IA
def _etat_fn(desk, ident):
    from . import navigateur56109

    def lire():
        row = desk.db.execute('SELECT revision, etat, reseau_interdit FROM poste56109_autorisations WHERE id=?', (ident,)).fetchone()
        if not row:
            return Etat(-1, annulee=True)
        pause = row['etat'] == 'suspendue' or bool(navigateur56109.pause_globale(desk))
        return Etat(row['revision'], pause=pause, annulee=row['etat'] == 'revoquee', reseau_interdit=bool(row['reseau_interdit']))
    return lire


def _source_fn(desk, ident, acteur):
    def peut(source):
        row = desk.db.execute('SELECT sources, catalogue FROM poste56109_autorisations WHERE id=?', (ident,)).fetchone()
        if not row:
            return False
        sources, catalogue = json.loads(row['sources']), json.loads(row['catalogue'])
        mid = source if source in sources else (catalogue.get(source) or {}).get('mid')
        return bool(mid) and mid in sources and _lisible(desk, acteur, mid)
    return peut


def _executeur(desk, row, acteur, auth):
    from .pause5629 import checkpoint

    def run(action):
        checkpoint(desk)                         # toute boucle d'effets : contrôle commun juste avant l'effet
        if action.outil not in LIVRES:
            raise Refus('pilote_indisponible')
        if action.outil == 'navigateur':
            if not _poste_local(auth):
                raise Refus('pilote_indisponible')
            from . import navigateur5673 as nv
            seance = nv.manager(desk.c).start(acteur[0], action.requete or ('Consulter ' + action.url), '', role=acteur[1])
            return {'public': {'seance': seance['id']}}
        from .common import load_matters
        from .fichiers5670 import client
        dav = client(desk)
        catalogue = json.loads(row['catalogue'])
        if action.outil == 'rechercher_fichiers':
            dossiers = {str(m['id']): m for m in load_matters(desk.c)}
            trouves, requete = [], str(action.requete or '').casefold()[:200]
            for mid in action.sources:
                m = dossiers.get(mid)
                if not m or not m.get('path'):
                    continue
                for item in dav.list_folder(m['path'])[:MAX_FICHIERS]:
                    nom = str(item.get('path', '')).rsplit('/', 1)[-1]
                    if item.get('directory') or (requete and requete not in nom.casefold()):
                        continue
                    fid = 'f-' + hashlib.sha256(('%s|%s' % (mid, item['path'])).encode('utf-8')).hexdigest()[:20]
                    catalogue[fid] = {'mid': mid, 'path': item['path'], 'etag': item.get('etag', ''), 'size': item.get('size', 0)}
                    trouves.append({'id': fid, 'nom': nom[:200], 'taille': item.get('size', 0)})
            # nouveaux fichiers d'une racine déjà accordée : catalogue privé de la mission (jamais modifié par le modèle)
            desk.db.execute('UPDATE poste56109_autorisations SET catalogue=?, maj_le=? WHERE id=?', (json.dumps(catalogue), _iso(), row['id']))
            desk.db.commit()
            return {'public': {'fichiers': trouves[:MAX_FICHIERS], 'provenance': 'dossiers autorisés', 'donnees_non_fiables': True}}
        lus = []
        contenu = {}
        for fid in action.sources:
            item = catalogue.get(fid)
            if not item:
                raise Refus('source_non_autorisee')
            data = dav.download({'path': item['path'], 'etag': item.get('etag', ''), 'size': item.get('size', 0)})
            contenu[fid] = data
            lus.append({'id': fid, 'taille': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        return {'public': {'lus': lus}, 'contenu': contenu}
    return run


def executer(desk, acteur, ident, action, cle, auth=None, horloge=time):
    """Étape IA d'une mission sur le poste. Rend l'état public (jamais le contenu lu) ; la même clé rend le même reçu sans refaire l'effet."""
    compte, role = acteur
    if not CLE.match(str(cle or '')):
        raise Stop('cle_requise')
    row = _autorisation(desk, acteur, ident)
    if not isinstance(action, Action):
        raise Stop('action_invalide')
    empreinte = hashlib.sha256(json.dumps(action.__dict__, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    recu = desk.db.execute('SELECT * FROM poste56109_recus WHERE autorisation=? AND cle=?', (row['id'], cle)).fetchone()
    if recu:
        if recu['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        if recu['etat'] == 'en_cours':     # panne pendant l'effet : jamais rejoué, rapproché
            desk.db.execute("UPDATE poste56109_recus SET etat='a_rapprocher', code='reprise_apres_interruption', maj_le=? WHERE id=?", (_iso(), recu['id']))
            desk.db.commit()
            return {'etat': 'a_rapprocher', 'code': 'reprise_apres_interruption', 'reessai_automatique': False, 'rejoue': True}
        return {**json.loads(recu['resume']), 'rejoue': True}
    quand = _iso()
    desk.db.execute('INSERT INTO poste56109_recus(autorisation,mission,utilisateur,outil,cle,empreinte,etat,code,resume,cree_le,maj_le) '
                    "VALUES(?,?,?,?,?,?,'en_cours','','{}',?,?)", (row['id'], row['mission'], compte, action.outil[:40], cle, empreinte, quand, quand))
    desk.db.commit()
    catalogue = json.loads(row['catalogue'])
    portee = Portee(row['mission'], row['utilisateur'], row['cabinet'], row['poste'], row['revision'], _ts(row['expire']),
                    frozenset(json.loads(row['sources'])) | frozenset(catalogue), frozenset(json.loads(row['sorties'])),
                    domaines=frozenset(json.loads(row['domaines'])), connecteurs=frozenset(json.loads(row['connecteurs'])),
                    limite_etapes=row['limite_etapes'])
    m = Mediateur(portee, _etat_fn(desk, row['id']), _source_fn(desk, row['id'], acteur), horloge=horloge)
    m.etapes = row['etapes']
    try:
        resultat = m.executer(action, _executeur(desk, row, acteur, auth), utilisateur=compte, cabinet=_cabinet(desk), poste=identite_poste(desk))
        public = resultat.public()
    except Stop as ex:
        code = str(ex).split(':')[0]
        public = {'etat': 'indisponible' if code == 'pilote_indisponible' else 'refusee', 'code': code, 'reessai_automatique': False}
    if public['etat'] in ('terminee', 'a_rapprocher'):
        desk.db.execute('UPDATE poste56109_autorisations SET etapes=etapes+1, maj_le=? WHERE id=?', (_iso(), row['id']))
    desk.db.execute('UPDATE poste56109_recus SET etat=?, code=?, resume=?, maj_le=? WHERE autorisation=? AND cle=?',
                    (public['etat'], public['code'], json.dumps(public, ensure_ascii=False), _iso(), row['id'], cle))
    from . import missions567
    missions567._inscrire(desk, row['mission'], 'poste_etape', {'outil': action.outil, 'etat': public['etat'], 'code': public['code']})
    desk.db.commit()
    return public


def recus(desk, acteur, ident, limite=50):
    row = _autorisation(desk, acteur, ident)
    return [{'outil': r['outil'], 'etat': r['etat'], 'code': r['code'], 'le': r['maj_le']} for r in
            desk.db.execute('SELECT outil, etat, code, maj_le FROM poste56109_recus WHERE autorisation=? ORDER BY id DESC LIMIT ?',
                            (row['id'], max(1, min(int(limite), 200))))]


# ======================================================================================== capacités et délégation
def capacites(desk, auth=None):
    """Disponible, Non installé, Permission requise ou Indisponible — sans test lourd ni effet pendant l'affichage."""
    poste = _poste_local(auth)
    out = {}
    racine = (desk.c.get('nextcloud') or {}).get('local_path')
    out['fichiers'] = 'Disponible' if poste and racine else ('Indisponible : dossier local non configuré' if poste else 'Indisponible : serveur (compagnon requis)')
    if poste:
        from .cdp5673 import find_browser
        out['navigateur'] = 'Disponible' if find_browser() else 'Non installé : Edge, Chrome ou Chromium requis'
    else:
        out['navigateur'] = 'Indisponible : poste seulement (délégation au compagnon associé)'
    out['recherche_juridique'] = 'Indisponible : pilote non livré dans ce lot'
    out['documents'] = 'Indisponible : préparation et conversion par ce médiateur non livrées (utilisez Produire)'
    out['recettes'] = 'Indisponible : aucun confinement OS qualifié'
    out['bureau'] = 'Indisponible : contrôle graphique non livré (option désactivée)'
    return out


def etat_delegation(desk, acteur, auth=None):
    """Serveur / ExApp : état du compagnon de poste. Sans poste associé et joignable : « Attente du poste », clairement signalé."""
    if _poste_local(auth):
        return {'etat': 'poste_local', 'message': 'Ce poste exécute lui-même les missions.'}
    compte = acteur[0]
    rows = desk.db.execute("SELECT id, libelle, etat, vu_le FROM poste56109_postes WHERE utilisateur=? AND etat='associe'", (compte,)).fetchall()
    if not rows:
        return {'etat': 'poste_absent', 'message': 'Attente du poste : aucun poste associé. L’import manuel des pièces reste possible.'}
    recent = [r for r in rows if r['vu_le'] and _maintenant().timestamp() - _ts(r['vu_le']) < 300]
    if not recent:
        return {'etat': 'poste_hors_ligne', 'message': 'Attente du poste : le poste associé ne s’est pas manifesté récemment. Rien n’est exécuté à sa place.',
                'postes': [{'id': r['id'][:8], 'libelle': r['libelle']} for r in rows]}
    return {'etat': 'poste_pret', 'message': 'Poste associé joignable.', 'postes': [{'id': r['id'][:8], 'libelle': r['libelle']} for r in recent]}


# ======================================================================================== contrat du compagnon (G13)
INTERDITS = re.compile(r'cookie|password|mot_de_passe|secret|token|jeton|cle_privee|private|devtools|cdp_url|ws_url|pin|otp', re.I)
DUREE_CODE = timedelta(minutes=10)


def _b64(b):
    return base64.urlsafe_b64encode(bytes(b)).decode('ascii').rstrip('=')


def _d64(t):
    t = str(t or '')
    return base64.urlsafe_b64decode(t + '=' * (-len(t) % 4))


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _sans_secret(obj, chemin=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if INTERDITS.search(str(k)):
                raise Stop('champ_interdit_dans_un_ordre')
            _sans_secret(v, chemin + '.' + str(k))
    elif isinstance(obj, list):
        for v in obj:
            _sans_secret(v, chemin)


def _identite(state_dir):
    from .liaison56103 import identite
    return identite(state_dir)


def _verifier_signature(cle_b64, signature_b64, donnees):
    from .liaison56103 import _verifier
    try:
        return _verifier(_d64(cle_b64), _d64(signature_b64), donnees)
    except (ValueError, TypeError):
        return False


def appairer(desk, acteur, libelle=''):
    """Serveur, session 1 : code court à saisir sur le poste (10 minutes), empreinte du serveur à comparer."""
    compte = acteur[0]
    code = '%04d-%04d' % (secrets.randbelow(10000), secrets.randbelow(10000))
    quand = _maintenant()
    desk.db.execute('INSERT INTO poste56109_postes(id,utilisateur,libelle,code_h,etat,expire,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                    ('attente:' + secrets.token_hex(8), compte, str(libelle or '')[:80], hashlib.sha256(('56109|' + code).encode()).hexdigest(),
                     'en_attente', _iso(quand + DUREE_CODE), _iso(quand), _iso(quand)))
    desk.db.commit()
    from .liaison56103 import empreinte_lisible
    serveur = _identite(desk.c['state_dir'])
    return {'code': code, 'expire': _iso(quand + DUREE_CODE), 'serveur': serveur.id, 'empreinte_serveur': empreinte_lisible(serveur.id)}


def demande_poste(state_dir_poste, code, serveur_id):
    """Poste : preuve de possession de sa clé pour ce code et ce serveur (rien d'autre ne quitte le poste)."""
    ident = _identite(state_dir_poste)
    preuve = _canon({'code': str(code), 'serveur': str(serveur_id), 'poste': ident.id})
    return {'poste': ident.id, 'cle': _b64(ident.cle_signature), 'signature': _b64(ident.signer(preuve))}


def associer(desk, acteur, code, demande):
    """Serveur : le poste a saisi le code ; sa preuve de clé est vérifiée. L'association attend encore l'approbation de l'empreinte."""
    compte = acteur[0]
    code_h = hashlib.sha256(('56109|' + str(code or '')).encode()).hexdigest()
    row = desk.db.execute("SELECT * FROM poste56109_postes WHERE utilisateur=? AND code_h=? AND etat='en_attente'", (compte, code_h)).fetchone()
    if not row or _ts(row['expire']) < _maintenant().timestamp():
        raise Stop('code_d_appairage_invalide_ou_expire')
    serveur = _identite(desk.c['state_dir'])
    preuve = _canon({'code': str(code), 'serveur': serveur.id, 'poste': str(demande.get('poste') or '')})
    from .liaison56103 import empreinte
    if not _verifier_signature(demande.get('cle'), demande.get('signature'), preuve) or empreinte(_d64(demande.get('cle'))) != demande.get('poste'):
        raise Stop('preuve_de_cle_invalide')
    if desk.db.execute("SELECT 1 FROM poste56109_postes WHERE id=?", (demande['poste'],)).fetchone():
        raise Stop('poste_deja_associe')
    desk.db.execute("UPDATE poste56109_postes SET id=?, cle_publique=?, etat='a_approuver', code_h='', maj_le=? WHERE id=?",
                    (demande['poste'], demande['cle'], _iso(), row['id']))
    desk.db.commit()
    from .liaison56103 import empreinte_lisible
    return {'poste': demande['poste'][:8], 'empreinte_poste': empreinte_lisible(demande['poste']), 'etat': 'a_approuver'}


def approuver(desk, acteur, poste_id, empreinte_vue):
    """Serveur, session 2 : l'avocat confirme que l'empreinte affichée par le poste est la même. Sinon rien n'est associé."""
    row = desk.db.execute("SELECT * FROM poste56109_postes WHERE id LIKE ? AND utilisateur=? AND etat='a_approuver'",
                          (str(poste_id or '')[:32] + '%', acteur[0])).fetchone()
    from .liaison56103 import empreinte_lisible
    if not row or empreinte_lisible(row['id']).replace(' ', '') != str(empreinte_vue or '').replace(' ', '').upper():
        raise Stop('introuvable')
    desk.db.execute("UPDATE poste56109_postes SET etat='associe', maj_le=? WHERE id=?", (_iso(), row['id']))
    desk.db.commit()
    desk.audit('poste56109_appairage', {'poste': row['id'][:8], 'par': acteur[0][:120]})
    return {'poste': row['id'][:8], 'etat': 'associe'}


def revoquer_poste(desk, acteur, poste_id):
    row = desk.db.execute("SELECT * FROM poste56109_postes WHERE id LIKE ? AND (utilisateur=? OR ?='administrateur') AND etat!='revoque'",
                          (str(poste_id or '')[:32] + '%', acteur[0], acteur[1])).fetchone()
    if not row:
        raise Stop('introuvable')
    desk.db.execute('BEGIN IMMEDIATE')
    desk.db.execute("UPDATE poste56109_postes SET etat='revoque', maj_le=? WHERE id=?", (_iso(), row['id']))
    desk.db.execute("UPDATE poste56109_baux SET etat='revoque', maj_le=? WHERE poste=? AND etat='actif'", (_iso(), row['id']))
    desk.db.commit()
    return {'poste': row['id'][:8], 'etat': 'revoque'}


def _bail(desk, operation, poste, duree):
    """Bail d'exécution : un seul poste agit sur une opération ; génération incrémentée à chaque nouveau bail (jeton de génération)."""
    maintenant = _maintenant()
    row = desk.db.execute('SELECT * FROM poste56109_baux WHERE operation=?', (operation,)).fetchone()
    if row and row['etat'] == 'actif' and _ts(row['expire']) > maintenant.timestamp() and row['poste'] != poste:
        raise Stop('operation_tenue_par_un_autre_poste')
    generation = (row['generation'] if row else 0) + 1
    desk.db.execute('INSERT INTO poste56109_baux(operation,poste,generation,expire,etat,maj_le) VALUES(?,?,?,?,?,?) '
                    'ON CONFLICT(operation) DO UPDATE SET poste=excluded.poste, generation=excluded.generation, expire=excluded.expire, '
                    "etat='actif', maj_le=excluded.maj_le", (operation, poste, generation, _iso(maintenant + duree), 'actif', _iso(maintenant)))
    return generation


def emettre_ordre(desk, acteur, poste_id, operation, mission, domaines, actions, documents=(), budget=None, duree_minutes=30):
    """Serveur : ordre signé et expirant pour UN poste associé, sous bail. Un ordre resté sans reçu doit être rapproché avant d'être refait."""
    compte = acteur[0]
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{4,120}', str(operation or '')):
        raise Stop('operation_invalide')
    poste = desk.db.execute("SELECT * FROM poste56109_postes WHERE id LIKE ? AND utilisateur=? AND etat='associe'",
                            (str(poste_id or '')[:32] + '%', compte)).fetchone()
    if not poste:
        raise Stop('poste_absent')
    docs = []
    for d in documents or ():
        if not isinstance(d, dict) or not re.fullmatch(r'[0-9a-f]{64}', str(d.get('sha256') or '')):
            raise Stop('document_sans_empreinte')
        docs.append({'id': str(d.get('id') or '')[:120], 'version': str(d.get('version') or '')[:40], 'sha256': d['sha256']})
    duree = timedelta(minutes=max(1, min(int(duree_minutes), 240)))
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        maintenant = _maintenant()
        for r in desk.db.execute("SELECT nonce, expire FROM poste56109_ordres WHERE operation=? AND sens='emis' AND etat='emis'", (operation,)).fetchall():
            if _ts(r['expire']) > maintenant.timestamp():
                raise Stop('ordre_en_cours')
            desk.db.execute("UPDATE poste56109_ordres SET etat='a_rapprocher', maj_le=? WHERE nonce=?", (_iso(), r['nonce']))
            desk.db.commit()
            raise Stop('ordre_a_rapprocher')         # réponse perdue : l'état distant est rapproché avant toute répétition
        generation = _bail(desk, operation, poste['id'], duree)
        ordre = {'cabinet': _cabinet(desk), 'utilisateur': compte, 'appareil': poste['id'], 'mission': str(mission or '')[:64],
                 'operation': operation, 'domaines': sorted(set(map(str, domaines or ())))[:20], 'actions': sorted(set(map(str, actions or ())))[:20],
                 'documents': docs[:20], 'budget': budget if isinstance(budget, (int, float)) and not isinstance(budget, bool) else None,
                 'nonce': secrets.token_hex(16), 'epoch': generation, 'expire': _iso(maintenant + duree)}
        _sans_secret(ordre)
        signature = _b64(_identite(desk.c['state_dir']).signer(_canon(ordre)))
        desk.db.execute("INSERT INTO poste56109_ordres(nonce,sens,poste,operation,epoch,expire,empreinte,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (ordre['nonce'], 'emis', poste['id'], operation, generation, ordre['expire'], hashlib.sha256(_canon(ordre)).hexdigest(),
                         'emis', _iso(), _iso()))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return {'ordre': ordre, 'signature': signature, 'cle_serveur': _b64(_identite(desk.c['state_dir']).cle_signature)}


def verifier_ordre(desk_poste, envoi, cle_serveur):
    """Poste (compagnon) : signature du serveur associé, appareil, échéance, nonce jamais vu, génération non régressive."""
    ordre, signature = envoi.get('ordre') or {}, envoi.get('signature') or ''
    if not isinstance(ordre, dict) or not _verifier_signature(cle_serveur, signature, _canon(ordre)):
        raise Stop('ordre_signature_invalide')
    _sans_secret(ordre)
    if ordre.get('appareil') != identite_poste(desk_poste):
        raise Stop('ordre_destine_a_un_autre_appareil')
    if _ts(ordre.get('expire')) <= _maintenant().timestamp():
        raise Stop('ordre_expire')
    ancien = desk_poste.db.execute("SELECT MAX(epoch) FROM poste56109_ordres WHERE sens='recu' AND operation=?", (str(ordre.get('operation')),)).fetchone()[0]
    if ancien is not None and int(ordre.get('epoch') or 0) <= int(ancien):
        raise Stop('ordre_ancien_ou_duplique')
    try:
        desk_poste.db.execute("INSERT INTO poste56109_ordres(nonce,sens,poste,operation,epoch,expire,empreinte,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (str(ordre['nonce']), 'recu', ordre['appareil'], str(ordre['operation']), int(ordre['epoch']), ordre['expire'],
                               hashlib.sha256(_canon(ordre)).hexdigest(), 'accepte', _iso(), _iso()))
        desk_poste.db.commit()
    except Exception:   # noqa: BLE001 — nonce déjà enregistré : ordre dupliqué
        desk_poste.db.rollback()
        raise Stop('ordre_ancien_ou_duplique') from None
    return ordre


def recu_poste(desk_poste, ordre, etat, resume=None):
    """Poste : reçu signé (aucun contenu de pièce, aucun secret) ; « a_rapprocher » si l'effet est incertain."""
    if etat not in ('confirme', 'a_rapprocher', 'refuse'):
        raise Stop('etat_invalide')
    corps = {'nonce': ordre['nonce'], 'operation': ordre['operation'], 'epoch': ordre['epoch'], 'appareil': ordre['appareil'], 'etat': etat,
             'resume': resume if isinstance(resume, dict) else {}, 'le': _iso()}
    _sans_secret(corps)
    ident = _identite(desk_poste.c['state_dir'])
    return {'recu': corps, 'signature': _b64(ident.signer(_canon(corps)))}


def enregistrer_recu(desk, envoi):
    """Serveur : reçu d'un poste associé, pour un ordre émis vers lui, sous la génération courante du bail. Idempotent."""
    corps, signature = envoi.get('recu') or {}, envoi.get('signature') or ''
    poste = desk.db.execute("SELECT * FROM poste56109_postes WHERE id=? AND etat='associe'", (str(corps.get('appareil') or ''),)).fetchone()
    if not poste or not _verifier_signature(poste['cle_publique'], signature, _canon(corps)):
        raise Stop('recu_invalide')
    ordre = desk.db.execute("SELECT * FROM poste56109_ordres WHERE nonce=? AND sens='emis' AND poste=?", (str(corps.get('nonce') or ''), poste['id'])).fetchone()
    if not ordre or int(corps.get('epoch') or -1) != ordre['epoch']:
        raise Stop('recu_invalide')
    if ordre['etat'] != 'emis':
        if ordre['recu'] and json.loads(ordre['recu']).get('etat') == corps.get('etat'):
            return {'etat': ordre['etat'], 'rejoue': True}
        raise Stop('recu_deja_enregistre')
    bail = desk.db.execute('SELECT * FROM poste56109_baux WHERE operation=?', (ordre['operation'],)).fetchone()
    if not bail or bail['generation'] != ordre['epoch'] or bail['poste'] != poste['id'] or bail['etat'] != 'actif':
        raise Stop('bail_perime')                    # un autre poste ou une génération plus récente tient l'opération
    etat = {'confirme': 'confirme', 'a_rapprocher': 'a_rapprocher', 'refuse': 'refuse'}.get(corps.get('etat'), 'a_rapprocher')
    desk.db.execute('BEGIN IMMEDIATE')
    desk.db.execute('UPDATE poste56109_ordres SET etat=?, recu=?, maj_le=? WHERE nonce=?', (etat, json.dumps(corps, ensure_ascii=False), _iso(), ordre['nonce']))
    desk.db.execute("UPDATE poste56109_baux SET etat=?, maj_le=? WHERE operation=?", ('termine' if etat == 'confirme' else 'actif', _iso(), ordre['operation']))
    desk.db.execute('UPDATE poste56109_postes SET vu_le=? WHERE id=?', (_iso(), poste['id']))
    desk.db.commit()
    return {'etat': etat}


# ======================================================================================== routes m56109/poste/
PREFIXE = 'm56109/poste/'
LECTURES = ('etat',)
ECRITURES = ('autoriser', 'suspendre', 'reprendre', 'revoquer', 'revoquer-tout', 'appairer', 'associer', 'approuver', 'revoquer-poste')
ROLES = ('administrateur', 'avocat', 'assistant')
MAX_POST = 20_000
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'autorisation_revoquee': '409 Conflict', 'poste_absent': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large', 'controle_acces_indisponible': '503 Service Unavailable'}
MESSAGES = {
    'introuvable': 'Élément introuvable, ou action non permise.', 'role_insuffisant': 'Votre rôle ne permet pas cette action.',
    'version_perimee': 'L’autorisation a changé entre-temps : rechargez la page. Rien n’a été modifié.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
    'autorisation_revoquee': 'Autorisation déjà révoquée.', 'poste_absent': 'Attente du poste : aucun poste associé et joignable pour exécuter la mission.',
    'sources_requises': 'Choisissez au moins un dossier.', 'requete_trop_longue': 'Demande trop longue.',
    'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
    'code_d_appairage_invalide_ou_expire': 'Code d’appairage invalide ou expiré.', 'preuve_de_cle_invalide': 'Le poste n’a pas prouvé la possession de sa clé.',
}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    return method == 'POST' and reste in ECRITURES and role in ROLES


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def etat(desk, acteur, auth=None):
    compte, role = acteur
    rows = desk.db.execute("SELECT * FROM poste56109_autorisations WHERE etat!='revoquee'" + ('' if role == 'administrateur' else ' AND utilisateur=?')
                           + ' ORDER BY maj_le DESC LIMIT 50', () if role == 'administrateur' else (compte,)).fetchall()
    try:
        poste = identite_poste(desk, creer=False)[:8] if _poste_local(auth) else ''
    except Stop:
        poste = ''
    return {'poste': poste, 'capacites': capacites(desk, auth), 'delegation': etat_delegation(desk, acteur, auth),
            'autorisations': [vue(r) for r in rows], 'controle_graphique': False, 'recettes': False}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    acteur = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES:
            raise Stop('introuvable')
        if not allowed(acteur[1], method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        if method in ('GET', 'HEAD'):
            return _json(etat(desk, acteur, auth))
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        if reste == 'autoriser':
            return _json(autoriser(desk, acteur, d.get('mission'), d.get('sources'), d.get('sorties'), d.get('domaines'), d.get('connecteurs'),
                                   d.get('duree') or 120, d.get('reseau_interdit') is True, d.get('limite') or 40, d.get('cle'), auth))
        if reste in ('suspendre', 'reprendre', 'revoquer'):
            if not CLE.match(str(d.get('cle') or '')):
                raise Stop('cle_requise')
            return _json(changer_etat(desk, acteur, d.get('id'), {'suspendre': 'suspendue', 'reprendre': 'active', 'revoquer': 'revoquee'}[reste],
                                      d.get('version')))
        if reste == 'revoquer-tout':
            return _json({'revoquees': revoquer_tout(desk, acteur), 'message': 'Autorisations de ce poste révoquées.'})
        if reste == 'appairer':
            return _json(appairer(desk, acteur, d.get('libelle') or ''))
        if reste == 'associer':
            return _json(associer(desk, acteur, d.get('code'), d.get('demande') if isinstance(d.get('demande'), dict) else {}))
        if reste == 'approuver':
            return _json(approuver(desk, acteur, d.get('poste'), d.get('empreinte')))
        return _json(revoquer_poste(desk, acteur, d.get('poste')))
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'dossier_absent', 'acteur_requis', 'mission_absente', 'mission_invalide'):
            code = 'introuvable'
        return _json({'error': code, 'message': MESSAGES.get(code, 'Demande refusée (%s).' % code)}, STATUTS.get(code, '422 Unprocessable Entity'))


def section_html(desk, acteur, auth=None):
    """« Accès à ce poste » (Paramètres › Assistant) : capacités réelles et délégation ; aucune écriture ni test pendant l'affichage."""
    from html import escape as e
    caps = capacites(desk, auth)
    dele = etat_delegation(desk, acteur, auth)
    libelles = {'fichiers': 'Fichiers des dossiers', 'navigateur': 'Navigateur supervisé', 'recherche_juridique': 'Recherche juridique',
                'documents': 'Préparation de documents', 'recettes': 'Recettes exécutables', 'bureau': 'Contrôle graphique du bureau'}
    lignes = ''.join('<tr><th scope="row">%s</th><td>%s</td></tr>' % (e(libelles[k]), e(v)) for k, v in caps.items())
    return ('<section class="ax-card n56109" id="acces-poste"><h2>Accès à ce poste</h2>'
            '<p class="ax-muted">Une mission n’utilise que les dossiers, sites et connecteurs autorisés pour elle ; la pause arrête les nouveaux '
            'effets, la révocation invalide les étapes en cours. Aucun terminal libre, aucun téléchargement de composant.</p>'
            '<table class="n56109-table"><tbody>%s</tbody></table><p class="n56109-delegation" role="status">%s</p></section>') % (lignes, e(dele['message']))
