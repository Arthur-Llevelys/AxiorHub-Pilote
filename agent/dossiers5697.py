"""5.6.97 (lot dossiers, B03 et § 9 à § 12) : registre unique des affaires du cabinet.

Un seul service écrit les dossiers : l'avocat (écran Nouveau dossier, courriel), l'IA et les connecteurs passent par ``creer``,
``lire``, ``lister``, ``modifier`` et ``rattacher_message``. Aucun appel à un modèle ; tout fonctionne IA en pause.

- La référence et l'identifiant sont réservés dans SQLite (``BEGIN IMMEDIATE``) **avant** tout accès au stockage : une référence
  invalide ne produit aucun MKCOL (B03). La même transaction insère l'opération de rangement (``dossier_outbox5697``), la commande
  idempotente (clé + empreinte du corps normalisé) et, si demandé, le lien avec un courriel.
- Le rangement (création ou rattachement du répertoire, sous-dossiers configurés) a lieu après validation, par ``traiter_rangements`` :
  bail, tentative, reprise par le même identifiant ; un répertoire déjà créé est retrouvé (jamais recréé ni supprimé).
- Le registre JSON historique (``registry-web.json``) devient une projection écrite sous verrou interprocessus, pour les pages
  existantes qui lisent ``load_matters`` ; les anciens identifiants restent valables par la table d'alias.
- Droits : ``acces5697`` (garde commune) avant tout filtre, tri ou LIMIT ; un dossier réservé et un dossier absent reçoivent la
  même réponse (``introuvable``).
- Aucun déplacement IMAP, aucun envoi : ``rattacher_message`` enregistre l'association (compte, boîte, UIDVALIDITY, UID, Message-ID,
  empreinte) ; la conservation de l'original et des pièces reste celle de ``correspondances5643`` sur demande explicite.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
import time
import unicodedata
import uuid

from .common import Stop, clean_path, under

NATURES = {'conseil': 'Conseil', 'contentieux': 'Contentieux', 'a_qualifier': 'À qualifier'}
STATUTS = {'actif': 'Actif', 'archive': 'Archivé'}
CLIENT_ETATS = {'identifie': 'Identifié', 'a_identifier': 'À identifier'}
CONFIDENTIALITES = {'cabinet': 'Tout le cabinet', 'reserve': 'Réservé aux membres désignés'}
PROVENANCES = ('manuel', 'courriel', 'detecte', 'migre', 'ia', 'connecteur')
ROLES_PARTIES = {'client': 'Client', 'adverse': 'Partie adverse', 'conseil_adverse': 'Conseil adverse', 'juridiction': 'Juridiction',
                 'expert': 'Expert', 'correspondant': 'Correspondant', 'autre': 'Autre',
                 # 5.6.98 (onglet Parties, ``fiche5698.GROUPES``) : rôles d'un logiciel de gestion de cabinet ; les anciens restent valables
                 'avocat_client': 'Avocat du client', 'avocat_postulant': 'Avocat postulant', 'commissaire_justice': 'Commissaire de justice',
                 'mandataire_judiciaire': 'Mandataire ou administrateur judiciaire', 'notaire': 'Notaire', 'assureur': 'Assureur'}
STOCKAGE = {'a_preparer': 'Rangement en préparation', 'en_cours': 'Rangement en cours', 'pret': 'Rangement prêt',
            'en_attente': 'Rangement en attente', 'a_verifier': 'Rangement à vérifier'}
CHAMPS_MODIFIABLES = ('titre', 'reference', 'client', 'client_etat', 'nature', 'statut', 'responsable', 'juridiction', 'rg', 'resume')
REF_OK = re.compile(r'[A-Z0-9][A-Z0-9._-]{0,39}')
CLE_OK = re.compile(r'[A-Za-z0-9_.:-]{8,80}')
ID_OK = re.compile(r'[A-Za-z0-9_-]{1,80}')
NOM_REP = re.compile(r"[^\w .'’()&,-]+")
BAIL_S = 120
ROLES_CABINET = ('administrateur', 'avocat', 'assistant')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS dossiers5697(
  id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL, ref TEXT NOT NULL, ref_norm TEXT NOT NULL, titre TEXT NOT NULL,
  client TEXT NOT NULL DEFAULT '', client_etat TEXT NOT NULL DEFAULT 'a_identifier', nature TEXT NOT NULL DEFAULT 'a_qualifier',
  statut TEXT NOT NULL DEFAULT 'actif', responsable TEXT NOT NULL DEFAULT '', provenance TEXT NOT NULL DEFAULT 'manuel',
  confidentialite TEXT NOT NULL DEFAULT 'cabinet', juridiction TEXT NOT NULL DEFAULT '', rg TEXT NOT NULL DEFAULT '',
  resume TEXT NOT NULL DEFAULT '', chemin TEXT NOT NULL DEFAULT '', stockage_mode TEXT NOT NULL DEFAULT 'creer',
  stockage_etat TEXT NOT NULL DEFAULT 'a_preparer', stockage_motif TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, auteur TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL,
  modifie_par TEXT NOT NULL DEFAULT '', UNIQUE(cabinet_id, ref_norm));
CREATE INDEX IF NOT EXISTS dossiers5697_chemin ON dossiers5697(chemin);
CREATE TABLE IF NOT EXISTS dossier_parties5697(
  id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, nom TEXT NOT NULL, nom_norm TEXT NOT NULL, role TEXT NOT NULL,
  courriel TEXT NOT NULL DEFAULT '', contact_id TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, UNIQUE(dossier_id, nom_norm, role));
CREATE TABLE IF NOT EXISTS dossier_alias5697(
  genre TEXT NOT NULL, alias TEXT NOT NULL, dossier_id TEXT NOT NULL, cree_le TEXT NOT NULL, PRIMARY KEY(genre, alias));
CREATE TABLE IF NOT EXISTS dossier_commandes5697(
  cabinet_id TEXT NOT NULL, acteur TEXT NOT NULL, operation TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  recu TEXT NOT NULL DEFAULT '{}', etat TEXT NOT NULL DEFAULT 'traite', cree_le TEXT NOT NULL,
  PRIMARY KEY(cabinet_id, acteur, operation, cle));
CREATE TABLE IF NOT EXISTS dossier_outbox5697(
  id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, kind TEXT NOT NULL, operation_key TEXT NOT NULL UNIQUE, args TEXT NOT NULL DEFAULT '{}',
  etat TEXT NOT NULL DEFAULT 'a_faire', tentatives INTEGER NOT NULL DEFAULT 0, bail TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '',
  resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS dossier_outbox5697_etat ON dossier_outbox5697(etat, dossier_id);
CREATE TABLE IF NOT EXISTS message_dossier5697(
  message_ref TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, mail_key TEXT NOT NULL DEFAULT '', compte TEXT NOT NULL DEFAULT '', boite TEXT NOT NULL DEFAULT '',
  uidvalidity TEXT NOT NULL DEFAULT '', uid TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '', empreinte TEXT NOT NULL DEFAULT '',
  sujet TEXT NOT NULL DEFAULT '', expediteur TEXT NOT NULL DEFAULT '', date_message TEXT NOT NULL DEFAULT '',
  provenance TEXT NOT NULL DEFAULT 'manuel', options TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
  auteur TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS message_dossier5697_d ON message_dossier5697(dossier_id);
CREATE INDEX IF NOT EXISTS message_dossier5697_mid ON message_dossier5697(compte, message_id);
CREATE INDEX IF NOT EXISTS message_dossier5697_src ON message_dossier5697(compte, boite, uidvalidity, uid);
CREATE INDEX IF NOT EXISTS message_dossier5697_cle ON message_dossier5697(mail_key);
CREATE TABLE IF NOT EXISTS dossier_journal5697(
  id INTEGER PRIMARY KEY AUTOINCREMENT, dossier_id TEXT NOT NULL, quand TEXT NOT NULL, acteur TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS dossier_journal5697_d ON dossier_journal5697(dossier_id, id);
CREATE TABLE IF NOT EXISTS dossier_migrations5697(
  id TEXT PRIMARY KEY, quand TEXT NOT NULL, acteur TEXT NOT NULL DEFAULT '', simulation INTEGER NOT NULL DEFAULT 1, rapport TEXT NOT NULL DEFAULT '{}');
'''
_SCHEMA_PRET = set()


class Refus(Stop):
    """Refus métier avec détail (champs invalides : 422 ; doublon : 409)."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.detail = detail or {}


# ------------------------------------------------------------------------------------------------ outils
def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(texte):
    return hashlib.sha256(str(texte).encode('utf-8')).hexdigest()


