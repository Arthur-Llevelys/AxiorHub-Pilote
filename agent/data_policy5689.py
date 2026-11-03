"""5.6.89 : décision commune sur les données (chapitres 41 et 42 du cahier).

Avant recherche, extrait, compteur, lecture de cache et transfert, pour le modèle, l'OCR, les embeddings, le classement, la recherche web, les
outils MCP, les journaux, la télémétrie et le support. Étend ``membres5650`` (droits, dossiers réservés), ``vueia5688`` (catégories fermées) et
``confidentiel5664`` (correspondances entre avocats) sans les remplacer : aucune décision ne peut être plus large que ces contrôles.

L'acteur, le cabinet et la politique viennent du serveur. Une autorisation annoncée par le navigateur, un texte reçu, une compétence ou un outil
n'accorde rien : ils ne sont jamais lus ici. La décision a une révision et une courte expiration ; le service d'effet la refait toujours.

Modes : ``local_strict`` (rien ne sort du périmètre déclaré ; une recherche de droit public n'est admise que générique et vers une destination
marquée publique), ``cloud`` et ``hybride`` (destinations du registre, admises par le cabinet). Sans choix exprès du cabinet, le mode hérité des
réglages existants s'applique et le comportement d'avant la mise à jour est conservé.

Une politique illisible bloque avec un incident clair, sans comportement ouvert par défaut. Les sauvegardes et les actions déterministes
autorisées suivent leur politique propre (elles n'appellent pas ce service).

Limites assumées, redites à l'écran : le lieu de stockage ne prouve pas celui de l'inférence ou de la sauvegarde ; une valeur inconnue reste
inconnue, jamais une promesse ; la pseudonymisation n'est pas une anonymisation certaine, en particulier pour les images et pièces jointes.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape as e
import json
import logging
import os
from pathlib import Path
import sqlite3
import uuid

from .common import Stop

KEY = 'politique5689'
MODES = ('local_strict', 'cloud', 'hybride')
LIBELLES_MODE = {'local_strict': 'Local strict : rien ne sort du périmètre déclaré', 'cloud': 'Cloud : prestataires admis par le cabinet',
                 'hybride': 'Hybride : local d’abord, prestataires admis pour certaines tâches', 'historique': 'Réglages d’origine conservés (aucun mode choisi)'}
OUTILS = ('llm', 'ocr', 'embeddings', 'classement', 'recherche_web', 'outil_mcp', 'journaux', 'telemetrie', 'support', 'connecteur_claude', 'voix', 'mise_a_jour')
LIBELLES_OUTIL = {'llm': 'Modèle d’IA', 'ocr': 'Lecture de scans', 'embeddings': 'Index sémantique', 'classement': 'Classement', 'recherche_web': 'Recherche web',
                  'outil_mcp': 'Outil connecté', 'journaux': 'Journaux', 'telemetrie': 'Télémétrie', 'support': 'Support', 'connecteur_claude': 'Connecteur Claude', 'voix': 'Voix',
                  'mise_a_jour': 'Mise à jour'}
LECTEURS = frozenset(('llm', 'ocr', 'embeddings', 'classement', 'recherche_web', 'outil_mcp', 'connecteur_claude', 'voix'))
RECHERCHE = frozenset(('recherche_web', 'outil_mcp'))
APRES_CHOIX = RECHERCHE | {'connecteur_claude', 'voix'}
TIERS = frozenset(('document_tiers', 'courrier', 'conclusions', 'depot'))
RANG = {'local_strict': 0, 'hybride': 1, 'cloud': 2, 'historique': 2}
LOCAUX = frozenset(('ollama', 'local', 'serveur_cabinet'))
EXPIRE_SECONDES = 120
GARDER_JOURS = 180
DEFAUT_QUALITE = 60
INCIDENT = 'politique5689.incident.json'
MOTIFS = {'politique_illisible': 'La politique des données est illisible : les nouveaux accès et transferts concernés sont bloqués.',
          'dossier_reserve': 'Ce dossier est réservé : ce compte n’y a pas accès.',
          'mode_local_strict': 'Le mode local strict interdit cet envoi hors du périmètre déclaré.',
          'nom_de_partie_dans_la_requete': 'La requête contient une donnée de dossier (nom de partie, référence) : elle ne peut pas partir en mode local strict.',
          'prestataire_non_admis': 'Ce prestataire n’est pas admis au registre du cabinet.',
          'region_inconnue': 'Le lieu de traitement de ce prestataire n’est pas renseigné, alors que la politique l’exige.',
          'region_non_admise': 'Le lieu de traitement de ce prestataire n’est pas admis par la politique.',
          'dossier_exclu': 'Ce dossier est exclu des envois externes.',
          'donnees_dossier_interdites': 'Aucune donnée de dossier ne peut partir par cette voie.',
          'correspondance_confidentielle': 'Correspondance confidentielle entre avocats : usage interne uniquement.',
          'outil_inconnu': 'Traitement inconnu : refusé.'}
log = logging.getLogger(__name__)
_SCHEMA = '''
CREATE TABLE IF NOT EXISTS policy_decisions5689(
  id TEXT PRIMARY KEY, at TEXT NOT NULL, cabinet TEXT NOT NULL, acteur_h TEXT NOT NULL, mission TEXT NOT NULL DEFAULT '', dossier_h TEXT NOT NULL DEFAULT '', finalite TEXT NOT NULL,
  outil TEXT NOT NULL, destination TEXT NOT NULL, region TEXT, labels TEXT NOT NULL, autorise INTEGER NOT NULL, motifs TEXT NOT NULL, revision INTEGER NOT NULL, mode TEXT NOT NULL, expire TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS policy_decisions5689_at ON policy_decisions5689(at);
CREATE INDEX IF NOT EXISTS policy_decisions5689_mission ON policy_decisions5689(mission);
CREATE TABLE IF NOT EXISTS prestataires5689(
  id TEXT PRIMARY KEY, nom TEXT NOT NULL, hote TEXT NOT NULL DEFAULT '', nature TEXT NOT NULL DEFAULT 'prestataire', public INTEGER NOT NULL DEFAULT 0, admis INTEGER NOT NULL DEFAULT 0,
  region_inference TEXT, region_stockage TEXT, region_sauvegarde TEXT, conservation TEXT, entrainement TEXT, categories TEXT NOT NULL DEFAULT '[]', finalites TEXT NOT NULL DEFAULT '[]',
  preuve TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, saisi_par TEXT NOT NULL DEFAULT '', saisi_le TEXT NOT NULL DEFAULT '');
'''


class PolitiqueIllisible(Stop):
    def __init__(self, cause=''):
        super().__init__('politique_illisible')
        self.cause = cause


def _maintenant():
    return datetime.now(timezone.utc)


def _h(valeur):
    return hashlib.sha256(str(valeur or '').encode('utf-8')).hexdigest()[:16]


def _base(state_dir):
    return Path(str(state_dir or '')) / 'desk.sqlite3'


# ------------------------------------------------------------------------------------------------ lecture fraîche
def _strict(raw):
    """Politique enregistrée, validée sans valeur de repli : une forme inattendue n'est jamais lue comme « tout est permis »."""
    if not isinstance(raw, dict) or raw.get('mode') not in MODES or not isinstance(raw.get('revision'), int) or raw['revision'] < 1:
        raise ValueError('forme')
    regions, fermees = raw.get('regions_admises', []), raw.get('categories_fermees', [])
    if not isinstance(regions, list) or not isinstance(fermees, list):
        raise ValueError('listes')
    for cle in ('repli_externe', 'confidentiel_local'):
        if not isinstance(raw.get(cle, False), bool):
            raise ValueError(cle)
    qualite = raw.get('qualite_minimale', DEFAUT_QUALITE)
    if not isinstance(qualite, int) or isinstance(qualite, bool) or not 0 <= qualite <= 100:
        raise ValueError('qualite')
    return {'mode': raw['mode'], 'revision': raw['revision'], 'regions_admises': [str(x) for x in regions], 'categories_fermees': [str(x) for x in fermees],
            'repli_externe': raw.get('repli_externe', False), 'confidentiel_local': raw.get('confidentiel_local', False), 'qualite_minimale': qualite,
            'perimetre': str(raw.get('perimetre') or ''), 'journal_contenu': raw.get('journal_contenu') if isinstance(raw.get('journal_contenu'), dict) else None, 'explicite': True}


