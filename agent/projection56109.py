"""5.6.109 (lot D, F04 ; C10 et C11 pour les candidats) : projection commune de la fiche du dossier.

Avant, deux ensembles de parties vivaient côte à côte : le registre de la fiche (``dossier_parties5697``, saisi et corrigé par l'avocat) et
les parties extraites des pièces (``modele5651``, ``d51_parties``), lues par la synthèse. Une correction dans l'un n'était pas une
correction de l'autre. Cette projection est la **seule lecture** de la fiche :

- **parties inscrites** : les valeurs du registre priment toujours ; une partie extraite rapprochée avec certitude (lien explicite
  ``fiche_liens56109`` ou même identité) n'apporte que sa provenance, ses sources et ses qualités par instance ;
- **candidats à vérifier** : les mentions extraites non rapprochées (parties ``d51`` et comparutions de contrats ``fiche_mentions56109``)
  restent des propositions ; elles n'entrent pas dans la synthèse comme parties établies. Accepter, Corriger, Fusionner, Écarter : une
  décision par ligne, jamais de traitement en masse ; un candidat écarté ne revient que si sa source a matériellement changé ;
- **profil** Contentieux, Conseil ou Mixte : mêmes pages, cartes adaptées ; faits (constatés, corroborés, retenus par une décision),
  allégations (attribuées à leur auteur), demandes, arguments, actes de procédure et décisions de justice restent **distincts** ; valider
  la transcription d'une allégation ne la rend pas établie ; un élément purement technique (format de fichier) n'est pas un fait ;
- chaque élément porte ses **sources** (pièce, page, version, extrait) ; une source retirée ou déplacée rend l'élément « à vérifier » et
  son extrait n'est plus montré ;
- **champs de la fiche** (mission, situation, élément nouveau, décision attendue, objectifs de négociation…) : valeur typée, version,
  origine. Une valeur saisie par l'avocat n'est jamais remplacée par une proposition sans confirmation expresse ; une proposition écartée
  garde son refus (même source, même contenu : jamais reproposée) ;
- **présentation** : modèle du cabinet, adaptation du dossier (profil), préférence personnelle (ordre, cartes masquées ou repliées) ; aucune
  préférence ne supprime une donnée ni ne masque un contrôle.

Aucun appel à un modèle dans une lecture ; aucune écriture pendant un GET ; tout fonctionne IA en pause. Les tables sont créées au
démarrage (``Desk``). Dossier absent ou réservé : ``Stop('introuvable')`` (même réponse) avant toute lecture. Routes : ``m56109/fiche/…``.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import uuid

from .common import Stop
from . import dossiers5697 as D

PREFIXE = 'm56109/fiche/'
PROFILS = {'contentieux': 'Contentieux', 'conseil': 'Conseil', 'mixte': 'Mixte'}
# cartes de la fiche : (identifiant, libellé, profils, masquable)
CARTES = (('en_bref', 'Le dossier en quatre questions', ('contentieux', 'conseil', 'mixte'), False),
          ('parties', 'Parties et qualités', ('contentieux', 'conseil', 'mixte'), True),
          ('candidats', 'À vérifier : personnes lues dans les pièces', ('contentieux', 'conseil', 'mixte'), True),
          ('operation', 'Opération et négociation', ('conseil', 'mixte'), True),
          ('contrats', 'Contrats et versions', ('conseil', 'mixte'), True),
          ('faits', 'Faits constatés ou corroborés', ('contentieux', 'conseil', 'mixte'), True),
          ('allegations', 'Affirmations des parties (attribuées)', ('contentieux', 'conseil', 'mixte'), True),
          ('pretentions', 'Demandes et montants', ('contentieux', 'mixte'), True),
          ('arguments', 'Arguments', ('contentieux', 'mixte'), True),
          ('procedure', 'Actes de procédure', ('contentieux', 'mixte'), True),
          ('decisions', 'Décisions de justice', ('contentieux', 'mixte'), True),
          ('decisions_cabinet', 'Décisions du cabinet', ('contentieux', 'conseil', 'mixte'), True),
          ('champs', 'Champs du cabinet', ('contentieux', 'conseil', 'mixte'), True))
CARTE_IDS = tuple(c[0] for c in CARTES)
TYPES = {'texte': 'Texte', 'date': 'Date', 'montant': 'Montant', 'choix': 'Choix', 'oui_non': 'Oui / non', 'liste': 'Liste', 'personne': 'Personne du dossier'}
# champs de la fiche livrés : (identifiant, libellé, type, carte, profils, aide)
CHAMPS = (('mission', 'Mission confiée au cabinet', 'texte', 'en_bref', ('contentieux', 'conseil', 'mixte'), 'Ce que le client attend du cabinet dans ce dossier.'),
          ('situation', 'Situation actuelle', 'texte', 'en_bref', ('contentieux', 'conseil', 'mixte'), 'Où en est le dossier aujourd’hui.'),
          ('element_nouveau', 'Dernier élément qui change l’analyse', 'texte', 'en_bref', ('contentieux', 'conseil', 'mixte'), 'Pièce, décision ou événement récent qui modifie l’analyse.'),
          ('attendu', 'Décision ou action attendue', 'texte', 'en_bref', ('contentieux', 'conseil', 'mixte'), 'Ce qui doit être décidé ou fait, et par qui.'),
          ('position_client', 'Position du client', 'texte', 'parties', ('contentieux', 'mixte'), 'Position du client dans l’instance en cours.'),
          ('operation', 'Opération ou question posée', 'texte', 'operation', ('conseil', 'mixte'), 'L’opération envisagée ou la question à laquelle répondre.'),
          ('negociation', 'Objectifs de négociation', 'liste', 'operation', ('conseil', 'mixte'), 'Un objectif par ligne.'),
          ('clauses', 'Clauses à discuter', 'liste', 'operation', ('conseil', 'mixte'), 'Une clause par ligne.'),
          ('risques', 'Options et risques', 'liste', 'operation', ('conseil', 'mixte'), 'Une option ou un risque par ligne.'),
          ('validations', 'Validations restant à obtenir', 'liste', 'operation', ('conseil', 'mixte'), 'Qui doit valider quoi.'),
          ('obligations', 'Obligations après signature', 'liste', 'operation', ('conseil', 'mixte'), 'Une obligation par ligne, avec son terme si connu.'))
CHAMP_PERSO = re.compile(r'^c_[a-z0-9_]{1,30}$')
CANDIDAT = re.compile(r'^m?[0-9a-f]{24}$')
IDENT = re.compile(r'^[0-9a-f]{32}$')
ORIGINES = {'avocat': 'saisi par l’avocat', 'ia_validee': 'proposition validée', 'registre': 'registre du dossier'}
FORCES = {'constate': 'Constaté dans une pièce', 'corrobore': 'Corroboré', 'retenu_decision': 'Retenu par une décision identifiée',
          'allegue': 'Allégué', 'conteste': 'Contesté', 'a_verifier': 'À vérifier'}
ETABLI = ('constate', 'corrobore', 'retenu_decision')
STATUTS_REVUE = {'enregistre': 'confirme', 'propose': 'propose', 'a_verifier': 'a_verifier'}
# rôles du registre qui désignent une partie (les avocats, experts et autres intervenants ne sont pas des parties)
ROLES_DE_PARTIE = ('client', 'adverse', 'cocontractant', 'tiers')
NATURES_LUES = ('conclusions', 'assignation', 'requete', 'jugement', 'ordonnance', 'arret', 'signification', 'contrat')
MAX_ELEMENTS = 40
MAX_TEXTE = 4000
MAX_LISTE = 30
TECHNIQUE = re.compile(r'\b(docx?|pdf|xlsx?|odt|rtf|format (?:du |de )?fichier|fichier (?:word|excel|pdf)|nom du fichier|taille du fichier|extension|octets|'
                       r'm[ée]tadonn[ée]es|num[ée]ris[ée]|scann[ée])\b', re.I)
ENJEU = re.compile(r'(authenti|falsifi|int[ée]grit|fid[ée]lit|antidat|horodat|sign[ée] [ée]lectroniquement|signature [ée]lectronique|alt[ée]r|modifi[ée] apr[èe]s|'
                   r'contest|original|copie non conforme|faux)', re.I)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS fiche_liens56109(
  dossier_id TEXT NOT NULL, candidat_id TEXT NOT NULL, partie_id TEXT NOT NULL, decision TEXT NOT NULL DEFAULT 'accepter',
  version INTEGER NOT NULL DEFAULT 1, decision_par TEXT NOT NULL, decision_le TEXT NOT NULL, PRIMARY KEY(dossier_id, candidat_id));
CREATE INDEX IF NOT EXISTS fiche_liens56109_p ON fiche_liens56109(dossier_id, partie_id);
CREATE TABLE IF NOT EXISTS fiche_decisions56109(
  id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, cible_type TEXT NOT NULL, cible_id TEXT NOT NULL, decision TEXT NOT NULL,
  empreinte TEXT NOT NULL, libelle TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', decision_par TEXT NOT NULL, decision_le TEXT NOT NULL,
  UNIQUE(dossier_id, cible_type, cible_id, empreinte));
CREATE TABLE IF NOT EXISTS fiche_participations56109(
  id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, partie_id TEXT NOT NULL, cadre_type TEXT NOT NULL DEFAULT 'instance', cadre_id TEXT NOT NULL,
  qualite TEXT NOT NULL, origine TEXT NOT NULL DEFAULT 'avocat', version INTEGER NOT NULL DEFAULT 1, modifie_par TEXT NOT NULL, modifie_le TEXT NOT NULL,
  UNIQUE(dossier_id, partie_id, cadre_type, cadre_id));
CREATE TABLE IF NOT EXISTS fiche_mentions56109(
  id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, source_path TEXT NOT NULL, source_nom TEXT NOT NULL DEFAULT '', empreinte TEXT NOT NULL,
  zone TEXT NOT NULL, ligne INTEGER NOT NULL DEFAULT 0, nom_ecrit TEXT NOT NULL, nom TEXT NOT NULL, nom_norm TEXT NOT NULL,
  nature_personne TEXT NOT NULL DEFAULT '', qualite TEXT NOT NULL DEFAULT '', cadre TEXT NOT NULL DEFAULT '', representant TEXT NOT NULL DEFAULT '',
  conseil TEXT NOT NULL DEFAULT '', extrait TEXT NOT NULL DEFAULT '', releve_le TEXT NOT NULL, UNIQUE(dossier_id, source_path, zone, nom_norm));
CREATE INDEX IF NOT EXISTS fiche_mentions56109_d ON fiche_mentions56109(dossier_id);
CREATE TABLE IF NOT EXISTS fiche_valeurs56109(
  dossier_id TEXT NOT NULL, champ TEXT NOT NULL, valeur_json TEXT NOT NULL, origine TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 1, modifie_par TEXT NOT NULL, modifie_le TEXT NOT NULL, PRIMARY KEY(dossier_id, champ));
CREATE TABLE IF NOT EXISTS fiche_valeurs_historique56109(
  id INTEGER PRIMARY KEY AUTOINCREMENT, dossier_id TEXT NOT NULL, champ TEXT NOT NULL, version INTEGER NOT NULL, valeur_json TEXT NOT NULL,
  origine TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]', par TEXT NOT NULL, le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fiche_valeurs_historique56109_d ON fiche_valeurs_historique56109(dossier_id, champ, version);
CREATE TABLE IF NOT EXISTS fiche_propositions56109(
  id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, champ TEXT NOT NULL, base_version INTEGER NOT NULL, valeur_json TEXT NOT NULL,
  sources_json TEXT NOT NULL, justification TEXT NOT NULL DEFAULT '', empreinte TEXT NOT NULL, origine TEXT NOT NULL DEFAULT 'ia',
  etat TEXT NOT NULL DEFAULT 'propose', version INTEGER NOT NULL DEFAULT 1, cree_le TEXT NOT NULL, decision_par TEXT NOT NULL DEFAULT '',
  decision_le TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', UNIQUE(dossier_id, champ, empreinte));
CREATE INDEX IF NOT EXISTS fiche_propositions56109_d ON fiche_propositions56109(dossier_id, etat);
CREATE TABLE IF NOT EXISTS fiche_modeles56109(
  profil TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1, config_json TEXT NOT NULL, modifie_par TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fiche_profils56109(
  dossier_id TEXT PRIMARY KEY, profil TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, modifie_par TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fiche_preferences56109(
  compte TEXT NOT NULL, profil TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, config_json TEXT NOT NULL, modifie_le TEXT NOT NULL,
  PRIMARY KEY(compte, profil));
'''
MESSAGES = {'introuvable': 'Dossier introuvable.', 'version_perimee': 'Cette information a changé depuis votre affichage : rechargez la page ; votre saisie est conservée et rien n’a été écrasé.',
            'version_en_conflit': 'Cette personne a été modifiée entre-temps : rechargez la page ; rien n’a été écrasé.',
            'champs_invalides': 'Certains champs sont à corriger.', 'cle_idempotence_absente': 'Requête incomplète : rechargez la page et recommencez.',
            'cle_idempotence_deja_utilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
            'validation_expresse_requise': 'Cette valeur a été saisie par l’avocat : confirmez expressément son remplacement.',
            'sources_requises': 'Cette proposition n’a plus de source vérifiable dans le dossier : elle ne peut pas être acceptée telle quelle.',
            'proposition_deja_decidee': 'Cette proposition a déjà été décidée.', 'partie_en_double': 'Cette personne figure déjà dans le dossier avec ce rôle.',
            'role_insuffisant': 'Action réservée à un autre rôle du cabinet.', 'route_inconnue': 'Route inconnue.',
            'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant.',
            'index_documents_indisponible': 'Lecture des pièces indisponible pour le moment : rien n’a été modifié.',
            'index_documents_verrouille': 'Lecture des pièces occupée : réessayez dans un instant.'}
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'dossier_absent': '404 Not Found', 'dossier_reserve': '404 Not Found',
           'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'acteur_requis': '403 Forbidden',
           'version_perimee': '409 Conflict', 'version_en_conflit': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict',
           'validation_expresse_requise': '409 Conflict', 'proposition_deja_decidee': '409 Conflict', 'partie_en_double': '409 Conflict',
           'champs_invalides': '422 Unprocessable Entity', 'sources_requises': '422 Unprocessable Entity', 'cle_idempotence_absente': '422 Unprocessable Entity',
           'controle_acces_indisponible': '503 Service Unavailable', 'index_documents_indisponible': '503 Service Unavailable',
           'index_documents_verrouille': '503 Service Unavailable', 'registre_dossiers_occupe': '503 Service Unavailable'}


# ------------------------------------------------------------------------------------------------ outils
def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*morceaux):
    return hashlib.sha256('|'.join(str(m) for m in morceaux).encode('utf-8')).hexdigest()


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk``) seulement ; jamais pendant une lecture."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _lignes(desk, sql, params=()):
    """Lecture tolérante d'un registre voisin : table jamais créée (module jamais utilisé) = aucune ligne."""
    try:
        return desk.db.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _dossier(desk, acteur, ident):
    """Garde du registre avant toute lecture : ``(acteur normalisé, identifiant courant, ligne du registre)``."""
    from . import fiche5698 as F
    a = F._acteur(acteur)
    did = F._dossier(desk, a, ident)
    return a, did, desk.db.execute('SELECT * FROM dossiers5697 WHERE id=?', (did,)).fetchone()