def _plier(texte):
    texte = unicodedata.normalize('NFKD', str(texte or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', texte).strip()


def _texte(valeur, maxi=200):
    valeur = unicodedata.normalize('NFC', re.sub(r'\s+', ' ', str(valeur or ''))).strip()
    if len(valeur) > maxi or any(ord(c) < 32 for c in valeur):
        raise ValueError
    return valeur


def ensure_schema(desk):
    """Schéma additif et idempotent ; jamais exécuté dans une transaction ouverte (``executescript`` la validerait)."""
    cle = (str(desk.c.get('state_dir')), id(desk.db))
    if cle in _SCHEMA_PRET:
        return
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()
    _SCHEMA_PRET.add(cle)


def _cabinet(desk):
    from .cabinet_profile5689 import cabinet_id
    return cabinet_id(desk)


@contextmanager
def _transaction(desk):
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        desk.db.rollback()
        raise
    desk.db.commit()


def _acteur(acteur):
    from .acces5697 import acteur_de
    return acteur_de(acteur)


def _peut_ecrire(a):
    from .acces5697 import AGENT
    if a != AGENT and a.role not in ROLES_CABINET:
        raise Stop('role_insuffisant')


def _visible(desk, a, dossier_id):
    from .acces5697 import peut_lire_dossier
    return peut_lire_dossier(desk, a, dossier_id)


def _journal(desk, dossier_id, acteur, action, details=None):
    """Historique utile (qui, quoi, quand) sans recopier le contenu des documents ni des courriels."""
    desk.db.execute('INSERT INTO dossier_journal5697(dossier_id,quand,acteur,action,details) VALUES(?,?,?,?,?)',
                    (dossier_id, _maintenant(), str(acteur or ''), action, json.dumps(details or {}, ensure_ascii=False)))


def _commande(desk, cabinet, a, operation, cle, corps):
    """Commande idempotente : même clé et même corps normalisé → reçu initial ; corps différent → 409."""
    if not CLE_OK.fullmatch(str(cle or '')):
        raise Stop('cle_idempotence_absente')
    empreinte = _sha(json.dumps(corps, sort_keys=True, ensure_ascii=False, default=str))
    row = desk.db.execute('SELECT empreinte,recu FROM dossier_commandes5697 WHERE cabinet_id=? AND acteur=? AND operation=? AND cle=?',
                          (cabinet, a.compte, operation, cle)).fetchone()
    if row:
        if row['empreinte'] != empreinte:
            raise Stop('cle_idempotence_deja_utilisee')
        return empreinte, {**json.loads(row['recu']), 'rejoue': True}
    return empreinte, None


def _enregistrer_commande(desk, cabinet, a, operation, cle, empreinte, recu):
    desk.db.execute('INSERT INTO dossier_commandes5697(cabinet_id,acteur,operation,cle,empreinte,recu,etat,cree_le) VALUES(?,?,?,?,?,?,?,?)',
                    (cabinet, a.compte, operation, cle, empreinte, json.dumps(recu, ensure_ascii=False, default=str), 'traite', _maintenant()))


# ------------------------------------------------------------------------------------------------ références et emplacements
def normaliser_reference(ref):
    """Référence normalisée (majuscules, espaces → tirets) ; invalide : ``None``. « ### » est invalide."""
    ref = unicodedata.normalize('NFKC', str(ref or '')).strip().upper()
    ref = re.sub(r'\s+', '-', ref)
    return ref if REF_OK.fullmatch(ref) else None


def _refs_existantes(desk, cabinet):
    refs = {r[0] for r in desk.db.execute('SELECT ref_norm FROM dossiers5697 WHERE cabinet_id=?', (cabinet,))}
    refs |= {r[0] for r in desk.db.execute("SELECT alias FROM dossier_alias5697 WHERE genre='ref'")}
    for m in _registre_json(desk):
        for x in [m.get('id')] + list(m.get('references') or [])[:1]:
            n = normaliser_reference(x)
            if n:
                refs.add(n)
    return refs


def proposer_reference(desk, annee=None):
    """Référence révisable « AAAA-NNNN », libre au moment de la proposition (la collision est revérifiée à l'enregistrement)."""
    if not desk.db.in_transaction:
        ensure_schema(desk)
    annee = int(annee or datetime.now(timezone.utc).year)
    prises = _refs_existantes(desk, _cabinet(desk))
    n = 1 + sum(1 for r in prises if r.startswith('%d-' % annee))
    while '%d-%04d' % (annee, n) in prises:
        n += 1
    return '%d-%04d' % (annee, n)


def racine(desk):
    """Racine configurée des dossiers (réglage, puis ``matter_roots``, puis première racine Nextcloud)."""
    nc = desk.c.get('nextcloud') or {}
    choix = desk.settings('dossiers5697:racine', '') or ''
    candidates = ([choix] if choix else []) + list(nc.get('matter_roots') or []) + list(nc.get('roots') or [])
    for r in candidates:
        try:
            r = clean_path(str(r))
        except Stop:
            continue
        if any(under(r, x) for x in nc.get('roots') or []):
            return r
    raise Stop('racine_dossiers_absente')


def nom_repertoire(titre, ref):
    nom = NOM_REP.sub(' ', str(titre or '')).strip(' .')
    nom = re.sub(r'\s+', ' ', nom)[:100].strip(' .') or 'Dossier'
    return '%s - %s' % (nom, ref)


def emplacement_propose(desk, titre, ref):
    return clean_path(racine(desk) + '/' + nom_repertoire(titre, ref))


def _chemin_autorise(desk, chemin):
    """Chemin canonique sous une racine autorisée, jamais la racine elle-même ni un répertoire exclu."""
    from .workspace import allowed
    chemin = allowed(desk.c, str(chemin or '').strip())
    if chemin in {clean_path(x) for x in desk.c['nextcloud']['roots']} or chemin == racine(desk):
        raise Stop('choisir_un_dossier_et_non_la_racine_du_cabinet')
    return chemin


def _registre_json(desk):
    from .common import load_matters
    try:
        return load_matters(desk.c, unfiltered=True)
    except (OSError, ValueError):
        return []


# ------------------------------------------------------------------------------------------------ validation
def valider(desk, donnees, a):
    """Contrôle complet AVANT toute écriture ; renvoie les valeurs normalisées ou ``Refus('champs_invalides', {champ: message})``."""
    d = donnees if isinstance(donnees, dict) else {}
    err, v = {}, {}
    try:
        v['titre'] = _texte(d.get('titre') or d.get('intitule'), 160)
        if not v['titre']:
            err['titre'] = 'Indiquez l’intitulé du dossier.'
    except ValueError:
        err['titre'] = 'Intitulé trop long ou caractères invalides.'
    try:
        v['client'] = _texte(d.get('client'), 160)
    except ValueError:
        err['client'] = 'Nom du client trop long ou caractères invalides.'
        v['client'] = ''
    etat = str(d.get('client_etat') or ('identifie' if v.get('client') else 'a_identifier'))
    if etat not in CLIENT_ETATS or (etat == 'identifie' and not v.get('client')):
        err['client'] = 'Choisissez un client ou « À identifier ».'
    v['client_etat'] = etat
    v['nature'] = str(d.get('nature') or 'a_qualifier')
    if v['nature'] not in NATURES:
        err['nature'] = 'Nature : Conseil, Contentieux ou À qualifier.'
    v['responsable'] = str(d.get('responsable') or a.compte).strip()[:120]
    if not v['responsable'] or any(ord(c) < 32 for c in v['responsable']):
        err['responsable'] = 'Responsable invalide.'
    v['confidentialite'] = str(d.get('confidentialite') or 'cabinet')
    if v['confidentialite'] not in CONFIDENTIALITES:
        err['confidentialite'] = 'Confidentialité inconnue.'
    membres = d.get('membres') or []
    if not isinstance(membres, list) or len(membres) > 50:
        err['membres'] = 'Liste de membres invalide.'
        membres = []
    v['membres'] = sorted({str(x).strip()[:120] for x in membres if str(x).strip()} | ({a.compte, v['responsable']} if v['confidentialite'] == 'reserve' else set()))
    for champ, maxi in (('juridiction', 160), ('rg', 40)):
        try:
            v[champ] = _texte(d.get(champ), maxi)
        except ValueError:
            err[champ] = 'Valeur trop longue ou caractères invalides.'
            v[champ] = ''
    if v.get('rg') and not re.fullmatch(r'[0-9A-Za-z /._-]{1,40}', v['rg']):
        err['rg'] = 'Numéro RG invalide.'
    brute = d.get('reference')
    if brute not in (None, ''):
        ref = normaliser_reference(brute)
        if not ref:
            err['reference'] = 'Référence invalide : lettres, chiffres, point, tiret ou soulignement (40 caractères au plus).'
        v['reference'] = ref or ''
    else:
        v['reference'] = ''
    parties = []
    for p in (d.get('parties') or [])[:40] if isinstance(d.get('parties'), list) else []:
        try:
            nom = _texte((p or {}).get('nom'), 160)
        except (ValueError, AttributeError):
            err['parties'] = 'Partie invalide.'
            continue
        role = str((p or {}).get('role') or 'autre')
        if not nom:
            continue
        if role not in ROLES_PARTIES:
            err['parties'] = 'Rôle de partie inconnu.'
            continue
        courriel = str((p or {}).get('courriel') or '').strip().lower()[:254]
        if courriel and not re.fullmatch(r'[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+', courriel):
            err['parties'] = 'Adresse de courriel invalide.'
            continue
        parties.append({'nom': nom, 'role': role, 'courriel': courriel, 'contact_id': str((p or {}).get('contact_id') or '')[:80]})
    if v.get('client') and not any(p['role'] == 'client' for p in parties):
        parties.insert(0, {'nom': v['client'], 'role': 'client', 'courriel': '', 'contact_id': ''})
    v['parties'] = parties
    emp = d.get('emplacement') if isinstance(d.get('emplacement'), dict) else {}
    v['stockage_mode'] = str(emp.get('mode') or 'creer')
    if v['stockage_mode'] not in ('creer', 'rattacher'):
        err['emplacement'] = 'Choisissez de créer le répertoire ou d’en rattacher un.'
    v['chemin_demande'] = str(emp.get('chemin') or '')
    if v['stockage_mode'] == 'rattacher':
        try:
            v['chemin_demande'] = _chemin_autorise(desk, v['chemin_demande'])
        except Stop as ex:
            err['emplacement'] = {'chemin_nextcloud_absolu_requis': 'Indiquez le répertoire à rattacher.',
                                  'fichier_hors_racines': 'Ce répertoire est hors des racines autorisées.'}.get(str(ex), 'Répertoire non autorisé.')
    v['provenance'] = str(d.get('provenance') or 'manuel')
    if v['provenance'] not in PROVENANCES:
        err['provenance'] = 'Provenance inconnue.'
    if err:
        raise Refus('champs_invalides', err)
    return v


# ------------------------------------------------------------------------------------------------ création
def creer(desk, acteur, donnees, cle, message=None, origine_ia=False):
    """Crée une affaire. Ordre : validation complète, transaction (unicité, idempotence, dossier, parties, alias, rangement en outbox,
    lien courriel), validation, projection locale. Aucun accès au stockage ici : voir ``traiter_rangements``.
    Rend le reçu : id, version, url, référence, état du rangement."""
    ensure_schema(desk)
    a = _acteur(acteur)
    _peut_ecrire(a)
    v = valider(desk, donnees, a)
    if origine_ia and v['provenance'] == 'manuel':
        v['provenance'] = 'ia'
    lien = _message_normalise(desk, message) if message else None
    cabinet = _cabinet(desk)
    corps = {k: v[k] for k in sorted(v)}
    if lien:
        corps['message'] = lien['message_ref']
    with _transaction(desk):
        empreinte, deja = _commande(desk, cabinet, a, 'creer', cle, corps)
        if deja:
            return deja
        ref = v['reference'] or proposer_reference(desk)
        if ref in _refs_existantes(desk, cabinet):
            raise Refus('reference_deja_utilisee', {'reference': 'Cette référence est déjà utilisée : choisissez-en une autre.',
                                                    'proposition': proposer_reference(desk)})
        chemin = v['chemin_demande'] if v['stockage_mode'] == 'rattacher' else emplacement_propose(desk, v['titre'], ref)
        _chemin_libre(desk, chemin)
        if lien:
            _message_libre(desk, a, lien)
        ident = uuid.uuid4().hex
        quand = _maintenant()
        desk.db.execute('INSERT INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,client,client_etat,nature,statut,responsable,provenance,'
                        'confidentialite,juridiction,rg,chemin,stockage_mode,stockage_etat,version,auteur,cree_le,modifie_le,modifie_par) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, cabinet, ref, ref, v['titre'], v['client'], v['client_etat'], v['nature'], 'actif', v['responsable'],
                         'courriel' if lien and v['provenance'] == 'manuel' else v['provenance'], v['confidentialite'], v['juridiction'], v['rg'],
                         chemin, v['stockage_mode'], 'a_preparer', 1, a.compte, quand, quand, a.compte))
        for p in v['parties']:
            _ajouter_partie(desk, ident, p)
        _alias(desk, 'ref', ref, ident)
        cle_rangement = 'rangement:%s:1' % ident
        desk.db.execute('INSERT INTO dossier_outbox5697(id,dossier_id,kind,operation_key,args,etat,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, ident, 'storage', cle_rangement,
                         json.dumps({'mode': v['stockage_mode'], 'chemin': chemin}, ensure_ascii=False), 'a_faire', quand, quand))
        if lien:
            _inserer_lien(desk, a, ident, lien, quand)
        if v['confidentialite'] == 'reserve':
            from .membres5650 import set_reserve
            set_reserve(desk.c['state_dir'], ident, v['membres'])   # avant la validation : le dossier n'est jamais visible d'un autre membre
        recu = _recu(desk, ident, 'creer', cle, quand)
        _journal(desk, ident, a.compte, 'creation', {'reference': ref, 'provenance': v['provenance'], 'stockage': v['stockage_mode'],
                                                     'message': bool(lien)})
        _enregistrer_commande(desk, cabinet, a, 'creer', cle, empreinte, recu)
    projeter(desk, ident)
    return recu