def _herite(rule, hybride):
    """Sans choix exprès : le mode découle des réglages existants, le comportement reste celui d'avant la mise à jour."""
    if rule.get('mode') == 'local':
        mode = 'local_strict'
    elif rule.get('mode') in ('auto', 'chatgpt', 'mistral', 'anthropic'):
        mode = 'cloud'
    elif hybride.get('mode') == 'hybrid':
        mode = 'hybride'
    elif hybride.get('mode') == 'manual':
        mode = 'cloud'
    else:
        mode = 'historique'
    return {'mode': mode, 'revision': 0, 'regions_admises': [], 'categories_fermees': [], 'repli_externe': False, 'confidentiel_local': False,
            'qualite_minimale': DEFAUT_QUALITE, 'perimetre': '', 'journal_contenu': None, 'explicite': False}


def charger(state_dir):
    """Politique en vigueur, relue à chaque contrôle, quel que soit le processus. Lève ``PolitiqueIllisible`` si elle ne peut pas être établie."""
    path = _base(state_dir)
    if not state_dir or not path.is_file():
        return _herite({}, {})
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            rows = dict(db.execute('SELECT key,value FROM settings WHERE key IN (?,?,?,?)', (KEY, 'ai:politique', 'ai:hybrid', 'ia_vue5688')).fetchall())
        finally:
            db.close()
    except sqlite3.OperationalError as error:
        if 'no such table' in str(error):
            return _herite({}, {})
        raise _incident(state_dir, 'lecture_impossible') from None
    except sqlite3.Error:
        raise _incident(state_dir, 'lecture_impossible') from None
    try:
        lus = {k: json.loads(v) for k, v in rows.items()}
        for cle in ('ai:politique', 'ai:hybrid', 'ia_vue5688'):
            if cle in lus and not isinstance(lus[cle], dict):
                raise ValueError(cle)
        valeur = _strict(lus[KEY]) if KEY in lus else _herite(lus.get('ai:politique') or {}, lus.get('ai:hybrid') or {})
    except (ValueError, TypeError, KeyError) as error:
        raise _incident(state_dir, 'valeur_invalide') from error
    fermees = {c for c, v in (lus.get('ia_vue5688') or {}).items() if v is False}
    valeur['categories_fermees'] = sorted(set(valeur['categories_fermees']) | fermees)
    _clore_incident(state_dir)
    return valeur