def _identite(nom):
    from .modele5651 import identity
    return identity(nom)


def _meme(a, b):
    """'meme', 'candidat' ou '' : même règle que le modèle du dossier (jamais une personne et une société, jamais deux civilités)."""
    from .modele5651 import compare
    return compare(_identite(a), _identite(b))


def _documents(desk, did):
    return {r['path']: dict(r) for r in _lignes(desk, 'SELECT path,name,etag,text_sha,nature,modified,doc_date,reading FROM d51_documents WHERE matter=?', (did,))}


def _sources(sources, docs, limite=4):
    """Sources résolues dans le dossier : pièce encore indexée → document, page, version, extrait ; pièce retirée ou déplacée → signalée,
    sans extrait (un extrait gardé en cache n'est jamais montré pour une source devenue inaccessible)."""
    out = []
    for s in (sources or [])[:limite]:
        if isinstance(s, str):
            s = {'path': s} if ('/' in s or '.' in s[-6:]) else {'label': s}
        if not isinstance(s, dict):
            continue
        if s.get('path'):
            d = docs.get(s['path'])
            if d is None:
                out.append({'label': 'Source retirée ou déplacée : ' + str(s['path']).rsplit('/', 1)[-1][:120], 'indisponible': True})
            else:
                out.append({'path': s['path'], 'document': d['name'], 'page': s.get('page') or '', 'extrait': str(s.get('extrait') or '')[:300],
                            'version': (d['etag'] or d['text_sha'] or '')[:24]})
        elif s.get('label'):
            out.append({'label': str(s['label'])[:200]})
    return out


def _verifiable(sources):
    return any(not s.get('indisponible') for s in sources)


def _empreinte_sources(sources):
    return _sha(*sorted('%s#%s#%s' % (s.get('path') or s.get('label') or '', s.get('page') or '', s.get('version') or '') for s in sources))


# ------------------------------------------------------------------------------------------------ profil et présentation
def _presentation_brute(desk, portee, proprietaire, profil):
    """Modèle de fiche du cabinet (``fiche_modeles56109``, partagé) ou préférence d'affichage d'un compte (``fiche_preferences56109``,
    personnelle : jamais une règle du cabinet, jamais exportée comme telle)."""
    if portee == 'cabinet':
        row = desk.db.execute('SELECT version,config_json FROM fiche_modeles56109 WHERE profil=?', (profil,)).fetchone()
    else:
        row = desk.db.execute('SELECT version,config_json FROM fiche_preferences56109 WHERE compte=? AND profil=?', (proprietaire, profil)).fetchone()
    if not row:
        return None
    try:
        return {'version': row['version'], 'config': json.loads(row['config_json'])}
    except ValueError:
        return None


def profil_du_dossier(desk, did, ligne=None):
    """Profil de la fiche : adaptation choisie pour le dossier, sinon nature du registre, sinon Contentieux s'il existe une instance."""
    adapte = desk.db.execute('SELECT profil,version FROM fiche_profils56109 WHERE dossier_id=?', (did,)).fetchone()
    if adapte and adapte['profil'] in PROFILS:
        return adapte['profil'], adapte['version']
    ligne = ligne or desk.db.execute('SELECT nature FROM dossiers5697 WHERE id=?', (did,)).fetchone()
    nature = ligne['nature'] if ligne else ''
    if nature in ('conseil', 'contentieux'):
        return nature, 0
    instance = _lignes(desk, 'SELECT 1 FROM dossier_instances5698 WHERE dossier_id=? LIMIT 1', (did,))
    return ('contentieux' if instance else 'conseil'), 0


def _champs_cabinet(desk, profil):
    """Champs métier définis par le cabinet pour ce profil (identifiant stable, type, aide) ; liste fermée de types."""
    out = []
    modele = _presentation_brute(desk, 'cabinet', '', profil)
    for c in (modele or {'config': {}})['config'].get('champs') or []:
        if isinstance(c, dict) and CHAMP_PERSO.fullmatch(str(c.get('id') or '')) and c.get('type') in TYPES and c['id'] not in [x['id'] for x in out]:
            out.append({'id': c['id'], 'libelle': str(c.get('libelle') or c['id'])[:80], 'type': c['type'], 'aide': str(c.get('aide') or '')[:200],
                        'choix': [str(x)[:80] for x in (c.get('choix') or [])][:20], 'carte': 'champs'})
    return out


def champs_du_profil(desk, profil):
    livres = [{'id': i, 'libelle': l, 'type': t, 'carte': c, 'aide': a, 'choix': []} for i, l, t, c, profils, a in CHAMPS if profil in profils]
    return livres + _champs_cabinet(desk, profil)


def presentation(desk, compte, did, profil):
    """Présentation effective : modèle du cabinet, puis préférence personnelle (ordre, cartes masquées, cartes repliées). Une carte non
    masquable reste affichée ; masquer une carte ne retire ni donnée ni contrôle (le compteur « à vérifier » reste dans la première carte)."""
    disponibles = [c for c in CARTES if profil in c[2]]
    ordre, masquees, repliees, source, version = [c[0] for c in disponibles], [], ['decisions_cabinet', 'arguments'], 'defaut', 0
    for portee, proprietaire in (('cabinet', ''), ('utilisateur', compte)):
        p = _presentation_brute(desk, portee, proprietaire, profil)
        if not p:
            continue
        cfg = p['config']
        connu = [c for c in cfg.get('ordre') or [] if c in ordre]
        ordre = connu + [c for c in ordre if c not in connu]
        masquees = [c for c in cfg.get('masquees') or [] if c in ordre]
        repliees = [c for c in cfg.get('repliees') or [] if c in ordre]
        source = portee
        if portee == 'utilisateur':
            version = p['version']
    non_masquables = {c[0] for c in CARTES if not c[3]}
    masquees = [c for c in masquees if c not in non_masquables]
    if 'en_bref' in ordre:   # la première vue reste en tête
        ordre = ['en_bref'] + [c for c in ordre if c != 'en_bref']
    libelles = {c[0]: c[1] for c in CARTES}
    return {'profil': profil, 'cartes': [{'id': c, 'libelle': libelles[c], 'masquee': c in masquees, 'repliee': c in repliees,
                                          'masquable': c not in non_masquables} for c in ordre],
            'source': source, 'version': version}


# ------------------------------------------------------------------------------------------------ parties : registre, extractions, candidats
def _registre(desk, did):
    return [dict(r) for r in desk.db.execute('SELECT * FROM dossier_parties5697 WHERE dossier_id=? ORDER BY nom_norm, role', (did,))]


def _extraites(desk, did, retirees=False):
    """Parties extraites des pièces (``d51``) : actives, ou retirées par l'avocat (pour signaler une source modifiée depuis)."""
    statut = 'retiree' if retirees else 'active'
    out = []
    for r in _lignes(desk, 'SELECT * FROM d51_parties WHERE matter=? AND status=? ORDER BY client DESC, display_name', (did, statut)):
        p = dict(r)
        p['aliases'] = [dict(a) for a in _lignes(desk, 'SELECT alias,source,via FROM d51_aliases WHERE party_id=? ORDER BY alias', (r['id'],))]
        p['roles'] = [dict(x) for x in _lignes(desk, 'SELECT pa.procedure_id,pa.role,pa.counsel,pr.juridiction,pr.rg FROM d51_participations pa '
                                                     'LEFT JOIN d51_procedures pr ON pr.id=pa.procedure_id WHERE pa.party_id=?', (r['id'],))]
        out.append(p)
    return out


def _mentions(desk, did):
    return [dict(r) for r in _lignes(desk, 'SELECT * FROM fiche_mentions56109 WHERE dossier_id=? ORDER BY source_nom, ligne', (did,))]