def _conteneurs(desk):
    """5.6.98 : racines configurées, racine des dossiers et leurs parents. Un ancien dossier dont le chemin est l'un d'eux (dossier
    « Cabinet » posé sur la racine, import d'un répertoire parent) ne possède pas pour autant tous les répertoires créés dessous."""
    nc = desk.c.get('nextcloud') or {}
    bases = []
    for r in list(nc.get('roots') or []) + list(nc.get('matter_roots') or []):
        try:
            bases.append(clean_path(str(r)))
        except Stop:
            continue
    try:
        bases.append(racine(desk))
    except Stop:
        pass
    return bases


def _occupe(chemin, existant, bases):
    try:
        existant = clean_path(str(existant))
    except Stop:
        return False
    if existant == chemin:
        return True
    if any(under(b, existant) for b in bases):   # la racine elle-même ou un parent : simple conteneur
        return False
    return under(chemin, existant) or under(existant, chemin)


def _chemin_libre(desk, chemin):
    """Refuse un répertoire déjà pris par un dossier : le même, un répertoire placé dans un dossier, ou un répertoire qui contient un dossier.
    Les conteneurs (racines, racine des dossiers, leurs parents) ne comptent pas (5.6.98 : un ancien dossier posé sur la racine bloquait
    toute création)."""
    chemin = clean_path(chemin)
    bases = _conteneurs(desk)
    message = {'emplacement': 'Ce répertoire est déjà celui d’un autre dossier (ou se trouve à l’intérieur) : modifiez l’intitulé, '
                              'la référence ou le répertoire choisi.'}
    for row in desk.db.execute('SELECT id,chemin FROM dossiers5697 WHERE chemin<>?', ('',)):
        if _occupe(chemin, row['chemin'], bases):
            raise Refus('emplacement_deja_utilise', message)
    connus = {r[0] for r in desk.db.execute('SELECT id FROM dossiers5697')}
    for m in _registre_json(desk):
        if m.get('id') in connus or not m.get('path'):
            continue
        if _occupe(chemin, m['path'], bases):
            raise Refus('emplacement_deja_utilise', message)