def _incident(state_dir, cause):
    """Fichier à côté de la base : une base illisible ne doit pas empêcher de dire pourquoi tout est bloqué."""
    try:
        chemin = Path(str(state_dir)) / INCIDENT
        temporaire = chemin.with_suffix('.tmp' + uuid.uuid4().hex[:6])
        temporaire.write_text(json.dumps({'code': 'politique_illisible', 'cause': cause, 'le': _maintenant().isoformat(), 'message': MOTIFS['politique_illisible']}, ensure_ascii=False), encoding='utf-8')
        os.replace(temporaire, chemin)
    except OSError:
        log.error('politique des données illisible (%s)', cause)
    return PolitiqueIllisible(cause)


def _clore_incident(state_dir):
    try:
        (Path(str(state_dir)) / INCIDENT).unlink(missing_ok=True)
    except OSError:
        pass


def incident(state_dir):
    try:
        return json.loads((Path(str(state_dir)) / INCIDENT).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def categorie_fermee(desk, categorie):
    """Point appelé par ``vueia5688.blocked`` : une politique illisible ferme toutes les lectures destinées au modèle."""
    try:
        return categorie in charger(desk.c.get('state_dir'))['categories_fermees']
    except PolitiqueIllisible:
        return True


def repli_externe_autorise(config):
    """Une panne du local n'est jamais masquée par un envoi externe, sauf choix exprès inscrit dans la politique."""
    etat = charger((config or {}).get('state_dir'))
    return etat['mode'] != 'local_strict' and etat['repli_externe']


def repli_bloque(config):
    """Secours et escalade hérités (options activées à la main) : coupés dès qu'une politique expresse ne les autorise pas, et toujours en local strict."""
    etat = charger((config or {}).get('state_dir'))
    return etat['mode'] == 'local_strict' or (etat['explicite'] and not etat['repli_externe'])


def qualite_minimale(config):
    return charger((config or {}).get('state_dir'))['qualite_minimale']


# ------------------------------------------------------------------------------------------------ registre des prestataires
def _ouvrir(state_dir):
    db = sqlite3.connect(_base(state_dir), timeout=5)
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA)
    return db


