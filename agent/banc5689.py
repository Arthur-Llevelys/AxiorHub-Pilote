"""5.6.89 (chapitres 45 et 46) : banc d'essai comparatif des missions, journal reproductible.

Il complète les bancs existants sans les remplacer : ``economie569`` éprouve un modèle local, ``evaluation410`` note des modèles sur des cas anonymisés et alimente le routage
(``routage5682._benchmarked``). Ici, on compare deux produits sur une mission complète, avec relecture humaine. Rien n'y appelle un modèle ni un service : chaque mesure est saisie
par une personne, ou lue dans le registre du budget pour un appel d'AxiorHub.

Règles tenues par le serveur :
- **Protocole figé avant essai** : cas, sources autorisées, livrables, faits attendus, réserves, critères, actions permises, erreurs critiques et cible. Une fois figé, il ne change
  plus : une évolution est une nouvelle version. Les empreintes sont recalculées à chaque ouverture : un corpus altéré retire toute conclusion.
- **Réglage et évaluation séparés** : un essai ne porte que sur une partie ; l'identifiant le dit ; le contenu des cas réservés reste masqué tant qu'aucune évaluation n'a commencé.
- **Comparaison équitable** : le canal d'une référence est une valeur fermée et l'intitulé est calculé ici. Un essai par API ne peut pas être présenté comme un essai de Claude Desktop.
  Offre, version, modèle, capacités et intervention humaine sont exigés. L'ordre des essais alterne. La préparation initiale se mesure à part de l'usage quotidien.
- **Aucune conclusion sans données** : médiane, dispersion et effectif par famille ; une valeur absente reste absente, jamais un zéro. Effectif insuffisant, relecture absente,
  réglage ou corpus d'exercice : la conclusion est refusée, avec son motif.
- **Erreurs critiques** : détectées sur la trace de l'essai ou signalées par le relecteur, elles empêchent de recevoir la famille concernée. Leur absence sur n cas ne prouve pas une
  absence universelle de défaut.
- **Coûts** : mesure, facture et estimation restent séparées ; un appel ou un crédit ne compte qu'une fois ; un appel incertain reste inconnu, jamais nul.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
import statistics
import uuid

from . import banc_corpus5689 as corpus
from .common import Stop

SCHEMA = '''
CREATE TABLE IF NOT EXISTS banc_protocoles5689(id TEXT PRIMARY KEY, nom TEXT NOT NULL, version INTEGER NOT NULL, etat TEXT NOT NULL, exercice INTEGER NOT NULL DEFAULT 0, effectif_minimal INTEGER NOT NULL,
  cible_json TEXT NOT NULL, erreurs_json TEXT NOT NULL, corpus_sha256 TEXT NOT NULL DEFAULT '', protocole_sha256 TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, parent TEXT NOT NULL DEFAULT '',
  cle TEXT NOT NULL UNIQUE, cree TEXT NOT NULL, cree_par TEXT NOT NULL, fige_le TEXT NOT NULL DEFAULT '', fige_par TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS banc_cas5689(id TEXT PRIMARY KEY, protocole TEXT NOT NULL, ref TEXT NOT NULL, famille TEXT NOT NULL, partage TEXT NOT NULL, origine TEXT NOT NULL, contenu_json TEXT NOT NULL,
  empreinte TEXT NOT NULL, UNIQUE(protocole, ref));
CREATE TABLE IF NOT EXISTS banc_configurations5689(id TEXT PRIMARY KEY, role TEXT NOT NULL, canal TEXT NOT NULL, produit TEXT NOT NULL, offre TEXT NOT NULL, version TEXT NOT NULL, modele TEXT NOT NULL,
  parametres_json TEXT NOT NULL DEFAULT '{}', capacites_json TEXT NOT NULL DEFAULT '{}', intervention_humaine TEXT NOT NULL DEFAULT '', preparation_s INTEGER, memes_sources INTEGER NOT NULL DEFAULT 0,
  mesures_json TEXT NOT NULL DEFAULT '{}', saisie_manuelle INTEGER NOT NULL, empreinte TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, cree TEXT NOT NULL, cree_par TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS banc_runs5689(run_id TEXT PRIMARY KEY, protocole TEXT NOT NULL, protocol_version TEXT NOT NULL, phase TEXT NOT NULL, dataset_sha256 TEXT NOT NULL, candidat TEXT NOT NULL,
  reference_cfg TEXT NOT NULL, number_of_cases INTEGER NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, droits TEXT NOT NULL DEFAULT '', version_logiciel TEXT NOT NULL,
  cle TEXT NOT NULL UNIQUE, cree TEXT NOT NULL, cree_par TEXT NOT NULL, demarre TEXT NOT NULL DEFAULT '', clos TEXT NOT NULL DEFAULT '', motif_echec TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS banc_evenements5689(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, at TEXT NOT NULL, genre TEXT NOT NULL, cas TEXT NOT NULL DEFAULT '', acteur TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS banc_evenements5689_run ON banc_evenements5689(run_id, at);
CREATE TABLE IF NOT EXISTS banc_essais5689(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, cas TEXT NOT NULL, configuration TEXT NOT NULL, ordre INTEGER NOT NULL, statut TEXT NOT NULL, preparation_s INTEGER,
  execution_s INTEGER, correction_s INTEGER, omissions INTEGER, destinataires_exacts INTEGER, fidelite TEXT NOT NULL DEFAULT '', incidents INTEGER, intervention TEXT NOT NULL DEFAULT '',
  livrable_ref TEXT NOT NULL DEFAULT '', livrable_version TEXT NOT NULL DEFAULT '', livrable_sha256 TEXT NOT NULL DEFAULT '', trace_json TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, empreinte TEXT NOT NULL,
  saisi_par TEXT NOT NULL, at TEXT NOT NULL, UNIQUE(run_id, cas, configuration));
CREATE TABLE IF NOT EXISTS banc_controles5689(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, essai TEXT NOT NULL, nature TEXT NOT NULL, resultat TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS banc_controles5689_essai ON banc_controles5689(essai);
CREATE TABLE IF NOT EXISTS banc_avis5689(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, essai TEXT NOT NULL, relecteur TEXT NOT NULL, aveugle INTEGER NOT NULL, motif_non_aveugle TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL, commentaire TEXT NOT NULL DEFAULT '', cle TEXT NOT NULL UNIQUE, at TEXT NOT NULL, UNIQUE(essai, relecteur));
CREATE TABLE IF NOT EXISTS banc_erreurs5689(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, essai TEXT NOT NULL, cas TEXT NOT NULL, famille TEXT NOT NULL, categorie TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  origine TEXT NOT NULL, acteur TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS banc_erreurs5689_run ON banc_erreurs5689(run_id, famille);
CREATE TABLE IF NOT EXISTS banc_couts5689(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, essai TEXT NOT NULL DEFAULT '', configuration TEXT NOT NULL, nature TEXT NOT NULL, origine TEXT NOT NULL, montant_micro INTEGER,
  devise TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '', call_id TEXT NOT NULL DEFAULT '', cle TEXT NOT NULL UNIQUE, at TEXT NOT NULL, acteur TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS banc_couts5689_appel ON banc_couts5689(run_id, call_id) WHERE call_id<>'';
CREATE UNIQUE INDEX IF NOT EXISTS banc_couts5689_ref ON banc_couts5689(run_id, configuration, nature, reference) WHERE reference<>'';
CREATE TABLE IF NOT EXISTS banc_receptions5689(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, famille TEXT NOT NULL, version_logiciel TEXT NOT NULL, candidat_empreinte TEXT NOT NULL, effectif INTEGER NOT NULL,
  cle TEXT NOT NULL UNIQUE, at TEXT NOT NULL, par TEXT NOT NULL, UNIQUE(run_id, famille));
'''
TABLES = ('banc_protocoles5689', 'banc_cas5689', 'banc_configurations5689', 'banc_runs5689', 'banc_evenements5689', 'banc_essais5689', 'banc_controles5689', 'banc_avis5689', 'banc_erreurs5689',
          'banc_couts5689', 'banc_receptions5689')

# Le canal décide de l'intitulé affiché : une personne ne le rédige pas.
CANAUX = {'axiorhub': 'AxiorHub', 'claude_desktop': 'Claude Desktop', 'claude_cowork': 'Claude Cowork', 'api': 'Essai par API (ce n’est pas un essai de Claude Desktop)', 'autre': 'Autre application'}
APPLICATIONS_CLAUDE = ('claude_desktop', 'claude_cowork')
ERREURS_CRITIQUES = {'divulgation_hors_droits': 'Divulgation hors droits', 'action_externe_non_autorisee': 'Action externe non autorisée', 'mauvais_destinataire': 'Mauvais destinataire',
                     'reference_fabriquee_verifiee': 'Référence fabriquée présentée comme vérifiée', 'echeance_certaine_sans_donnees': 'Échéance certaine sans données suffisantes'}
STATUTS = {'rejete': 'Rejeté', 'partiel': 'Partiel', 'exploitable': 'Exploitable'}
RANG_STATUT = ('rejete', 'partiel', 'exploitable')
PHASES = {'reglage': 'Réglage', 'evaluation': 'Évaluation finale'}
ETATS_RUN = {'planned': 'Prévu', 'running': 'En cours', 'completed': 'Terminé', 'failed': 'Échoué'}
NATURES_COUT = {'usage': 'Usage', 'fixe': 'Part des coûts fixes', 'maintenance': 'Maintenance', 'credit': 'Crédit effectif'}
ORIGINES_COUT = {'mesure': 'Mesuré', 'facture': 'Facturé', 'estime': 'Estimé', 'inconnu': 'Inconnu'}
ORIGINES_CAS = ('fictif', 'autorise')
EFFECTIF_MINIMAL_DEFAUT = 5
SITUATIONS_MINIMALES = 50
CIBLE_DEFAUT = {'reduction_mediane_pct': 30, 'parcours': [], 'nature': 'objectif de réception à confirmer, ni performance observée ni garantie'}
MOTIFS = {'phase_reglage': 'Essai de réglage : il ne sert pas à conclure.', 'corpus_d_exercice': 'Corpus d’exercice fictif : il exerce l’outil et ne mesure aucun produit.',
          'corpus_incomplet': 'Moins de cinquante situations dans ce protocole.', 'corpus_altere': 'L’empreinte du corpus ne correspond plus : ce journal n’est pas fiable.',
          'essai_non_termine': 'L’essai n’est pas terminé.', 'aucune_paire_mesurable': 'Aucune paire de mesures complètes.', 'effectif_insuffisant': 'Effectif inférieur au minimum fixé par le protocole.',
          'relecture_incomplete': 'Tous les résultats n’ont pas reçu un avis de relecteur.', 'controles_incomplets': 'La trace de certains essais n’a pas été renseignée.',
          'couts_non_rapproches': 'Des coûts restent inconnus ou incertains.', 'cible_non_figee': 'Le protocole ne fixe pas les parcours prioritaires.', 'erreur_critique': 'Une erreur critique a été relevée.'}
MOTIFS_CAS = {'resultat_manquant': 'Résultat manquant', 'sources_differentes': 'Sources différentes ou non relevées', 'mesure_incomplete': 'Mesure incomplète', 'resultat_rejete': 'Résultat rejeté'}
EVENEMENTS = {'prevu': 'Essai prévu', 'demarre': 'Essai démarré', 'termine': 'Essai terminé', 'echoue': 'Essai échoué', 'resultat_saisi': 'Résultat saisi', 'avis': 'Avis de relecteur', 'cout': 'Coût ajouté',
              'erreur_signalee': 'Erreur critique signalée', 'reception': 'Famille reçue'}
_ID_RUN = re.compile(r'^(reg|eva)-[0-9a-f]{32}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')
MAX_TEXTE = 4000


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def ensure_schema(desk):
    """Instructions séparées : ``executescript`` validerait la transaction de l'appelant."""
    if getattr(desk, '_schema5689banc', False):
        return
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='banc_receptions5689'").fetchone():
        for instruction in SCHEMA.split(';'):
            if instruction.strip():
                desk.db.execute(instruction)
        desk.db.commit()
    desk._schema5689banc = True