def _ajouter_partie(desk, ident, p):
    desk.db.execute('INSERT OR IGNORE INTO dossier_parties5697(id,dossier_id,nom,nom_norm,role,courriel,contact_id,cree_le) VALUES(?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, ident, p['nom'], _plier(p['nom']), p['role'], p.get('courriel', ''), p.get('contact_id', ''), _maintenant()))


def _alias(desk, genre, alias, ident):
    alias = str(alias or '').strip()
    if not alias:
        return
    row = desk.db.execute('SELECT dossier_id FROM dossier_alias5697 WHERE genre=? AND alias=?', (genre, alias)).fetchone()
    if row and row['dossier_id'] != ident:
        raise Refus('alias_en_conflit', {'alias': alias})
    desk.db.execute('INSERT OR IGNORE INTO dossier_alias5697(genre,alias,dossier_id,cree_le) VALUES(?,?,?,?)', (genre, alias, ident, _maintenant()))


def _recu(desk, ident, operation, cle, quand):
    row = desk.db.execute('SELECT * FROM dossiers5697 WHERE id=?', (ident,)).fetchone()
    return {'id': ident, 'version': row['version'], 'reference': row['ref'], 'titre': row['titre'], 'url': '/dossier?id=' + ident,
            'stockage': {'etat': row['stockage_etat'], 'libelle': STOCKAGE.get(row['stockage_etat'], ''), 'chemin': row['chemin'],
                         'motif': row['stockage_motif']},
            'message': 'Dossier créé · rangement en préparation' if operation == 'creer' else 'Enregistré',
            'recu': {'operation': operation, 'cle': cle, 'quand': quand}}


# ------------------------------------------------------------------------------------------------ lecture
def resoudre(desk, ident):
    """Identifiant courant d'un dossier depuis son id, un ancien identifiant, une référence ou un ancien lien (alias)."""
    ensure_schema(desk)
    ident = str(ident or '').strip()
    if not ident or len(ident) > 200:
        return ''
    if desk.db.execute('SELECT 1 FROM dossiers5697 WHERE id=?', (ident,)).fetchone():
        return ident
    for genre in ('id', 'ref', 'chemin', 'mission', 'mail'):
        row = desk.db.execute('SELECT dossier_id FROM dossier_alias5697 WHERE genre=? AND alias=?',
                              (genre, (normaliser_reference(ident) or ident) if genre == 'ref' else ident)).fetchone()
        if row:
            return row['dossier_id']
    return ''


def _vue(desk, row, parties=True):
    out = {k: row[k] for k in row.keys() if k not in ('cabinet_id', 'ref_norm')}
    out['reference'] = row['ref']
    out['nature_libelle'] = NATURES.get(row['nature'], row['nature'])
    out['statut_libelle'] = STATUTS.get(row['statut'], row['statut'])
    out['stockage'] = {'etat': row['stockage_etat'], 'libelle': STOCKAGE.get(row['stockage_etat'], ''), 'chemin': row['chemin'],
                       'motif': row['stockage_motif'], 'mode': row['stockage_mode']}
    if parties:
        out['parties'] = [dict(p) for p in desk.db.execute(
            'SELECT id,nom,role,courriel,contact_id FROM dossier_parties5697 WHERE dossier_id=? ORDER BY role,nom_norm', (row['id'],))]
    return out


def lire(desk, acteur, ident):
    """Fiche du registre ; dossier absent ou réservé : ``Stop('introuvable')`` (même réponse, rien n'est divulgué)."""
    ensure_schema(desk)
    a = _acteur(acteur)
    courant = resoudre(desk, ident)
    if not courant or not _visible(desk, a, courant):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM dossiers5697 WHERE id=?', (courant,)).fetchone()
    if not row:
        raise Stop('introuvable')
    return _vue(desk, row)


def _virtuels(desk, perimetre):
    """Avant la migration : dossiers du registre JSON pas encore importés, en lecture seule (mêmes droits, jamais d'écriture)."""
    connus = {r[0] for r in desk.db.execute('SELECT id FROM dossiers5697')}
    out = []
    from .bruit5668 import depot_folder
    for m in _registre_json(desk):
        mid = str(m.get('id') or '')
        if not mid or mid in connus or mid not in perimetre or depot_folder(m):   # 5.6.99 : un répertoire de dépôt n'est pas un dossier
            continue
        from .common import matter_display
        out.append({'id': mid, 'reference': mid, 'ref': mid, 'titre': matter_display(m), 'client': str(m.get('client_name') or ''),
                    'client_etat': 'identifie' if m.get('client_name') else 'a_identifier', 'nature': _nature_devinee(m.get('path', '')),
                    'nature_libelle': NATURES[_nature_devinee(m.get('path', ''))], 'statut': 'actif', 'statut_libelle': STATUTS['actif'],
                    'responsable': '', 'provenance': 'non_importe', 'confidentialite': 'cabinet', 'juridiction': '', 'rg': '', 'resume': '',
                    'chemin': m.get('path', ''), 'version': 0, 'modifie_le': '', 'cree_le': '',
                    'stockage': {'etat': 'pret', 'libelle': STOCKAGE['pret'], 'chemin': m.get('path', ''), 'motif': '', 'mode': 'rattacher'}})
    return out


def lister(desk, acteur, filtres=None, limite=200):
    """Dossiers visibles de l'acteur : droits joints AVANT recherche, tri et LIMIT. Filtres : ``q``, ``mes`` (responsable = acteur),
    ``statut`` (actif / archive / tous), ``nature``, ``responsable``."""
    ensure_schema(desk)
    from .acces5697 import dossiers_autorises
    a = _acteur(acteur)
    f = filtres or {}
    perimetre = dossiers_autorises(desk, a)
    clause, params = perimetre.sql('d.id')
    where, args = [clause], list(params)
    statut = str(f.get('statut') or 'actif')
    if statut in STATUTS:
        where.append('d.statut=?')
        args.append(statut)
    if f.get('nature') in NATURES:
        where.append('d.nature=?')
        args.append(f['nature'])
    if f.get('mes'):
        where.append('d.responsable=?')
        args.append(a.compte)
    elif f.get('responsable'):
        where.append('d.responsable=?')
        args.append(str(f['responsable'])[:120])
    rows = desk.db.execute('SELECT d.* FROM dossiers5697 d WHERE %s ORDER BY d.modifie_le DESC, d.id' % ' AND '.join(where), args).fetchall()
    q = _plier(f.get('q') or '')[:120]
    out = []
    from .bruit5668 import depot_label
    for row in rows:
        if depot_label(row['chemin']) or depot_label(row['titre']):   # 5.6.99 : répertoire de dépôt importé avant la 5.6.99 : hors de la liste
            continue
        if q:
            noms = ' '.join(p[0] for p in desk.db.execute('SELECT nom FROM dossier_parties5697 WHERE dossier_id=?', (row['id'],)))
            if q not in _plier(' '.join((row['titre'], row['ref'], row['client'], row['rg'], noms))):
                continue
        out.append(_vue(desk, row, parties=False))
        if len(out) >= max(1, min(int(limite or 200), 5000)):
            return out
    if not migration_faite(desk) and statut != 'archive' and not f.get('nature') and not f.get('mes') and not f.get('responsable'):
        for v in _virtuels(desk, perimetre):
            if q and q not in _plier(' '.join((v['titre'], v['ref'], v['client']))):
                continue
            out.append(v)
            if len(out) >= max(1, min(int(limite or 200), 5000)):
                break
    return out


def journal(desk, acteur, ident, limite=50):
    dossier = lire(desk, acteur, ident)
    return [dict(r) for r in desk.db.execute('SELECT quand,acteur,action,details FROM dossier_journal5697 WHERE dossier_id=? ORDER BY id DESC LIMIT ?',
                                             (dossier['id'], int(limite)))]


# ------------------------------------------------------------------------------------------------ modification
def modifier(desk, acteur, ident, version, changements, cle):
    """Modification avec version attendue (périmée : 409 ``version_perimee``) et clé d'idempotence. Une référence changée garde
    l'ancienne comme alias (anciens liens valables)."""
    ensure_schema(desk)
    a = _acteur(acteur)
    _peut_ecrire(a)
    courant = resoudre(desk, ident)
    if not courant or not _visible(desk, a, courant):
        raise Stop('introuvable')
    ch = {k: changements[k] for k in CHAMPS_MODIFIABLES if isinstance(changements, dict) and k in changements}
    if not ch:
        raise Refus('champs_invalides', {'': 'Aucune modification.'})
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise Refus('champs_invalides', {'version': 'Version attendue manquante.'}) from None
    if ch.get('statut') == 'archive' and a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    cabinet = _cabinet(desk)
    with _transaction(desk):
        empreinte, deja = _commande(desk, cabinet, a, 'modifier:' + courant, cle, {'version': version, **ch})
        if deja:
            return deja
        row = desk.db.execute('SELECT * FROM dossiers5697 WHERE id=?', (courant,)).fetchone()
        if row['version'] != version:
            raise Stop('version_perimee')
        base = {'titre': row['titre'], 'client': row['client'], 'client_etat': row['client_etat'], 'nature': row['nature'],
                'responsable': row['responsable'], 'juridiction': row['juridiction'], 'rg': row['rg'], 'reference': row['ref'],
                'confidentialite': row['confidentialite']}
        v = valider(desk, {**base, **ch, 'emplacement': {'mode': 'creer'}}, a)
        maj = {}
        for k in ('titre', 'client', 'client_etat', 'nature', 'responsable', 'juridiction', 'rg'):
            if k in ch and v[k] != row[k]:
                maj[k] = v[k]
        if 'statut' in ch:
            if ch['statut'] not in STATUTS:
                raise Refus('champs_invalides', {'statut': 'Statut inconnu.'})
            if ch['statut'] != row['statut']:
                maj['statut'] = ch['statut']
        if 'resume' in ch:
            try:
                resume = _texte(str(ch['resume']).replace('\n', ' '), 4000)
            except ValueError:
                raise Refus('champs_invalides', {'resume': 'Résumé trop long.'}) from None
            if resume != row['resume']:
                maj['resume'] = resume
        if 'reference' in ch and v['reference'] and v['reference'] != row['ref']:
            if v['reference'] in _refs_existantes(desk, cabinet):
                raise Refus('reference_deja_utilisee', {'reference': 'Cette référence est déjà utilisée.'})
            _alias(desk, 'ref', row['ref'], courant)
            _alias(desk, 'ref', v['reference'], courant)
            maj['ref'] = maj['ref_norm'] = v['reference']
        quand = _maintenant()
        if maj:
            sets = ', '.join('%s=?' % k for k in maj)
            desk.db.execute('UPDATE dossiers5697 SET %s, version=version+1, modifie_le=?, modifie_par=? WHERE id=? AND version=?' % sets,
                            list(maj.values()) + [quand, a.compte, courant, version])
            _journal(desk, courant, a.compte, 'modification', {'champs': sorted(k for k in maj if k != 'ref_norm'), 'version': version + 1})
        recu = _recu(desk, courant, 'modifier', cle, quand)
        _enregistrer_commande(desk, cabinet, a, 'modifier:' + courant, cle, empreinte, recu)
    projeter(desk, courant)
    return recu


# ------------------------------------------------------------------------------------------------ courriels (§ 11)
def reference_message(compte, boite, uidvalidity, uid, message_id='', empreinte=''):
    """Identité stable d'un message : compte + Message-ID (ou empreinte des octets) ; à défaut compte + boîte + UIDVALIDITY + UID.
    Jamais l'UID seul ni l'objet seul."""
    compte = str(compte or '')
    if message_id:
        return 'mid:' + _sha('%s|%s' % (compte, str(message_id).strip().strip('<>').lower()))[:40]
    if empreinte:
        return 'sha:' + _sha('%s|%s' % (compte, empreinte))[:40]
    if not (boite and str(uidvalidity or '').strip() and str(uid or '').isdigit()):
        raise Refus('message_invalide', {'message': 'Message incomplet : boîte, UIDVALIDITY et UID requis.'})
    return 'uid:' + _sha('%s|%s|%s|%s' % (compte, boite, uidvalidity, uid))[:40]


def _message_normalise(desk, message):
    if not isinstance(message, dict):
        raise Refus('message_invalide', {'message': 'Message absent.'})
    from .messages5697 import cle_canonique, compte_de
    compte = str(message.get('compte') or '') or compte_de(desk.c.get('mail'))   # « utilisateur@hôte », comme la clé des messages (lot proteger)
    m = {'compte': compte[:120], 'boite': str(message.get('boite') or message.get('folder') or 'INBOX')[:200],
         'uidvalidity': str(message.get('uidvalidity') or '')[:40], 'uid': str(message.get('uid') or '')[:20],
         'message_id': str(message.get('message_id') or '').strip()[:300], 'empreinte': str(message.get('empreinte') or '')[:64],
         'sujet': str(message.get('sujet') or '')[:300], 'expediteur': str(message.get('expediteur') or '')[:300],
         'date_message': str(message.get('date') or '')[:40]}
    if m['uid'] and not m['uid'].isdigit():
        raise Refus('message_invalide', {'message': 'UID invalide.'})
    m['message_ref'] = reference_message(m['compte'], m['boite'], m['uidvalidity'], m['uid'], m['message_id'], m['empreinte'])
    m['mail_key'] = cle_canonique(m['compte'], m['boite'], m['uidvalidity'], m['uid']) if (m['uidvalidity'] and m['uid']) else ''
    return m


def _message_libre(desk, a, lien):
    """Un message déjà classé dans un dossier que l'acteur ne voit pas : refus neutre (le rattachement ne donne aucun droit)."""
    row = desk.db.execute('SELECT dossier_id FROM message_dossier5697 WHERE message_ref=?', (lien['message_ref'],)).fetchone()
    if row and not _visible(desk, a, row['dossier_id']):
        raise Stop('introuvable')
    if lien['mail_key']:
        from .acces5697 import exiger
        exiger(desk, a, ref=lien['mail_key'])
    return row


def _inserer_lien(desk, a, ident, lien, quand, options=None, provenance='manuel'):
    desk.db.execute('INSERT INTO message_dossier5697(message_ref,dossier_id,mail_key,compte,boite,uidvalidity,uid,message_id,empreinte,sujet,expediteur,'
                    'date_message,provenance,options,version,auteur,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)',
                    (lien['message_ref'], ident, lien['mail_key'], lien['compte'], lien['boite'], lien['uidvalidity'], lien['uid'], lien['message_id'],
                     lien['empreinte'], lien['sujet'], lien['expediteur'], lien['date_message'], provenance,
                     json.dumps(options or {}, ensure_ascii=False), a.compte, quand, quand))
    _journal(desk, ident, a.compte, 'courriel_classe', {'message_ref': lien['message_ref'], 'provenance': provenance})


def rattacher_message(desk, acteur, ident, message, cle, version=None, motif='', options=None):
    """Classe un message dans un dossier (choix de l'avocat, prioritaire sur toute proposition). Aucun déplacement IMAP, aucun envoi.
    Message déjà classé ailleurs : ``version`` attendue exigée (Modifier le classement), ancien lien journalisé.
    Rend ``{'dossier', 'message_ref', 'version', 'etat': 'classe'|'deja_classe'|'reclasse', 'message': 'Classé dans …'}``."""
    ensure_schema(desk)
    a = _acteur(acteur)
    _peut_ecrire(a)
    courant = resoudre(desk, ident)
    if not courant or not _visible(desk, a, courant):
        raise Stop('introuvable')
    lien = _message_normalise(desk, message)
    from .acces5697 import exiger   # B01 : garde commune ; un message rattaché (par n'importe quel registre) à un dossier invisible reste invisible
    exiger(desk, a, dossier_id=courant, ref=lien['mail_key'] or None)
    opts = {k: bool((options or {}).get(k)) for k in ('archiver', 'pieces_jointes')}
    cabinet = _cabinet(desk)
    with _transaction(desk):
        empreinte, deja = _commande(desk, cabinet, a, 'classer', cle, {'dossier': courant, 'message': lien['message_ref'], 'version': version,
                                                                      'options': opts})
        if deja:
            return deja
        row = desk.db.execute('SELECT * FROM message_dossier5697 WHERE message_ref=?', (lien['message_ref'],)).fetchone()
        if row and not _visible(desk, a, row['dossier_id']):
            raise Stop('introuvable')
        quand = _maintenant()
        if row is None:
            _inserer_lien(desk, a, courant, lien, quand, opts)
            etat, nv = 'classe', 1
        elif row['dossier_id'] == courant:
            etat, nv = 'deja_classe', row['version']
            if lien['mail_key'] and lien['mail_key'] != row['mail_key']:   # même message (Message-ID) retrouvé ailleurs : emplacement réconcilié
                desk.db.execute('UPDATE message_dossier5697 SET mail_key=?, boite=?, uidvalidity=?, uid=?, modifie_le=? WHERE message_ref=?',
                                (lien['mail_key'], lien['boite'], lien['uidvalidity'], lien['uid'], quand, lien['message_ref']))
        else:
            if version is None or int(version) != row['version']:
                raise Stop('version_perimee')
            desk.db.execute('UPDATE message_dossier5697 SET dossier_id=?, version=version+1, provenance=?, options=?, auteur=?, modifie_le=?, '
                            "mail_key=CASE WHEN ?<>'' THEN ? ELSE mail_key END WHERE message_ref=? AND version=?",
                            (courant, 'manuel', json.dumps(opts), a.compte, quand, lien['mail_key'], lien['mail_key'], lien['message_ref'], row['version']))
            _journal(desk, row['dossier_id'], a.compte, 'courriel_declasse', {'message_ref': lien['message_ref'], 'vers': courant,
                                                                              'motif': str(motif or '')[:300]})
            _journal(desk, courant, a.compte, 'courriel_reclasse', {'message_ref': lien['message_ref'], 'depuis': row['dossier_id'],
                                                                    'motif': str(motif or '')[:300]})
            etat, nv = 'reclasse', row['version'] + 1
        d = desk.db.execute('SELECT titre,ref FROM dossiers5697 WHERE id=?', (courant,)).fetchone()
        recu = {'dossier': courant, 'message_ref': lien['message_ref'], 'version': nv, 'etat': etat,
                'message': 'Classé dans %s (%s)' % (d['titre'], d['ref']), 'url': '/dossier?id=%s&onglet=correspondances' % courant,
                'imap': 'Aucun déplacement dans la boîte de courriel.', 'recu': {'operation': 'classer', 'cle': cle, 'quand': quand}}
        _enregistrer_commande(desk, cabinet, a, 'classer', cle, empreinte, recu)
    if etat != 'deja_classe':
        try:   # 5.6.97 (lot gestion) : nouvelle entrée sur un dossier archivé, signalée au responsable sans rouvrir le dossier
            from .cloture5697 import signaler_entree
            signaler_entree(desk, courant, 'courriel', lien['message_ref'])
        except Stop:
            pass
    return recu


def dossier_du_message(desk, compte='', boite='', uidvalidity='', uid='', message_id='', empreinte=''):
    """Dossier choisi à la main pour ce message (priorité sur toute proposition de l'analyse) ; ``''`` si aucun."""
    ensure_schema(desk)
    if not compte:
        from .messages5697 import compte_de
        compte = compte_de(desk.c.get('mail'))
    refs = []
    for args in ((compte, boite, uidvalidity, uid, message_id, ''), (compte, boite, uidvalidity, uid, '', empreinte),
                 (compte, boite, uidvalidity, uid, '', '')):
        try:
            refs.append(reference_message(*args))
        except Stop:
            continue
    for ref in refs:
        row = desk.db.execute('SELECT dossier_id FROM message_dossier5697 WHERE message_ref=?', (ref,)).fetchone()
        if row:
            return row['dossier_id']
    if uid and boite and uidvalidity:
        row = desk.db.execute('SELECT dossier_id FROM message_dossier5697 WHERE compte=? AND boite=? AND uidvalidity=? AND uid=?',
                              (compte, boite, str(uidvalidity), str(uid))).fetchone()
        if row:
            return row['dossier_id']
    return ''


def rattachements_messages(desk, cles):
    """Pour le résolveur des messages (lot proteger, ``messages5697``) : {clé du message: {dossier}} choisis à la main dans ce registre."""
    ensure_schema(desk)
    cles = [str(c) for c in cles or () if c]
    out = {}
    for i in range(0, len(cles), 400):
        lot = cles[i:i + 400]
        for row in desk.db.execute('SELECT mail_key, dossier_id FROM message_dossier5697 WHERE mail_key IN (%s)' % ','.join('?' * len(lot)), lot):
            out.setdefault(row['mail_key'], set()).add(row['dossier_id'])
    return out


def messages_du_dossier(desk, acteur, ident, limite=200):
    dossier = lire(desk, acteur, ident)
    return [dict(r) for r in desk.db.execute('SELECT message_ref,boite,uid,message_id,sujet,expediteur,date_message,provenance,options,version,auteur,'
                                             'cree_le,modifie_le FROM message_dossier5697 WHERE dossier_id=? ORDER BY date_message DESC, cree_le DESC LIMIT ?',
                                             (dossier['id'], int(limite)))]


# ------------------------------------------------------------------------------------------------ projection JSON (pages existantes)
class verrou_registre:
    """Verrou entre processus autour du cycle lecture–vérification–écriture du registre JSON (B03, correctif immédiat)."""

    _tenus = threading.local()   # réentrant dans un même fil : vérification puis enregistrement sous le même verrou

    def __init__(self, state_dir, attente=10):
        self.path = Path(str(state_dir)) / 'registry-web.verrou'
        self.attente = attente

    def __enter__(self):
        tenus = self._tenus.__dict__.setdefault('compte', {})
        if tenus.get(str(self.path)):
            tenus[str(self.path)] += 1
            return self
        limite = time.time() + self.attente
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                tenus[str(self.path)] = 1
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 60:   # verrou abandonné par un processus arrêté
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > limite:
                    raise Stop('registre_dossiers_occupe') from None
                time.sleep(0.02)

    def __exit__(self, *exc):
        tenus = self._tenus.__dict__.setdefault('compte', {})
        tenus[str(self.path)] = tenus.get(str(self.path), 1) - 1
        if tenus[str(self.path)] > 0:
            return
        tenus.pop(str(self.path), None)
        try:
            self.path.unlink()
        except OSError:
            pass


def projeter(desk, ident):
    """Écrit (ou met à jour) l'entrée du dossier dans le registre JSON lu par les pages existantes ; jamais d'autre entrée modifiée."""
    row = desk.db.execute('SELECT * FROM dossiers5697 WHERE id=?', (ident,)).fetchone()
    if not row or not row['chemin']:
        return
    try:
        existant = next((m for m in _registre_json(desk) if m.get('id') == ident), {})
    except Stop:
        existant = {}
    refs = [row['ref']] + [r for r in (existant.get('references') or []) if r != row['ref']]
    if row['rg'] and row['rg'] not in refs:
        refs.append(row['rg'])
    corr = list(existant.get('correspondents') or [])
    entree = {**existant, 'id': ident, 'path': row['chemin'], 'client_name': row['client'] or row['titre'],
              'aliases': list(dict.fromkeys([row['titre']] + list(existant.get('aliases') or []))), 'references': list(dict.fromkeys(refs))[:20],
              'correspondents': corr, 'registre5697': {'reference': row['ref'], 'nature': row['nature'], 'statut': row['statut'], 'version': row['version']}}
    if entree != existant:
        from .desk import save_matter
        save_matter(desk.c, entree)


# ------------------------------------------------------------------------------------------------ rangement (outbox)
def _client_stockage(desk):
    from .office440 import dav_client
    return dav_client(desk)


def _existe(client, chemin):
    try:
        client.list_folder(chemin)
        return True
    except Stop as ex:
        if str(ex) in ('fichier_nextcloud_introuvable', 'repertoire_absent', 'http_404', 'chemin_absent') or 'introuvable' in str(ex) or '404' in str(ex):
            return False
        raise


def traiter_rangements(desk, client=None, dossier=None, limite=5):
    """Exécute les opérations de rangement en attente : bail court en transaction, effet après validation, relecture avant tout rejeu.
    Un répertoire déjà présent (panne après MKCOL) est retrouvé, jamais recréé ; rien n'est supprimé. Stockage injoignable : état
    « Rangement en attente » avec motif, l'affaire reste utilisable."""
    ensure_schema(desk)
    from .pause5629 import checkpoint
    faits = []
    for _ in range(max(1, int(limite))):
        quand = _maintenant()
        with _transaction(desk):
            sql = ("SELECT * FROM dossier_outbox5697 WHERE kind='storage' AND etat IN ('a_faire','en_attente','en_cours') AND (bail='' OR bail<?)"
                   + (' AND dossier_id=?' if dossier else '') + ' ORDER BY cree_le LIMIT 1')
            op = desk.db.execute(sql, (quand,) + ((dossier,) if dossier else ())).fetchone()
            if not op:
                break
            bail = (datetime.now(timezone.utc) + timedelta(seconds=BAIL_S)).isoformat(timespec='seconds')
            desk.db.execute("UPDATE dossier_outbox5697 SET etat='en_cours', bail=?, tentatives=tentatives+1, modifie_le=? WHERE id=?",
                            (bail, quand, op['id']))
            desk.db.execute("UPDATE dossiers5697 SET stockage_etat='en_cours' WHERE id=?", (op['dossier_id'],))
        faits.append(_ranger(desk, op, client, checkpoint))
    return faits


def _ranger(desk, op, client, checkpoint):
    args = json.loads(op['args'] or '{}')
    chemin, mode = clean_path(args['chemin']), args.get('mode', 'creer')
    resultat, etat, motif = {}, 'pret', ''
    try:
        checkpoint(desk)
        client = client or _client_stockage(desk)
        present = _existe(client, chemin)
        if mode == 'rattacher':
            if not present:
                raise Stop('repertoire_a_rattacher_absent')
            resultat['repertoire'] = 'rattache'
        elif present:
            resultat['repertoire'] = 'retrouve'   # reprise après une panne survenue après MKCOL : aucun nouveau répertoire
        else:
            client.create_folder(chemin)
            resultat['repertoire'] = 'cree'
        resultat['sous_dossiers'] = _sous_dossiers(desk, op['dossier_id'], chemin, client)
    except Stop as ex:
        code = str(ex).split(':')[0]
        etat = 'a_verifier' if code in ('repertoire_a_rattacher_absent', 'creation_racine_refusee', 'fichier_hors_racines') else 'en_attente'
        motif = MOTIFS.get(code, 'Stockage indisponible pour le moment : le rangement reprendra.')
        resultat['erreur'] = code
    except OSError:
        etat, motif, resultat['erreur'] = 'en_attente', MOTIFS['stockage_injoignable'], 'stockage_injoignable'
    quand = _maintenant()
    with _transaction(desk):
        # Échec : nouvel essai après une attente croissante (1 min, 2, 4… 1 h au plus), jamais dans le même passage.
        attente = '' if etat == 'pret' else (datetime.now(timezone.utc) + timedelta(seconds=min(3600, 60 * 2 ** min(6, op['tentatives'])))).isoformat(timespec='seconds')
        desk.db.execute('UPDATE dossier_outbox5697 SET etat=?, bail=?, motif=?, resultat=?, modifie_le=? WHERE id=?',
                        ('fait' if etat == 'pret' else etat, attente, motif, json.dumps(resultat), quand, op['id']))
        desk.db.execute('UPDATE dossiers5697 SET stockage_etat=?, stockage_motif=? WHERE id=?', (etat, motif, op['dossier_id']))
        _journal(desk, op['dossier_id'], 'rangement', 'rangement_' + etat, resultat)
    if etat == 'pret':
        projeter(desk, op['dossier_id'])
    return {'dossier': op['dossier_id'], 'etat': etat, 'motif': motif, **resultat}


MOTIFS = {'repertoire_a_rattacher_absent': 'Le répertoire à rattacher est introuvable : vérifiez son emplacement.',
          'stockage_injoignable': 'Stockage injoignable : le rangement reprendra automatiquement.',
          'creation_racine_refusee': 'Emplacement refusé : choisissez un répertoire sous la racine des dossiers.',
          'fichier_hors_racines': 'Emplacement hors des racines autorisées.',
          'capacite_revoquee': 'Écriture dans le stockage suspendue par le cabinet : le rangement reprendra après rétablissement.'}


def _sous_dossiers(desk, ident, chemin, client):
    """Sous-dossiers configurés (PROCEDURE, CORRESPONDANCES, PROJETS, LIVRABLES, puis NOS PIECES, FACTURATION…) : seuls les absents sont créés."""
    from .docrequest520 import folders
    noms = [n for n in folders(desk).values() if isinstance(n, str) and n]
    try:
        from .dossiers5632 import resolve
        m = {'id': ident, 'path': chemin, 'client_name': ''}
        noms += [r['name'] for r in resolve(desk, m) if r.get('name')]
    except Exception:   # noqa: BLE001 — un réglage de sous-dossiers illisible n'empêche jamais l'affaire d'exister
        pass
    crees = []
    try:
        presents = {_plier(PurePosixPath(i['path']).name) for i in client.list_folder(chemin) if i.get('directory')}
    except Stop:
        presents = set()
    for nom in dict.fromkeys(noms):
        if _plier(nom) in presents or '/' in nom:
            continue
        client.ensure_folder(clean_path(chemin + '/' + nom), chemin)
        crees.append(nom)
    return crees


def reprendre_rangement(desk, acteur, ident, cle):
    """Bouton « Reprendre le rangement » : remet l'opération du MÊME dossier en file (aucun nouvel identifiant, aucun nouveau répertoire)."""
    ensure_schema(desk)
    a = _acteur(acteur)
    _peut_ecrire(a)
    courant = resoudre(desk, ident)
    if not courant or not _visible(desk, a, courant):
        raise Stop('introuvable')
    cabinet = _cabinet(desk)
    with _transaction(desk):
        empreinte, deja = _commande(desk, cabinet, a, 'reprendre:' + courant, cle, {'dossier': courant})
        if deja:
            return deja
        desk.db.execute("UPDATE dossier_outbox5697 SET etat='a_faire', bail='', modifie_le=? WHERE dossier_id=? AND kind='storage' AND etat<>'fait'",
                        (_maintenant(), courant))
        recu = {'dossier': courant, 'etat': 'a_preparer', 'message': 'Rangement remis en file.'}
        _enregistrer_commande(desk, cabinet, a, 'reprendre:' + courant, cle, empreinte, recu)
    return recu


# ------------------------------------------------------------------------------------------------ migration des dossiers existants (§ 10)
def _nature_devinee(texte):
    t = _plier(texte)
    if 'contentieux' in t or 'postulation' in t:
        return 'contentieux'
    if 'conseil' in t or 'assistance' in t:
        return 'conseil'
    return 'a_qualifier'


ROLES_CORRESPONDANTS = {'client': 'client', 'confrere_adverse': 'conseil_adverse', 'juridiction': 'juridiction', 'expert': 'expert'}


def _etat_portefeuille(desk, mid):
    try:
        row = desk.db.execute('SELECT state FROM matter_portfolio WHERE matter=?', (mid,)).fetchone()
        return row[0] if row else ''
    except Exception:   # noqa: BLE001 — table absente : dossier réputé actif
        return ''


def _refs_sqlite(desk):
    return ({r[0] for r in desk.db.execute('SELECT ref_norm FROM dossiers5697')}
            | {r[0] for r in desk.db.execute("SELECT alias FROM dossier_alias5697 WHERE genre='ref'")})


def _importer(desk, m, reserves, refs_prises, rapport, ecrire, cabinet, auteur):
    """Un dossier JSON (enregistré ou détecté) → une ligne du registre, par son identifiant existant et son chemin canonique.
    Aucune fusion silencieuse : un conflit est signalé et la ligne n'est pas importée."""
    mid = str(m.get('id') or '')
    try:
        chemin = clean_path(str(m.get('path') or ''))
    except Stop:
        rapport['conflits'].append({'id': mid, 'motif': 'chemin_invalide'})
        return
    if not ID_OK.fullmatch(mid):
        rapport['conflits'].append({'id': mid, 'motif': 'identifiant_invalide'})
        return
    row = desk.db.execute('SELECT id,chemin FROM dossiers5697 WHERE id=?', (mid,)).fetchone()
    if row:
        if row['chemin'] != chemin:
            rapport['conflits'].append({'id': mid, 'motif': 'chemin_different', 'registre': row['chemin'], 'json': chemin})
        else:
            rapport['deja_importes'] += 1
        return
    autre = desk.db.execute('SELECT id FROM dossiers5697 WHERE chemin=?', (chemin,)).fetchone()
    if autre:
        rapport['conflits'].append({'id': mid, 'motif': 'chemin_deja_rattache', 'autre': autre['id']})
        return
    ref = normaliser_reference(mid)
    note = ''
    if not ref or ref in refs_prises:
        ref = normaliser_reference('M-' + _sha(mid)[:10])
        note = 'reference_reecrite'
    refs_prises.add(ref)
    from .common import matter_display
    titre = matter_display(m)[:160]
    entree = {'id': mid, 'reference': ref, 'titre': titre, 'chemin': chemin, 'note': note,
              'confidentialite': 'reserve' if mid in reserves else 'cabinet'}
    rapport['a_importer'].append(entree)
    if not ecrire:
        return
    quand = _maintenant()
    client = re.sub(r'\s+', ' ', str(m.get('client_name') or '')).strip()[:160]
    provenance = 'detecte' if m.get('discovered_at') else 'migre'
    desk.db.execute('INSERT INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,client,client_etat,nature,statut,responsable,provenance,'
                    'confidentialite,chemin,stockage_mode,stockage_etat,version,auteur,cree_le,modifie_le,modifie_par) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (mid, cabinet, ref, ref, titre, client, 'identifie' if client else 'a_identifier', _nature_devinee(chemin),
                     'archive' if _etat_portefeuille(desk, mid) == 'archived' else 'actif', '', provenance, entree['confidentialite'],
                     chemin, 'rattacher', 'pret', 1, auteur, str(m.get('registered_at') or m.get('discovered_at') or quand)[:40], quand, auteur))
    if client:
        _ajouter_partie(desk, mid, {'nom': client, 'role': 'client'})
    for c in m.get('correspondents') or []:
        courriel = str(c.get('email') or '').strip().lower()[:254]
        if client and c.get('role') == 'client' and not c.get('name'):   # adresse du client : sur sa fiche de partie, pas en doublon
            if desk.db.execute("UPDATE dossier_parties5697 SET courriel=? WHERE dossier_id=? AND role='client' AND courriel=''",
                               (courriel, mid)).rowcount:
                continue
        nom = str(c.get('name') or courriel)[:160]
        if nom:
            _ajouter_partie(desk, mid, {'nom': nom, 'role': ROLES_CORRESPONDANTS.get(c.get('role'), 'correspondant'), 'courriel': courriel})
    for genre, alias in [('ref', ref), ('chemin', chemin)] + [('ref', normaliser_reference(r)) for r in (m.get('references') or [])[:20]]:
        if not alias:
            continue
        pris = desk.db.execute('SELECT dossier_id FROM dossier_alias5697 WHERE genre=? AND alias=?', (genre, alias)).fetchone()
        if pris and pris['dossier_id'] != mid:
            rapport['alias_ignores'].append({'id': mid, 'alias': alias})   # même référence dans deux dossiers : jamais attribuée en silence
            continue
        if not pris:
            _alias(desk, genre, alias, mid)
            rapport['alias'] += 1
    _journal(desk, mid, auteur, 'migration', {'provenance': provenance, 'note': note})