def _fiche(state_dir, destination):
    if not state_dir or not _base(state_dir).is_file():
        return None
    try:
        db = _ouvrir(state_dir)
        try:
            return db.execute('SELECT * FROM prestataires5689 WHERE id=?', (str(destination),)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None


INCONNU = 'Non renseigné : aucune garantie'
SERVICES = (('sources_juridiques', 'Sources juridiques connectées'), ('registres_publics', 'Registres publics'), ('recherche_entreprises', 'Annuaire des entreprises'), ('tavily', 'Tavily'),
            ('elevenlabs', 'ElevenLabs'), ('connecteur_claude', 'Connecteur Claude'))


def lisible(ligne):
    """Fiche à l'écran : une valeur absente est dite absente, jamais remplacée par une promesse."""
    r = dict(ligne)
    out = {k: r.get(k) for k in ('id', 'nom', 'hote', 'nature', 'revision')}
    out.update({'admis': bool(r.get('admis')), 'public': bool(r.get('public')), 'categories': json.loads(r.get('categories') or '[]'), 'finalites': json.loads(r.get('finalites') or '[]')})
    for cle in ('region_inference', 'region_stockage', 'region_sauvegarde', 'conservation', 'entrainement'):
        out[cle] = r.get(cle)
        out[cle + '_affichage'] = r.get(cle) or INCONNU
    return out


def synchroniser(desk):
    """Connexions déjà configurées reportées au registre, sans aucune valeur inventée : non admises tant que le cabinet ne les a pas qualifiées,
    sauf le fait qu'elles sont des connexions existantes (visible : ``admis`` reste 0)."""
    from .ai_gateway import provider_registry
    db = _ouvrir(desk.c['state_dir'])
    try:
        for pid, nom in SERVICES:
            db.execute('INSERT OR IGNORE INTO prestataires5689(id,nom,saisi_le) VALUES(?,?,?)', (pid, nom, _maintenant().isoformat()))
        for pid, item in provider_registry(desk.c).items():
            if item.get('type', 'ollama') == 'ollama':
                continue
            db.execute('INSERT OR IGNORE INTO prestataires5689(id,nom,hote,saisi_le) VALUES(?,?,?,?)',
                       (pid, pid, str(item.get('url') or '').split('/')[2] if '//' in str(item.get('url') or '') else '', _maintenant().isoformat()))
        db.commit()
        return [lisible(r) for r in db.execute('SELECT * FROM prestataires5689 ORDER BY id')]
    finally:
        db.close()


def enregistrer_prestataire(desk, data, acteur):
    """Écriture versionnée (409 si la page est périmée). Un champ vide reste inconnu."""
    ident = str(data.get('id') or '').strip()[:80]
    if not ident:
        raise Stop('prestataire_requis')

    def texte(cle):
        return str(data.get(cle) or '').strip()[:200] or None
    db = _ouvrir(desk.c['state_dir'])
    try:
        db.execute('BEGIN IMMEDIATE')
        ligne = db.execute('SELECT revision FROM prestataires5689 WHERE id=?', (ident,)).fetchone()
        attendue = data.get('version')
        if ligne is not None and str(attendue) != str(ligne['revision']):
            raise Stop('version_en_conflit')
        revision = (ligne['revision'] + 1) if ligne is not None else 1
        db.execute('INSERT OR REPLACE INTO prestataires5689(id,nom,hote,nature,public,admis,region_inference,region_stockage,region_sauvegarde,conservation,entrainement,categories,finalites,preuve,revision,saisi_par,saisi_le) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (ident, str(data.get('nom') or ident)[:120], str(data.get('hote') or '')[:200].lower(), str(data.get('nature') or 'prestataire')[:40], 1 if data.get('public') is True else 0,
                    1 if data.get('admis') is True else 0, texte('region_inference'), texte('region_stockage'), texte('region_sauvegarde'), texte('conservation'), texte('entrainement'),
                    json.dumps([str(x)[:40] for x in data.get('categories') or []][:20], ensure_ascii=False), json.dumps([str(x)[:40] for x in data.get('finalites') or []][:20], ensure_ascii=False),
                    str(data.get('preuve') or '')[:400], revision, _h(acteur), _maintenant().isoformat()))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    desk.audit('prestataire5689_modifie', {'id': ident, 'revision': revision})
    return {'ok': True, 'id': ident, 'revision': revision, 'message': 'Prestataire enregistré.'}


# ------------------------------------------------------------------------------------------------ donnée de dossier dans une requête
def donnee_de_dossier(config, texte):
    """Vrai si le texte contient une référence, un nom de partie ou de correspondant connu du cabinet : c'est une donnée du dossier, même sans pièce jointe."""
    texte = str(texte or '')
    if not texte.strip():
        return False
    from .model import pseudo_sources
    from .policy567 import matter_ids
    from .pseudo540 import Pseudonymizer
    if config and matter_ids(config, [texte]):
        return True
    masque = Pseudonymizer(pseudo_sources(config or {}))
    return masque.apply(texte) != texte


# ------------------------------------------------------------------------------------------------ décision
def _caches_viewer(state_dir, acteur, role):
    from . import membres5650
    masques = set(membres5650.viewer_hidden() or ())
    if acteur and role and state_dir:
        masques |= set(membres5650.hidden_for(state_dir, acteur, role) or ())
    return masques


def _exclus(config):
    from .hybrid400 import policy
    return set(policy(config or {}).get('excluded_matters') or ())