def _sources_partie(p, docs):
    """Sources d'une partie extraite : ses mentions (alias) rattachées aux pièces du dossier par leur nom de fichier."""
    par_nom = {}
    for chemin, d in docs.items():
        par_nom.setdefault(d['name'], chemin)
    brutes = []
    for a in p.get('aliases') or []:
        if not a.get('source') or a['source'] in ('fusion', 'avant correction', 'saisie de l’avocat'):
            continue
        chemin = a['source'] if a['source'] in docs else par_nom.get(a['source'])
        brutes.append({'path': chemin, 'extrait': a['alias']} if chemin else {'label': a['source']})
    return _sources(brutes, docs)


def _fragments_juridiction(desk, did):
    """Fins d'intitulés de juridiction du dossier (« de lyon », « judiciaire de lyon ») : sert à proposer le nettoyage d'une ancienne
    extraction fautive d'après sa provenance et son contexte, jamais d'après une liste de villes."""
    from .entete5679 import fold
    intitules = [r[0] for r in _lignes(desk, "SELECT juridiction FROM d51_procedures WHERE matter=? AND juridiction<>''", (did,))]
    intitules += [r[0] for r in _lignes(desk, "SELECT juridiction FROM dossier_instances5698 WHERE dossier_id=? AND juridiction<>''", (did,))]
    intitules += [r[0] for r in _lignes(desk, "SELECT juridiction FROM dossiers5697 WHERE id=? AND juridiction<>''", (did,))]
    out = {}
    for intitule in intitules:
        mots = fold(intitule).replace('’', "'").split()
        for n in range(1, len(mots)):
            out.setdefault(' '.join(mots[n:]), intitule)
    return out


def _motif_nettoyage(p, fragments):
    """Motif de nettoyage proposé pour une partie extraite d'un en-tête par une version antérieure du lecteur ; '' sinon. Une partie
    corrigée ou ajoutée par l'avocat, ou désignée comme cliente, n'est jamais proposée au nettoyage."""
    from .entete5679 import _AVOCAT_LIGNE, _structure, fold
    if p.get('origin') != 'entete' or p.get('client'):
        return ''
    nom = fold(p['display_name']).replace('’', "'").strip()
    if nom in fragments and len(nom.split()) <= 4:
        return 'Probable fragment de l’intitulé « %s », lu comme un nom par une version antérieure.' % fragments[nom]
    if _structure(p['display_name']):
        return 'Probable ligne de structure de l’acte (titre, formule, section), lue comme un nom par une version antérieure.'
    if _AVOCAT_LIGNE.match(p['display_name']):
        return 'Probable avocat d’une partie, lu comme une partie par une version antérieure.'
    return ''


def _id_mention(nom):
    ident = _identite(nom)
    return 'm' + _sha('mention', ident['kind'], ident['core'], ident['forme'], ident['gender'])[:24]


def rapprocher(desk, did, docs=None):
    """Rapprochement du registre et des extractions (lecture seule) : ``(inscrites, candidats, a_revoir)``.

    Certitude = lien explicite décidé par l'avocat, ou même identité qu'UNE seule partie du registre. Sinon la mention reste un candidat,
    avec ses doublons possibles ; rien n'est fusionné d'office (homonymes, personne et sa société)."""
    docs = _documents(desk, did) if docs is None else docs
    registre = _registre(desk, did)
    liens = {r['candidat_id']: r['partie_id'] for r in _lignes(desk, 'SELECT candidat_id,partie_id FROM fiche_liens56109 WHERE dossier_id=?', (did,))}
    ecartes = {(r['cible_id'], r['empreinte']) for r in _lignes(desk, "SELECT cible_id,empreinte FROM fiche_decisions56109 WHERE dossier_id=? AND cible_type='candidat' "
                                                                      "AND decision='ecarter'", (did,))}
    ids_ecartes = {c for c, _ in ecartes}
    par_id = {p['id']: p for p in registre}
    apports = {p['id']: {'sources': [], 'qualites': [], 'conseils': [], 'extraites': []} for p in registre}
    fragments = _fragments_juridiction(desk, did)
    instances = [dict(r) for r in _lignes(desk, 'SELECT id,juridiction,rg,degre,principale FROM dossier_instances5698 WHERE dossier_id=?', (did,))]
    candidats = {}

    def cible(nom, ident):
        """Partie du registre certaine pour cette mention : lien explicite, sinon même identité qu'une seule partie ; et les doublons possibles."""
        if liens.get(ident) in par_id:
            return liens[ident], []
        memes = [p for p in registre if _meme(nom, p['nom'])[0] == 'meme']
        if len(memes) == 1:
            return memes[0]['id'], []
        possibles = [{'id': p['id'], 'nom': p['nom'], 'role': p['role'], 'motif': _meme(nom, p['nom'])[1] or 'même nom'}
                     for p in registre if _meme(nom, p['nom'])[0] in ('meme', 'candidat')]
        return '', possibles

    def instance_de(role):
        from .entete5679 import same_case_number
        for i in instances:
            if role.get('rg') and same_case_number(role['rg'], i['rg']):
                return i['id']
        return ''

    for p in _extraites(desk, did):
        sources = _sources_partie(p, docs)
        qualites = [{'qualite': r['role'], 'instance': instance_de(r), 'cadre': ' '.join(x for x in (r.get('juridiction') or '', ('RG ' + r['rg']) if r.get('rg') else '') if x),
                     'conseil': r.get('counsel') or '', 'origine': 'piece'} for r in p['roles'] if r.get('role')]
        pid, possibles = cible(p['display_name'], p['id'])
        if pid:
            a = apports[pid]
            a['sources'] += sources
            a['qualites'] += qualites
            a['conseils'] += [x for x in [p.get('counsel')] + [q['conseil'] for q in qualites] if x]
            a['extraites'].append(p['id'])
            continue
        candidats[p['id']] = {'id': p['id'], 'nom': p['display_name'], 'nature_personne': {'societe': 'morale', 'personne': 'physique'}.get(p['kind'], ''),
                              'qualite': ', '.join(sorted({q['qualite'] for q in qualites})), 'qualites': qualites, 'conseil': p.get('counsel') or '',
                              'representant': '', 'role_suggere': 'client' if p.get('client') else '', 'sources': sources, 'version': p['version'],
                              'origine': p.get('origin') or '', 'doublons_possibles': possibles, 'nettoyage': _motif_nettoyage(p, fragments), 'd51': True,
                              'morceaux': {p['id']: list(sources)}}
    for m in _mentions(desk, did):
        source = _sources([{'path': m['source_path'], 'extrait': m['extrait'], 'page': 1}], docs)
        qualite = {'qualite': m['qualite'], 'instance': '', 'cadre': m['cadre'], 'conseil': m['conseil'], 'origine': 'piece'} if m['qualite'] else None
        mid_ = _id_mention(m['nom'])
        pid, possibles = cible(m['nom'], mid_)
        if pid:
            apports[pid]['sources'] += source
            if qualite:
                apports[pid]['qualites'].append(qualite)
            continue
        existant = next((c for c in candidats.values() if _meme(m['nom'], c['nom'])[0] == 'meme'), None)
        if existant is None:
            existant = candidats.setdefault(mid_, {'id': mid_, 'nom': m['nom'], 'nature_personne': m['nature_personne'], 'qualite': '', 'qualites': [],
                                                   'conseil': m['conseil'], 'representant': m['representant'], 'role_suggere': '', 'sources': [], 'version': 0,
                                                   'origine': 'mention', 'doublons_possibles': possibles, 'nettoyage': '', 'd51': False, 'morceaux': {}})
        existant['sources'] = (existant['sources'] + source)[:6]
        existant['morceaux'].setdefault(mid_, []).extend(source)
        existant['representant'] = existant['representant'] or m['representant']
        if qualite and qualite['qualite'] not in [q['qualite'] for q in existant['qualites']]:
            existant['qualites'].append(qualite)
            existant['qualite'] = ', '.join(q['qualite'] for q in existant['qualites'])
    # refus durables : un candidat écarté ne revient que si l'empreinte de ses sources a changé depuis la décision. Un candidat peut réunir
    # une partie extraite et une mention de contrat : chaque morceau garde sa propre empreinte (et son propre lien une fois accepté).
    visibles, a_revoir = [], []
    for c in candidats.values():
        c['empreintes'] = {i: _empreinte_sources(srcs) for i, srcs in c.pop('morceaux').items()}
        c['empreinte'] = _sha(*sorted(c['empreintes'].values()))[:24]
        if all((i, e) in ecartes for i, e in c['empreintes'].items()):
            continue
        if any(i in ids_ecartes and (i, e) not in ecartes for i, e in c['empreintes'].items()):
            c['revu'] = 'Source modifiée depuis que cette mention a été écartée : à revoir.'
            a_revoir.append(c)
        visibles.append(c)
    for p in _extraites(desk, did, retirees=True):   # partie écartée dans le modèle : signalée seulement si sa source a changé depuis
        sources = _sources_partie(p, docs)
        empreinte = _empreinte_sources(sources)
        if p['id'] in ids_ecartes and (p['id'], empreinte) not in ecartes and _verifiable(sources):
            c = {'id': p['id'], 'nom': p['display_name'], 'nature_personne': '', 'qualite': '', 'qualites': [], 'conseil': p.get('counsel') or '', 'representant': '',
                 'role_suggere': '', 'sources': sources, 'version': p['version'], 'origine': p.get('origin') or '', 'doublons_possibles': [], 'nettoyage': '',
                 'd51': True, 'empreinte': empreinte[:24], 'empreintes': {p['id']: empreinte},
                 'revu': 'Source modifiée depuis que cette mention a été écartée : à revoir.'}
            a_revoir.append(c)
            visibles.append(c)
    # parties inscrites : valeurs du registre, enrichies de la provenance
    participations = {}
    for r in _lignes(desk, 'SELECT * FROM fiche_participations56109 WHERE dossier_id=?', (did,)):
        participations.setdefault(r['partie_id'], []).append(dict(r))
    libelle_instance = {i['id']: ' '.join(x for x in (i['juridiction'] or 'Juridiction à préciser', ('RG ' + i['rg']) if i['rg'] else '') if x) for i in instances}
    noms = {p['id']: p['nom'] for p in registre}
    inscrites = []
    for p in registre:
        a = apports[p['id']]
        humaines = {x['cadre_id']: x for x in participations.get(p['id'], []) if x['cadre_type'] == 'instance'}
        roles = [{'instance': iid, 'cadre': libelle_instance.get(iid, 'Instance retirée'), 'qualite': x['qualite'], 'origine': 'avocat', 'version': x['version'], 'id': x['id']}
                 for iid, x in humaines.items()]
        for q in a['qualites']:   # qualité lue dans une pièce : montrée seulement si l'avocat n'a rien fixé pour cette instance
            if q['instance'] and q['instance'] in humaines:
                continue
            if not any(r['qualite'] == q['qualite'] and r['cadre'] == (libelle_instance.get(q['instance']) or q['cadre']) for r in roles):
                roles.append({'instance': q['instance'], 'cadre': libelle_instance.get(q['instance']) or q['cadre'], 'qualite': q['qualite'], 'origine': 'piece'})
        try:
            propres = json.loads(p.get('source_json') or '[]')
        except ValueError:
            propres = []
        sources = _sources(propres, docs) + [s for s in a['sources']]
        vus, uniques = set(), []
        for s in sources:
            cle = json.dumps(s, sort_keys=True, ensure_ascii=False)
            if cle not in vus:
                vus.add(cle)
                uniques.append(s)
        inscrites.append({'id': p['id'], 'nom': p['nom'], 'role': p['role'], 'role_libelle': D.ROLES_PARTIES.get(p['role'], p['role']),
                          'qualite': p.get('qualite') or '', 'type_personne': p.get('type_personne') or '', 'avocat': noms.get(p.get('avocat_id') or '', ''),
                          'conseil_lu': '' if p.get('avocat_id') else next(iter(a['conseils']), ''), 'roles_par_instance': roles,
                          'provenance': p.get('provenance') or 'manuel', 'role_par': p.get('role_par') or p.get('modifie_par') or '', 'role_le': p.get('role_le') or '',
                          'sources': uniques[:6], 'version': p.get('version') or 1, 'origine': 'registre', 'extraites': a['extraites'],
                          'partie': p['role'] in ROLES_DE_PARTIE})
    visibles.sort(key=lambda c: (bool(c['nettoyage']), D._plier(c['nom'])))
    return inscrites, visibles, a_revoir