def migrer(desk, acteur, simulation=True):
    """Importe les dossiers existants (JSON du cabinet, enregistrés depuis le navigateur, détectés) dans le registre unique.
    Simulation d'abord ; en réel : sauvegarde des fichiers JSON, import en une transaction, compte rendu conservé. Rejouable : un
    dossier déjà importé est compté, jamais dupliqué ; un conflit est signalé, jamais fusionné. Aucun effet externe."""
    ensure_schema(desk)
    a = _acteur(acteur)
    from .acces5697 import AGENT
    if a != AGENT and a.role != 'administrateur':
        raise Stop('role_insuffisant')
    from .membres5650 import load
    reserves = set(load(desk.c['state_dir'])['reserves'])
    matters = _registre_json(desk)
    cabinet = _cabinet(desk)
    from .bruit5668 import depot_folder
    depots = [str(m.get('id') or '') for m in matters if depot_folder(m)]
    matters = [m for m in matters if not depot_folder(m)]   # 5.6.99 : un répertoire de dépôt (« 09 - Scanner ») n'est jamais importé comme dossier
    rapport = {'simulation': bool(simulation), 'a_importer': [], 'deja_importes': 0, 'conflits': [], 'alias': 0, 'alias_ignores': [],
               'dossiers_lus': len(matters), 'depots_ignores': depots}
    if simulation:
        refs = _refs_sqlite(desk)
        for m in matters:
            _importer(desk, m, reserves, refs, rapport, False, cabinet, a.compte)
        return rapport
    rapport['sauvegarde'] = _sauvegarder_json(desk)
    with _transaction(desk):
        refs = _refs_sqlite(desk)
        for m in matters:
            _importer(desk, m, reserves, refs, rapport, True, cabinet, a.compte)
        desk.db.execute('INSERT INTO dossier_migrations5697(id,quand,acteur,simulation,rapport) VALUES(?,?,?,?,?)',
                        (uuid.uuid4().hex, _maintenant(), a.compte, 0, json.dumps(rapport, ensure_ascii=False)))
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('dossiers5697:migre', json.dumps(_maintenant())))
    return rapport