def evaluer(config, outil, destination, finalite='', dossiers=(), labels=(), region=None, acteur='', role='', requete='', local=None, mission='', source_refs=(), journal=True):
    """Décision complète (contrat ``PolicyDecision``). N'exécute rien ; une évaluation visible ne dispense jamais le service d'effet de refaire ce contrôle.
    Le texte de ``requete`` sert à détecter une donnée de dossier ; il n'est jamais conservé."""
    config = config or {}
    state_dir = config.get('state_dir')
    motifs, revision, mode = [], 0, 'indetermine'
    labels = sorted({str(x) for x in labels})
    dossiers = sorted({str(x) for x in ([dossiers] if isinstance(dossiers, str) else dossiers) if str(x)})
    destination = str(destination or '')
    local = destination in LOCAUX if local is None else bool(local)
    fiche = None
    try:
        etat = charger(state_dir)
        revision, mode = etat['revision'], etat['mode']
        if outil not in OUTILS:
            motifs.append('outil_inconnu')
        caches = _caches_viewer(state_dir, acteur, role)
        if caches & set(dossiers):
            motifs.append('dossier_reserve')
        if outil in LECTEURS:
            from .membres5650 import ai_denied
            fermees = set(etat['categories_fermees']) | set(ai_denied())
            motifs += ['categorie_fermee:' + x for x in labels if x in fermees]
        donnee_requete = bool(requete) and donnee_de_dossier(config, requete)
        if donnee_requete and 'dossier' not in labels:
            labels.append('dossier')
        if 'confidentiel' in labels and (finalite in TIERS or (not local and etat['confidentiel_local'])):
            motifs.append('correspondance_confidentielle')
        if not local:
            fiche = _fiche(state_dir, destination)
            region = region or (fiche['region_inference'] if fiche is not None else None)
            porte_dossier = 'dossier' in labels or bool(set(labels) & {'documents', 'courriels', 'contacts', 'factures', 'temps', 'agenda', 'confidentiel'})
            if outil in ('telemetrie', 'support', 'journaux', 'mise_a_jour') and (porte_dossier or dossiers):
                motifs.append('donnees_dossier_interdites')
            if mode == 'local_strict' and (etat['explicite'] or outil not in APRES_CHOIX):   # « IA locale uniquement » d'origine : voies hors modèle inchangées tant que le cabinet n'a pas choisi
                public = fiche is not None and bool(fiche['public']) and bool(fiche['admis'])
                if outil in RECHERCHE and donnee_requete:
                    motifs.append('nom_de_partie_dans_la_requete')
                elif not (outil in RECHERCHE and public and not porte_dossier) and outil != 'mise_a_jour':
                    motifs.append('mode_local_strict')
            elif etat['explicite'] and outil not in ('mise_a_jour',):
                if fiche is None or not fiche['admis']:
                    motifs.append('prestataire_non_admis')
            if etat['regions_admises'] and mode != 'local_strict':
                if not region:
                    motifs.append('region_inconnue')
                elif region not in etat['regions_admises']:
                    motifs.append('region_non_admise')
            if set(dossiers) & _exclus(config):
                motifs.append('dossier_exclu')
    except PolitiqueIllisible:
        motifs.append('politique_illisible')
    autorise = not motifs
    stamp = _maintenant()
    decision = {'cabinet_id': str(config.get('cabinet_id') or 'cabinet'), 'actor_ref': _h(acteur or 'serveur'), 'purpose': str(finalite or ''), 'source_refs': [str(x)[:120] for x in source_refs],
                'destination_ref': destination, 'policy_revision': revision, 'allowed': autorise, 'denial_reasons': motifs, 'expires_at': (stamp + timedelta(seconds=EXPIRE_SECONDES)).isoformat()}
    extras = {'id': uuid.uuid4().hex, 'outil': outil, 'labels': labels, 'region': region or None, 'mode': mode, 'local': local}
    if journal:
        _journaliser(state_dir, decision, extras, dossiers, mission)
    return {**decision, **extras}


def contrat(decision):
    """Champs du contrat logique seuls (JSON Schema PolicyDecision)."""
    return {k: decision[k] for k in ('cabinet_id', 'actor_ref', 'purpose', 'source_refs', 'destination_ref', 'policy_revision', 'allowed', 'denial_reasons', 'expires_at')}


def exiger(config, outil, destination, **options):
    """Refus par ``Stop`` : premier motif. À appeler au plus près de l'accès ou du transfert."""
    decision = evaluer(config, outil, destination, **options)
    if not decision['allowed']:
        raise Stop(decision['denial_reasons'][0].split(':')[0])
    return decision


def _journaliser(state_dir, decision, extras, dossiers, mission):
    """Identifiants techniques et décisions seulement : ni prompt, ni titre de dossier, ni contenu, ni secret. Une panne du journal n'ouvre aucun droit et ne bloque pas seule."""
    if not state_dir or not _base(state_dir).is_file():
        return
    try:
        db = _ouvrir(state_dir)
        try:
            db.execute('INSERT INTO policy_decisions5689(id,at,cabinet,acteur_h,mission,dossier_h,finalite,outil,destination,region,labels,autorise,motifs,revision,mode,expire) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (extras['id'], _maintenant().isoformat(), decision['cabinet_id'], decision['actor_ref'], str(mission or _mission_courante()), _h(','.join(dossiers)) if dossiers else '', decision['purpose'][:60],
                        extras['outil'], decision['destination_ref'][:120], extras['region'], json.dumps(extras['labels']), int(decision['allowed']), json.dumps(decision['denial_reasons']),
                        decision['policy_revision'], extras['mode'], decision['expires_at']))
            db.execute('DELETE FROM policy_decisions5689 WHERE at<?', ((_maintenant() - timedelta(days=GARDER_JOURS)).isoformat(),))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        log.warning('journal des décisions indisponible')