def unifier_representation(desk, mid, extraites):
    """« Qui représente qui » (``resume5659.representation``) lu par la projection commune : dès qu'un dossier du registre a des parties
    inscrites, ce sont elles qui sont montrées (nom, rôle et qualité du registre), enrichies du conseil et des sources lus dans les pièces ;
    les mentions non rapprochées n'y figurent plus (elles sont dans « À vérifier »). Dossier hors registre ou sans partie inscrite :
    la liste extraite est rendue telle quelle."""
    did = D.resoudre(desk, mid)
    if not did:
        return extraites
    inscrites, _, _ = rapprocher(desk, did)
    parties = [p for p in inscrites if p['partie']]
    if not parties:
        return extraites
    par_id = {x.get('id'): x for x in extraites}
    out = []
    for p in parties:
        lue = next((par_id[i] for i in p['extraites'] if i in par_id), {})
        qualites = [r['qualite'] for r in p['roles_par_instance']]
        out.append({'id': lue.get('id') or 'registre:' + p['id'], 'nom': p['nom'], 'client': p['role'] == 'client',
                    'qualite': p['qualite'] or ', '.join(dict.fromkeys(qualites)), 'conseil': p['avocat'] or p['conseil_lu'] or lue.get('conseil') or '',
                    'version': '%s.%s' % (p['version'], lue.get('version') or 0),
                    'sources': [{k: v for k, v in s.items() if k in ('path', 'page', 'extrait', 'label') and v} for s in p['sources'] if not s.get('indisponible')][:3],
                    'origine': 'registre'})
    out.sort(key=lambda x: (not x['client'], x['nom']))
    return out


def noms_unifies(desk, mid):
    """{identifiant d'une partie extraite: nom du registre} pour les parties rapprochées : les récits nomment les parties comme la fiche."""
    try:
        did = D.resoudre(desk, mid)
        if not did:
            return {}
        inscrites, _, _ = rapprocher(desk, did)
        return {i: p['nom'] for p in inscrites for i in p['extraites']}
    except (sqlite3.Error, Stop):
        return {}


# ------------------------------------------------------------------------------------------------ éléments du dossier par fonction
def _technique_seule(texte):
    return bool(TECHNIQUE.search(texte or '')) and not ENJEU.search(texte or '') and len(str(texte)) < 240


def _elements(desk, did, docs, noms):
    """Informations du dossier (``d51_assertions``) rangées par fonction, sans jamais changer leur portée : ``(sections, limites)``."""
    sections = {'faits': [], 'allegations': [], 'pretentions': [], 'arguments': []}
    ecartes = {'technique': 0, 'sans_source': 0}
    rows = _lignes(desk, "SELECT * FROM d51_assertions WHERE matter=? AND validity<>'historique' AND review IN ('enregistre','propose','a_verifier') "
                         "AND relevance<>'hors_sujet' ORDER BY CASE WHEN date_value='' THEN 1 ELSE 0 END, date_value, created", (did,))
    for a in rows:
        try:
            brutes = json.loads(a['sources'] or '[]')
        except ValueError:
            brutes = []
        if _technique_seule(a['text']):   # le format d'un fichier n'est pas un fait de l'affaire, sauf enjeu juridique (authenticité, fidélité)
            ecartes['technique'] += 1
            continue
        sources = _sources(brutes, docs)
        humain = a['origin'] == 'avocat' and bool(a['review_by'] or a['author'])
        if not sources and not humain:   # une information sans source ni auteur humain n'est pas présentée
            ecartes['sans_source'] += 1
            continue
        attribue = a['author'] in noms
        if a['kind'] == 'fait':
            section = 'faits' if a['proof'] in ETABLI or (not attribue and a['proof'] == 'a_verifier') else 'allegations'
        else:
            section = {'demande': 'pretentions', 'montant': 'pretentions', 'argument': 'arguments'}.get(a['kind'], 'faits')
        statut = STATUTS_REVUE.get(a['review'], 'propose')
        source_perdue = bool(sources) and not _verifiable(sources)
        sections[section].append({
            'id': a['id'], 'type': a['kind'], 'texte': a['text'], 'auteur': noms.get(a['author']) or a['author_label'] or 'document', 'attribue': attribue,
            # la validation d'une transcription ne change ni la nature ni la force probante de l'information
            'force_probante': a['proof'], 'force_libelle': FORCES.get(a['proof'], a['proof']), 'statut': statut,
            'a_verifier': statut != 'confirme' or source_perdue or a['validity'] == 'a_recontroler',
            'decision_humaine': {'par': a['review_by'], 'le': a['review_at'], 'motif': a['review_motif']} if a['review_by'] else None,
            'origine': a['origin'], 'revision': a['version'], 'date': a['date_value'], 'precision_date': a['date_precision'], 'montant': a['amount'],
            'sources': sources})
    limites = []
    if ecartes['technique']:
        limites.append('%d élément(s) purement technique(s) (format de fichier) non repris dans la fiche ; ils restent consultables dans les informations du dossier.' % ecartes['technique'])
    if ecartes['sans_source']:
        limites.append('%d information(s) sans source vérifiable non reprise(s).' % ecartes['sans_source'])
    return sections, limites


def _procedure(desk, did, docs, noms):
    """Actes de procédure (dates d'acte, de signification, de dépôt, d'audience séparées) et décisions de justice (original examiné ou
    seulement mentionnée). Une date lue dans un acte n'est jamais une échéance confirmée : celles-ci viennent du registre des échéances."""
    actes, decisions = [], []
    for ev in _lignes(desk, "SELECT * FROM d51_events WHERE matter=? AND review<>'refuse' ORDER BY updated DESC LIMIT 80", (did,)):
        try:
            dates, brutes = json.loads(ev['dates'] or '{}'), json.loads(ev['sources'] or '[]')
        except ValueError:
            dates, brutes = {}, []
        sources = _sources(brutes, docs)
        actes.append({'id': ev['id'], 'type': 'acte', 'texte': ev['title'], 'nature': ev['nature'], 'auteur': noms.get(ev['author'], ''),
                      'dates': {k: str(v)[:10] for k, v in dates.items() if v}, 'statut': 'confirme' if ev['review'] == 'enregistre' else 'propose',
                      'etat_acte': ev['status'], 'a_verifier': ev['review'] != 'enregistre' or (bool(sources) and not _verifiable(sources)),
                      'revision': ev['version'], 'sources': sources})
    actes.sort(key=lambda x: max(x['dates'].values(), default=''), reverse=True)
    for j in _lignes(desk, "SELECT * FROM d51_judgments WHERE matter=? AND review<>'refuse' ORDER BY date DESC LIMIT 30", (did,)):
        try:
            brutes = json.loads(j['sources'] or '[]')
        except ValueError:
            brutes = []
        if j['path'] and not brutes:
            brutes = [{'path': j['path']}]
        sources = _sources(brutes, docs)
        examine = bool(j['examined'])
        decisions.append({'id': j['id'], 'type': 'decision', 'texte': ' '.join(x for x in (j['nature'] or 'Décision', j['juridiction'], j['date']) if x),
                          'date': j['date'], 'portee': j['portee'], 'reserves': j['reserves'],
                          # une décision seulement rapportée garde ce statut jusqu'à la lecture de son original
                          'statut': 'confirme' if examine and j['review'] == 'enregistre' else ('propose' if examine else 'rapportee'),
                          'original_examine': examine, 'a_verifier': not examine or j['review'] != 'enregistre' or (bool(sources) and not _verifiable(sources)),
                          'sources': sources})
    return actes, decisions


def _decisions_cabinet(desk, did):
    objectif, autres = [], []
    libelles = {'objectif': 'Objectif du client', 'consigne': 'Consigne du cabinet', 'argument_retenu': 'Argument retenu',
                'argument_abandonne': 'Argument abandonné (ne plus proposer)', 'choix': 'Choix stratégique'}
    for d in _lignes(desk, "SELECT * FROM d51_cabinet_decisions WHERE matter=? AND status='active' ORDER BY kind, created", (did,)):
        item = {'id': d['id'], 'type': 'decision_cabinet', 'genre': d['kind'], 'libelle': libelles.get(d['kind'], d['kind']), 'texte': d['text'],
                'auteur': d['author'], 'statut': 'confirme', 'a_verifier': False, 'origine': 'avocat', 'revision': d['version'],
                'sources': [{'label': 'Décision du cabinet — %s, %s' % (d['author'], str(d['created'])[:10])}]}
        (objectif if d['kind'] == 'objectif' else autres).append(item)
    return objectif, autres


def _contrats(desk, did, docs):
    out = [{'id': chemin, 'type': 'contrat', 'texte': d['name'], 'date': (d['doc_date'] or d['modified'] or '')[:10], 'statut': 'confirme',
            'a_verifier': d['reading'] not in ('lisible', 'partielle'), 'lecture': d['reading'],
            'sources': [{'path': chemin, 'document': d['name'], 'version': (d['etag'] or d['text_sha'] or '')[:24]}]}
           for chemin, d in docs.items() if d['nature'] == 'contrat']
    out.sort(key=lambda x: x['date'], reverse=True)
    return out[:MAX_ELEMENTS]


def _valeurs(desk, did, champs, docs):
    """Valeurs des champs de la fiche et propositions en attente : ``{champ: {...}}``."""
    stockees = {r['champ']: r for r in _lignes(desk, 'SELECT * FROM fiche_valeurs56109 WHERE dossier_id=?', (did,))}
    propositions = {}
    for r in _lignes(desk, "SELECT * FROM fiche_propositions56109 WHERE dossier_id=? AND etat='propose' ORDER BY cree_le", (did,)):
        try:
            sources = _sources(json.loads(r['sources_json'] or '[]'), docs)
            valeur = json.loads(r['valeur_json'])
        except ValueError:
            continue
        propositions.setdefault(r['champ'], []).append({'id': r['id'], 'valeur': valeur, 'justification': r['justification'], 'base_version': r['base_version'],
                                                        'version': r['version'], 'origine': r['origine'], 'sources': sources, 'a_verifier': True,
                                                        'verifiable': _verifiable(sources)})
    out = {}
    for c in champs:
        r = stockees.get(c['id'])
        try:
            valeur = json.loads(r['valeur_json']) if r else None
            sources = _sources(json.loads(r['sources_json'] or '[]'), docs) if r else []
        except ValueError:
            valeur, sources = None, []
        out[c['id']] = {**c, 'valeur': valeur, 'version': r['version'] if r else 0, 'origine': r['origine'] if r else '',
                        'origine_libelle': ORIGINES.get(r['origine'], '') if r else '', 'modifie_par': r['modifie_par'] if r else '',
                        'modifie_le': r['modifie_le'] if r else '', 'sources': sources, 'propositions': propositions.get(c['id'], []),
                        # une valeur validée dont toutes les sources ont disparu redevient « à vérifier » ; une saisie de l'avocat reste la sienne
                        'a_verifier': bool(r) and r['origine'] != 'avocat' and bool(sources) and not _verifiable(sources)}
    return out