def _sauvegarder_json(desk, garder=5):
    """Copie des registres JSON avant migration (rétention : les ``garder`` dernières copies)."""
    base = Path(str(desk.c['state_dir'])) / 'migration5697'
    base.mkdir(mode=0o700, exist_ok=True)
    cible = base / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    cible.mkdir(mode=0o700)
    for source in (Path(str(desk.c['matters_file'])), Path(str(desk.c['state_dir'])) / 'registry-web.json'):
        if source.is_file():
            (cible / source.name).write_bytes(source.read_bytes())
    for ancien in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)[:-garder]:
        for f in ancien.iterdir():
            f.unlink()
        ancien.rmdir()
    return cible.name


def importer_un(desk, m, auteur='cabinet'):
    """Un dossier enregistré par l'ancien chemin (Enregistrer un répertoire, découverte) entre aussi au registre unique."""
    ensure_schema(desk)
    from .membres5650 import load
    try:
        reserves = set(load(desk.c['state_dir'])['reserves'])
    except Stop:
        reserves = set()
    rapport = {'a_importer': [], 'deja_importes': 0, 'conflits': [], 'alias': 0, 'alias_ignores': []}
    with _transaction(desk):
        _importer(desk, m, reserves, _refs_sqlite(desk), rapport, True, _cabinet(desk), auteur)
    return rapport