def _mission_courante():
    from .routage5682 import _mission
    try:
        return _mission()
    except (sqlite3.Error, OSError):
        return ''


def garde_modele(cfg, messages, stage=''):
    """Contrôle commun, dernière étape avant l'envoi au modèle (local compris). Tout passe par ``Model._leaf``. Les contrôles de connexion, sans contenu de dossier, sont hors champ."""
    policy_config = cfg.get('external_policy_config') if isinstance(cfg.get('external_policy_config'), dict) else {}
    state_dir = cfg.get('state_dir') or policy_config.get('state_dir')
    if not state_dir or cfg.get('diagnostic5682'):
        return None
    config = policy_config or {'state_dir': state_dir}
    from .policy567 import matter_ids
    kind = cfg.get('provider_type') or cfg.get('type') or 'ollama'
    local = kind == 'ollama'
    dossiers = matter_ids(config, [cfg.get('external_context'), messages])
    from .membres5650 import current_author
    return exiger(config, 'llm', 'ollama' if local else str(cfg.get('provider_id') or kind), finalite=str(cfg.get('purpose') or stage or ''), dossiers=dossiers,
                  labels=('dossier',) if dossiers else (), local=local, acteur=current_author(), mission=str(cfg.get('mission5682') or ''))


def garde_destination(cfg, state_dir):
    """À la construction d'un modèle externe : une destination refusée n'est pas contactée, même sans contenu. Le contrôle des messages suit dans ``garde_modele``."""
    kind = cfg.get('provider_type') or cfg.get('type') or 'ollama'
    if kind == 'ollama':
        return None
    config = cfg.get('external_policy_config') if isinstance(cfg.get('external_policy_config'), dict) else {'state_dir': state_dir}
    return exiger(config, 'llm', str(cfg.get('provider_id') or cfg.get('id') or kind), finalite=str(cfg.get('purpose') or ''), local=False, journal=False)


def destination_admise(config, provider_id, provider, finalite, dossiers):
    """Filtre du routeur, sans journal : il ne choisit que parmi les destinations admises. Une politique illisible y est un refus comme ailleurs."""
    kind = provider.get('type', 'ollama')
    return evaluer(config, 'llm', provider_id, finalite=finalite, dossiers=dossiers, labels=('dossier',) if dossiers else (), local=kind == 'ollama', journal=False)


def filtrer_sources(config, sources, finalite, destination, local=False):
    """Extraits destinés à un service : un extrait refusé n'est ni transmis ni compté. Rend (gardés, nombre retiré)."""
    from .confidentiel5664 import entry_is_confidential
    gardes, retires = [], 0
    for source in sources:
        labels = ['confidentiel'] if entry_is_confidential(source) else []
        mid = str(source.get('matter') or source.get('dossier') or '')
        if evaluer(config, 'llm', destination, finalite=finalite, dossiers=(mid,) if mid else (), labels=labels, local=local, journal=False)['allowed']:
            gardes.append(source)
        else:
            retires += 1
    return gardes, retires


# ------------------------------------------------------------------------------------------------ changement de politique
def enregistrer(desk, data, acteur):
    """Nouvelle politique, révisée ; 409 si la révision attendue est périmée. Plus restrictive : caches et propositions incompatibles invalidés."""
    if data.get('mode') not in MODES:
        raise Stop('mode_politique_invalide')
    try:
        avant = _strict(desk.settings(KEY, None)) if desk.db.execute('SELECT 1 FROM settings WHERE key=?', (KEY,)).fetchone() else None
    except (ValueError, TypeError):
        avant = None                  # réglage illisible : l'administrateur le remplace, c'est la sortie de l'incident
    attendue = data.get('version')
    if str(attendue if attendue not in (None, '') else 0) != str(avant['revision'] if avant else 0):
        raise Stop('version_en_conflit')
    if data.get('mode') == 'local_strict' and str(data.get('perimetre') or '').strip() == '':
        raise Stop('perimetre_local_requis')
    valeur = {'mode': data['mode'], 'revision': (avant['revision'] if avant else 0) + 1, 'regions_admises': [str(x)[:40] for x in data.get('regions_admises') or []][:20],
              'categories_fermees': [str(x)[:40] for x in data.get('categories_fermees') or []][:20], 'repli_externe': data.get('repli_externe') is True,
              'confidentiel_local': data.get('confidentiel_local') is True, 'qualite_minimale': int(data.get('qualite_minimale', DEFAUT_QUALITE)),
              'perimetre': str(data.get('perimetre') or '')[:400], 'par': _h(acteur), 'le': _maintenant().isoformat()}
    try:
        _strict(valeur)
    except ValueError:
        raise Stop('politique_invalide') from None
    restrictive = plus_restrictive(avant or _herite({}, {}), valeur)
    desk.setting(KEY, valeur)
    invalide = invalider_incompatibles(desk, valeur) if restrictive else {}
    desk.audit('politique5689_modifiee', {'mode': valeur['mode'], 'revision': valeur['revision'], 'restrictive': restrictive, 'invalide': invalide})
    return {'ok': True, 'revision': valeur['revision'], 'restrictive': restrictive, 'invalide': invalide, 'message': 'Politique enregistrée : elle s’applique aux étapes suivantes.'}