def _en_bref(desk, did, ligne, profil, valeurs, objectif, actes, decisions, candidats, jour):
    """Première vue : client, responsable, type, quatre questions, prochaine échéance CONFIRMÉE et dernier changement juridique."""
    clients = [r['nom'] for r in _lignes(desk, "SELECT nom FROM dossier_parties5697 WHERE dossier_id=? AND role='client' ORDER BY nom_norm", (did,))]
    echeance = _lignes(desk, "SELECT due,note,rule_id FROM deadlines450 WHERE matter=? AND status IN ('confirmee','manuel') AND due>=? AND due<>'' ORDER BY due LIMIT 1",
                       (did, jour))
    proposees = _lignes(desk, "SELECT COUNT(*) FROM deadlines450 WHERE matter=? AND status IN ('a_confirmer','a_completer') AND (due='' OR due>=?)", (did, jour))
    changement = _lignes(desk, 'SELECT title,created,kind FROM d51_changes WHERE matter=? AND technical=0 ORDER BY id DESC LIMIT 1', (did,))
    return {'client': ', '.join(clients) or ligne['client'] or '', 'client_etat': ligne['client_etat'], 'responsable': ligne['responsable'],
            'type': profil, 'type_libelle': PROFILS[profil], 'nature_registre': ligne['nature'],
            'questions': [valeurs[c] for c in ('mission', 'situation', 'element_nouveau', 'attendu') if c in valeurs],
            'objectif': objectif[:2],
            'echeance_confirmee': {'date': echeance[0]['due'][:10], 'libelle': (echeance[0]['note'] or str(echeance[0]['rule_id']).replace('_', ' '))[:160]} if echeance else None,
            'dates_a_verifier': proposees[0][0] if proposees else 0,
            'dernier_changement': {'date': str(changement[0]['created'])[:10], 'libelle': str(changement[0]['title'])[:200]} if changement else None,
            'a_verifier': len(candidats),
            'propositions': sum(len(v['propositions']) for v in valeurs.values())}


def fiche(desk, acteur, ident, profil=None):
    """Projection de la fiche pour un acteur autorisé (garde du registre AVANT toute lecture). Aucune écriture, aucun appel à un modèle."""
    a, did, ligne = _dossier(desk, acteur, ident)
    choisi, version_profil = profil_du_dossier(desk, did, ligne)
    profil = profil if profil in PROFILS else choisi
    docs = _documents(desk, did)
    inscrites, candidats, a_revoir = rapprocher(desk, did, docs)
    noms = {r['id']: r['display_name'] for r in _lignes(desk, 'SELECT id,display_name FROM d51_parties WHERE matter=?', (did,))}
    noms.update({i: p['nom'] for p in inscrites for i in p['extraites']})   # une partie rapprochée porte le nom du registre partout
    sections, limites = _elements(desk, did, docs, noms)
    actes, decisions = _procedure(desk, did, docs, noms)
    objectif, cabinet = _decisions_cabinet(desk, did)
    champs = champs_du_profil(desk, profil)
    valeurs = _valeurs(desk, did, champs, docs)
    try:
        from .dossiers56100 import jour_cabinet
        jour = jour_cabinet(desk).isoformat()
    except Exception:   # noqa: BLE001 — fuseau illisible : jour UTC
        jour = _maintenant()[:10]
    contenu = {'objectif': objectif, 'faits': sections['faits'], 'allegations': sections['allegations'], 'pretentions': sections['pretentions'],
               'arguments': sections['arguments'], 'procedure': actes, 'decisions': decisions, 'decisions_cabinet': cabinet,
               'contrats': _contrats(desk, did, docs)}
    visibles = {c[0] for c in CARTES if profil in c[2]} | {'objectif'}
    compteurs = {k: len(v) for k, v in contenu.items()}
    sections_profil = {k: v[:MAX_ELEMENTS] for k, v in contenu.items() if k in visibles}
    lus = sum(1 for d in docs.values() if d['reading'] in ('lisible', 'partielle'))
    out = {'dossier': did, 'reference': ligne['ref'], 'titre': ligne['titre'], 'profil': profil, 'profil_dossier': choisi, 'version_profil': version_profil,
           'profils': PROFILS, 'en_bref': _en_bref(desk, did, ligne, profil, valeurs, objectif, actes, decisions, candidats, jour),
           'parties_inscrites': inscrites, 'candidats_a_verifier': candidats, 'a_revoir': [c['id'] for c in a_revoir],
           'sections': sections_profil, 'champs': valeurs, 'compteurs': compteurs,
           'couverture': {'documents': len(docs), 'lus': lus, 'limites': limites},
           'presentation': presentation(desk, a.compte, did, profil)}
    out['revision'] = _sha(json.dumps({k: out[k] for k in ('parties_inscrites', 'candidats_a_verifier', 'sections', 'champs', 'profil')}, sort_keys=True,
                                      ensure_ascii=False, default=str))[:16]
    return out


# ------------------------------------------------------------------------------------------------ écritures : candidats
def _candidat(desk, did, cid):
    if not CANDIDAT.fullmatch(str(cid or '')):
        raise Stop('introuvable')
    _, candidats, _ = rapprocher(desk, did)
    c = next((x for x in candidats if x['id'] == cid), None)
    if c is None:
        raise Stop('introuvable')
    return c


def _sources_a_garder(c):
    return [{k: v for k, v in s.items() if k in ('path', 'page', 'extrait', 'label') and v} for s in c['sources'] if not s.get('indisponible')][:4]


def decider_candidat(desk, acteur, ident, cid, decision, donnees, cle):
    """Décision de l'avocat sur UNE mention lue dans les pièces (jamais en masse) :

    - ``accepter`` (ou ``corriger`` : nom, qualité ou coordonnées modifiés) : la personne est inscrite au registre avec le RÔLE CHOISI
      (jamais « adverse » par défaut), sa provenance et ses sources ; le lien explicite avec la mention est gardé ;
    - ``fusionner`` : la mention est rattachée à une partie déjà inscrite, dont aucune valeur n'est modifiée ;
    - ``ecarter`` : refus durable avec l'empreinte des sources (la mention ne revient que si sa source change).
    """
    from . import fiche5698 as F
    a, did, _ = _dossier(desk, acteur, ident)
    D._peut_ecrire(a)
    d = donnees if isinstance(donnees, dict) else {}
    if decision not in ('accepter', 'corriger', 'fusionner', 'ecarter'):
        raise D.Refus('champs_invalides', {'decision': 'Choisissez : accepter, corriger, fusionner ou écarter.'})
    cabinet = D._cabinet(desk)
    deja = desk.db.execute('SELECT empreinte,recu FROM dossier_commandes5697 WHERE cabinet_id=? AND acteur=? AND operation=? AND cle=?',
                           (cabinet, a.compte, 'fiche_candidat:%s:%s' % (did, cid), str(cle or ''))).fetchone()
    c = None if deja else _candidat(desk, did, cid)   # un rejeu retrouve son reçu même si le candidat a déjà disparu de la liste
    quand = _maintenant()
    if decision in ('accepter', 'corriger'):
        empreinte_corps = _sha('accepter', cid, json.dumps({k: d.get(k) for k in sorted(d) if k not in ('cle', 'decision')}, sort_keys=True, ensure_ascii=False, default=str))
        if deja:   # rejeu : même clé et même corps → reçu initial, aucun second effet ; autre corps → 409
            if deja['empreinte'] != empreinte_corps:
                raise Stop('cle_idempotence_deja_utilisee')
            return {**json.loads(deja['recu']), 'rejoue': True}
        champs = {'nom': d.get('nom') or c['nom'], 'role': d.get('role') or '', 'qualite': d.get('qualite') if 'qualite' in d else c['qualite'][:160],
                  'type_personne': d.get('type_personne') or c['nature_personne'], 'courriel': d.get('courriel') or '', 'telephone': d.get('telephone') or '',
                  'adresse': d.get('adresse') or '', 'siren': d.get('siren') or '', 'avocat_id': d.get('avocat_id') or '', 'note': d.get('note') or ''}
        # rôle exigé (« Choisissez un rôle »), commande idempotente du registre, conflits relus pour une partie adverse
        recu = F.ajouter_partie(desk, acteur, did, champs, cle)
        corrige = D._plier(champs['nom']) != D._plier(c['nom']) or (champs['qualite'] or '') != (c['qualite'] or '')
        with D._transaction(desk):
            desk.db.execute('UPDATE dossier_parties5697 SET provenance=?, source_json=?, role_par=?, role_le=? WHERE id=? AND dossier_id=?',
                            ('piece', json.dumps(_sources_a_garder(c), ensure_ascii=False), a.compte, quand, recu['id'], did))
            for morceau in c['empreintes']:
                desk.db.execute('INSERT OR REPLACE INTO fiche_liens56109(dossier_id,candidat_id,partie_id,decision,version,decision_par,decision_le) VALUES(?,?,?,?,1,?,?)',
                                (did, morceau, recu['id'], 'corriger' if corrige else 'accepter', a.compte, quand))
            D._journal(desk, did, a.compte, 'candidat_accepte', {'candidat': cid, 'partie': recu['id'], 'role': champs['role'], 'corrige': corrige})
            recu = {**recu, 'candidat': cid, 'decision': 'corriger' if corrige else 'accepter'}
            D._enregistrer_commande(desk, cabinet, a, 'fiche_candidat:%s:%s' % (did, cid), cle, empreinte_corps, recu)
        return recu
    corps = {'decision': decision, 'partie': str(d.get('partie_id') or ''), 'motif': str(d.get('motif') or '')[:300], 'version': d.get('version')}
    if c is not None and c['d51'] and decision == 'ecarter' and d.get('version') not in (None, '') and int(d['version']) != c['version']:
        raise Stop('version_perimee')
    with D._transaction(desk):
        empreinte, rejoue = D._commande(desk, cabinet, a, 'fiche_candidat:%s:%s' % (did, cid), cle, corps)
        if rejoue:
            return rejoue
        if decision == 'fusionner':
            cible = desk.db.execute('SELECT id,nom,source_json FROM dossier_parties5697 WHERE id=? AND dossier_id=?', (corps['partie'], did)).fetchone()
            if not cible:
                raise D.Refus('champs_invalides', {'partie_id': 'Choisissez une personne déjà inscrite dans ce dossier.'})
            try:
                connues = json.loads(cible['source_json'] or '[]')
            except ValueError:
                connues = []
            ajout = [s for s in _sources_a_garder(c) if s not in connues]
            # seule la provenance s'enrichit : nom, rôle, qualité et coordonnées de la partie inscrite ne changent pas
            desk.db.execute('UPDATE dossier_parties5697 SET source_json=? WHERE id=?', (json.dumps((connues + ajout)[:8], ensure_ascii=False), cible['id']))
            for morceau in c['empreintes']:
                desk.db.execute('INSERT OR REPLACE INTO fiche_liens56109(dossier_id,candidat_id,partie_id,decision,version,decision_par,decision_le) VALUES(?,?,?,?,1,?,?)',
                                (did, morceau, cible['id'], 'fusionner', a.compte, quand))
            D._journal(desk, did, a.compte, 'candidat_fusionne', {'candidat': cid, 'partie': cible['id']})
            recu = {'id': cible['id'], 'dossier': did, 'candidat': cid, 'decision': 'fusionner', 'message': 'Mention rattachée à %s : sa fiche n’a pas été modifiée.' % cible['nom'],
                    'recu': {'operation': 'fiche_candidat', 'cle': cle, 'quand': quand}}
        else:
            for morceau, empreinte_morceau in c['empreintes'].items():
                desk.db.execute('INSERT OR REPLACE INTO fiche_decisions56109(id,dossier_id,cible_type,cible_id,decision,empreinte,libelle,motif,decision_par,decision_le) '
                                'VALUES(?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, did, 'candidat', morceau, 'ecarter', empreinte_morceau, c['nom'][:200], corps['motif'],
                                                                a.compte, quand))
            D._journal(desk, did, a.compte, 'candidat_ecarte', {'candidat': cid, 'nom': c['nom'], 'nettoyage': bool(c['nettoyage'])})
            recu = {'id': cid, 'dossier': did, 'candidat': cid, 'decision': 'ecarter', 'message': '« %s » écarté(e) : la mention ne sera plus proposée tant que sa source ne change pas.' % c['nom'],
                    'recu': {'operation': 'fiche_candidat', 'cle': cle, 'quand': quand}}
        D._enregistrer_commande(desk, cabinet, a, 'fiche_candidat:%s:%s' % (did, cid), cle, empreinte, recu)
    if decision == 'ecarter':
        for morceau in c['empreintes']:
            if not morceau.startswith('m'):
                _retirer_du_modele(desk, did, morceau, a.compte, corps['motif'])
    return recu