def migration_faite(desk):
    return bool(desk.settings('dossiers5697:migre', ''))


# ------------------------------------------------------------------------------------------------ conflits d'intérêts (conflicts500)
ROLES_CONFLITS = {'client': 'client', 'adverse': 'adverse', 'autre': 'tiers'}
DECISIONS_CREATION = ('accepter', 'a_etudier')


def parties_conflits(parties):
    return [{'name': p['nom'], 'role': ROLES_CONFLITS[p['role']], 'email': p.get('courriel', '')}
            for p in parties if p.get('role') in ROLES_CONFLITS]


def verifier_conflits(desk, acteur, donnees):
    """Recherche de conflits AVANT création (conflicts500, local, sans modèle). Un rapprochement vers un dossier que l'acteur ne voit
    pas est signalé sans en révéler l'intitulé ni les extraits. Les limites de la recherche sont toujours rendues."""
    from . import conflicts500
    from .acces5697 import dossiers_autorises
    a = _acteur(acteur)
    _peut_ecrire(a)
    try:
        v = valider(desk, {**(donnees if isinstance(donnees, dict) else {}), 'titre': (donnees or {}).get('titre') or 'Vérification'}, a)
    except Refus as ex:
        if 'parties' in ex.detail or 'client' in ex.detail:
            raise
        v = {'parties': [], 'client': ''}
    parties = parties_conflits(v['parties'])
    if not parties:
        raise Refus('champs_invalides', {'parties': 'Indiquez au moins le client ou une partie adverse.'})
    out = conflicts500.check(desk, parties, '', 'nouveau_dossier5697')
    perimetre = dossiers_autorises(desk, a)
    for m in out['result']['matches']:
        if m.get('matter') and m['matter'] not in perimetre:
            m.update({'matter': '', 'matter_label': 'Dossier réservé à d’autres membres du cabinet : demandez à un associé.',
                      'other_name': m['party'], 'sources': []})
    partiel = not any(p['role'] == 'adverse' for p in parties)
    return {'id': out['id'], 'statut': out['status'], 'resume': out['result']['summary'], 'correspondances': out['result']['matches'],
            'limites': out['result']['limits'], 'partielle': partiel,
            'avis': 'Recherche partielle : elle ne conclut pas à l’absence de conflit.' if partiel else
                    'Seule votre analyse fait foi : l’absence de résultat n’est pas une garantie.'}