def plus_restrictive(avant, apres):
    if RANG[apres['mode']] < RANG[avant['mode']]:
        return True
    if set(apres['categories_fermees']) - set(avant['categories_fermees']):
        return True
    if apres['regions_admises'] and (not avant['regions_admises'] or set(avant['regions_admises']) - set(apres['regions_admises'])):
        return True
    return apres['confidentiel_local'] and not avant['confidentiel_local']


def invalider_incompatibles(desk, valeur):
    """Caches et aperçus préparés sous une politique plus large : rien de ce qui a été produit par un service désormais refusé n'est réutilisé.
    Les propositions de routage non encore exécutées sont refusées. Le contrôle de ``garde_modele`` reste le verrou : ceci évite de resservir l'ancien résultat."""
    from . import cache_extraction5689
    out = {'extractions': cache_extraction5689.invalider(desk, motif='politique_plus_restrictive', sous_revision=valeur['revision'])}
    lignes = desk.db.execute("SELECT key FROM settings WHERE key LIKE 'juridique5649:cache:%'").fetchall()
    desk.db.execute("DELETE FROM settings WHERE key LIKE 'juridique5649:cache:%'")
    out['recherches'] = len(lignes)
    for table, sql in (('apercus', 'DELETE FROM hybrid_previews_v400'), ('compositions', 'DELETE FROM composition_cache_v567'),
                       ('decisions', "UPDATE decisions_5682 SET etat='perimee' WHERE etat='decidee'"),
                       # Le service qui a produit une proposition n'y est pas noté : par prudence, toute proposition de l'IA non décidée est périmée ; elle se redemande sous la nouvelle politique.
                       ('propositions', "UPDATE document_patches5689 SET state='perimee',error='politique_plus_restrictive' WHERE origin='ia' AND state IN ('demandee','proposee')")):
        try:
            out[table] = desk.db.execute(sql).rowcount
        except sqlite3.OperationalError:
            out[table] = 0                                 # table jamais créée sur cette installation
    desk.db.commit()
    return out


# ------------------------------------------------------------------------------------------------ synthèse par mission
def synthese_mission(desk, mission):
    """Trois rubriques lisibles, puis le détail à la demande. Tirées du journal des décisions : destinations réellement admises, sans aucun contenu."""
    state_dir = desk.c['state_dir']
    db = _ouvrir(state_dir)
    try:
        lignes = [dict(r) for r in db.execute('SELECT outil,destination,region,labels,autorise,motifs,revision,mode,at FROM policy_decisions5689 WHERE mission=? ORDER BY at', (str(mission),))]
        fiches = {r['id']: lisible(r) for r in db.execute('SELECT * FROM prestataires5689')}
    finally:
        db.close()
    from .vueia5688 import LABELS
    donnees, services, limites = set(), {}, []
    for r in lignes:
        if not r['autorise']:
            continue
        for label in json.loads(r['labels']):
            donnees.add({'dossier': 'Contenu du dossier', 'confidentiel': 'Correspondance confidentielle (usage interne)'}.get(label, LABELS.get(label, label)))
        services.setdefault(r['destination'], set()).add(r['outil'])
    destinataires = []
    externe = False
    for ident, outils in sorted(services.items()):
        fiche = fiches.get(ident)
        local = ident in LOCAUX
        externe = externe or not local
        destinataires.append({'id': ident, 'nom': (fiche or {}).get('nom') or ('Modèle local du cabinet' if local else ident), 'local': local, 'usages': sorted(LIBELLES_OUTIL.get(o, o) for o in outils),
                              'lieu_traitement': 'Serveur du cabinet' if local else (fiche or {}).get('region_inference_affichage', INCONNU),
                              'conservation': 'Selon la configuration du serveur' if local else (fiche or {}).get('conservation_affichage', INCONNU)})
    if not lignes:
        limites.append('Aucun envoi enregistré pour cette mission à ce jour.')
    if externe:
        limites.append('La pseudonymisation n’est pas une anonymisation certaine, notamment pour les images et les pièces jointes.')
        limites.append('Le lieu de stockage annoncé ne prouve pas le lieu d’inférence ni celui des sauvegardes.')
    if any(d['lieu_traitement'] == INCONNU or d['conservation'] == INCONNU for d in destinataires):
        limites.append('Certaines valeurs ne sont pas renseignées : elles ne valent ni promesse de localisation ni absence de conservation.')
    refus = [r for r in lignes if not r['autorise']]
    if refus:
        limites.append('%d accès ou transfert(s) refusés par la politique.' % len(refus))
    return {'mission': str(mission), 'donnees_utilisees': sorted(donnees) or ['Aucune donnée de dossier enregistrée'], 'services_destinataires': destinataires, 'limites': limites,
            'detail': [{k: r[k] for k in ('at', 'outil', 'destination', 'autorise', 'revision', 'mode')} | {'motifs': json.loads(r['motifs'])} for r in lignes][-200:]}