def _retirer_du_modele(desk, did, cid, compte, motif):
    """La partie extraite écartée est aussi retirée du modèle du dossier (décision humaine existante : alias gardés, jamais recréée),
    pour que toutes les vues actuelles cessent de la montrer. Une panne ici ne défait pas le refus enregistré."""
    try:
        from . import modele5651
        row = desk.db.execute("SELECT origin,status FROM d51_parties WHERE id=? AND matter=?", (cid, did)).fetchone()
        if row and row['status'] == 'active' and row['origin'] != 'avocat':
            modele5651.set_party(desk, did, cid, compte, {'retirer': True})
    except (Stop, sqlite3.Error):
        if desk.db.in_transaction:
            desk.db.rollback()


# ------------------------------------------------------------------------------------------------ écritures : qualité par instance
def enregistrer_participation(desk, acteur, ident, donnees, cle):
    """Qualité d'une partie inscrite dans UNE instance (défenderesse en première instance, appelante ensuite). Qualité vide : retrait."""
    a, did, _ = _dossier(desk, acteur, ident)
    D._peut_ecrire(a)
    d = donnees if isinstance(donnees, dict) else {}
    partie, instance = str(d.get('partie') or ''), str(d.get('instance') or '')
    err = {}
    try:
        qualite = D._texte(d.get('qualite'), 160)
    except ValueError:
        qualite, err['qualite'] = '', 'Qualité trop longue ou caractères invalides.'
    if not desk.db.execute('SELECT 1 FROM dossier_parties5697 WHERE id=? AND dossier_id=?', (partie, did)).fetchone():
        err['partie'] = 'Choisissez une personne inscrite dans ce dossier.'
    if not desk.db.execute('SELECT 1 FROM dossier_instances5698 WHERE id=? AND dossier_id=?', (instance, did)).fetchone():
        err['instance'] = 'Choisissez une instance de ce dossier.'
    if err:
        raise D.Refus('champs_invalides', err)
    try:
        version = int(d.get('version') or 0)
    except (TypeError, ValueError):
        raise D.Refus('champs_invalides', {'version': 'Version attendue manquante : rechargez la page.'}) from None
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'fiche_participation:%s:%s:%s' % (did, partie, instance), cle, {'qualite': qualite, 'version': version})
        if deja:
            return deja
        row = desk.db.execute("SELECT id,version FROM fiche_participations56109 WHERE dossier_id=? AND partie_id=? AND cadre_type='instance' AND cadre_id=?",
                              (did, partie, instance)).fetchone()
        if (row['version'] if row else 0) != version:
            raise Stop('version_perimee')
        quand = _maintenant()
        if not qualite:
            desk.db.execute('DELETE FROM fiche_participations56109 WHERE id=?', (row['id'] if row else '',))
            nouvelle = 0
        elif row:
            desk.db.execute('UPDATE fiche_participations56109 SET qualite=?, origine=?, version=version+1, modifie_par=?, modifie_le=? WHERE id=? AND version=?',
                            (qualite, 'avocat', a.compte, quand, row['id'], version))
            nouvelle = version + 1
        else:
            desk.db.execute('INSERT INTO fiche_participations56109(id,dossier_id,partie_id,cadre_type,cadre_id,qualite,origine,version,modifie_par,modifie_le) '
                            "VALUES(?,?,?,'instance',?,?,'avocat',1,?,?)", (uuid.uuid4().hex, did, partie, instance, qualite, a.compte, quand))
            nouvelle = 1
        D._journal(desk, did, a.compte, 'qualite_par_instance', {'partie': partie, 'instance': instance, 'retiree': not qualite})
        recu = {'dossier': did, 'partie': partie, 'instance': instance, 'version': nouvelle,
                'message': 'Qualité enregistrée pour cette instance.' if qualite else 'Qualité retirée pour cette instance.',
                'recu': {'operation': 'fiche_participation', 'cle': cle, 'quand': quand}}
        D._enregistrer_commande(desk, cabinet, a, 'fiche_participation:%s:%s:%s' % (did, partie, instance), cle, empreinte, recu)
    return recu


# ------------------------------------------------------------------------------------------------ écritures : champs de la fiche
def _champ(desk, did, champ, profil=None):
    profil = profil or profil_du_dossier(desk, did)[0]
    connus = {c['id']: c for p in PROFILS for c in champs_du_profil(desk, p)}
    if champ not in connus:   # le nom du champ vient d'une liste fermée, jamais du modèle ni du navigateur
        raise D.Refus('champs_invalides', {'champ': 'Champ inconnu de la fiche.'})
    return connus[champ]


def valider_valeur(definition, valeur, parties=()):
    """Valeur typée et bornée d'un champ ; refus détaillé sinon. Aucune valeur n'est interprétée comme du code ou du HTML."""
    t = definition['type']
    msg = lambda m: D.Refus('champs_invalides', {'valeur': m})
    if valeur is None or (isinstance(valeur, str) and not valeur.strip()):
        return None
    if t == 'texte':
        texte = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(valeur)).strip()
        if len(texte) > MAX_TEXTE:
            raise msg('Texte trop long (%d caractères au plus).' % MAX_TEXTE)
        return texte
    if t == 'liste':
        lignes = valeur if isinstance(valeur, list) else str(valeur).replace('\r', '').split('\n')
        lignes = [re.sub(r'\s+', ' ', str(x)).strip(' -•\t')[:400] for x in lignes]
        lignes = [x for x in lignes if x]
        if len(lignes) > MAX_LISTE:
            raise msg('Trop de lignes (%d au plus).' % MAX_LISTE)
        return lignes or None
    if t == 'date':
        from datetime import date
        try:
            return date.fromisoformat(str(valeur).strip()[:10]).isoformat()
        except ValueError:
            raise msg('Date invalide (AAAA-MM-JJ).') from None
    if t == 'montant':
        from decimal import Decimal, InvalidOperation
        brut = str(valeur).replace(' ', '').replace('\xa0', '').replace(' ', '').replace('€', '').replace(',', '.')
        try:
            montant = Decimal(brut)
        except InvalidOperation:
            raise msg('Montant invalide.') from None
        if not montant.is_finite() or abs(montant) > Decimal('1000000000'):
            raise msg('Montant invalide.')
        return {'cents': int((montant * 100).quantize(Decimal('1'))), 'devise': 'EUR'}
    if t == 'oui_non':
        if valeur in (True, 'oui', 'true', '1', 'on'):
            return True
        if valeur in (False, 'non', 'false', '0'):
            return False
        raise msg('Répondez oui ou non.')
    if t == 'choix':
        if str(valeur) not in definition.get('choix', []):
            raise msg('Choisissez une valeur de la liste.')
        return str(valeur)
    if t == 'personne':
        if str(valeur) not in parties:
            raise msg('Choisissez une personne inscrite dans ce dossier.')
        return str(valeur)
    raise msg('Type de champ inconnu.')


def _ecrire_valeur(desk, did, champ, valeur, origine, sources, compte, version, quand):
    """Écriture d'une valeur avec contrôle de version (zéro ligne modifiée : ``version_perimee``) et historique ; dans la transaction de l'appelant."""
    courant = desk.db.execute('SELECT * FROM fiche_valeurs56109 WHERE dossier_id=? AND champ=?', (did, champ)).fetchone()
    if (courant['version'] if courant else 0) != version:
        actuel = json.loads(courant['valeur_json']) if courant else None
        raise D.Refus('version_perimee', {'valeur': 'Valeur actuelle : %s' % _apercu(actuel), 'version': str(courant['version'] if courant else 0)})
    brut, src = json.dumps(valeur, ensure_ascii=False), json.dumps(sources or [], ensure_ascii=False)
    if courant:
        n = desk.db.execute('UPDATE fiche_valeurs56109 SET valeur_json=?, origine=?, sources_json=?, version=version+1, modifie_par=?, modifie_le=? '
                            'WHERE dossier_id=? AND champ=? AND version=?', (brut, origine, src, compte, quand, did, champ, version)).rowcount
        if n != 1:
            raise Stop('version_perimee')
    else:
        desk.db.execute('INSERT INTO fiche_valeurs56109(dossier_id,champ,valeur_json,origine,sources_json,version,modifie_par,modifie_le) VALUES(?,?,?,?,?,1,?,?)',
                        (did, champ, brut, origine, src, compte, quand))
    desk.db.execute('INSERT INTO fiche_valeurs_historique56109(dossier_id,champ,version,valeur_json,origine,sources_json,par,le) VALUES(?,?,?,?,?,?,?,?)',
                    (did, champ, version + 1, brut, origine, src, compte, quand))
    return version + 1


def _apercu(valeur):
    if valeur is None:
        return '(vide)'
    if isinstance(valeur, list):
        return ' ; '.join(str(x) for x in valeur)[:300]
    if isinstance(valeur, dict) and 'cents' in valeur:   # entiers seulement : aucun flottant pour un montant
        return '%s%d,%02d €' % ('-' if valeur['cents'] < 0 else '', abs(valeur['cents']) // 100, abs(valeur['cents']) % 100)
    if isinstance(valeur, bool):
        return 'oui' if valeur else 'non'
    return str(valeur)[:300]


def _dependants(desk, did, titre):
    """Les vues et projets dérivés passent « à actualiser » (jamais réécrits) : événement métier du dossier, comme toute correction."""
    try:
        from . import memoire5651
        memoire5651.record_change(desk, did, 'correction', 'fiche', did, _maintenant(), titre[:240], None, commit=False)
        memoire5651.mark_dependents(desk, did, titre[:240], commit=False)
    except (sqlite3.Error, Stop):
        pass


def enregistrer_valeur(desk, acteur, ident, champ, valeur, version, cle):
    """Saisie ou correction de l'avocat : origine « avocat », durable et prioritaire (aucune proposition ne la remplace sans confirmation)."""
    a, did, _ = _dossier(desk, acteur, ident)
    D._peut_ecrire(a)
    definition = _champ(desk, did, champ)
    parties = [r[0] for r in desk.db.execute('SELECT id FROM dossier_parties5697 WHERE dossier_id=?', (did,))]
    valeur = valider_valeur(definition, valeur, parties)
    try:
        version = int(version or 0)
    except (TypeError, ValueError):
        raise D.Refus('champs_invalides', {'version': 'Version attendue manquante : rechargez la page.'}) from None
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'fiche_valeur:%s:%s' % (did, champ), cle, {'valeur': valeur, 'version': version})
        if deja:
            return deja
        quand = _maintenant()
        nouvelle = _ecrire_valeur(desk, did, champ, valeur, 'avocat', [], a.compte, version, quand)
        D._journal(desk, did, a.compte, 'fiche_valeur', {'champ': champ, 'version': nouvelle})
        _dependants(desk, did, 'Fiche corrigée : %s' % definition['libelle'])
        recu = {'dossier': did, 'champ': champ, 'version': nouvelle, 'message': '%s : enregistré.' % definition['libelle'],
                'recu': {'operation': 'fiche_valeur', 'cle': cle, 'quand': quand}}
        D._enregistrer_commande(desk, cabinet, a, 'fiche_valeur:%s:%s' % (did, champ), cle, empreinte, recu)
    return recu