def _canon(valeur):
    return json.dumps(valeur, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _sha(valeur):
    return hashlib.sha256(_canon(valeur).encode('utf-8')).hexdigest()


def _texte(valeur, limite=400):
    return str(valeur if valeur is not None else '').strip()[:limite]


def _liste(valeur, limite=200):
    if isinstance(valeur, str):
        valeur = valeur.splitlines()
    if not isinstance(valeur, (list, tuple)):
        return []
    return [x for x in (_texte(v, limite) for v in valeur) if x]


def _duree(valeur):
    """Secondes entières ; une mesure non relevée reste absente, jamais zéro."""
    if valeur in (None, ''):
        return None
    try:
        n = int(str(valeur).strip())
    except ValueError:
        raise Stop('mesure_invalide') from None
    if n < 0:
        raise Stop('mesure_invalide')
    return n


def _cle(valeur):
    cle = str(valeur or '').strip()
    if not 8 <= len(cle) <= 100 or not re.fullmatch(r'[A-Za-z0-9_-]+', cle):
        raise Stop('cle_idempotence_requise')
    return cle


def _reprise(desk, table, cle, empreinte, colonne='id', comparer='empreinte'):
    """Même clé et même contenu : même ligne, aucun second effet. Une clé réutilisée pour autre chose est refusée."""
    ligne = desk.db.execute('SELECT * FROM %s WHERE cle=?' % table, (cle,)).fetchone()
    if ligne is None:
        return None
    if empreinte is not None and ligne[comparer] != empreinte:
        raise Stop('cle_idempotence_deja_utilisee')
    return ligne[colonne]


def _evenement(desk, run_id, genre, acteur, cas='', **detail):
    desk.db.execute('INSERT INTO banc_evenements5689(id,run_id,at,genre,cas,acteur,detail_json) VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, run_id, _now(), genre, cas, _texte(acteur, 200), _canon(detail)))


# ------------------------------------------------------------------------------------------------ cas et protocole
def valider_cas(brut):
    """Un cas bien formé, ou ``Stop`` : c'est aussi la porte d'entrée du corpus de cinquante situations."""
    from .evaluation410 import _obviously_identifying    # même contrôle que le banc de 4.1 : ni adresse, ni téléphone, ni chemin de dossier réel
    ref = _texte(brut.get('ref'), 20)
    if not re.fullmatch(r'[A-Za-z0-9-]{1,20}', ref):
        raise Stop('cas_invalide:reference')
    famille, partage = _texte(brut.get('famille')), _texte(brut.get('partage'))
    if famille not in corpus.FAMILLES:
        raise Stop('cas_invalide:famille')
    if partage not in PHASES:
        raise Stop('cas_invalide:partage')
    origine = _texte(brut.get('origine') or 'fictif')
    if origine not in ORIGINES_CAS:
        raise Stop('cas_invalide:origine')
    autorisation = {'droits': _texte(brut.get('droits'), 600), 'destinations': [c for c in _liste(brut.get('destinations')) if c in CANAUX]}
    if origine == 'autorise' and not (autorisation['droits'] and autorisation['destinations']):
        raise Stop('cas_autorise_sans_droits')    # un benchmark n'autorise pas à transmettre un dossier : le droit se documente, destination par destination
    question = _texte(brut.get('question'), MAX_TEXTE) if isinstance(brut.get('question'), str) else ''
    sources = []
    for s in brut.get('sources') or ():
        if not isinstance(s, dict) or not _texte(s.get('id'), 40) or not _texte(s.get('texte'), MAX_TEXTE):
            raise Stop('cas_invalide:source')
        sources.append({'id': _texte(s['id'], 40), 'titre': _texte(s.get('titre')), 'texte': _texte(s['texte'], MAX_TEXTE)})
    contenu = {'question': question, 'sources': sources, 'livrables': _liste(brut.get('livrables')), 'faits_attendus': _liste(brut.get('faits_attendus')), 'reserves': _liste(brut.get('reserves')),
               'criteres': _liste(brut.get('criteres')), 'actions_permises': _liste(brut.get('actions_permises')), 'destinataires': [d.lower() for d in _liste(brut.get('destinataires'))],
               'references_verifiables': _liste(brut.get('references_verifiables')), 'echeance_donnees_manquantes': _liste(brut.get('echeance_donnees_manquantes')), 'autorisation': autorisation}
    for champ in ('question', 'sources', 'livrables', 'faits_attendus', 'criteres', 'actions_permises'):
        if not contenu[champ]:
            raise Stop('cas_invalide:' + champ)
    if len({s['id'] for s in sources}) != len(sources):
        raise Stop('cas_invalide:source')
    if origine == 'fictif' and _obviously_identifying(' '.join([question] + [s['texte'] for s in sources])):
        raise Stop('cas_identifiant')
    return {'ref': ref, 'famille': famille, 'partage': partage, 'origine': origine, 'contenu': contenu}


def _inserer_cas(desk, protocole, cas):
    empreinte = _sha({k: cas[k] for k in ('ref', 'famille', 'partage', 'origine', 'contenu')})
    desk.db.execute('INSERT INTO banc_cas5689(id,protocole,ref,famille,partage,origine,contenu_json,empreinte) VALUES(?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, protocole, cas['ref'], cas['famille'], cas['partage'], cas['origine'], _canon(cas['contenu']), empreinte))


def creer_protocole(desk, data, owner):
    ensure_schema(desk)
    cle = _cle(data.get('cle'))
    existe = _reprise(desk, 'banc_protocoles5689', cle, None)
    if existe:
        return lire_protocole(desk, existe)
    nom = _texte(data.get('nom'), 200)
    if not nom:
        raise Stop('nom_requis')
    effectif = int(_duree(data.get('effectif_minimal')) or EFFECTIF_MINIMAL_DEFAUT)
    if effectif < 3:
        raise Stop('effectif_minimal_trop_bas')
    parcours = [p for p in _liste(data.get('parcours')) if p in corpus.FAMILLES]
    cible = {**CIBLE_DEFAUT, 'parcours': parcours}
    ident = uuid.uuid4().hex
    desk.db.execute('INSERT INTO banc_protocoles5689(id,nom,version,etat,exercice,effectif_minimal,cible_json,erreurs_json,cle,cree,cree_par) VALUES(?,?,1,\'brouillon\',?,?,?,?,?,?,?)',
                    (ident, nom, 1 if data.get('_exercice') else 0, effectif, _canon(cible), _canon(sorted(ERREURS_CRITIQUES)), cle, _now(), _texte(owner, 200)))
    desk.audit('banc_protocole_cree5689', {'protocole': ident, 'par': _texte(owner, 80)})
    desk.db.commit()
    return lire_protocole(desk, ident)


def charger_exercice(desk, owner, cle):
    """Corpus fictif du logiciel, figé d'emblée. Marqué « exercice » : il ne peut mener à aucune réception."""
    ensure_schema(desk)
    cle = _cle(cle)
    existe = _reprise(desk, 'banc_protocoles5689', cle, None)
    if existe:
        return lire_protocole(desk, existe)
    protocole = creer_protocole(desk, {'cle': cle, 'nom': 'Corpus d’exercice fictif', '_exercice': True}, owner)
    for brut in corpus.CAS:
        _inserer_cas(desk, protocole['id'], valider_cas({**brut, 'origine': 'fictif'}))
    desk.db.commit()
    return figer(desk, protocole['id'], owner, protocole['revision'])


def _protocole(desk, ident):
    ensure_schema(desk)
    ligne = desk.db.execute('SELECT * FROM banc_protocoles5689 WHERE id=?', (str(ident),)).fetchone()
    if ligne is None:
        raise Stop('protocole_absent')
    return ligne


def ajouter_cas(desk, protocole, data, owner, revision):
    p = _protocole(desk, protocole)
    if p['etat'] != 'brouillon':
        raise Stop('protocole_fige')
    if int(revision or 0) != p['revision']:
        raise Stop('revision_perimee')
    cas = valider_cas(data)
    if desk.db.execute('SELECT 1 FROM banc_cas5689 WHERE protocole=? AND ref=?', (p['id'], cas['ref'])).fetchone():
        raise Stop('cas_deja_present')
    _inserer_cas(desk, p['id'], cas)
    desk.db.execute('UPDATE banc_protocoles5689 SET revision=revision+1 WHERE id=?', (p['id'],))
    desk.db.commit()
    return lire_protocole(desk, p['id'])


def _lignes_cas(desk, protocole):
    return desk.db.execute('SELECT * FROM banc_cas5689 WHERE protocole=? ORDER BY ref', (protocole,)).fetchall()


def empreinte_corpus(desk, protocole):
    """Recalculée depuis le contenu, jamais recopiée : toute altération directe de la base se voit."""
    return _sha([{'ref': c['ref'], 'famille': c['famille'], 'partage': c['partage'], 'origine': c['origine'], 'contenu': json.loads(c['contenu_json'])} for c in _lignes_cas(desk, protocole)])


def _empreinte_protocole(p, corpus_sha):
    return _sha({'version': p['version'], 'effectif_minimal': p['effectif_minimal'], 'cible': json.loads(p['cible_json']), 'erreurs': json.loads(p['erreurs_json']), 'corpus': corpus_sha})


def figer(desk, protocole, owner, revision):
    p = _protocole(desk, protocole)
    if p['etat'] != 'brouillon':
        raise Stop('protocole_fige')
    if int(revision or 0) != p['revision']:
        raise Stop('revision_perimee')
    lignes = _lignes_cas(desk, p['id'])
    if not {'reglage', 'evaluation'} <= {c['partage'] for c in lignes}:
        raise Stop('reglage_et_evaluation_requis')
    sha = empreinte_corpus(desk, p['id'])
    desk.db.execute("UPDATE banc_protocoles5689 SET etat='fige',corpus_sha256=?,protocole_sha256=?,fige_le=?,fige_par=?,revision=revision+1 WHERE id=? AND etat='brouillon'",
                    (sha, _empreinte_protocole(p, sha), _now(), _texte(owner, 200), p['id']))
    desk.audit('banc_protocole_fige5689', {'protocole': p['id'], 'corpus': sha, 'cas': len(lignes)})
    desk.db.commit()
    return lire_protocole(desk, p['id'])


def nouvelle_version(desk, protocole, owner, cle):
    """Seule manière de changer un protocole figé : les essais déjà faits gardent la version qu'ils ont suivie."""
    p = _protocole(desk, protocole)
    if p['etat'] != 'fige':
        raise Stop('protocole_non_fige')
    if p['exercice']:
        raise Stop('corpus_d_exercice_non_modifiable')
    cle = _cle(cle)
    existe = _reprise(desk, 'banc_protocoles5689', cle, None)
    if existe:
        return lire_protocole(desk, existe)
    ident = uuid.uuid4().hex
    desk.db.execute("INSERT INTO banc_protocoles5689(id,nom,version,etat,exercice,effectif_minimal,cible_json,erreurs_json,parent,cle,cree,cree_par) VALUES(?,?,?,'brouillon',0,?,?,?,?,?,?,?)",
                    (ident, p['nom'], p['version'] + 1, p['effectif_minimal'], p['cible_json'], p['erreurs_json'], p['id'], cle, _now(), _texte(owner, 200)))
    for c in _lignes_cas(desk, p['id']):
        desk.db.execute('INSERT INTO banc_cas5689(id,protocole,ref,famille,partage,origine,contenu_json,empreinte) VALUES(?,?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, ident, c['ref'], c['famille'], c['partage'], c['origine'], c['contenu_json'], c['empreinte']))
    desk.db.commit()
    return lire_protocole(desk, ident)


def _evaluation_ouverte(desk, protocole):
    return bool(desk.db.execute("SELECT 1 FROM banc_runs5689 WHERE protocole=? AND phase='evaluation' AND state<>'planned'", (protocole,)).fetchone())


def lire_protocole(desk, ident):
    """Les cas réservés restent masqués tant qu'aucune évaluation n'a démarré : on ne règle pas un produit sur eux."""
    p = _protocole(desk, ident)
    ouverte = _evaluation_ouverte(desk, p['id']) or p['etat'] == 'brouillon'
    cas = []
    for c in _lignes_cas(desk, p['id']):
        ligne = {'ref': c['ref'], 'famille': c['famille'], 'partage': c['partage'], 'origine': c['origine'], 'empreinte': c['empreinte']}
        if c['partage'] == 'reglage' or ouverte:
            ligne['contenu'] = json.loads(c['contenu_json'])
        else:
            ligne['reserve'] = True
        cas.append(ligne)
    sha = empreinte_corpus(desk, p['id'])
    return {'id': p['id'], 'nom': p['nom'], 'version': p['version'], 'etat': p['etat'], 'exercice': bool(p['exercice']), 'effectif_minimal': p['effectif_minimal'], 'cible': json.loads(p['cible_json']),
            'erreurs_critiques': json.loads(p['erreurs_json']), 'corpus_sha256': p['corpus_sha256'], 'protocole_sha256': p['protocole_sha256'], 'revision': p['revision'], 'parent': p['parent'],
            'corpus_intact': p['etat'] != 'fige' or sha == p['corpus_sha256'], 'fige_le': p['fige_le'], 'fige_par': p['fige_par'], 'cas': cas, 'couverture': couverture(desk, p['id'])}


def couverture(desk, protocole):
    """Écart à la répartition cible : la page dit ce qui manque au lieu de laisser croire qu'un petit corpus suffit."""
    compte = {}
    for c in desk.db.execute('SELECT famille,partage,COUNT(*) n FROM banc_cas5689 WHERE protocole=? GROUP BY famille,partage', (protocole,)):
        compte[(c['famille'], c['partage'])] = c['n']
    lignes = [{'famille': f, 'libelle': corpus.FAMILLES[f], 'reglage': compte.get((f, 'reglage'), 0), 'reglage_cible': r, 'evaluation': compte.get((f, 'evaluation'), 0), 'evaluation_cible': v}
              for f, (r, v) in corpus.REPARTITION_CIBLE.items()]
    total = sum(compte.values())
    return {'total': total, 'cible': SITUATIONS_MINIMALES, 'complet': total >= SITUATIONS_MINIMALES, 'familles': lignes}


# ------------------------------------------------------------------------------------------------ configurations des produits
def capturer_axiorhub(desk):
    """Ce que le logiciel sait de lui-même : relevé ici, jamais saisi. Le banc interne des modèles (``routage5682``) est rappelé, il ne vaut pas relecture d'une mission."""
    from . import __version__
    from . import data_policy5689 as dp
    from .hybrid400 import _benchmark_recommendation
    mode = dp.etat_public(desk)
    ollama = desk.c.get('ollama') or {}
    mesures = {}
    for finalite in ('legal_analysis', 'document_drafting', 'mail_drafting'):
        lignes = [{'fournisseur': r.get('provider'), 'modele': r.get('model'), 'essais': r.get('samples')} for r in _benchmark_recommendation(desk.c, finalite).get('all', [])]
        if lignes:
            mesures[finalite] = lignes
    return {'version': __version__, 'modele': _texte(ollama.get('model')) or 'selon la politique du cabinet', 'parametres': {'politique': mode.get('mode', ''), 'revision_politique': mode.get('revision', 0)}, 'mesures': mesures}


def enregistrer_configuration(desk, data, owner):
    ensure_schema(desk)
    cle = _cle(data.get('cle'))
    canal = _texte(data.get('canal'))
    if canal not in CANAUX:
        raise Stop('canal_inconnu')
    capacites = {k: _liste(data.get(k)) for k in ('competences', 'connecteurs', 'sources')}
    memes_sources = data.get('memes_sources') in (True, 'on', '1', 1)
    preparation = _duree(data.get('preparation_s'))
    if canal == 'axiorhub':
        capte = capturer_axiorhub(desk)
        contenu = {'role': 'candidat', 'canal': canal, 'produit': 'AxiorHub', 'offre': _texte(data.get('offre')) or 'installation du cabinet', 'version': capte['version'], 'modele': capte['modele'],
                   'parametres': capte['parametres'], 'capacites': capacites, 'intervention_humaine': _texte(data.get('intervention_humaine'), 1000), 'preparation_s': preparation,
                   'memes_sources': memes_sources, 'mesures': capte['mesures'], 'saisie_manuelle': 0}
    else:
        contenu = {'role': 'reference', 'canal': canal, 'produit': _texte(data.get('produit')), 'offre': _texte(data.get('offre')), 'version': _texte(data.get('version')), 'modele': _texte(data.get('modele')),
                   'parametres': {'reglages': _texte(data.get('parametres'), 1000)}, 'capacites': capacites, 'intervention_humaine': _texte(data.get('intervention_humaine'), 1000),
                   'preparation_s': preparation, 'memes_sources': memes_sources, 'mesures': {}, 'saisie_manuelle': 1}
        manque = [k for k in ('produit', 'offre', 'version', 'modele', 'intervention_humaine') if not contenu[k]]
        if manque:
            raise Stop('configuration_incomplete:' + manque[0])
        if canal in ('api', 'autre') and re.search(r'(?i)desktop|cowork', ' '.join((contenu['produit'], contenu['offre']))):
            raise Stop('essai_api_presente_comme_desktop')
        if canal in APPLICATIONS_CLAUDE and not any(capacites.values()) and not data.get('aucune_capacite'):
            raise Stop('capacites_a_consigner')    # l'absence de compétence ou de connecteur se déclare : une référence appauvrie sans le dire n'est pas équitable
    empreinte = _sha(contenu)
    existe = _reprise(desk, 'banc_configurations5689', cle, empreinte)
    if existe:
        return configuration(desk, existe)
    ident = uuid.uuid4().hex
    desk.db.execute('INSERT INTO banc_configurations5689(id,role,canal,produit,offre,version,modele,parametres_json,capacites_json,intervention_humaine,preparation_s,memes_sources,mesures_json,saisie_manuelle,empreinte,cle,cree,cree_par) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, contenu['role'], canal, contenu['produit'], contenu['offre'], contenu['version'], contenu['modele'], _canon(contenu['parametres']),
                                                                    _canon(capacites), contenu['intervention_humaine'], preparation, int(memes_sources), _canon(contenu['mesures']), contenu['saisie_manuelle'],
                                                                    empreinte, cle, _now(), _texte(owner, 200)))
    desk.db.commit()
    return configuration(desk, ident)


def configuration(desk, ident):
    ensure_schema(desk)
    c = desk.db.execute('SELECT * FROM banc_configurations5689 WHERE id=?', (str(ident),)).fetchone()
    if c is None:
        raise Stop('configuration_absente')
    return {'id': c['id'], 'role': c['role'], 'canal': c['canal'], 'intitule': CANAUX[c['canal']], 'produit': c['produit'], 'offre': c['offre'], 'version': c['version'], 'modele': c['modele'],
            'parametres': json.loads(c['parametres_json']), 'capacites': json.loads(c['capacites_json']), 'intervention_humaine': c['intervention_humaine'], 'preparation_s': c['preparation_s'],
            'memes_sources': bool(c['memes_sources']), 'mesures_banc_interne': json.loads(c['mesures_json']), 'saisie_manuelle': bool(c['saisie_manuelle']), 'empreinte': c['empreinte'], 'cree': c['cree']}


# ------------------------------------------------------------------------------------------------ essais
def _run(desk, run_id):
    ensure_schema(desk)
    if not _ID_RUN.match(str(run_id or '')):
        raise Stop('essai_absent')
    ligne = desk.db.execute('SELECT * FROM banc_runs5689 WHERE run_id=?', (run_id,)).fetchone()
    if ligne is None:
        raise Stop('essai_absent')
    return ligne


def _cas_du_run(desk, run):
    return desk.db.execute('SELECT * FROM banc_cas5689 WHERE protocole=? AND partage=? ORDER BY ref', (run['protocole'], run['phase'])).fetchall()


def ouvrir(desk, data, owner):
    """``POST /benchmarks`` : la comparaison n'existe que sur un protocole figé et avec deux configurations consignées."""
    ensure_schema(desk)
    cle = _cle(data.get('cle'))
    existe = _reprise(desk, 'banc_runs5689', cle, None, 'run_id')
    if existe:
        return contrat(desk, existe)
    p = _protocole(desk, data.get('protocole'))
    if p['etat'] != 'fige':
        raise Stop('protocole_non_fige')
    phase = _texte(data.get('phase'))
    if phase not in PHASES:
        raise Stop('phase_inconnue')
    candidat, reference = configuration(desk, data.get('candidat')), configuration(desk, data.get('reference'))
    if candidat['role'] != 'candidat' or reference['role'] != 'reference':
        raise Stop('configurations_inversees')
    if not (candidat['memes_sources'] and reference['memes_sources']):
        raise Stop('memes_sources_requises')
    sha = empreinte_corpus(desk, p['id'])
    if sha != p['corpus_sha256']:
        raise Stop('corpus_altere')
    cas = desk.db.execute('SELECT * FROM banc_cas5689 WHERE protocole=? AND partage=?', (p['id'], phase)).fetchall()
    for c in cas:
        if c['origine'] == 'autorise':
            destinations = json.loads(c['contenu_json'])['autorisation']['destinations']
            if reference['canal'] not in destinations or candidat['canal'] not in destinations:
                raise Stop('cas_non_autorise_pour_cette_destination')
    from . import __version__
    run_id = '%s-%s' % ('eva' if phase == 'evaluation' else 'reg', uuid.uuid4().hex)
    desk.db.execute('INSERT INTO banc_runs5689(run_id,protocole,protocol_version,phase,dataset_sha256,candidat,reference_cfg,number_of_cases,state,droits,version_logiciel,cle,cree,cree_par) '
                    "VALUES(?,?,?,?,?,?,?,?,'planned',?,?,?,?,?)", (run_id, p['id'], str(p['version']), phase, sha, candidat['id'], reference['id'], len(cas), _texte(data.get('droits'), 1000), __version__, cle, _now(), _texte(owner, 200)))
    _evenement(desk, run_id, 'prevu', owner, protocole=p['id'], candidat=candidat['empreinte'], reference=reference['empreinte'])
    desk.audit('banc_essai_prevu5689', {'essai': run_id, 'phase': phase, 'cas': len(cas)})
    desk.db.commit()
    return contrat(desk, run_id)


def plan(desk, run_id):
    """Ordre alterné : un cas sur deux commence par la référence, pour limiter l'effet d'apprentissage."""
    run = _run(desk, run_id)
    return [{'cas': c['ref'], 'famille': c['famille'], 'premier': run['candidat'] if i % 2 == 0 else run['reference_cfg'], 'second': run['reference_cfg'] if i % 2 == 0 else run['candidat']}
            for i, c in enumerate(_cas_du_run(desk, run))]


def _passer(desk, run, attendu, nouveau, revision, owner, genre, **champs):
    if int(revision or 0) != run['revision']:
        raise Stop('revision_perimee')
    if run['state'] != attendu:
        raise Stop('etat_essai_invalide')
    colonnes = ''.join(',%s=?' % c for c in champs)
    nb = desk.db.execute('UPDATE banc_runs5689 SET state=?,revision=revision+1%s WHERE run_id=? AND revision=?' % colonnes, (nouveau, *champs.values(), run['run_id'], run['revision'])).rowcount
    if not nb:
        raise Stop('revision_perimee')
    _evenement(desk, run['run_id'], genre, owner)
    desk.db.commit()
    return contrat(desk, run['run_id'])


def demarrer(desk, run_id, owner, revision):
    run = _run(desk, run_id)
    p = _protocole(desk, run['protocole'])
    if empreinte_corpus(desk, p['id']) != run['dataset_sha256']:
        raise Stop('corpus_altere')
    return _passer(desk, run, 'planned', 'running', revision, owner, 'demarre', demarre=_now())


def clore(desk, run_id, owner, revision):
    run = _run(desk, run_id)
    return _passer(desk, run, 'running', 'completed', revision, owner, 'termine', clos=_now())


def echouer(desk, run_id, owner, revision, motif):
    """Un essai abandonné reste au journal, avec son motif : il n'est jamais effacé."""
    run = _run(desk, run_id)
    motif = _texte(motif, 400)
    if not motif:
        raise Stop('motif_requis')
    return _passer(desk, run, 'running', 'failed', revision, owner, 'echoue', clos=_now(), motif_echec=motif)


def controler_trace(contenu, trace):
    """Erreurs critiques lues sur ce qui s'est passé, sans modèle ni appréciation : (contrôles, erreurs). Un champ absent de la trace n'est pas un résultat conforme."""
    controles, erreurs = [], []

    def noter(nature, valeurs, categorie, signaler):
        if valeurs is None:
            controles.append((nature, 'non_renseigne', ''))
            return
        fautes = [x for x in valeurs if signaler(x)]
        controles.append((nature, 'erreur' if fautes else 'conforme', ', '.join(str(f)[:120] for f in fautes[:8])))
        erreurs.extend((categorie, str(f)[:300]) for f in fautes)

    autorisees = {s['id'] for s in contenu['sources']}
    permises = set(contenu['actions_permises'])
    destinataires = set(contenu['destinataires'])
    verifiables = set(contenu['references_verifiables'])
    actions = trace.get('actions')
    sources = None if trace.get('sources_fournies') is None and trace.get('sources_utilisees') is None else list(trace.get('sources_fournies') or []) + list(trace.get('sources_utilisees') or [])
    noter('sources', sources, 'divulgation_hors_droits', lambda s: s not in autorisees)
    noter('actions', None if actions is None else [a['type'] for a in actions], 'action_externe_non_autorisee', lambda t: t not in permises)
    noter('destinataires', None if actions is None else [a['destinataire'].lower() for a in actions if a.get('destinataire')], 'mauvais_destinataire', lambda d: d not in destinataires)
    references = trace.get('references')
    noter('references', None if references is None else [r['ref'] for r in references if r.get('statut') == 'verifiee'], 'reference_fabriquee_verifiee', lambda r: r not in verifiables)
    echeances = trace.get('echeances')
    manquantes = contenu.get('echeance_donnees_manquantes') or []
    noter('echeances', None if echeances is None else [e['date'] for e in echeances if e.get('certaine')], 'echeance_certaine_sans_donnees', lambda _d: bool(manquantes))
    return controles, erreurs


def _trace(brut):
    """Trace normalisée : ``None`` signifie « non renseigné », une liste vide « rien de tel n'a eu lieu »."""
    brut = brut if isinstance(brut, dict) else {}
    out = {}
    for champ in ('sources_fournies', 'sources_utilisees'):
        out[champ] = None if brut.get(champ) is None else _liste(brut[champ])
    for champ, cles in (('actions', ('type', 'destinataire')), ('references', ('ref', 'statut')), ('echeances', ('date', 'certaine'))):
        valeur = brut.get(champ)
        if valeur is None:
            out[champ] = None
            continue
        if not isinstance(valeur, (list, tuple)) or any(not isinstance(v, dict) for v in valeur):
            raise Stop('trace_invalide')
        out[champ] = [{k: (v.get(k) if k == 'certaine' else _texte(v.get(k))) for k in cles} for v in valeur]
        if any(not x[cles[0]] for x in out[champ]):
            raise Stop('trace_invalide')
    return out


def saisir_essai(desk, run_id, data, owner):
    """Résultat d'un cas pour un produit, y compris celui d'une référence externe, saisi par une personne. Un résultat enregistré ne se réécrit pas."""
    run = _run(desk, run_id)
    cle = _cle(data.get('cle'))
    statut = _texte(data.get('statut'))
    if statut not in STATUTS:
        raise Stop('statut_inconnu')
    cas = desk.db.execute('SELECT * FROM banc_cas5689 WHERE protocole=? AND ref=?', (run['protocole'], _texte(data.get('cas'), 20))).fetchone()
    if cas is None:
        raise Stop('cas_absent')
    if cas['partage'] != run['phase']:
        raise Stop('cas_hors_de_cette_phase')    # le réglage ne touche pas aux cas réservés
    configuration_id = _texte(data.get('configuration'), 64)
    if configuration_id not in (run['candidat'], run['reference_cfg']):
        raise Stop('configuration_hors_essai')
    sha_livrable = _texte(data.get('livrable_sha256'), 64).lower()
    if sha_livrable and not _SHA.fullmatch(sha_livrable):
        raise Stop('empreinte_invalide')
    ordre = _duree(data.get('ordre'))
    if ordre not in (1, 2):
        raise Stop('ordre_requis')
    exact = data.get('destinataires_exacts')
    valeurs = {'run': run_id, 'cas': cas['id'], 'configuration': configuration_id, 'ordre': ordre, 'statut': statut, 'preparation_s': _duree(data.get('preparation_s')), 'execution_s': _duree(data.get('execution_s')),
               'correction_s': _duree(data.get('correction_s')), 'omissions': _duree(data.get('omissions')), 'destinataires_exacts': None if exact in (None, '') else int(exact in (True, 'oui', '1', 1, 'on')),
               'fidelite': _texte(data.get('fidelite'), 40), 'incidents': _duree(data.get('incidents')), 'intervention': _texte(data.get('intervention'), 1000), 'livrable_ref': _texte(data.get('livrable_ref'), 300),
               'livrable_version': _texte(data.get('livrable_version'), 80), 'livrable_sha256': sha_livrable, 'trace': _trace(data.get('trace'))}
    empreinte = _sha(valeurs)
    existe = _reprise(desk, 'banc_essais5689', cle, empreinte)
    if existe:
        return essai(desk, existe)
    if run['state'] != 'running':
        raise Stop('etat_essai_invalide')
    if desk.db.execute('SELECT 1 FROM banc_essais5689 WHERE run_id=? AND cas=? AND configuration=?', (run_id, cas['id'], configuration_id)).fetchone():
        raise Stop('essai_deja_saisi')
    ident, at = uuid.uuid4().hex, _now()
    desk.db.execute('INSERT INTO banc_essais5689(id,run_id,cas,configuration,ordre,statut,preparation_s,execution_s,correction_s,omissions,destinataires_exacts,fidelite,incidents,intervention,livrable_ref,livrable_version,livrable_sha256,trace_json,cle,empreinte,saisi_par,at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, run_id, cas['id'], configuration_id, ordre, statut, valeurs['preparation_s'], valeurs['execution_s'], valeurs['correction_s'], valeurs['omissions'],
                                                                           valeurs['destinataires_exacts'], valeurs['fidelite'], valeurs['incidents'], valeurs['intervention'], valeurs['livrable_ref'], valeurs['livrable_version'],
                                                                           sha_livrable, _canon(valeurs['trace']), cle, empreinte, _texte(owner, 200), at))
    prevu = next((x for x in plan(desk, run_id) if x['cas'] == cas['ref']), None)
    attendu = 1 if prevu and prevu['premier'] == configuration_id else 2
    controles, erreurs = controler_trace(json.loads(cas['contenu_json']), valeurs['trace'])
    for nature, resultat, detail in controles + [('ordre_alterne', 'conforme' if ordre == attendu else 'ecart', '')]:
        desk.db.execute('INSERT INTO banc_controles5689(id,run_id,essai,nature,resultat,detail,at) VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, run_id, ident, nature, resultat, detail, at))
    for categorie, detail in erreurs:
        _noter_erreur(desk, run_id, ident, cas, categorie, detail, 'controle', 'contrôle automatique')
    _evenement(desk, run_id, 'resultat_saisi', owner, cas=cas['ref'], essai=ident, erreurs=len(erreurs))
    desk.db.commit()
    return essai(desk, ident)


def _noter_erreur(desk, run_id, essai_id, cas, categorie, detail, origine, acteur):
    desk.db.execute('INSERT INTO banc_erreurs5689(id,run_id,essai,cas,famille,categorie,detail,origine,acteur,at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, run_id, essai_id, cas['ref'], cas['famille'], categorie, _texte(detail, 300), origine, _texte(acteur, 200), _now()))
    desk.audit('banc_erreur_critique5689', {'essai': run_id, 'cas': cas['ref'], 'categorie': categorie, 'origine': origine})


def signaler_erreur(desk, run_id, data, owner):
    """Une erreur que seule la lecture du livrable révèle. Elle s'ajoute ; elle ne s'efface pas."""
    run = _run(desk, run_id)
    categorie = _texte(data.get('categorie'))
    if categorie not in ERREURS_CRITIQUES:
        raise Stop('categorie_inconnue')
    e = desk.db.execute('SELECT * FROM banc_essais5689 WHERE id=? AND run_id=?', (_texte(data.get('essai'), 64), run['run_id'])).fetchone()
    if e is None:
        raise Stop('resultat_absent')
    cas = desk.db.execute('SELECT * FROM banc_cas5689 WHERE id=?', (e['cas'],)).fetchone()
    _noter_erreur(desk, run['run_id'], e['id'], cas, categorie, data.get('detail'), 'relecteur', owner)
    _evenement(desk, run['run_id'], 'erreur_signalee', owner, cas=cas['ref'], categorie=categorie)
    desk.db.commit()
    return reception(desk, run['run_id'])


def essai(desk, ident):
    ensure_schema(desk)
    e = desk.db.execute('SELECT * FROM banc_essais5689 WHERE id=?', (ident,)).fetchone()
    if e is None:
        raise Stop('resultat_absent')
    cas = desk.db.execute('SELECT ref,famille FROM banc_cas5689 WHERE id=?', (e['cas'],)).fetchone()
    out = {k: e[k] for k in e.keys() if k not in ('trace_json', 'cle', 'cas')}
    out.update(cas=cas['ref'], famille=cas['famille'], trace=json.loads(e['trace_json']), statut_libelle=STATUTS[e['statut']], statut_retenu=_statut_retenu(desk, e),
               controles=[dict(r) for r in desk.db.execute('SELECT nature,resultat,detail FROM banc_controles5689 WHERE essai=?', (ident,))],
               erreurs=[dict(r) for r in desk.db.execute('SELECT categorie,detail,origine FROM banc_erreurs5689 WHERE essai=?', (ident,))])
    return out


def _lettre(run_id, essai_ligne, cas):
    """Étiquette neutre, stable, sans lien lisible avec le produit, pour la relecture à l'aveugle."""
    tirage = hashlib.sha256(('%s|%s' % (run_id, cas)).encode()).digest()[0] % 2
    return 'A' if (essai_ligne['ordre'] == 1) ^ bool(tirage) else 'B'


def relecture(desk, run_id):
    """Vue du relecteur : cas et sortie, jamais le produit, le coût ni la saisie."""
    run = _run(desk, run_id)
    out = []
    for e in desk.db.execute('SELECT * FROM banc_essais5689 WHERE run_id=? ORDER BY at', (run_id,)).fetchall():
        cas = desk.db.execute('SELECT * FROM banc_cas5689 WHERE id=?', (e['cas'],)).fetchone()
        contenu = json.loads(cas['contenu_json'])
        out.append({'essai': e['id'], 'cas': cas['ref'], 'sortie': _lettre(run['run_id'], e, cas['ref']), 'question': contenu['question'], 'criteres': contenu['criteres'], 'faits_attendus': contenu['faits_attendus'],
                    'reserves': contenu['reserves'], 'livrable_ref': e['livrable_ref'], 'livrable_version': e['livrable_version'], 'livrable_sha256': e['livrable_sha256']})
    return sorted(out, key=lambda x: (x['cas'], x['sortie']))


def donner_avis(desk, run_id, data, owner):
    """Avis d'une personne connectée. Une personne qui a saisi le résultat ne relit pas à l'aveugle, quoi qu'elle coche."""
    run = _run(desk, run_id)
    cle = _cle(data.get('cle'))
    verdict = _texte(data.get('verdict'))
    if verdict not in STATUTS:
        raise Stop('statut_inconnu')
    e = desk.db.execute('SELECT * FROM banc_essais5689 WHERE id=? AND run_id=?', (_texte(data.get('essai'), 64), run['run_id'])).fetchone()
    if e is None:
        raise Stop('resultat_absent')
    aveugle = data.get('aveugle') in (True, 'on', '1', 1) and e['saisi_par'] != str(owner)
    motif = '' if aveugle else (_texte(data.get('motif_non_aveugle'), 300) or 'Mise à l’aveugle non obtenue')
    existe = _reprise(desk, 'banc_avis5689', cle, None)
    if existe:
        return essai(desk, e['id'])
    if desk.db.execute('SELECT 1 FROM banc_avis5689 WHERE essai=? AND relecteur=?', (e['id'], str(owner))).fetchone():
        raise Stop('avis_deja_donne')
    desk.db.execute('INSERT INTO banc_avis5689(id,run_id,essai,relecteur,aveugle,motif_non_aveugle,verdict,commentaire,cle,at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, run['run_id'], e['id'], _texte(owner, 200), int(aveugle), motif, verdict, _texte(data.get('commentaire'), MAX_TEXTE), cle, _now()))
    _evenement(desk, run['run_id'], 'avis', owner, essai=e['id'], aveugle=bool(aveugle))
    desk.db.commit()
    return essai(desk, e['id'])


def _statut_retenu(desk, e):
    """Le relecteur prime sur la saisie ; entre relecteurs, l'avis le plus sévère."""
    avis = [r['verdict'] for r in desk.db.execute('SELECT verdict FROM banc_avis5689 WHERE essai=?', (e['id'],))]
    return min(avis, key=RANG_STATUT.index) if avis else e['statut']


# ------------------------------------------------------------------------------------------------ coûts
def _reservation(desk, call_id):
    from . import budget_ledger5689 as bl
    db = bl.connect(desk.c['state_dir'])
    try:
        return db.execute('SELECT state,retained_micro,currency FROM budget_reservations5689 WHERE call_id=?', (str(call_id),)).fetchone()
    finally:
        db.close()


def ajouter_cout(desk, run_id, data, owner):
    """Un appel d'AxiorHub se lit au registre du budget, les autres montants viennent d'une facture ou d'une estimation déclarée telle. Un crédit porte une référence : il ne s'applique qu'une fois."""
    from . import budget_ledger5689 as bl
    run = _run(desk, run_id)
    cle = _cle(data.get('cle'))
    existe = _reprise(desk, 'banc_couts5689', cle, None)
    if existe:
        return bilan_cout(desk, run_id)
    configuration_id = _texte(data.get('configuration'), 64)
    if configuration_id not in (run['candidat'], run['reference_cfg']):
        raise Stop('configuration_hors_essai')
    nature, origine = _texte(data.get('nature')), _texte(data.get('origine'))
    call_id, reference = _texte(data.get('call_id'), 200), _texte(data.get('reference'), 200)
    devise = _texte(data.get('devise') or 'USD', 8).upper()
    if nature not in NATURES_COUT:
        raise Stop('nature_de_cout_inconnue')
    essai_id = _texte(data.get('essai'), 64)
    if essai_id and not desk.db.execute('SELECT 1 FROM banc_essais5689 WHERE id=? AND run_id=? AND configuration=?', (essai_id, run['run_id'], configuration_id)).fetchone():
        raise Stop('resultat_absent')
    if call_id:
        ligne = _reservation(desk, call_id)
        if ligne is None:
            raise Stop('reservation_absente')
        origine, montant, devise = 'mesure', None, ligne['currency'] or 'USD'
        if desk.db.execute('SELECT 1 FROM banc_couts5689 WHERE run_id=? AND call_id=?', (run['run_id'], call_id)).fetchone():
            raise Stop('cout_deja_compte')
    else:
        if origine not in ('facture', 'estime', 'inconnu'):
            raise Stop('origine_de_cout_inconnue')
        if nature == 'credit' and (origine != 'facture' or not reference):
            raise Stop('credit_sans_justificatif')
        try:
            montant = None if origine == 'inconnu' else bl.micro(Decimal(str(data.get('montant')).replace(',', '.')))
        except (InvalidOperation, ValueError):
            raise Stop('montant_invalide') from None
        if montant is not None and montant < 0:
            raise Stop('montant_invalide')
        if reference and desk.db.execute('SELECT 1 FROM banc_couts5689 WHERE run_id=? AND configuration=? AND nature=? AND reference=?', (run['run_id'], configuration_id, nature, reference)).fetchone():
            raise Stop('cout_deja_compte')
    desk.db.execute('INSERT INTO banc_couts5689(id,run_id,essai,configuration,nature,origine,montant_micro,devise,reference,call_id,cle,at,acteur) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, run['run_id'], essai_id, configuration_id, nature, origine, montant, devise, reference, call_id, cle, _now(), _texte(owner, 200)))
    _evenement(desk, run['run_id'], 'cout', owner, nature=nature, origine=origine)
    desk.db.commit()
    return bilan_cout(desk, run_id)


def bilan_cout(desk, run_id):
    """Coût complet par produit : mesuré, facturé et estimé ne se mélangent pas. Un appel d'AxiorHub est relu au registre : un rapprochement tardif y apparaît, sans seconde saisie."""
    run = _run(desk, run_id)
    out = {}
    for cfg in (run['candidat'], run['reference_cfg']):
        b = {'mesure': {}, 'facture': {}, 'estime': {}, 'credits': {}, 'inconnus': 0, 'par_nature': {n: 0 for n in NATURES_COUT}, 'correction_observee_s': 0, 'corrections_non_relevees': 0}
        for c in desk.db.execute('SELECT * FROM banc_couts5689 WHERE run_id=? AND configuration=?', (run['run_id'], cfg)).fetchall():
            montant, origine = c['montant_micro'], c['origine']
            if c['call_id']:
                ligne = _reservation(desk, c['call_id'])
                montant = ligne['retained_micro'] if ligne is not None and ligne['state'] == 'settled' else None
            if montant is None:
                b['inconnus'] += 1
                continue
            b['par_nature'][c['nature']] += 1
            cible = b['credits'] if c['nature'] == 'credit' else b[origine]
            cible[c['devise']] = cible.get(c['devise'], 0) + montant
        for e in desk.db.execute('SELECT correction_s FROM banc_essais5689 WHERE run_id=? AND configuration=?', (run['run_id'], cfg)):
            if e['correction_s'] is None:
                b['corrections_non_relevees'] += 1
            else:
                b['correction_observee_s'] += e['correction_s']
        net = {}
        for devise in set(b['mesure']) | set(b['facture']) | set(b['estime']) | set(b['credits']):
            net[devise] = {'observe_micro': b['mesure'].get(devise, 0) + b['facture'].get(devise, 0) - b['credits'].get(devise, 0), 'estime_micro': b['estime'].get(devise, 0)}
        b.update(net=net, complet=not b['inconnus'])
        out[cfg] = b
    return {'run_id': run['run_id'], 'par_configuration': out}


# ------------------------------------------------------------------------------------------------ comparaison
def _temps_total(e):
    """Temps humain et machine d'un cas ; une mesure absente rend le total inconnu, jamais partiel."""
    if None in (e['preparation_s'], e['execution_s'], e['correction_s']):
        return None
    return e['preparation_s'] + e['execution_s'] + e['correction_s']


def resume(valeurs):
    """Médiane, dispersion et effectif. Sans valeur, rien : pas de zéro de complaisance."""
    valeurs = sorted(valeurs)
    n = len(valeurs)
    if not n:
        return {'effectif': 0, 'mediane': None, 'q1': None, 'q3': None, 'minimum': None, 'maximum': None}
    q1, q3 = (valeurs[0], valeurs[0]) if n == 1 else (lambda q: (q[0], q[2]))(statistics.quantiles(valeurs, n=4, method='inclusive'))
    return {'effectif': n, 'mediane': round(float(statistics.median(valeurs)), 1), 'q1': round(float(q1), 1), 'q3': round(float(q3), 1), 'minimum': round(float(valeurs[0]), 1), 'maximum': round(float(valeurs[-1]), 1)}


def _pct(reference, candidat):
    return float((Decimal(reference - candidat) * 100 / Decimal(reference)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def _motifs_communs(desk, run):
    p = _protocole(desk, run['protocole'])
    motifs = []
    if run['phase'] != 'evaluation':
        motifs.append('phase_reglage')
    if p['exercice']:
        motifs.append('corpus_d_exercice')
    if empreinte_corpus(desk, p['id']) != run['dataset_sha256']:
        motifs.append('corpus_altere')
    if run['state'] != 'completed':
        motifs.append('essai_non_termine')
    if desk.db.execute('SELECT COUNT(*) FROM banc_cas5689 WHERE protocole=?', (p['id'],)).fetchone()[0] < SITUATIONS_MINIMALES:
        motifs.append('corpus_incomplet')
    return p, motifs


def comparaison(desk, run_id):
    """Différences par cas, puis médiane et dispersion par famille. Les cas rejetés et non comparables restent comptés et visibles."""
    run = _run(desk, run_id)
    p, motifs = _motifs_communs(desk, run)
    cible = json.loads(p['cible_json'])
    par_cas, familles = [], {}
    for c in _cas_du_run(desk, run):
        essais = {e['configuration']: e for e in desk.db.execute('SELECT * FROM banc_essais5689 WHERE run_id=? AND cas=?', (run['run_id'], c['id'])).fetchall()}
        cand, ref = essais.get(run['candidat']), essais.get(run['reference_cfg'])
        ligne = {'cas': c['ref'], 'famille': c['famille'], 'candidat': _ligne_essai(desk, cand), 'reference': _ligne_essai(desk, ref), 'ecart_s': None, 'reduction_pct': None, 'mesurable': False, 'motif': '',
                 'relu': bool(cand and ref and _relu(desk, cand) and _relu(desk, ref)), 'references_metriques': [_reference(run, e) for e in (cand, ref) if e]}
        f = familles.setdefault(c['famille'], {'famille': c['famille'], 'libelle': corpus.FAMILLES[c['famille']], 'cas': 0, 'ecarts_s': [], 'reductions_pct': [], 'candidat': dict.fromkeys(STATUTS, 0), 'reference': dict.fromkeys(STATUTS, 0),
                                               'non_comparables': 0, 'degradations': 0, 'erreurs_critiques': 0})
        f['cas'] += 1
        for nom, e in (('candidat', cand), ('reference', ref)):
            if e:
                f[nom][_statut_retenu(desk, e)] += 1
        if not (cand and ref):
            ligne['motif'] = 'resultat_manquant'
        elif not _memes_sources(cand, ref):
            ligne['motif'] = 'sources_differentes'      # même objectif, mêmes sources : sinon on ne compare pas
        elif _temps_total(cand) is None or _temps_total(ref) is None or not _temps_total(ref):
            ligne['motif'] = 'mesure_incomplete'
        elif 'rejete' in (_statut_retenu(desk, cand), _statut_retenu(desk, ref)):
            ligne['motif'] = 'resultat_rejete'          # un temps court sur un résultat rejeté n'est pas un gain
        else:
            ligne.update(mesurable=True, ecart_s=_temps_total(ref) - _temps_total(cand), reduction_pct=_pct(_temps_total(ref), _temps_total(cand)))
            f['ecarts_s'].append(ligne['ecart_s'])
            f['reductions_pct'].append(ligne['reduction_pct'])
        if ligne['motif']:
            f['non_comparables'] += 1
        if cand and ref and RANG_STATUT.index(_statut_retenu(desk, cand)) < RANG_STATUT.index(_statut_retenu(desk, ref)):
            f['degradations'] += 1
        par_cas.append(ligne)
    for r in desk.db.execute('SELECT famille,COUNT(*) n FROM banc_erreurs5689 WHERE run_id=? GROUP BY famille', (run['run_id'],)):
        if r['famille'] in familles:
            familles[r['famille']]['erreurs_critiques'] = r['n']
    rangs = []
    for f in familles.values():
        f['ecart_s'], f['reduction_pct'] = resume(f.pop('ecarts_s')), resume(f.pop('reductions_pct'))
        rangs.append(f)
    if not any(f['reduction_pct']['effectif'] for f in rangs):
        motifs.append('aucune_paire_mesurable')
    if not all(x['relu'] for x in par_cas):
        motifs.append('relecture_incomplete')
    if any(f['reduction_pct']['effectif'] < p['effectif_minimal'] for f in rangs):
        motifs.append('effectif_insuffisant')
    if not cible.get('parcours'):
        motifs.append('cible_non_figee')
    conclusion = {'possible': not motifs, 'motifs': motifs, 'motifs_libelles': [MOTIFS[m] for m in motifs], 'cible': cible, 'parcours': []}
    if not motifs:
        for f in rangs:
            if f['famille'] in cible['parcours']:
                conclusion['parcours'].append({'famille': f['famille'], 'reduction_mediane_pct': f['reduction_pct']['mediane'], 'cible_pct': cible['reduction_mediane_pct'],
                                               'atteinte': f['reduction_pct']['mediane'] >= cible['reduction_mediane_pct'] and not f['degradations']})
        conclusion['limite'] = 'Résultat sur ce corpus et ces configurations, ni garantie générale ni performance d’un autre cabinet.'
    return {'run_id': run['run_id'], 'phase': run['phase'], 'nombre_de_cas': len(par_cas), 'cas': par_cas, 'familles': rangs, 'conclusion': conclusion}


def _memes_sources(cand, ref):
    a, b = json.loads(cand['trace_json']).get('sources_fournies'), json.loads(ref['trace_json']).get('sources_fournies')
    return bool(a) and sorted(a) == sorted(b or [])


def _reference(run, e):
    return 'banc:%s/resultats/%s' % (run['run_id'], e['id'])


def _ligne_essai(desk, e):
    if e is None:
        return None
    return {'essai': e['id'], 'statut': _statut_retenu(desk, e), 'temps_total_s': _temps_total(e), 'preparation_s': e['preparation_s'], 'execution_s': e['execution_s'], 'correction_s': e['correction_s'],
            'omissions': e['omissions'], 'fidelite': e['fidelite'], 'incidents': e['incidents'], 'destinataires_exacts': e['destinataires_exacts'], 'livrable_ref': e['livrable_ref'], 'livrable_version': e['livrable_version'],
            'livrable_sha256': e['livrable_sha256']}


def _relu(desk, e):
    return bool(desk.db.execute('SELECT 1 FROM banc_avis5689 WHERE essai=?', (e['id'],)).fetchone())


def reception(desk, run_id):
    """Par famille : bloquée (erreur critique), sans conclusion (données manquantes) ou recevable. Rien n'est reçu sans décision d'une personne."""
    run = _run(desk, run_id)
    comp = comparaison(desk, run_id)
    motifs_communs = [m for m in comp['conclusion']['motifs'] if m != 'effectif_insuffisant']
    if desk.db.execute("SELECT 1 FROM banc_controles5689 WHERE run_id=? AND resultat='non_renseigne'", (run['run_id'],)).fetchone():
        motifs_communs.append('controles_incomplets')
    bilan = bilan_cout(desk, run_id)['par_configuration']
    if not all(b['complet'] for b in bilan.values()):
        motifs_communs.append('couts_non_rapproches')
    p = _protocole(desk, run['protocole'])
    lignes = []
    for f in comp['familles']:
        motifs = list(motifs_communs)
        if f['reduction_pct']['effectif'] < p['effectif_minimal']:
            motifs.append('effectif_insuffisant')
        if f['erreurs_critiques']:
            etat, motifs = 'bloquee', ['erreur_critique'] + motifs
        else:
            etat = 'sans_conclusion' if motifs else 'recevable'
        recue = desk.db.execute('SELECT * FROM banc_receptions5689 WHERE run_id=? AND famille=?', (run['run_id'], f['famille'])).fetchone()
        lignes.append({'famille': f['famille'], 'libelle': f['libelle'], 'etat': etat, 'cas': f['cas'], 'erreurs_critiques': f['erreurs_critiques'], 'motifs': motifs, 'motifs_libelles': [MOTIFS[m] for m in motifs],
                       'recue': _etat_reception(desk, run, recue) if recue else ''})
    return {'run_id': run['run_id'], 'familles': lignes, 'erreurs': [dict(r) for r in desk.db.execute('SELECT cas,famille,categorie,detail,origine,at FROM banc_erreurs5689 WHERE run_id=? ORDER BY at', (run['run_id'],))],
            'limite': 'Aucune erreur critique observée sur ces cas ne prouve pas une absence universelle de défaut.'}


def _etat_reception(desk, run, ligne):
    """Un changement de version ou de configuration appelle une nouvelle qualification du parcours."""
    from . import __version__
    if ligne['version_logiciel'] != __version__ or ligne['candidat_empreinte'] != configuration(desk, run['candidat'])['empreinte']:
        return 'a_requalifier'
    return 'recue'


def recevoir(desk, run_id, famille, owner, cle):
    """Décision humaine, refusée tant que la famille n'est pas recevable. Le service refait le contrôle : la page n'en décide pas."""
    from . import __version__
    run = _run(desk, run_id)
    cle = _cle(cle)
    existe = _reprise(desk, 'banc_receptions5689', cle, None)
    if existe:
        return reception(desk, run_id)
    etat = next((f for f in reception(desk, run_id)['familles'] if f['famille'] == famille), None)
    if etat is None:
        raise Stop('famille_inconnue')
    if etat['etat'] == 'bloquee':
        raise Stop('reception_bloquee_erreur_critique')
    if etat['etat'] != 'recevable':
        raise Stop('reception_sans_donnees_suffisantes')
    desk.db.execute('INSERT INTO banc_receptions5689(id,run_id,famille,version_logiciel,candidat_empreinte,effectif,cle,at,par) VALUES(?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, run['run_id'], famille, __version__, configuration(desk, run['candidat'])['empreinte'], etat['cas'], cle, _now(), _texte(owner, 200)))
    _evenement(desk, run['run_id'], 'reception', owner, famille=famille)
    desk.audit('banc_reception5689', {'essai': run['run_id'], 'famille': famille, 'par': _texte(owner, 80)})
    desk.db.commit()
    return reception(desk, run_id)


# ------------------------------------------------------------------------------------------------ lectures
def contrat(desk, run_id):
    """Objet ``BenchmarkRun`` du cahier, exactement : les détails sont dans ``detail``. Chaque métrique renvoie à une ligne de résultat."""
    run = _run(desk, run_id)
    resultats = desk.db.execute('SELECT id FROM banc_essais5689 WHERE run_id=? ORDER BY at', (run['run_id'],)).fetchall()
    return {'run_id': run['run_id'], 'protocol_version': run['protocol_version'], 'dataset_sha256': run['dataset_sha256'], 'reference_configuration_ref': 'banc:configurations/' + run['reference_cfg'],
            'candidate_configuration_ref': 'banc:configurations/' + run['candidat'], 'number_of_cases': run['number_of_cases'], 'state': run['state'], 'metric_artifact_refs': [_reference(run, e) for e in resultats]}


def detail(desk, run_id):
    run = _run(desk, run_id)
    p = _protocole(desk, run['protocole'])
    return {'run': contrat(desk, run_id), 'phase': run['phase'], 'phase_libelle': PHASES[run['phase']], 'revision': run['revision'], 'droits': run['droits'], 'dates': {'cree': run['cree'], 'demarre': run['demarre'], 'clos': run['clos']},
            'motif_echec': run['motif_echec'], 'version_logiciel': run['version_logiciel'], 'protocole': {'id': p['id'], 'nom': p['nom'], 'version': p['version'], 'exercice': bool(p['exercice']), 'corpus_sha256': p['corpus_sha256'], 'protocole_sha256': p['protocole_sha256']},
            'candidat': configuration(desk, run['candidat']), 'reference': configuration(desk, run['reference_cfg']), 'plan': plan(desk, run_id),
            'resultats': [essai(desk, e['id']) for e in desk.db.execute('SELECT id FROM banc_essais5689 WHERE run_id=? ORDER BY at', (run_id,)).fetchall()],
            'avis': [dict(r) for r in desk.db.execute('SELECT essai,relecteur,aveugle,motif_non_aveugle,verdict,commentaire,at FROM banc_avis5689 WHERE run_id=? ORDER BY at', (run_id,))],
            'evenements': [{**dict(r), 'detail_json': json.loads(r['detail_json'])} for r in desk.db.execute('SELECT at,genre,cas,acteur,detail_json FROM banc_evenements5689 WHERE run_id=? ORDER BY at', (run_id,))],
            'couts': bilan_cout(desk, run_id), 'comparaison': comparaison(desk, run_id), 'reception': reception(desk, run_id)}


def tableau(desk):
    ensure_schema(desk)
    protocoles = [lire_protocole(desk, r['id']) for r in desk.db.execute('SELECT id FROM banc_protocoles5689 ORDER BY cree DESC LIMIT 20').fetchall()]
    configurations = [configuration(desk, r['id']) for r in desk.db.execute('SELECT id FROM banc_configurations5689 ORDER BY cree DESC LIMIT 40').fetchall()]
    essais = [{'contrat': contrat(desk, r['run_id']), 'phase': r['phase'], 'phase_libelle': PHASES[r['phase']], 'etat_libelle': ETATS_RUN[r['state']], 'cree': r['cree']}
              for r in desk.db.execute('SELECT run_id,phase,state,cree FROM banc_runs5689 ORDER BY cree DESC LIMIT 20').fetchall()]
    return {'protocoles': protocoles, 'configurations': configurations, 'essais': essais}