def synthese_html(desk, mission):
    return synthese_rendu(synthese_mission(desk, mission))


def synthese_rendu(s):
    services = ''.join('<li>%s : %s. Lieu de traitement : %s. Conservation : %s.</li>' % (e(d['nom']), e(', '.join(d['usages'])), e(d['lieu_traitement']), e(d['conservation'])) for d in s['services_destinataires'])
    detail = ''.join('<li>%s · %s · %s · %s%s</li>' % (e(d['at'][:16].replace('T', ' ')), e(LIBELLES_OUTIL.get(d['outil'], d['outil'])), e(d['destination']), 'admis' if d['autorise'] else 'refusé',
                                                       (' (' + e(', '.join(d['motifs'])) + ')') if d['motifs'] else '') for d in s['detail'])
    return ('<section class="ax-card"><h3>Données et services</h3><h4>Données utilisées</h4><ul>%s</ul><h4>Services destinataires</h4><ul>%s</ul><h4>Limites</h4><ul>%s</ul>'
            '<details><summary>Voir le détail</summary><ul>%s</ul></details></section>') % (
        ''.join('<li>%s</li>' % e(x) for x in s['donnees_utilisees']), services or '<li>Aucun service externe enregistré.</li>', ''.join('<li>%s</li>' % e(x) for x in s['limites']), detail)


def etat_public(desk):
    """Résumé pour la page et le diagnostic. Une politique illisible y est dite, jamais remplacée par une valeur par défaut."""
    state_dir = desk.c['state_dir']
    try:
        etat = charger(state_dir)
    except PolitiqueIllisible:
        return {'lisible': False, 'incident': incident(state_dir), 'message': MOTIFS['politique_illisible']}
    return {'lisible': True, 'mode': etat['mode'], 'libelle': LIBELLES_MODE.get(etat['mode'], etat['mode']), 'explicite': etat['explicite'], 'revision': etat['revision'], 'regions_admises': etat['regions_admises'],
            'categories_fermees': etat['categories_fermees'], 'repli_externe': etat['repli_externe'], 'confidentiel_local': etat['confidentiel_local'], 'qualite_minimale': etat['qualite_minimale'],
            'perimetre': etat['perimetre'], 'incident': None}


def journal_contenu_autorise(desk, portee):
    """Journalisation exceptionnelle de contenu : seulement avec autorisation, durée et accès définis ; sinon jamais."""
    try:
        autorisation = charger(desk.c['state_dir'])['journal_contenu']
    except PolitiqueIllisible:
        return False
    if not isinstance(autorisation, dict) or autorisation.get('portee') != portee or not autorisation.get('acces') or not autorisation.get('par'):
        return False
    try:
        return datetime.fromisoformat(str(autorisation.get('jusqu_au'))) > _maintenant()
    except ValueError:
        return False


CHAMPS_INTERDITS = frozenset(('prompt', 'prompts', 'messages', 'contenu', 'content', 'texte', 'text', 'titre', 'title', 'secret', 'api_key', 'password', 'token', 'corps', 'body'))


def journal_sur(data):
    """Détail de journal sans prompt, titre, contenu ni secret : ces clés sont retirées à toute profondeur (par défaut). À utiliser pour tout événement de ce domaine."""
    if isinstance(data, dict):
        return {k: journal_sur(v) for k, v in data.items() if str(k).lower() not in CHAMPS_INTERDITS}
    if isinstance(data, (list, tuple)):
        return [journal_sur(x) for x in data]
    return data