def deposer_proposition(desk, ident, champ, valeur, sources, justification='', origine='ia'):
    """Dépôt d'une proposition pour un champ (appelé par un travail du cabinet, jamais par le navigateur). Les sources sont résolues dans
    le dossier ; sans source vérifiable, rien n'est déposé. Même champ, même contenu et mêmes versions de sources qu'une proposition déjà
    décidée (écartée notamment) : rien n'est redéposé. Rend l'identifiant, ou None."""
    did = D.resoudre(desk, ident)
    if not did:
        raise Stop('introuvable')
    definition = _champ(desk, did, champ)
    parties = [r[0] for r in desk.db.execute('SELECT id FROM dossier_parties5697 WHERE dossier_id=?', (did,))]
    valeur = valider_valeur(definition, valeur, parties)
    resolues = [s for s in _sources(sources, _documents(desk, did), 6) if not s.get('indisponible') and s.get('path')]
    if valeur is None or not resolues:
        return None
    empreinte = _sha(champ, json.dumps(valeur, sort_keys=True, ensure_ascii=False), _empreinte_sources(resolues))
    courant = desk.db.execute('SELECT valeur_json,version FROM fiche_valeurs56109 WHERE dossier_id=? AND champ=?', (did, champ)).fetchone()
    if courant and json.loads(courant['valeur_json']) == valeur:
        return None
    if desk.db.execute('SELECT 1 FROM fiche_propositions56109 WHERE dossier_id=? AND champ=? AND empreinte=?', (did, champ, empreinte)).fetchone():
        return None
    pid = uuid.uuid4().hex
    garder = [{k: v for k, v in s.items() if k in ('path', 'page', 'extrait') and v} for s in resolues]
    desk.db.execute('INSERT INTO fiche_propositions56109(id,dossier_id,champ,base_version,valeur_json,sources_json,justification,empreinte,origine,etat,version,cree_le) '
                    "VALUES(?,?,?,?,?,?,?,?,?,'propose',1,?)", (pid, did, champ, courant['version'] if courant else 0, json.dumps(valeur, ensure_ascii=False),
                                                              json.dumps(garder, ensure_ascii=False), str(justification or '')[:600], empreinte,
                                                              str(origine or 'ia')[:40], _maintenant()))
    desk.db.commit()
    return pid


def decider_proposition(desk, acteur, ident, pid, donnees, cle):
    """Accepter, corriger ou écarter UNE proposition. Version attendue et version de base contrôlées ; une valeur saisie par l'avocat n'est
    remplacée qu'avec ``remplacement`` (confirmation expresse) ; l'identité de celui qui décide vient de la requête, jamais du corps."""
    a, did, _ = _dossier(desk, acteur, ident)
    D._peut_ecrire(a)
    d = donnees if isinstance(donnees, dict) else {}
    decision = str(d.get('decision') or '')
    if decision not in ('accepter', 'corriger', 'ecarter') or not IDENT.fullmatch(str(pid or '')):
        raise D.Refus('champs_invalides', {'decision': 'Choisissez : accepter, corriger ou écarter.'})
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        corps = {k: d.get(k) for k in ('decision', 'version_attendue', 'base_version', 'valeur_corrigee', 'motif', 'remplacement')}
        empreinte, deja = D._commande(desk, cabinet, a, 'fiche_proposition:%s' % pid, cle, corps)
        if deja:
            return deja
        p = desk.db.execute('SELECT * FROM fiche_propositions56109 WHERE id=? AND dossier_id=?', (pid, did)).fetchone()
        if not p:
            raise Stop('introuvable')
        if p['etat'] != 'propose':
            raise Stop('proposition_deja_decidee')
        definition = _champ(desk, did, p['champ'])
        courant = desk.db.execute('SELECT * FROM fiche_valeurs56109 WHERE dossier_id=? AND champ=?', (did, p['champ'])).fetchone()
        version = courant['version'] if courant else 0
        quand = _maintenant()
        nouvelle = version
        if decision != 'ecarter':
            try:
                attendue, base = int(d.get('version_attendue')), int(d.get('base_version'))
            except (TypeError, ValueError):
                raise D.Refus('champs_invalides', {'version_attendue': 'Version attendue manquante : rechargez la page.'}) from None
            if attendue != version or base != p['base_version'] or p['base_version'] != version:
                actuel = json.loads(courant['valeur_json']) if courant else None
                raise D.Refus('version_perimee', {'valeur': 'Valeur actuelle : %s · proposition : %s' % (_apercu(actuel), _apercu(json.loads(p['valeur_json']))),
                                                  'version': str(version)})
        if decision == 'accepter':
            if courant and courant['origine'] == 'avocat' and d.get('remplacement') not in (True, 'true', 'on', '1', 'oui'):
                raise D.Refus('validation_expresse_requise', {'remplacement': 'Valeur saisie par l’avocat : %s. Cochez « Remplacer » pour confirmer.'
                                                              % _apercu(json.loads(courant['valeur_json']))})
            sources = [s for s in _sources(json.loads(p['sources_json'] or '[]'), _documents(desk, did), 6) if not s.get('indisponible')]
            if not sources:
                raise Stop('sources_requises')
            garder = [{k: v for k, v in s.items() if k in ('path', 'page', 'extrait') and v} for s in sources]
            nouvelle = _ecrire_valeur(desk, did, p['champ'], json.loads(p['valeur_json']), 'ia_validee', garder, a.compte, version, quand)
        elif decision == 'corriger':
            parties = [r[0] for r in desk.db.execute('SELECT id FROM dossier_parties5697 WHERE dossier_id=?', (did,))]
            valeur = valider_valeur(definition, d.get('valeur_corrigee'), parties)
            if valeur is None:
                raise D.Refus('champs_invalides', {'valeur_corrigee': 'Indiquez la valeur corrigée.'})
            nouvelle = _ecrire_valeur(desk, did, p['champ'], valeur, 'avocat', [], a.compte, version, quand)
        etat = {'accepter': 'accepte', 'corriger': 'corrige', 'ecarter': 'ecarte'}[decision]
        n = desk.db.execute("UPDATE fiche_propositions56109 SET etat=?, version=version+1, decision_par=?, decision_le=?, motif=? WHERE id=? AND etat='propose'",
                            (etat, a.compte, quand, str(d.get('motif') or '')[:300], pid)).rowcount
        if n != 1:
            raise Stop('proposition_deja_decidee')
        D._journal(desk, did, a.compte, 'fiche_proposition', {'proposition': pid, 'champ': p['champ'], 'decision': decision, 'version': nouvelle})
        if decision != 'ecarter':
            _dependants(desk, did, 'Fiche mise à jour : %s' % definition['libelle'])
        recu = {'dossier': did, 'proposition': pid, 'champ': p['champ'], 'decision': decision, 'version': nouvelle,
                'message': {'accepte': 'Proposition acceptée.', 'corrige': 'Valeur corrigée enregistrée.',
                            'ecarte': 'Proposition écartée : elle ne sera pas reproposée tant que sa source ne change pas.'}[etat],
                'recu': {'operation': 'fiche_proposition', 'cle': cle, 'quand': quand}}
        D._enregistrer_commande(desk, cabinet, a, 'fiche_proposition:%s' % pid, cle, empreinte, recu)
    return recu


# ------------------------------------------------------------------------------------------------ écritures : profil et présentation
def _peut_regler(desk, a):
    """Modèle de fiche du cabinet : administrateur, ou membre qui a le droit « Réglages du cabinet »."""
    if a.role == 'administrateur':
        return
    from .membres5650 import effective_rights
    try:
        droits = effective_rights(desk.c['state_dir'], a.compte, a.role)
    except Stop:
        raise Stop('controle_acces_indisponible') from None
    if not droits.get('reglages'):
        raise Stop('role_insuffisant')


def _config_presentation(d, portee):
    """Corps d'une présentation réduit à sa liste fermée : identifiants de cartes, ordre, cartes masquées et repliées (et, pour le cabinet,
    champs métier typés). Aucun HTML, script, style ni chemin libre ne peut venir d'une préférence."""
    ordre, masquees, repliees = [], [], []
    rangs = []
    for c in CARTE_IDS:
        try:
            rang = int(d.get('carte_%s_ordre' % c) or 0)
        except (TypeError, ValueError):
            rang = 0
        rangs.append((rang if rang > 0 else 999, CARTE_IDS.index(c), c))
        if d.get('carte_%s_masquee' % c) in (True, 'true', 'on', '1'):
            masquees.append(c)
        if d.get('carte_%s_repliee' % c) in (True, 'true', 'on', '1'):
            repliees.append(c)
    ordre = [c for _, _, c in sorted(rangs)]
    cfg = {'ordre': ordre, 'masquees': [c for c in masquees if c in {x[0] for x in CARTES if x[3]}], 'repliees': repliees}
    if portee == 'cabinet':
        champs, err = [], {}
        for n in range(1, 13):
            ident, libelle, type_ = str(d.get('champ_%d_id' % n) or '').strip().lower(), str(d.get('champ_%d_libelle' % n) or '').strip(), str(d.get('champ_%d_type' % n) or '')
            if not ident and not libelle:
                continue
            ident = ident or ('c_' + re.sub(r'[^a-z0-9]+', '_', D._plier(libelle)).strip('_')[:28])
            if not ident.startswith('c_'):
                ident = 'c_' + ident
            if not CHAMP_PERSO.fullmatch(ident) or not libelle or type_ not in TYPES:
                err['champ_%d_libelle' % n] = 'Indiquez un libellé et un type ; identifiant : lettres, chiffres et tirets bas.'
                continue
            choix = [x.strip()[:80] for x in str(d.get('champ_%d_choix' % n) or '').split(';') if x.strip()][:20]
            if type_ == 'choix' and not choix:
                err['champ_%d_choix' % n] = 'Indiquez les valeurs possibles, séparées par des points-virgules.'
                continue
            if ident not in [c['id'] for c in champs]:
                champs.append({'id': ident, 'libelle': libelle[:80], 'type': type_, 'aide': str(d.get('champ_%d_aide' % n) or '')[:200], 'choix': choix})
        if err:
            raise D.Refus('champs_invalides', err)
        cfg['champs'] = champs
    return cfg