def lier_conflits(desk, acteur, ident, conflit, parties):
    """Après création : rattache la vérification au dossier et enregistre la décision de l'avocat (ou la mise en attente). Sans
    vérification, le dossier reste « conflits à examiner » (production d'actes refusée tant que l'avocat n'a pas décidé)."""
    from . import conflicts500
    from .common import digest
    m5 = conflicts500.m5
    m5.ensure_schema(desk)
    quand = m5.now()
    for p in parties_conflits(parties):
        try:
            desk.db.execute('INSERT OR IGNORE INTO conflicts500_parties VALUES(?,?,?,?,?,?,?,?)',
                            (digest('c500party|%s|%s|%s' % (ident, conflicts500.norm(p['name']), p['role'])), ident, p['name'],
                             conflicts500.norm(p['name']), p['role'], p['email'], 'nouveau_dossier5697', quand))
        except Exception:   # noqa: BLE001 — nom non normalisable : il reste dans les parties du dossier
            continue
    cid = str((conflit or {}).get('id') or '')
    decision = str((conflit or {}).get('decision') or '')
    desk.db.execute('INSERT OR IGNORE INTO conflicts500_matters VALUES(?,?,?,?,?)', (ident, quand, 0, cid, 'a_examiner'))
    desk.db.commit()
    if cid and decision in DECISIONS_CREATION:
        desk.db.execute("UPDATE conflicts500_checks SET matter=? WHERE id=? AND matter=''", (ident, cid))
        desk.db.commit()
        conflicts500.decide(desk, cid, decision, str((conflit or {}).get('motif') or ''))
        _journal(desk, ident, _acteur(acteur).compte, 'conflits_decision', {'decision': decision})
        desk.db.commit()
    return conflicts500.matter_gate_state(desk, ident)


def controler_decision_conflit(desk, conflit):
    """Avant toute écriture : une décision « Poursuivre » sur une recherche qui a trouvé quelque chose exige un motif."""
    if not conflit:
        return
    from . import conflicts500
    cid, decision = str(conflit.get('id') or ''), str(conflit.get('decision') or '')
    if decision not in DECISIONS_CREATION:
        raise Refus('champs_invalides', {'conflit': 'Choisissez « Poursuivre » ou « Mettre en attente ».'})
    try:
        row = conflicts500.get(desk, cid, log=False)
    except Stop:
        raise Refus('champs_invalides', {'conflit': 'Vérification des conflits introuvable : relancez-la.'}) from None
    if row['matter']:
        raise Refus('champs_invalides', {'conflit': 'Cette vérification est déjà rattachée à un dossier.'})
    if decision == 'accepter' and row['status'] != 'aucun_resultat' and len(re.sub(r'\s+', ' ', str(conflit.get('motif') or '')).strip()) < 10:
        raise Refus('champs_invalides', {'conflit': 'Motivez la décision de poursuivre (10 caractères au moins).'})