def enregistrer_presentation(desk, acteur, donnees, cle):
    """Préférence personnelle (``portee`` = utilisateur) ou modèle du cabinet (``portee`` = cabinet, droit « réglages »). La préférence d'un
    compte ne change que SON affichage ; elle ne supprime aucune donnée et n'est jamais une règle partagée."""
    from . import fiche5698 as F
    a = F._acteur(acteur)
    D._peut_ecrire(a)
    d = donnees if isinstance(donnees, dict) else {}
    portee, profil = str(d.get('portee') or 'utilisateur'), str(d.get('profil') or '')
    if portee not in ('utilisateur', 'cabinet') or profil not in PROFILS:
        raise D.Refus('champs_invalides', {'profil': 'Profil inconnu.'})
    if portee == 'cabinet':
        _peut_regler(desk, a)
    proprietaire = a.compte if portee == 'utilisateur' else ''
    reinitialiser = d.get('reinitialiser') in (True, 'true', 'on', '1')
    cfg = None if reinitialiser else _config_presentation(d, portee)
    try:
        version = int(d.get('version') or 0)
    except (TypeError, ValueError):
        raise D.Refus('champs_invalides', {'version': 'Version attendue manquante : rechargez la page.'}) from None
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'fiche_presentation:%s:%s' % (portee, profil), cle, {'cfg': cfg, 'version': version, 'r': reinitialiser})
        if deja:
            return deja
        courante = _presentation_brute(desk, portee, proprietaire, profil)
        if (courante['version'] if courante else 0) != version:
            raise Stop('version_perimee')
        quand = _maintenant()
        if reinitialiser:   # « Revenir à la présentation du cabinet » (ou, pour le modèle du cabinet, à la présentation livrée)
            if portee == 'cabinet':
                desk.db.execute('DELETE FROM fiche_modeles56109 WHERE profil=?', (profil,))
            else:
                desk.db.execute('DELETE FROM fiche_preferences56109 WHERE compte=? AND profil=?', (proprietaire, profil))
            nouvelle, message = 0, 'Présentation du cabinet rétablie.'
        elif portee == 'cabinet':
            desk.db.execute('INSERT INTO fiche_modeles56109(profil,version,config_json,modifie_par,modifie_le) VALUES(?,?,?,?,?) ON CONFLICT(profil) DO UPDATE SET '
                            'version=excluded.version, config_json=excluded.config_json, modifie_par=excluded.modifie_par, modifie_le=excluded.modifie_le',
                            (profil, version + 1, json.dumps(cfg, ensure_ascii=False), a.compte, quand))
            nouvelle, message = version + 1, 'Modèle de fiche du cabinet enregistré.'
        else:
            desk.db.execute('INSERT INTO fiche_preferences56109(compte,profil,version,config_json,modifie_le) VALUES(?,?,?,?,?) ON CONFLICT(compte,profil) DO UPDATE SET '
                            'version=excluded.version, config_json=excluded.config_json, modifie_le=excluded.modifie_le',
                            (proprietaire, profil, version + 1, json.dumps(cfg, ensure_ascii=False), quand))
            nouvelle, message = version + 1, 'Présentation enregistrée.'
        recu = {'portee': portee, 'profil': profil, 'version': nouvelle, 'message': message, 'recu': {'operation': 'fiche_presentation', 'cle': cle, 'quand': quand}}
        D._enregistrer_commande(desk, cabinet, a, 'fiche_presentation:%s:%s' % (portee, profil), cle, empreinte, recu)
    return recu


def choisir_profil(desk, acteur, ident, donnees, cle):
    """Adaptation du profil de la fiche pour CE dossier (Contentieux, Conseil, Mixte) : n'écrit ni le registre ni aucune donnée du dossier."""
    a, did, _ = _dossier(desk, acteur, ident)
    D._peut_ecrire(a)
    d = donnees if isinstance(donnees, dict) else {}
    profil = str(d.get('profil') or '')
    if profil not in PROFILS:
        raise D.Refus('champs_invalides', {'profil': 'Choisissez Contentieux, Conseil ou Mixte.'})
    try:
        version = int(d.get('version') or 0)
    except (TypeError, ValueError):
        raise D.Refus('champs_invalides', {'version': 'Version attendue manquante : rechargez la page.'}) from None
    cabinet = D._cabinet(desk)
    with D._transaction(desk):
        empreinte, deja = D._commande(desk, cabinet, a, 'fiche_profil:' + did, cle, {'profil': profil, 'version': version})
        if deja:
            return deja
        row = desk.db.execute('SELECT version FROM fiche_profils56109 WHERE dossier_id=?', (did,)).fetchone()
        if (row['version'] if row else 0) != version:
            raise Stop('version_perimee')
        quand = _maintenant()
        desk.db.execute('INSERT INTO fiche_profils56109(dossier_id,profil,version,modifie_par,modifie_le) VALUES(?,?,?,?,?) ON CONFLICT(dossier_id) DO UPDATE SET '
                        'profil=excluded.profil, version=excluded.version, modifie_par=excluded.modifie_par, modifie_le=excluded.modifie_le',
                        (did, profil, version + 1, a.compte, quand))
        D._journal(desk, did, a.compte, 'fiche_profil', {'profil': profil})
        recu = {'dossier': did, 'profil': profil, 'version': version + 1, 'message': 'Fiche présentée en profil %s.' % PROFILS[profil],
                'recu': {'operation': 'fiche_profil', 'cle': cle, 'quand': quand}}
        D._enregistrer_commande(desk, cabinet, a, 'fiche_profil:' + did, cle, empreinte, recu)
    return recu


# ------------------------------------------------------------------------------------------------ relevé des mentions (C10)
def relever_mentions(desk, mid, inv=None):
    """Relit, sans IA, l'en-tête des actes et la comparution des contrats du dossier et garde chaque mention avec son contexte (source et
    empreinte de sa version, zone, ligne, nom tel qu'écrit, nom normalisé, nature de la personne, qualité, cadre, représentant, conseil,
    extrait). Remplace les mentions d'une pièce dont le texte a changé ; n'inscrit ni ne retire aucune partie. Rend le nombre de mentions.

    Appelé après l'actualisation des données du dossier (jamais pendant une lecture) et par « Relire les actes et contrats »."""
    from . import entete5679 as E
    did = D.resoudre(desk, mid) or str(mid)
    if inv is None:
        from . import synthese5632
        inv = synthese5632.inventory(desk, did)
    docs = _documents(desk, did)
    quand, total = _maintenant(), 0
    presents = set()
    for d in inv:
        doc = docs.get(d['path'])
        if not doc or doc['nature'] not in NATURES_LUES or not d.get('text'):
            continue
        presents.add(d['path'])
        empreinte = str(d.get('sha256') or d.get('etag') or '')[:64]
        connue = desk.db.execute('SELECT empreinte FROM fiche_mentions56109 WHERE dossier_id=? AND source_path=? LIMIT 1', (did, d['path'])).fetchone()
        if connue and connue['empreinte'] == empreinte:
            total += desk.db.execute('SELECT COUNT(*) FROM fiche_mentions56109 WHERE dossier_id=? AND source_path=?', (did, d['path'])).fetchone()[0]
            continue
        try:
            lu = E.lire_acte(d['text'])
        except Exception:   # noqa: BLE001 — un texte inattendu ne bloque jamais le relevé des autres pièces
            continue
        desk.db.execute('DELETE FROM fiche_mentions56109 WHERE dossier_id=? AND source_path=?', (did, d['path']))
        cadre_acte = ' '.join(x for x in (lu['juridiction'], ('RG ' + lu['rg']) if lu['rg'] else '') if x)
        for p in (lu['contractants'] if doc['nature'] == 'contrat' else lu['parties']):
            qualite = p['qualite'] if p['zone'] == 'soussignes' else E.ROLE_LABELS.get(p['qualite'], p['qualite'])
            desk.db.execute('INSERT OR IGNORE INTO fiche_mentions56109(id,dossier_id,source_path,source_nom,empreinte,zone,ligne,nom_ecrit,nom,nom_norm,nature_personne,'
                            'qualite,cadre,representant,conseil,extrait,releve_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (uuid.uuid4().hex, did, d['path'], doc['name'], empreinte, p['zone'], p['ligne'], p['nom_ecrit'][:200], p['nom'][:160], D._plier(p['nom']),
                             p['nature_personne'], qualite[:80], ('Contrat : ' + doc['name'])[:200] if doc['nature'] == 'contrat' else cadre_acte[:200],
                             p['representant'][:160], ', '.join(p['avocats'])[:200], p['extrait'][:300], quand))
            total += 1
    for r in desk.db.execute('SELECT DISTINCT source_path FROM fiche_mentions56109 WHERE dossier_id=?', (did,)).fetchall():
        if r['source_path'] not in presents:   # pièce retirée du dossier : ses mentions ne sont plus des propositions
            desk.db.execute('DELETE FROM fiche_mentions56109 WHERE dossier_id=? AND source_path=?', (did, r['source_path']))
    desk.db.commit()
    return total


def relire(desk, acteur, ident, cle):
    """« Relire les actes et contrats » : relevé sans IA, à la demande (fonctionne IA en pause)."""
    a, did, _ = _dossier(desk, acteur, ident)
    D._peut_ecrire(a)
    if not D.CLE_OK.fullmatch(str(cle or '')):
        raise Stop('cle_idempotence_absente')
    n = relever_mentions(desk, did)
    return {'dossier': did, 'mentions': n, 'message': '%d mention(s) relevée(s) dans les actes et contrats. Rien n’a été inscrit : vérifiez « À vérifier ».' % n}


# ------------------------------------------------------------------------------------------------ routes
ROLES = ('administrateur', 'avocat', 'assistant')
ECRITURES_DOSSIER = ('candidats', 'participations', 'valeurs', 'propositions', 'profil', 'relire')


def _route(api_):
    route = str(api_ or '').split('?', 1)[0]
    if not route.startswith(PREFIXE):
        return None
    return [p for p in route[len(PREFIXE):].split('/') if p]


def allowed(role, method, api_):
    """Contrôle de rôle sans état (``standalone_auth``) : les trois rôles du cabinet lisent et écrivent la fiche ; le dossier, la version,
    la clé et, pour le modèle du cabinet, le droit « réglages » sont relus par le service."""
    parts = _route(api_)
    if role not in ROLES or parts is None:
        return False
    if method in ('GET', 'HEAD'):
        return (len(parts) == 2 and parts[0] == 'dossiers') or parts == ['presentation']
    if method != 'POST':
        return False
    if parts == ['presentation']:
        return True
    return len(parts) in (3, 4) and parts[0] == 'dossiers' and parts[2] in ECRITURES_DOSSIER and (len(parts) == 4) == (parts[2] in ('candidats', 'valeurs', 'propositions'))


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        corps = {'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}
        detail = getattr(ex, 'detail', None)
        if isinstance(detail, dict) and detail:
            corps['champs'] = detail
        return _out(corps, STATUTS.get(code, '400 Bad Request'))
    except sqlite3.OperationalError:   # base occupée : panne annoncée, jamais un accès élargi ni un faux succès
        return _out({'error': 'registre_dossiers_occupe', 'message': 'Registre des dossiers occupé : réessayez dans un instant.'}, '503 Service Unavailable')


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    acteur = (owner, role)
    parts = _route(name)
    if parts is None:
        raise Stop('route_inconnue')
    if not allowed(role, method, name):
        raise Stop('role_insuffisant' if role not in ROLES else 'route_inconnue')
    if method in ('GET', 'HEAD'):
        if parts == ['presentation']:
            profil = str(args.get('profil') or 'contentieux')
            if profil not in PROFILS:
                raise Stop('route_inconnue')
            from . import fiche5698 as F
            return _out(presentation(desk, F._acteur(acteur).compte, '', profil))
        return _out(fiche(desk, acteur, parts[1], str(args.get('profil') or '') or None))
    from .web440 import check_post
    data = check_post(env, auth)   # origine, jeton CSRF, corps borné, objet JSON
    cle = data.get('cle')
    if parts == ['presentation']:
        return _out(enregistrer_presentation(desk, acteur, data, cle))
    ident, genre = parts[1], parts[2]
    if genre == 'candidats':
        recu = decider_candidat(desk, acteur, ident, parts[3], str(data.get('decision') or ''), data, cle)
        return _out(recu, '201 Created' if recu.get('decision') in ('accepter', 'corriger') and not recu.get('rejoue') else '200 OK')
    if genre == 'participations':
        return _out(enregistrer_participation(desk, acteur, ident, data, cle))
    if genre == 'valeurs':
        return _out(enregistrer_valeur(desk, acteur, ident, parts[3], data.get('valeur'), data.get('version'), cle))
    if genre == 'propositions':
        return _out(decider_proposition(desk, acteur, ident, parts[3], data, cle))
    if genre == 'profil':
        return _out(choisir_profil(desk, acteur, ident, data, cle))
    return _out(relire(desk, acteur, ident, cle))
