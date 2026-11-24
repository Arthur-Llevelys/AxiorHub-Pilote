"""5.6.109 (lot G3 : G14, G15, G16, G20) : formalités supervisées sur l'opération ou le dossier EXISTANTS — INPI (Guichet unique, FranceConnect+),
Infogreffe (Kbis, état d'endettement), RIB CARPA documentaire, formalités et portails étrangers.

Rien n'est installé ni qualifié contre un vrai site : les **profils de portail sont déclaratifs et NON qualifiés** (``PROFILS``,
``qualifie=False``) ; tant qu'un profil n'est pas qualifié, aucun effet n'est tenté (« profil non qualifié »), les préparations, les
imports manuels et les constats restent utilisables, IA en pause comprise. Les essais emploient un faux navigateur (CDP simulé).

- **Une formalité = une ligne** (``formalites56109``) rattachée à l'opération Corporate ou Cession existante (``closing56109.operation``)
  ou, pour le RIB CARPA, au dossier : tableau de données sourcées (vérifiée / manquante / contradictoire), pièces exactes (chemin,
  version, SHA-256, taille, rubrique), références distantes, documents reçus, journal immuable. Pas de seconde opération ni de second
  registre de documents : le PDF officiel reste UN document du registre commun (``livrables5689.observe``), lié à l'opération et au dossier.
- **Effets distincts** (``formalites_effets56109``) : signature, paiement, transmission INPI ; achat Infogreffe ; demande de RIB ;
  signature, paiement, transmission à l'étranger. Chacun a SON autorisation, préparée dans le registre commun des opérations à effet
  externe (``connexion_ops5652``, état « À valider », empreinte de la fiche ``connexions5652.sheet``) et accordée **seulement** par la route
  POST de l'interface (marque ``bland56109.requete_humaine``, rôle avocat ou administrateur, case « relu », empreinte et version) : jamais
  par un modèle, une routine ou un travail de fond. Le permis (``portails56109.Permis``) est reconstruit côté serveur à partir de cette
  autorisation et vérifié par ``portails56109.verifier_permis`` contre l'instantané COURANT (compte, dossier, données, PDF, synthèse,
  prix) : toute modification l'invalide ; il expire. Une routine ne signe, ne paie, n'achète ni ne transmet (``routine=True``).
- **Barrière de prise de main obligatoire** (lot O) : chaque observation et chaque action passe par ``navigateur56109.Pilote`` /
  ``barriere`` avec la génération de la séance ; une main reprise par l'avocat périme l'action en cours. FranceConnect+, PIN, OTP et mots
  de passe se saisissent dans le portail ou la fenêtre du système : aucune route ne les reçoit (``secret_refuse``) ; seule la mention
  « authentification effectuée » et sa date sont conservées.
- **Réponse perdue** : un effet tenté devient « À rapprocher » ; il n'est jamais rejoué ; le rapprochement relit le portail (ou l'avocat
  constate). Infogreffe : prix et options contrôlés sur le panier observé, aucun abonnement, **budget réservé** atomiquement avant le clic
  (``formalites_reservations56109``) et accord explicite ; après une coupure suivant le paiement, aucun nouvel achat avant rapprochement.
  PDF officiel contrôlé (page de connexion, facture commerciale, SIREN), facture d'achat distincte inscrite aux frais par le lot G1
  (``gestion56109.enregistrer_frais_achat``).
- **RIB CARPA documentaire** : récupération d'un RIB émis ou demande nouvelle ; accusé distinct du RIB ; IBAN jamais généré, masqué au
  journal, versionné « À vérifier » par le lot G1 (``gestion56109.changer_coordonnees``) ; **aucun virement, retrait ni libération** :
  ces actions n'existent pas au catalogue (``portails56109.empreinte_action`` les refuse). Service non qualifié : bordereau à relire,
  « Non transmis », transmission manuelle constatée avec sa preuve.
- **Étranger** : profils tirés du catalogue ``international56109`` (services « formalites.preparer »), déclaratifs ; préparation LOCALE
  des champs et pièces (certains portails ne gardent aucun brouillon) ; états « envoyée » et « acceptée / refusée » distincts.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape as e
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .common import Stop, load_matters
from . import portails56109 as P
from .closing56109 import (Refus, _cache, _form, acteur_de, dossier_visible, humain, jour, maintenant, operation, sha, texte, transaction,
                           version_attendue)

PORTAILS = {'inpi': 'INPI — Guichet unique', 'infogreffe': 'Infogreffe', 'carpa': 'e-CARPA', 'etranger': 'Portail étranger'}
NATURES = {'inpi:modification': ('inpi', 'Modification de société (Guichet unique)'), 'infogreffe:kbis': ('infogreffe', 'Extrait Kbis'),
           'infogreffe:endettement': ('infogreffe', 'État d’endettement'), 'carpa:rib_existant': ('carpa', 'RIB CARPA déjà émis'),
           'carpa:rib_demande': ('carpa', 'Demande documentaire de RIB CARPA'), 'etranger:formalite': ('etranger', 'Formalité étrangère')}
ACTIONS = {'signer_inpi': 'Signer la formalité INPI', 'payer_inpi': 'Payer les frais INPI', 'transmettre_inpi': 'Transmettre la formalité INPI',
           'acheter_document_infogreffe': 'Acheter le document Infogreffe', 'demander_rib_carpa': 'Transmettre la demande de RIB',
           'signer_etranger': 'Signer (portail étranger)', 'payer_etranger': 'Payer (portail étranger)', 'transmettre_etranger': 'Transmettre (portail étranger)'}
ACTIONS_PAR_PORTAIL = {'inpi': ('signer_inpi', 'payer_inpi', 'transmettre_inpi'), 'infogreffe': ('acheter_document_infogreffe',),
                       'carpa': ('demander_rib_carpa',), 'etranger': ('signer_etranger', 'payer_etranger', 'transmettre_etranger')}
ETATS = {'preparee': 'Préparée', 'donnees_a_completer': 'Données à compléter', 'attente_connexion': 'Attente de connexion (main à l’avocat)',
         'brouillon_distant': 'Brouillon distant créé', 'synthese_ecarts': 'Synthèse : écarts à examiner', 'synthese_conforme': 'Synthèse conforme',
         'transmise': 'Transmise (pas encore validée)', 'attente_paiement': 'En attente de paiement', 'attente_validation': 'En attente de validation',
         'a_regulariser': 'À régulariser', 'validee': 'Validée par l’autorité', 'rejetee': 'Rejetée',
         'panier_a_autoriser': 'Panier à autoriser', 'achat_a_rapprocher': 'Achat à rapprocher (réponse perdue)', 'commande_confirmee': 'Commande confirmée',
         'document_recu': 'Document officiel reçu', 'document_a_verifier': 'Document à vérifier', 'deja_detenu': 'Document déjà détenu',
         'bordereau_prepare': 'Bordereau préparé — non transmis', 'transmise_manuellement': 'Transmise par l’avocat (preuve)', 'attente_carpa': 'En attente de la CARPA',
         'rib_recu': 'RIB reçu', 'rib_a_verifier': 'RIB à vérifier', 'echec': 'Échec', 'preparation_locale': 'Préparation locale',
         'preparee_localement': 'Préparée localement', 'envoyee': 'Envoyée (reçu d’envoi)', 'acceptee': 'Acceptée par le greffe', 'refusee': 'Refusée par le greffe',
         'annulee': 'Annulée'}
ETATS_CONSTATES = {'inpi': ('attente_paiement', 'attente_validation', 'a_regulariser', 'validee', 'rejetee'), 'infogreffe': (),
                   'carpa': ('bordereau_prepare', 'transmise_manuellement', 'attente_carpa', 'echec'), 'etranger': ('preparee_localement', 'acceptee', 'refusee')}
EFFETS = {'a_autoriser': 'À autoriser', 'autorise': 'Autorisé', 'envoi_en_cours': 'En cours', 'a_rapprocher': 'À rapprocher (jamais rejoué)',
          'confirme': 'Confirmé sur le portail', 'annule': 'Annulé (rien n’est parti)', 'a_revalider': 'Données modifiées : nouvelle autorisation'}
STATUTS_DONNEE = ('verifiee', 'manquante', 'contradictoire', 'a_verifier')
CHAMPS_SIEGE = (('societe', 'Société et forme'), ('siren', 'SIREN'), ('siege_actuel', 'Siège actuel'), ('nouveau_siege', 'Nouveau siège'),
                ('date_effet', 'Date d’effet'), ('decision', 'Décision et organe'), ('representant', 'Représentant'), ('signataire', 'Signataire (déclarant)'),
                ('mandat', 'Mandat du mandataire'))
CHAMPS_CARPA = (('cabinet', 'Cabinet'), ('avocat', 'Avocat'), ('dossier', 'Dossier'), ('parties', 'Parties'), ('operation', 'Opération'),
                ('reference_affaire', 'Référence de l’affaire'), ('sous_compte', 'Sous-compte (s’il existe)'))
PERMIS_MINUTES = 30
SIREN = re.compile(r'^\d{9}$')
DEVISE = re.compile(r'^[A-Z]{3}$')


def _hote(url):
    from urllib.parse import urlsplit
    return (urlsplit(url).hostname or '').lower()


def _profils():
    """Profils déclaratifs, NON qualifiés : origines, authentification, effets, méthode de rapprochement. Un profil devient utilisable pour un
    effet seulement après qualification réelle (hors de cette version) ; ici ``qualifie`` reste ``False``."""
    out = {
        'inpi_guichet_unique': {'portail': 'inpi', 'label': 'INPI — Guichet unique des formalités', 'version': '0-a-qualifier', 'qualifie': False,
                                'origines': ('procedures.inpi.fr',), 'authentification': 'FranceConnect+ saisi par l’avocat dans le portail (première association parfois irréversible)',
                                'effets': ACTIONS_PAR_PORTAIL['inpi'], 'brouillon': True, 'formats': ('pdf',),
                                'repere_reference': r'(?i)(?:référence|numéro de formalité)\s*:?\s*([A-Z0-9-]{6,40})', 'repere_prix': r'(?i)montant\s*TTC\s*:?\s*([\d\s ]+,\d{2})\s*€'},
        'infogreffe_commande': {'portail': 'infogreffe', 'label': 'Infogreffe — commande de documents', 'version': '0-a-qualifier', 'qualifie': False,
                                'origines': ('www.infogreffe.fr',), 'authentification': 'Compte du cabinet (saisie humaine)', 'effets': ACTIONS_PAR_PORTAIL['infogreffe'],
                                'brouillon': False, 'formats': ('pdf',), 'repere_prix': r'(?i)total\s*TTC\s*:?\s*([\d\s ]+,\d{2})\s*€',
                                'repere_reference': r'(?i)commande\s*n[°o]?\s*:?\s*([A-Z0-9-]{4,40})', 'repere_abonnement': r'(?i)abonnement|renouvellement automatique|périodique'},
        'carpa_rib': {'portail': 'carpa', 'label': 'e-CARPA — demande documentaire de RIB', 'version': '0-a-qualifier', 'qualifie': False, 'origines': (),
                      'authentification': 'Authentification forte de l’avocat (saisie humaine)', 'effets': ACTIONS_PAR_PORTAIL['carpa'], 'brouillon': False,
                      'formats': ('pdf',), 'repere_reference': r'(?i)demande\s*n[°o]?\s*:?\s*([A-Z0-9-]{4,40})',
                      'note': 'Le parcours de demande dépend de chaque CARPA : non qualifié ; préparez le bordereau et transmettez-le vous-même.'},
    }
    try:
        from .international56109 import catalogue
        for sid, s in catalogue().items():
            if 'formalites.preparer' not in s.get('capacites', ()):
                continue
            out['etranger:' + sid] = {'portail': 'etranger', 'service': sid, 'label': s['label'], 'version': '0-a-qualifier', 'qualifie': False,
                                      'origines': (_hote(s['acces_url']),), 'territoires': tuple(s.get('territoires') or ()),
                                      'authentification': 'Compte, certificat ou carte du professionnel ; PIN saisi par la personne sur le poste',
                                      'effets': ACTIONS_PAR_PORTAIL['etranger'], 'brouillon': sid not in ('uk_myhmcts', 'ca_federal_court'),
                                      'notaire': sid == 'de_notar', 'formats': ('pdf',), 'repere_reference': r'(?i)(?:reference|référence|número|aktenzeichen)\s*:?\s*([A-Z0-9/-]{4,40})'}
    except Stop:
        pass
    return out


PROFILS = _profils()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS formalites56109(id TEXT PRIMARY KEY, portail TEXT NOT NULL CHECK(portail IN ('inpi','infogreffe','carpa','etranger')), nature TEXT NOT NULL,
  profil TEXT NOT NULL, profil_version TEXT NOT NULL DEFAULT '', op_kind TEXT NOT NULL DEFAULT '', op_id TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL,
  siren TEXT NOT NULL DEFAULT '', siren_confirme_par TEXT NOT NULL DEFAULT '', societe TEXT NOT NULL DEFAULT '', donnees TEXT NOT NULL DEFAULT '{}',
  etat TEXT NOT NULL DEFAULT 'preparee', reference_distante TEXT NOT NULL DEFAULT '', brouillon_ref TEXT NOT NULL DEFAULT '', synthese_sha TEXT NOT NULL DEFAULT '',
  synthese_ecarts TEXT NOT NULL DEFAULT '[]', montant_minor INTEGER CHECK(montant_minor IS NULL OR montant_minor>=0), devise TEXT NOT NULL DEFAULT 'EUR',
  options TEXT NOT NULL DEFAULT '[]', plafond_minor INTEGER CHECK(plafond_minor IS NULL OR plafond_minor>=0), panier TEXT NOT NULL DEFAULT '{}',
  auth_etat TEXT NOT NULL DEFAULT 'attente' CHECK(auth_etat IN ('attente','effectuee','a_verifier')), auth_le TEXT NOT NULL DEFAULT '',
  responsable TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS formalites56109_op ON formalites56109(op_kind, op_id);
CREATE INDEX IF NOT EXISTS formalites56109_m ON formalites56109(matter);
CREATE TABLE IF NOT EXISTS formalites_pieces56109(formalite TEXT NOT NULL, rubrique TEXT NOT NULL, chemin TEXT NOT NULL, version TEXT NOT NULL, sha256 TEXT NOT NULL,
  octets INTEGER NOT NULL CHECK(octets>0), televersement TEXT NOT NULL DEFAULT '' CHECK(televersement IN ('','a_rapprocher','recu_constate')),
  accuse TEXT NOT NULL DEFAULT '', ajoute_le TEXT NOT NULL, PRIMARY KEY(formalite, chemin, version));
CREATE TABLE IF NOT EXISTS formalites_effets56109(id TEXT PRIMARY KEY, formalite TEXT NOT NULL, action TEXT NOT NULL, operation TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL CHECK(etat IN ('a_autoriser','autorise','envoi_en_cours','a_rapprocher','confirme','annule','a_revalider')),
  reference_distante TEXT NOT NULL DEFAULT '', prix_minor INTEGER, demande_par TEXT NOT NULL, cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL,
  UNIQUE(formalite, action, empreinte));
CREATE TABLE IF NOT EXISTS formalites_documents56109(id TEXT PRIMARY KEY, formalite TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('pdf_officiel','facture_achat','synthese','recu','accuse','rib','bordereau')),
  chemin TEXT NOT NULL, sha256 TEXT NOT NULL, octets INTEGER NOT NULL, provenance TEXT NOT NULL, numero_commande TEXT NOT NULL DEFAULT '', emis_le TEXT NOT NULL DEFAULT '',
  recupere_le TEXT NOT NULL, controle TEXT NOT NULL, livrable TEXT NOT NULL DEFAULT '', frais TEXT NOT NULL DEFAULT '', iban_masque TEXT NOT NULL DEFAULT '',
  iban_empreinte TEXT NOT NULL DEFAULT '', UNIQUE(formalite, type, sha256));
CREATE TABLE IF NOT EXISTS formalites_reservations56109(id TEXT PRIMARY KEY, op_key TEXT NOT NULL, formalite TEXT NOT NULL, effet TEXT NOT NULL UNIQUE,
  montant_minor INTEGER NOT NULL CHECK(montant_minor>=0), devise TEXT NOT NULL, etat TEXT NOT NULL CHECK(etat IN ('reservee','consommee','liberee')),
  cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS formalites_journal56109(id INTEGER PRIMARY KEY AUTOINCREMENT, formalite TEXT NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL, auteur TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}');
CREATE TRIGGER IF NOT EXISTS formalites_journal56109_fige_maj BEFORE UPDATE ON formalites_journal56109 BEGIN SELECT RAISE(ABORT, 'journal_immuable'); END;
CREATE TRIGGER IF NOT EXISTS formalites_journal56109_fige_sup BEFORE DELETE ON formalites_journal56109 BEGIN SELECT RAISE(ABORT, 'journal_immuable'); END;
'''
TABLES = ('formalites56109', 'formalites_pieces56109', 'formalites_effets56109', 'formalites_documents56109', 'formalites_reservations56109', 'formalites_journal56109')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _journal(desk, fid, action, auteur, detail=None):
    desk.db.execute('INSERT INTO formalites_journal56109(formalite,at,action,auteur,detail) VALUES(?,?,?,?,?)',
                    (fid, maintenant(), action, str(auteur)[:160], json.dumps(detail or {}, ensure_ascii=False, default=str)))


def _masquer_iban(iban):
    s = re.sub(r'\s+', '', str(iban or '')).upper()
    return (s[:4] + ' **** **** ' + s[-4:]) if len(s) >= 8 else '****'


def _montant(valeur, champ='montant'):
    if valeur is None or str(valeur).strip() == '':
        return None
    if isinstance(valeur, (float, bool)):
        raise Refus('champs_invalides', {champ: 'Montant en texte (centimes), jamais un nombre flottant.'})
    m = re.fullmatch(r'(\d{1,9})(?:[,.](\d{2}))?', re.sub(r'[\s €]', '', str(valeur)))
    if not m:
        raise Refus('champs_invalides', {champ: 'Montant TTC illisible (ex. 3,93).'})
    return int(m.group(1)) * 100 + int(m.group(2) or 0)


# ------------------------------------------------------------------------------------------------ lecture
def _formalite(desk, acteur, fid):
    f = desk.db.execute('SELECT * FROM formalites56109 WHERE id=?', (str(fid or ''),)).fetchone()
    if f is None:
        raise Stop('introuvable')
    dossier_visible(desk, acteur, f['matter'])
    return dict(f)


def pieces(desk, fid):
    return [dict(r) for r in desk.db.execute('SELECT * FROM formalites_pieces56109 WHERE formalite=? ORDER BY rubrique, chemin', (fid,))]


def effets(desk, fid):
    return [dict(r) for r in desk.db.execute('SELECT * FROM formalites_effets56109 WHERE formalite=? ORDER BY cree_le', (fid,))]


def documents(desk, fid):
    return [dict(r) for r in desk.db.execute('SELECT id,formalite,type,chemin,sha256,octets,provenance,numero_commande,emis_le,recupere_le,controle,livrable,frais,iban_masque '
                                             'FROM formalites_documents56109 WHERE formalite=? ORDER BY recupere_le', (fid,))]


def fiche(desk, acteur, fid):
    f = _formalite(desk, acteur, fid)
    p = PROFILS.get(f['profil'], {})
    return {**{k: v for k, v in f.items() if k not in ('donnees', 'synthese_ecarts', 'options', 'panier')}, 'donnees': json.loads(f['donnees']),
            'synthese_ecarts': json.loads(f['synthese_ecarts']), 'options': json.loads(f['options']), 'panier': json.loads(f['panier']),
            'etat_libelle': ETATS.get(f['etat'], f['etat']), 'nature_libelle': NATURES.get(f['nature'], ('', f['nature']))[1],
            'profil_libelle': p.get('label', f['profil']), 'profil_qualifie': bool(p.get('qualifie')), 'brouillon_distant_possible': bool(p.get('brouillon', False)),
            'pieces': pieces(desk, f['id']), 'effets': [{**x, 'etat_libelle': EFFETS[x['etat']], 'action_libelle': ACTIONS[x['action']]} for x in effets(desk, f['id'])],
            'documents': documents(desk, f['id']), 'mentions': _mentions(f)}


def _mentions(f):
    out = ['Une connexion réussie ne vaut ni mandat, ni signature, ni dépôt ; le succès d’un clic n’est pas la validation par l’autorité.',
           'Les codes, PIN et mots de passe se saisissent dans le portail ou la fenêtre du système : jamais ici.']
    if f['nature'] == 'infogreffe:endettement':
        out.append('L’état d’endettement porte sur les inscriptions publiées à sa date : une absence d’inscription ne prouve pas l’absence de dette.')
    if f['portail'] == 'carpa':
        out.append('Documentaire seulement : aucun virement, retrait ni libération de fonds n’est possible depuis AxiorHub.')
    if f['portail'] == 'etranger':
        out.append('Une inscription au barreau français ne crée aucune habilitation étrangère ; la réception d’un dépôt n’est pas son acceptation par le greffe.')
    if not PROFILS.get(f['profil'], {}).get('qualifie'):
        out.append('Profil de portail non qualifié dans cette version : préparation et import manuel seulement, aucun effet n’est tenté.')
    return out


def liste(desk, acteur, op_kind, op_id):
    op = operation(desk, acteur, op_kind, op_id)
    rows = desk.db.execute('SELECT id FROM formalites56109 WHERE op_kind=? AND op_id=? ORDER BY cree_le', (op['kind'], op['id'])).fetchall()
    return {'operation': {'kind': op['kind'], 'id': op['id']}, 'formalites': [fiche(desk, acteur, r['id']) for r in rows]}


def fiche_effet(desk, acteur, eid):
    """Fiche d'autorisation : ce qui ENGAGE (instantané exact) et l'empreinte de la fiche du registre commun à reprendre pour autoriser."""
    from . import connexions5652 as cx
    a = acteur_de(acteur)
    ef = desk.db.execute('SELECT * FROM formalites_effets56109 WHERE id=?', (str(eid or ''),)).fetchone()
    if ef is None:
        raise Stop('introuvable')
    _formalite(desk, acteur, ef['formalite'])
    try:
        s = cx.sheet(desk, a.compte, a.role, ef['operation'])
    except Stop:
        raise Stop('introuvable') from None
    return {'effet': ef['id'], 'action': ef['action'], 'action_libelle': ACTIONS[ef['action']], 'etat': ef['etat'], 'etat_libelle': EFFETS[ef['etat']],
            'operation': ef['operation'], 'version': s['version'], 'empreinte': s['empreinte'], 'donnees': s['donnees_transmises'], 'etat_operation': s['etat']}


def deja_detenu(desk, matter, siren, nature, fraicheur_jours):
    """Document déjà détenu, contrôlé, du même produit pour la même société et assez récent (fraîcheur fixée par la mission) ; lecture seule."""
    if not fraicheur_jours:
        return None
    limite = (datetime.now(timezone.utc) - timedelta(days=int(fraicheur_jours))).date().isoformat()
    row = desk.db.execute("SELECT d.* FROM formalites_documents56109 d JOIN formalites56109 f ON f.id=d.formalite WHERE f.matter=? AND f.siren=? AND f.nature=? "
                          "AND d.type='pdf_officiel' AND d.controle='ok' AND COALESCE(NULLIF(d.emis_le,''), substr(d.recupere_le,1,10))>=? ORDER BY d.recupere_le DESC LIMIT 1",
                          (matter, siren, nature, limite)).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------------------------------------ préparation
def _tableau_siege(desk, op):
    """Tableau sourcé d'un transfert de siège (INPI) : valeurs de la saisie de l'opération, À VÉRIFIER contre le PV et les statuts signés ;
    contradiction signalée ; la date d'effet ne vient jamais de la date d'un fichier."""
    d = op.get('data') or {}
    c = desk.db.execute('SELECT * FROM companies5636 WHERE id=?', (op.get('company') or '',)).fetchone() if op.get('company') else None
    valeurs = {'societe': '%s %s' % (c['denomination'], c['forme']) if c else '', 'siren': (c['siren'] if c else '') or '',
               'siege_actuel': d.get('ancien_siege') or (c['siege'] if c else ''), 'nouveau_siege': d.get('nouveau_siege') or '',
               'date_effet': d.get('date_effet') or '', 'decision': ('%s du %s' % (d.get('organe') or '', d.get('date_decision') or d.get('date_ag') or '')).strip(' du'),
               'representant': d.get('representant') or '', 'signataire': d.get('signataire') or '', 'mandat': ''}
    out = {}
    for k, label in CHAMPS_SIEGE:
        v = str(valeurs.get(k) or '').strip()
        out[k] = {'libelle': label, 'valeur': v, 'source': 'Saisie de l’opération Corporate' if v else '', 'statut': 'a_verifier' if v else 'manquante'}
    if c and d.get('ancien_siege') and c['siege'] and d['ancien_siege'] != c['siege'] and op.get('state') != 'realisee':
        out['siege_actuel']['statut'] = 'contradictoire'
        out['siege_actuel']['source'] += ' ; fiche de la société : %s' % c['siege']
    return out


def preparer(desk, acteur, data):
    """« Préparer sur le Guichet unique », « Obtenir un Kbis », « Obtenir un état d'endettement », « RIB CARPA », « Formalité étrangère » :
    une formalité sur l'opération (ou le dossier pour le RIB), aucun effet, aucune connexion."""
    a = humain(acteur)
    nature = str(data.get('nature') or '')
    if nature not in NATURES:
        raise Refus('champs_invalides', {'nature': 'Formalité inconnue.'})
    portail = NATURES[nature][0]
    op = None
    if data.get('op_kind') or portail != 'carpa':
        op = operation(desk, acteur, data.get('op_kind'), data.get('op_id'))
        matter = op['matter']
    else:
        matter = dossier_visible(desk, acteur, data.get('dossier'))
    if portail == 'etranger':
        profil = str(data.get('profil') or '')
        if not profil.startswith('etranger:') or profil not in PROFILS:
            raise Refus('champs_invalides', {'profil': 'Service étranger inconnu ou sans formalité au catalogue.'})
    else:
        profil = {'inpi': 'inpi_guichet_unique', 'infogreffe': 'infogreffe_commande', 'carpa': 'carpa_rib'}[portail]
    err = {}
    siren = re.sub(r'\D', '', str(data.get('siren') or ''))
    societe = texte(data.get('societe'), 200)
    if portail in ('inpi', 'infogreffe'):
        if op and op['kind'] == 'corporate5636':
            c = desk.db.execute('SELECT siren, denomination FROM companies5636 WHERE id=?', (op['company'],)).fetchone()
            societe = societe or (c['denomination'] if c else '')
            if c and c['siren'] and siren and c['siren'] != siren:
                err['siren'] = 'Ce SIREN n’est pas celui de la société de l’opération (%s).' % c['siren']
            siren = siren or (c['siren'] if c else '')
        if not SIREN.match(siren):
            err['siren'] = err.get('siren') or 'SIREN à neuf chiffres requis (société de l’opération).'
    if portail == 'inpi' and not (op and op['kind'] == 'corporate5636'):
        err['op_kind'] = 'La formalité INPI se prépare depuis l’opération Corporate concernée.'
    if err:
        raise Refus('champs_invalides', err)
    donnees = {}
    if portail == 'inpi' and op['type'] == 'siege':
        donnees = _tableau_siege(desk, op)
    elif portail == 'carpa':
        donnees = {k: {'libelle': label, 'valeur': '', 'source': '', 'statut': 'manquante'} for k, label in CHAMPS_CARPA}
        donnees['dossier'].update(valeur=matter, source='Dossier AxiorHub', statut='verifiee')
    deja = None
    if portail == 'infogreffe':
        deja = deja_detenu(desk, matter, siren, nature, int(data.get('fraicheur_jours') or 0))
    fid = 'fo-' + uuid.uuid4().hex[:20]
    etat = 'deja_detenu' if deja else 'preparation_locale' if portail == 'etranger' else 'preparee'
    with transaction(desk):
        desk.db.execute('INSERT INTO formalites56109(id,portail,nature,profil,profil_version,op_kind,op_id,matter,siren,siren_confirme_par,societe,donnees,etat,responsable,cree_par,'
                        'cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (fid, portail, nature, profil, PROFILS[profil]['version'], op['kind'] if op else '', op['id'] if op else '', matter, siren,
                         a.compte if siren and str(data.get('siren_confirme') or '') in ('1', 'true', 'on', 'True') else '', societe,
                         json.dumps(donnees, ensure_ascii=False), etat, texte(data.get('responsable'), 160) or a.compte, a.compte, maintenant(), maintenant()))
        _journal(desk, fid, 'preparation', a.compte, {'nature': nature, 'profil': profil, 'deja_detenu': bool(deja)})
    msg = ('Document déjà détenu (%s) : aucun achat proposé.' % deja['chemin']) if deja else 'Formalité préparée : aucune connexion, aucun effet.'
    return {'formalite': fid, 'etat': etat, 'message': msg}


def donnees(desk, acteur, data):
    """Une donnée du tableau : valeur, source (pièce, page) et statut vérifié / manquant / contradictoire. Aucune valeur inventée."""
    a = humain(acteur)
    f = _formalite(desk, acteur, data.get('formalite'))
    version_attendue(f, data.get('version'))
    champ = texte(data.get('champ'), 60)
    statut = str(data.get('statut') or '')
    tableau = json.loads(f['donnees'])
    if not re.fullmatch(r'[a-z_0-9]{2,60}', champ):
        raise Refus('champs_invalides', {'champ': 'Champ invalide.'})
    if statut not in STATUTS_DONNEE:
        raise Refus('champs_invalides', {'statut': 'Vérifiée, manquante, contradictoire ou à vérifier.'})
    valeur, source = texte(data.get('valeur'), 400), texte(data.get('source'), 300)
    if statut == 'verifiee' and (not valeur or len(source) < 3):
        raise Refus('champs_invalides', {'source': 'Une donnée vérifiée a une valeur et une source (pièce, page).'})
    avant = tableau.get(champ, {})
    tableau[champ] = {'libelle': avant.get('libelle') or texte(data.get('libelle'), 120) or champ, 'valeur': valeur, 'source': source, 'statut': statut,
                      'par': a.compte, 'le': maintenant()}
    siren = f['siren']
    if champ == 'siren' and statut == 'verifiee':
        if not SIREN.match(re.sub(r'\D', '', valeur)) or (f['siren'] and re.sub(r'\D', '', valeur) != f['siren']):
            raise Refus('champs_invalides', {'valeur': 'SIREN différent de celui de la formalité : préparez une nouvelle formalité.'})
    with transaction(desk):
        cur = desk.db.execute('UPDATE formalites56109 SET donnees=?, siren=?, version=version+1, modifie_le=? WHERE id=? AND version=?',
                              (json.dumps(tableau, ensure_ascii=False), siren, maintenant(), f['id'], f['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, f['id'], 'donnee', a.compte, {'champ': champ, 'statut': statut, 'avant': avant.get('statut', '')})
    return {'formalite': f['id'], 'message': 'Donnée enregistrée.'}


def piece(desk, acteur, data, client=None):
    """Pièce EXACTE (chemin du dossier, version, SHA-256 et taille relus) : le PV et les statuts signés sont joints dans leurs octets, sans
    conversion. Toute nouvelle pièce change l'instantané : les autorisations déjà accordées deviennent caduques."""
    a = humain(acteur)
    f = _formalite(desk, acteur, data.get('formalite'))
    version_attendue(f, data.get('version'))
    chemin = str(data.get('chemin') or '')
    rubrique = texte(data.get('rubrique'), 60) or 'justificatif'
    m = next((x for x in load_matters(desk.c) if x['id'] == f['matter']), None)
    if not m or not chemin.startswith(m['path'].rstrip('/') + '/') or '..' in chemin:
        raise Refus('champs_invalides', {'chemin': 'Pièce hors du dossier.'})
    raw = _lire(desk, chemin, client)
    if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-2048:]:
        raise Refus('champs_invalides', {'chemin': 'PDF requis (aucune conversion ni reconstruction d’un acte signé).'})
    empreinte = hashlib.sha256(raw).hexdigest()
    with transaction(desk):
        desk.db.execute('INSERT OR IGNORE INTO formalites_pieces56109(formalite,rubrique,chemin,version,sha256,octets,ajoute_le) VALUES(?,?,?,?,?,?,?)',
                        (f['id'], rubrique, chemin, empreinte[:16], empreinte, len(raw), maintenant()))
        desk.db.execute('UPDATE formalites56109 SET version=version+1, modifie_le=? WHERE id=?', (maintenant(), f['id']))
        _journal(desk, f['id'], 'piece', a.compte, {'chemin': chemin, 'sha256': empreinte, 'octets': len(raw), 'rubrique': rubrique})
    return {'formalite': f['id'], 'sha256': empreinte, 'octets': len(raw), 'message': 'Pièce jointe (octets exacts, empreinte retenue).'}


def _lire(desk, chemin, client=None):
    if client is None:
        from .dav import DAV
        client = DAV(desk.c['nextcloud'])
    try:
        return client.download({**client.stat(chemin), 'path': chemin})
    except Stop:
        raise Refus('champs_invalides', {'chemin': 'Fichier introuvable dans le dossier.'}) from None


def synthese(desk, acteur, data):
    """Synthèse téléchargée du portail comparée aux valeurs attendues (données vérifiées) et à la liste des PDF joints : chaque écart est
    montré ; aucune signature ni transmission ne peut être demandée avec un écart."""
    a = humain(acteur)
    f = _formalite(desk, acteur, data.get('formalite'))
    version_attendue(f, data.get('version'))
    t = str(data.get('texte') or '')
    if len(t) < 10 or len(t) > 200_000:
        raise Refus('champs_invalides', {'texte': 'Texte de la synthèse requis.'})
    plat = re.sub(r'\s+', ' ', t).casefold()
    ecarts = []
    for k, v in json.loads(f['donnees']).items():
        if v.get('statut') != 'verifiee':
            ecarts.append({'champ': k, 'libelle': v.get('libelle', k), 'motif': 'donnée non vérifiée (%s)' % v.get('statut')})
        elif re.sub(r'\s+', ' ', v['valeur']).casefold() not in plat:
            ecarts.append({'champ': k, 'libelle': v.get('libelle', k), 'motif': 'absente ou différente dans la synthèse', 'attendu': v['valeur']})
    for p in pieces(desk, f['id']):
        nom = p['chemin'].rsplit('/', 1)[-1].casefold()
        if nom not in plat:
            ecarts.append({'champ': 'piece', 'libelle': p['chemin'].rsplit('/', 1)[-1], 'motif': 'PDF joint absent de la synthèse'})
    etat = 'synthese_conforme' if not ecarts else 'synthese_ecarts'
    with transaction(desk):
        cur = desk.db.execute('UPDATE formalites56109 SET synthese_sha=?, synthese_ecarts=?, etat=?, brouillon_ref=CASE WHEN ?<>\'\' THEN ? ELSE brouillon_ref END, '
                              'version=version+1, modifie_le=? WHERE id=? AND version=?',
                              (hashlib.sha256(t.encode('utf-8')).hexdigest(), json.dumps(ecarts, ensure_ascii=False), etat, texte(data.get('brouillon_ref'), 60),
                               texte(data.get('brouillon_ref'), 60), maintenant(), f['id'], f['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, f['id'], 'synthese', a.compte, {'ecarts': len(ecarts)})
    return {'formalite': f['id'], 'etat': etat, 'ecarts': ecarts, 'message': 'Synthèse conforme.' if not ecarts else '%d écart(s) à examiner.' % len(ecarts)}


def panier(desk, acteur, data):
    """Panier Infogreffe tel que présenté par le portail : produit, quantité, format, prix TTC, frais, devise, options. Aucun abonnement,
    aucune option non prévue ; budget accordé pour l'opération (plafond). Un prix ou une option changés exigent une nouvelle autorisation."""
    a = humain(acteur)
    f = _formalite(desk, acteur, data.get('formalite'))
    version_attendue(f, data.get('version'))
    if f['portail'] != 'infogreffe':
        raise Stop('introuvable')
    err = {}
    prix = None
    try:
        prix = _montant(data.get('prix_ttc'), 'prix_ttc')
    except Refus as ex:
        err.update(ex.detail)
    plafond = None
    try:
        plafond = _montant(data.get('plafond'), 'plafond')
    except Refus as ex:
        err.update(ex.detail)
    devise = str(data.get('devise') or 'EUR').upper()
    if devise != 'EUR':
        err['devise'] = 'Euros seulement pour ce portail.'
    options = data.get('options') or []
    if isinstance(options, str):
        options = [x for x in re.split(r'[,;]\s*', options) if x]
    if str(data.get('abonnement') or '') in ('1', 'true', 'on', 'True') or any(re.search(PROFILS['infogreffe_commande']['repere_abonnement'], str(o)) for o in options):
        err['options'] = 'Abonnement ou option périodique refusés : rien n’est commandé.'
    elif options:
        err['options'] = 'Option non prévue par la mission : retirez-la du panier.'
    if prix is None:
        err.setdefault('prix_ttc', 'Prix TTC affiché requis.')
    if plafond is None:
        err.setdefault('plafond', 'Budget accordé pour l’opération requis (plafond).')
    if not f['siren_confirme_par']:
        err['siren'] = 'Confirmez la société (SIREN) avant tout achat.'
    if err:
        raise Refus('champs_invalides', err)
    contenu = {'produit': NATURES[f['nature']][1], 'quantite': 1, 'format': 'électronique', 'prix_ttc_minor': prix, 'frais_minor': _montant(data.get('frais'), 'frais') or 0,
               'devise': devise, 'options': [], 'observe_par': a.compte, 'observe_le': maintenant()}
    with transaction(desk):
        cur = desk.db.execute("UPDATE formalites56109 SET panier=?, montant_minor=?, devise=?, options='[]', plafond_minor=?, etat='panier_a_autoriser', version=version+1, modifie_le=? "
                              'WHERE id=? AND version=?', (json.dumps(contenu, ensure_ascii=False), prix, devise, plafond, maintenant(), f['id'], f['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, f['id'], 'panier', a.compte, contenu)
    return {'formalite': f['id'], 'etat': 'panier_a_autoriser', 'message': 'Panier enregistré : autorisation explicite requise avant tout achat.'}


def authentification(desk, acteur, data):
    """L'avocat a fait FranceConnect+ (ou son équivalent) dans le portail : seule la mention et sa date sont conservées. Sans identité constatée,
    l'état reste « à vérifier ». Aucun secret n'arrive ici (route : ``secret_refuse``)."""
    a = humain(acteur)
    f = _formalite(desk, acteur, data.get('formalite'))
    version_attendue(f, data.get('version'))
    identite = texte(data.get('identite_constatee'), 160)
    etat = 'effectuee' if identite else 'a_verifier'
    with transaction(desk):
        cur = desk.db.execute('UPDATE formalites56109 SET auth_etat=?, auth_le=?, version=version+1, modifie_le=? WHERE id=? AND version=?',
                              (etat, maintenant(), maintenant(), f['id'], f['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, f['id'], 'authentification_effectuee', a.compte, {'identite_constatee': bool(identite)})
    return {'formalite': f['id'], 'auth_etat': etat, 'message': 'Authentification effectuée notée (%s).' % ('identité constatée' if identite else 'identité à vérifier')}


def etat(desk, acteur, data):
    """État CONSTATÉ par l'avocat (portail lu, courrier reçu) avec sa preuve : INPI (attente de paiement, de validation, régularisation,
    validée, rejetée), CARPA (bordereau préparé, transmis par l'avocat, en attente), étranger (préparée localement, acceptée, refusée)."""
    a = humain(acteur, valideur=True)
    f = _formalite(desk, acteur, data.get('formalite'))
    version_attendue(f, data.get('version'))
    nouvel = str(data.get('etat') or '')
    if nouvel not in ETATS_CONSTATES[f['portail']]:
        raise Refus('champs_invalides', {'etat': 'État non constatable pour ce portail.'})
    preuve = texte(data.get('preuve'), 300)
    if nouvel not in ('bordereau_prepare', 'preparee_localement') and len(preuve) < 3:
        raise Refus('champs_invalides', {'preuve': 'Preuve du constat requise (reçu, capture conservée, courrier).'})
    if nouvel in ('validee', 'rejetee', 'a_regulariser', 'attente_validation') and f['etat'] not in ('transmise', 'attente_paiement', 'attente_validation', 'a_regulariser'):
        raise Refus('champs_invalides', {'etat': 'La formalité n’a pas été transmise : aucune décision de l’autorité ne peut être constatée.'})
    if nouvel in ('acceptee', 'refusee') and f['etat'] != 'envoyee':
        raise Refus('champs_invalides', {'etat': 'Envoi non constaté : acceptation ou refus impossibles à ce stade.'})
    if nouvel == 'preparee_localement':
        manquants = [k for k, v in json.loads(f['donnees']).items() if v.get('statut') != 'verifiee']
        if manquants or not pieces(desk, f['id']):
            raise Refus('champs_invalides', {'donnees': 'Champs et pièces à préparer d’abord (%s).' % ', '.join(manquants[:5]) or 'pièces'})
    with transaction(desk):
        cur = desk.db.execute('UPDATE formalites56109 SET etat=?, version=version+1, modifie_le=? WHERE id=? AND version=?', (nouvel, maintenant(), f['id'], f['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _journal(desk, f['id'], 'etat_constate', a.compte, {'etat': nouvel, 'preuve': preuve})
    return {'formalite': f['id'], 'etat': nouvel, 'message': ETATS[nouvel] + '.'}


# ------------------------------------------------------------------------------------------------ autorisations (registre commun des effets externes)
def instantane(desk, f, action):
    """Ce qui ENGAGE, figé : compte (profil), dossier, opération, société, données, PDF (id, version, SHA), synthèse, prix et options.
    Aucun secret, aucun contenu de pièce."""
    cab = desk.c.get('cabinet') if isinstance(desk.c.get('cabinet'), dict) else {}
    return {'cabinet': str(cab.get('id') or 'cabinet'),
            'utilisateur': f['responsable'], 'compte': f['profil'] + '@' + f['profil_version'], 'portail': f['portail'], 'dossier': f['matter'],
            'action': action, 'operation': ('%s:%s' % (f['op_kind'], f['op_id'])) if f['op_kind'] else 'dossier:' + f['matter'], 'formalite': f['id'],
            'nature': f['nature'], 'siren': f['siren'], 'donnees': sha(json.loads(f['donnees'])), 'synthese': f['synthese_sha'],
            'documents': [{'id': p['chemin'], 'version': p['version'], 'sha256': p['sha256']} for p in pieces(desk, f['id'])],
            'prix_minor': f['montant_minor'], 'devise': f['devise'], 'plafond_minor': f['plafond_minor'], 'options': json.loads(f['options']),
            'reference_distante': f['brouillon_ref']}


def _prealables(desk, f, action):
    """Conditions métier avant de DEMANDER une autorisation (aucun effet) ; un écart, une donnée non vérifiée ou un achat incertain bloquent."""
    eff = {x['action']: x for x in effets(desk, f['id']) if x['etat'] not in ('annule', 'a_revalider')}
    if action not in ACTIONS_PAR_PORTAIL[f['portail']] or action not in PROFILS.get(f['profil'], {}).get('effets', ()):
        raise Stop('action_portail_non_prevue')
    if f['portail'] == 'inpi':
        if f['etat'] != 'synthese_conforme' and action == 'signer_inpi':
            raise Refus('champs_invalides', {'synthese': 'Comparez d’abord la synthèse du portail : aucun écart ne doit subsister.'})
        if not f['brouillon_ref']:
            raise Refus('champs_invalides', {'brouillon_ref': 'Référence du brouillon distant requise.'})
        if action == 'payer_inpi' and f['montant_minor'] is None:
            raise Refus('champs_invalides', {'montant': 'Montant des frais affiché par le portail requis.'})
        if action == 'transmettre_inpi':
            if eff.get('signer_inpi', {}).get('etat') != 'confirme':
                raise Refus('champs_invalides', {'signature': 'La signature doit être confirmée sur le portail avant la transmission.'})
            if (f['montant_minor'] or 0) > 0 and eff.get('payer_inpi', {}).get('etat') != 'confirme':
                raise Refus('champs_invalides', {'paiement': 'Le paiement des frais doit être confirmé avant la transmission.'})
    elif f['portail'] == 'infogreffe':
        if f['etat'] == 'achat_a_rapprocher' or any(x['action'] == action and x['etat'] in ('envoi_en_cours', 'a_rapprocher') for x in effets(desk, f['id'])):
            raise Stop('achat_a_rapprocher_avant_tout_nouvel_achat')
        if f['etat'] != 'panier_a_autoriser':
            raise Refus('champs_invalides', {'panier': 'Enregistrez le panier exact (prix, options, budget) avant l’autorisation.'})
    elif f['portail'] == 'carpa':
        if f['nature'] != 'carpa:rib_demande':
            raise Refus('champs_invalides', {'nature': 'Un RIB déjà émis se récupère : aucune demande n’est transmise.'})
        manquants = [k for k, v in json.loads(f['donnees']).items() if v.get('statut') != 'verifiee' and k != 'sous_compte']
        if manquants:
            raise Refus('champs_invalides', {'donnees': 'Informations à vérifier : %s.' % ', '.join(manquants)})
    elif f['portail'] == 'etranger' and f['etat'] != 'preparee_localement':
        raise Refus('champs_invalides', {'preparation': 'Préparez localement les champs et les pièces avant d’ouvrir le formulaire.'})


def demander(desk, acteur, data, declencheur='utilisateur'):
    """Prépare l'autorisation d'UN effet (« À valider ») dans le registre commun : rien n'est transmis. Même instantané : même demande."""
    from . import connexions5652 as cx
    a = acteur_de(acteur)
    f = _formalite(desk, acteur, data.get('formalite'))
    action = str(data.get('action') or '')
    _prealables(desk, f, action)
    snap = instantane(desk, f, action)
    P.empreinte_action(snap)                                   # catalogue fermé : virer, retirer, libérer n'existent pas
    payload = json.dumps(snap, ensure_ascii=False, sort_keys=True)
    phash = cx._sha(payload, '')
    cx.ensure_schema(desk)
    idem = cx._sha('formalites56109', f['id'], action, phash)[:40]
    with transaction(desk):
        old = desk.db.execute('SELECT id FROM formalites_effets56109 WHERE formalite=? AND action=? AND empreinte=?', (f['id'], action, phash)).fetchone()
        if old:
            return {'effet': old['id'], 'deja': True, 'message': 'Autorisation déjà demandée pour ce contenu exact.'}
        oid = 'op-' + cx._sha(idem, uuid.uuid4().hex)[:20]
        desk.db.execute('INSERT INTO connexion_ops5652(id,connection,provider,capability,matter,owner,state,idem_key,payload_json,payload_hash,document_path,document_sha,'
                        "connection_generation,identity_hash,environment,trigger,created,updated) VALUES(?,'',?,?,?,?,'a_valider',?,?,?,'','',0,'',?,?,?,?)",
                        (oid, 'portail_' + f['portail'], action, f['matter'], a.compte, idem, payload, phash, f['profil_version'], str(declencheur)[:80], maintenant(), maintenant()))
        eid = 'ef-' + uuid.uuid4().hex[:20]
        desk.db.execute("INSERT INTO formalites_effets56109(id,formalite,action,operation,empreinte,etat,prix_minor,demande_par,cree_le,modifie_le) VALUES(?,?,?,?,?,'a_autoriser',?,?,?,?)",
                        (eid, f['id'], action, oid, phash, f['montant_minor'], a.compte, maintenant(), maintenant()))
        _journal(desk, f['id'], 'autorisation_demandee', a.compte, {'action': action, 'operation': oid, 'declencheur': declencheur})
    return {'effet': eid, 'operation': oid, 'message': 'Autorisation demandée : à accorder par l’avocat, sur la fiche exacte.'}


def autoriser(desk, acteur, data):
    """Accord de l'avocat sur l'effet EXACT : marque de la route POST (``bland56109.requete_humaine``), rôle avocat ou administrateur,
    relecture cochée, empreinte de la fiche du registre commun et version identiques. Jamais un modèle ni une routine."""
    from . import connexions5652 as cx
    from . import bland56109                    # marque posée uniquement par la route POST de l'interface (mécanisme commun du lot B)
    bland56109.exiger_humain()
    a = humain(acteur, valideur=True)
    if str(data.get('relu') or '') not in ('1', 'true', 'on', 'True'):
        raise Refus('champs_invalides', {'relu': 'Cochez « J’ai relu ce qui engage le cabinet ».'})
    ef = desk.db.execute('SELECT * FROM formalites_effets56109 WHERE id=?', (str(data.get('effet') or ''),)).fetchone()
    if ef is None:
        raise Stop('introuvable')
    f = _formalite(desk, acteur, ef['formalite'])
    if ef['etat'] != 'a_autoriser':
        return {'effet': ef['id'], 'etat': ef['etat'], 'message': EFFETS[ef['etat']]}
    courant = cx._sha(json.dumps(instantane(desk, f, ef['action']), ensure_ascii=False, sort_keys=True), '')
    if courant != ef['empreinte']:
        _caduque(desk, ef, a.compte)
        raise Stop('fiche_modifiee_depuis_affichage')
    s = cx.sheet(desk, a.compte, a.role, ef['operation'])
    if str(data.get('empreinte') or '') != s['empreinte']:
        raise Stop('fiche_modifiee_depuis_affichage')
    if str(data.get('version') or '') != str(s['version']):
        raise Stop('version_perimee')
    with transaction(desk):
        cur = desk.db.execute("UPDATE connexion_ops5652 SET state='autorisee', validated_by=?, validated_at=?, approved_hash=?, version=version+1, updated=? "
                              "WHERE id=? AND state='a_valider' AND version=?", (a.compte, maintenant(), s['empreinte'], maintenant(), ef['operation'], s['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        desk.db.execute("UPDATE formalites_effets56109 SET etat='autorise', modifie_le=? WHERE id=? AND etat='a_autoriser'", (maintenant(), ef['id']))
        _journal(desk, f['id'], 'autorisation_accordee', a.compte, {'action': ef['action'], 'operation': ef['operation']})
    return {'effet': ef['id'], 'etat': 'autorise', 'message': '%s : autorisé par %s (valable %d minutes, pour ce contenu exact).' % (ACTIONS[ef['action']], a.compte, PERMIS_MINUTES)}


def _caduque(desk, ef, auteur):
    desk.db.execute("UPDATE formalites_effets56109 SET etat='a_revalider', modifie_le=? WHERE id=? AND etat IN ('a_autoriser','autorise')", (maintenant(), ef['id']))
    desk.db.execute("UPDATE connexion_ops5652 SET state='a_revalider', error='donnees_modifiees', version=version+1, updated=? WHERE id=? AND state IN ('a_valider','autorisee')",
                    (maintenant(), ef['operation']))
    _journal(desk, ef['formalite'], 'autorisation_caduque', auteur, {'action': ef['action']})
    desk.db.commit()


def annuler(desk, acteur, data):
    a = humain(acteur)
    ef = desk.db.execute('SELECT * FROM formalites_effets56109 WHERE id=?', (str(data.get('effet') or ''),)).fetchone()
    if ef is None:
        raise Stop('introuvable')
    _formalite(desk, acteur, ef['formalite'])
    if ef['etat'] not in ('a_autoriser', 'autorise', 'a_revalider'):
        raise Stop('effet_deja_tente')                   # un effet tenté n'est pas rétracté par AxiorHub : il se rapproche
    with transaction(desk):
        desk.db.execute("UPDATE formalites_effets56109 SET etat='annule', modifie_le=? WHERE id=?", (maintenant(), ef['id']))
        desk.db.execute("UPDATE connexion_ops5652 SET state='annulee', version=version+1, updated=? WHERE id=?", (maintenant(), ef['operation']))
        _journal(desk, ef['formalite'], 'autorisation_annulee', a.compte, {'action': ef['action']})
    return {'effet': ef['id'], 'etat': 'annule', 'message': 'Autorisation annulée : rien n’est parti.'}


def permis_courant(desk, ef):
    """Permis RECONSTRUIT côté serveur depuis l'autorisation enregistrée (jamais depuis un objet fourni par un modèle) ; ``None`` sinon."""
    from . import connexions5652 as cx
    op = desk.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (ef['operation'],)).fetchone()
    if op is None or op['state'] != 'autorisee' or not op['validated_by'] or not op['validated_at'] or not op['approved_hash']:
        return None
    try:
        s = cx.sheet(desk, op['validated_by'], 'administrateur', op['id'])
    except Stop:
        return None
    if s['empreinte'] != op['approved_hash']:
        return None
    expire = datetime.fromisoformat(op['validated_at']) + timedelta(minutes=PERMIS_MINUTES)
    return P.Permis(P.empreinte_action(json.loads(op['payload_json'])), expire, op['id'])


# ------------------------------------------------------------------------------------------------ effets dans le navigateur supervisé (barrière obligatoire)
def _observer(pilote, profil):
    url, titre = pilote.info()
    if _hote(url) not in profil.get('origines', ()):
        raise Stop('page_inattendue_main_a_l_avocat')
    return url, titre, pilote.text()


def executer(desk, acteur, eid, seance, epoch, tab, ref, routine=False, maintenant_=None):
    """UN effet autorisé, dans la séance du navigateur supervisé : profil qualifié, permis exact non expiré (instantané COURANT), pause,
    barrière de prise de main (génération), page attendue, prix et options relus pour un achat, budget réservé AVANT le clic. Réponse
    perdue : « À rapprocher », jamais rejoué. Un clic réussi sans référence observée reste « À rapprocher »."""
    from . import navigateur56109 as N
    from .pause5629 import checkpoint
    a = acteur_de(acteur)
    ef = desk.db.execute('SELECT * FROM formalites_effets56109 WHERE id=?', (str(eid or ''),)).fetchone()
    if ef is None:
        raise Stop('introuvable')
    f = _formalite(desk, acteur, ef['formalite'])
    if ef['etat'] in ('envoi_en_cours', 'a_rapprocher', 'confirme'):
        raise Stop('effet_deja_tente_a_rapprocher')
    profil = PROFILS.get(f['profil'], {})
    if not profil.get('qualifie'):
        raise Stop('profil_non_qualifie')
    if f['auth_etat'] != 'effectuee':
        raise Stop('attente_connexion')
    snap = instantane(desk, f, ef['action'])
    from . import connexions5652 as cx
    if cx._sha(json.dumps(snap, ensure_ascii=False, sort_keys=True), '') != ef['empreinte']:
        _caduque(desk, ef, a.compte)
        raise Stop('autorisation_exacte_requise')
    permis = permis_courant(desk, ef) if ef['etat'] == 'autorise' else None
    P.verifier_permis(snap, permis, maintenant_ or datetime.now(timezone.utc), routine=bool(routine))   # routine : jamais un engagement
    checkpoint(desk)
    pilote = N.Pilote(tab, seance, epoch, desk)                 # chaque observation et action : barrière de prise de main (lot O)
    url, titre, page = _observer(pilote, profil)
    prix = f['montant_minor']
    if ef['action'] == 'acheter_document_infogreffe':
        if re.search(profil['repere_abonnement'], page):
            raise Stop('option_panier_non_autorisee')
        m = re.search(profil['repere_prix'], page)
        observe = _montant(m.group(1)) if m else None
        if observe is None:
            raise Stop('prix_panier_illisible')
        reserve = _reserver(desk, f, ef, observe)
        prix = observe
    desk.db.execute("UPDATE formalites_effets56109 SET etat='envoi_en_cours', modifie_le=? WHERE id=? AND etat='autorise'", (maintenant(), ef['id']))
    desk.db.execute("UPDATE connexion_ops5652 SET state='envoi_en_cours', attempt=attempt+1, updated=? WHERE id=?", (maintenant(), ef['operation']))
    _journal(desk, f['id'], 'effet_tente', a.compte, {'action': ef['action'], 'prix_minor': prix})
    desk.db.commit()
    try:
        pilote.click(ref)
        _u, _t, apres = _observer(pilote, profil)
    except Stop as ex:
        _incertain(desk, f, ef, a.compte, str(ex).split(':')[0])
        raise
    m = re.search(profil.get('repere_reference') or r'$^', apres)
    reference = m.group(1) if m else ''
    issue = P.rapprochement(soumis=True, recu_verifie=bool(reference), reference_distante=reference)
    if issue != 'confirme':
        _incertain(desk, f, ef, a.compte, 'reference_non_observee')
        return {'effet': ef['id'], 'etat': 'a_rapprocher', 'message': 'Action faite, référence non observée : à rapprocher (jamais rejouée).'}
    _confirmer(desk, f, ef, reference, a.compte)
    return {'effet': ef['id'], 'etat': 'confirme', 'reference': reference, 'message': '%s : confirmé (référence %s).' % (ACTIONS[ef['action']], reference)}


def _reserver(desk, f, ef, prix):
    """Budget de l'opération réservé AVANT le clic, dans une transaction : prix observé = prix autorisé, réservations + prix ≤ plafond."""
    op_key = '%s:%s' % (f['op_kind'], f['op_id']) if f['op_kind'] else 'dossier:' + f['matter']
    with transaction(desk):
        deja = desk.db.execute("SELECT COALESCE(SUM(montant_minor),0) FROM formalites_reservations56109 WHERE op_key=? AND etat IN ('reservee','consommee')", (op_key,)).fetchone()[0]
        P.verifier_panier(prix_minor=prix, autorise_minor=f['montant_minor'], plafond_minor=f['plafond_minor'] if f['plafond_minor'] is not None else -1,
                          reserve_minor=int(deja))
        desk.db.execute("INSERT INTO formalites_reservations56109(id,op_key,formalite,effet,montant_minor,devise,etat,cree_le,modifie_le) VALUES(?,?,?,?,?,?,'reservee',?,?)",
                        ('rs-' + uuid.uuid4().hex[:20], op_key, f['id'], ef['id'], prix, f['devise'], maintenant(), maintenant()))
    return prix


def _incertain(desk, f, ef, auteur, motif):
    desk.db.execute("UPDATE formalites_effets56109 SET etat='a_rapprocher', modifie_le=? WHERE id=?", (maintenant(), ef['id']))
    desk.db.execute("UPDATE connexion_ops5652 SET state='resultat_a_verifier', error=?, updated=? WHERE id=?", (motif, maintenant(), ef['operation']))
    if ef['action'] == 'acheter_document_infogreffe':
        desk.db.execute("UPDATE formalites56109 SET etat='achat_a_rapprocher', version=version+1, modifie_le=? WHERE id=?", (maintenant(), f['id']))
    _journal(desk, f['id'], 'effet_a_rapprocher', auteur, {'action': ef['action'], 'motif': motif})
    desk.db.commit()


ETAT_APRES = {'signer_inpi': None, 'payer_inpi': None, 'transmettre_inpi': 'transmise', 'acheter_document_infogreffe': 'commande_confirmee',
              'demander_rib_carpa': 'attente_carpa', 'signer_etranger': None, 'payer_etranger': None, 'transmettre_etranger': 'envoyee'}


def _confirmer(desk, f, ef, reference, auteur):
    with transaction(desk):
        desk.db.execute("UPDATE formalites_effets56109 SET etat='confirme', reference_distante=?, modifie_le=? WHERE id=?", (reference, maintenant(), ef['id']))
        desk.db.execute("UPDATE connexion_ops5652 SET state='envoyee', remote_ref=?, error='', updated=? WHERE id=?", (reference, maintenant(), ef['operation']))
        nouvel = ETAT_APRES[ef['action']]
        if nouvel:
            desk.db.execute('UPDATE formalites56109 SET etat=?, reference_distante=?, version=version+1, modifie_le=? WHERE id=?', (nouvel, reference, maintenant(), f['id']))
        desk.db.execute("UPDATE formalites_reservations56109 SET etat='consommee', modifie_le=? WHERE effet=?", (maintenant(), ef['id']))
        _journal(desk, f['id'], 'effet_confirme', auteur, {'action': ef['action'], 'reference': reference})
    if ef['action'] in ('acheter_document_infogreffe', 'payer_inpi', 'payer_etranger') and ef['prix_minor'] is not None:
        try:
            from .budget_ledger5689 import noter_quantite
            noter_quantite(desk.c['state_dir'], ef['id'] + ':montant', PORTAILS[f['portail']], 'montant_%s_%s' % (f['devise'].lower(), f['portail']),
                           Decimal(ef['prix_minor']) / 100, etat='declaree', operation=ef['action'], source='formalites56109', config=desk.c)
        except Stop as ex:   # le registre refuse : l'effet a eu lieu ; le refus est journalisé, jamais un faux zéro
            desk.audit('formalites56109_cout_non_inscrit', {'effet': ef['id'], 'motif': str(ex).split(':')[0]})


def rapprocher(desk, acteur, eid, seance, epoch, tab):
    """Lecture du portail (historique de commandes, liste des formalités) à travers la barrière : la référence retrouvée confirme ; sinon
    l'effet reste « À rapprocher ». Aucun nouveau clic d'effet."""
    from . import navigateur56109 as N
    a = acteur_de(acteur)
    ef = desk.db.execute('SELECT * FROM formalites_effets56109 WHERE id=?', (str(eid or ''),)).fetchone()
    if ef is None:
        raise Stop('introuvable')
    f = _formalite(desk, acteur, ef['formalite'])
    if ef['etat'] != 'a_rapprocher':
        raise Stop('effet_non_a_rapprocher')
    profil = PROFILS.get(f['profil'], {})
    pilote = N.Pilote(tab, seance, epoch, desk)
    _u, _t, page = _observer(pilote, profil)
    m = re.search(profil.get('repere_reference') or r'$^', page)
    if not m:
        return {'effet': ef['id'], 'etat': 'a_rapprocher', 'message': 'Aucune trace sur le portail : restez « À rapprocher » ; ne recommencez pas l’action.'}
    _confirmer(desk, f, ef, m.group(1), a.compte)
    return {'effet': ef['id'], 'etat': 'confirme', 'reference': m.group(1), 'message': 'Retrouvé sur le portail : confirmé.'}


def constater(desk, acteur, data):
    """Constat humain d'un effet « À rapprocher » : parti (référence lue sur le portail) ou non parti (réservation libérée)."""
    a = humain(acteur, valideur=True)
    ef = desk.db.execute('SELECT * FROM formalites_effets56109 WHERE id=?', (str(data.get('effet') or ''),)).fetchone()
    if ef is None:
        raise Stop('introuvable')
    f = _formalite(desk, acteur, ef['formalite'])
    if ef['etat'] != 'a_rapprocher':
        raise Stop('effet_non_a_rapprocher')
    constat = str(data.get('constat') or '')
    if constat == 'parti':
        reference = texte(data.get('reference'), 60)
        if len(reference) < 3:
            raise Refus('champs_invalides', {'reference': 'Référence lue sur le portail requise.'})
        _confirmer(desk, f, ef, reference, a.compte)
        return {'effet': ef['id'], 'etat': 'confirme', 'message': 'Constaté : parti (référence %s).' % reference}
    if constat != 'non_parti':
        raise Refus('champs_invalides', {'constat': 'Parti ou non parti.'})
    with transaction(desk):
        desk.db.execute("UPDATE formalites_effets56109 SET etat='annule', modifie_le=? WHERE id=?", (maintenant(), ef['id']))
        desk.db.execute("UPDATE connexion_ops5652 SET state='annulee', error=?, version=version+1, updated=? WHERE id=?", ('constate_non_parti_par_' + a.compte[:60], maintenant(), ef['operation']))
        desk.db.execute("UPDATE formalites_reservations56109 SET etat='liberee', modifie_le=? WHERE effet=?", (maintenant(), ef['id']))
        if ef['action'] == 'acheter_document_infogreffe':
            desk.db.execute("UPDATE formalites56109 SET etat='panier_a_autoriser', version=version+1, modifie_le=? WHERE id=?", (maintenant(), f['id']))
        _journal(desk, f['id'], 'constat_non_parti', a.compte, {'action': ef['action']})
    return {'effet': ef['id'], 'etat': 'annule', 'message': 'Constaté : non parti ; une nouvelle autorisation sera nécessaire.'}


def televerser(desk, acteur, fid, chemin, seance, epoch, tab, ref, client=None):
    """Téléversement du PDF EXACT d'une pièce de la formalité (``portails56109.DepotPDF``) : copie privée, contrôle avant et après
    (barrière de prise de main, pause, formalité, version), réception NON présumée (« À rapprocher »)."""
    from . import navigateur56109 as N
    a = humain(acteur)
    f = _formalite(desk, acteur, fid)
    p = desk.db.execute('SELECT * FROM formalites_pieces56109 WHERE formalite=? AND chemin=? ORDER BY ajoute_le DESC LIMIT 1', (f['id'], chemin)).fetchone()
    if p is None:
        raise Stop('introuvable')
    profil = PROFILS.get(f['profil'], {})
    if not profil.get('qualifie'):
        raise Stop('profil_non_qualifie')
    raw = _lire(desk, chemin, client)
    staging = Path(desk.c['state_dir']) / 'formalites56109-staging'
    staging.mkdir(mode=0o700, exist_ok=True)
    try:
        os.chmod(staging, 0o700)
    except OSError:
        pass
    dep = P.DepotPDF(str(staging), document_id=chemin, version=p['version'], nom=chemin.rsplit('/', 1)[-1], contenu=raw, sha256=p['sha256'])

    def controle(ticket):
        N.controler(seance, epoch, desk)                         # prise de main, arrêt, génération, pause globale
        actuel = desk.db.execute('SELECT sha256 FROM formalites_pieces56109 WHERE formalite=? AND chemin=? AND version=?', (f['id'], chemin, p['version'])).fetchone()
        if actuel is None or actuel['sha256'] != ticket['sha256']:
            raise Stop('version_document_modifiee')
    try:
        with N.barriere(seance, epoch, desk):
            out = dep.televerser(tab, ref, controle)
    except Stop:
        if dep.etat == 'a_rapprocher':
            desk.db.execute("UPDATE formalites_pieces56109 SET televersement='a_rapprocher' WHERE formalite=? AND chemin=? AND version=?", (f['id'], chemin, p['version']))
            desk.db.commit()
        else:
            dep.liberer()
        raise
    desk.db.execute("UPDATE formalites_pieces56109 SET televersement='a_rapprocher' WHERE formalite=? AND chemin=? AND version=?", (f['id'], chemin, p['version']))
    _journal(desk, f['id'], 'televersement_tente', a.compte, {'chemin': chemin, 'sha256': p['sha256']})
    desk.db.commit()
    return {**out, 'message': 'Fichier présenté au portail : réception à constater (accusé du portail).', '_depot': dep}


# ------------------------------------------------------------------------------------------------ documents reçus
def controle_pdf(raw, nature, siren=''):
    """Contrôle d'un document reçu (marqueurs, non cryptographique) : page de connexion, PDF invalide, facture commerciale, société non lisible."""
    tete = raw[:4096].lower()
    if not raw.startswith(b'%PDF-'):
        return 'page_connexion' if (b'<html' in tete or b'<!doctype' in tete or b'connexion' in tete or b'login' in tete) else 'pdf_invalide'
    if b'%%EOF' not in raw[-2048:]:
        return 'pdf_invalide'
    t = raw.decode('latin-1', 'replace').lower()
    if 'identifiez-vous' in t or 'mot de passe' in t:
        return 'page_connexion'
    attendu = {'infogreffe:kbis': ('kbis', 'extrait'), 'infogreffe:endettement': ('endettement', 'inscriptions'), 'carpa:rib_existant': ('rib', 'iban'),
               'carpa:rib_demande': ('rib', 'iban')}.get(nature)
    if attendu and 'facture' in t and not any(x in t for x in attendu):
        return 'facture_commerciale'
    if siren and siren not in re.sub(r'\D', '', t):
        return 'a_verifier'
    return 'ok'


def document(desk, acteur, data, client=None):
    """Document reçu du portail (collecté par la séance du navigateur ou importé à la main) : contrôlé, archivé INCHANGÉ dans le dossier,
    inscrit au registre commun (un seul document, lié à l'opération et au dossier). Un accusé n'est pas un RIB ; une page de connexion
    n'est pas un Kbis."""
    a = humain(acteur)
    f = _formalite(desk, acteur, data.get('formalite'))
    version_attendue(f, data.get('version'))
    type_ = str(data.get('type') or 'pdf_officiel')
    if type_ not in ('pdf_officiel', 'synthese', 'recu', 'accuse', 'rib', 'bordereau'):
        raise Refus('champs_invalides', {'type': 'Type de document inconnu.'})
    chemin = str(data.get('chemin') or '')
    m = next((x for x in load_matters(desk.c) if x['id'] == f['matter']), None)
    if not m or not chemin.startswith(m['path'].rstrip('/') + '/') or '..' in chemin:
        raise Refus('champs_invalides', {'chemin': 'Document hors du dossier.'})
    raw = _lire(desk, chemin, client)
    ctrl = controle_pdf(raw, f['nature'], f['siren'] if type_ == 'pdf_officiel' else '') if type_ in ('pdf_officiel', 'rib') else (
        'ok' if raw.startswith(b'%PDF-') else 'pdf_invalide')
    if ctrl in ('page_connexion', 'pdf_invalide', 'facture_commerciale') and type_ in ('pdf_officiel', 'rib'):
        with transaction(desk):
            _journal(desk, f['id'], 'document_refuse', a.compte, {'chemin': chemin, 'controle': ctrl})
        raise Refus('document_non_conforme', {'chemin': {'page_connexion': 'Page de connexion enregistrée : ce n’est pas le document officiel.',
                                                         'pdf_invalide': 'PDF illisible ou incomplet.',
                                                         'facture_commerciale': 'C’est une facture : enregistrez-la comme facture d’achat.'}[ctrl]})
    if type_ == 'rib' and ctrl == 'ok':      # bon dossier, bon sous-compte : les références propres à l'affaire doivent figurer sur le RIB
        t = raw.decode('latin-1', 'replace').casefold()
        refs = [json.loads(f['donnees']).get(k, {}).get('valeur', '') for k in ('reference_affaire', 'sous_compte')]
        if any(r and r.casefold() not in t for r in refs):
            ctrl = 'a_verifier'
    empreinte = hashlib.sha256(raw).hexdigest()
    iban = re.sub(r'\s+', '', str(data.get('iban') or '')).upper()
    if iban and (type_ != 'rib' or not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{10,30}', iban)):
        raise Refus('champs_invalides', {'iban': 'IBAN lu sur le RIB seulement (jamais généré).'})
    livrable = ''
    try:
        from . import livrables5689
        livrables5689.ensure_schema(desk)
        livrable = livrables5689.observe(desk, {'service': 'formalites56109', 'object': f['id'], 'version': empreinte[:24], 'matter': f['matter'], 'path': chemin,
                                                'sha256': empreinte, 'size': len(raw), 'title': '%s — %s' % (NATURES[f['nature']][1], chemin.rsplit('/', 1)[-1]),
                                                'review_required': False, 'provenance': {'portail': f['portail'], 'formalite': f['id'], 'type': type_}}) or ''
    except (Stop, sqlite3.Error, KeyError) as ex:
        desk.audit('formalites56109_registre_indisponible', {'formalite': f['id'], 'cause': type(ex).__name__})
    nouvel = f['etat']
    if type_ == 'pdf_officiel':
        nouvel = 'document_recu' if ctrl == 'ok' else 'document_a_verifier'
    elif type_ == 'accuse':
        nouvel = 'attente_carpa' if f['portail'] == 'carpa' else f['etat']
    elif type_ == 'rib':
        nouvel = 'rib_recu' if ctrl == 'ok' else 'rib_a_verifier'
    elif type_ == 'bordereau':
        nouvel = 'bordereau_prepare'
    did = 'do-' + uuid.uuid4().hex[:20]
    with transaction(desk):
        desk.db.execute('INSERT OR IGNORE INTO formalites_documents56109(id,formalite,type,chemin,sha256,octets,provenance,numero_commande,emis_le,recupere_le,controle,livrable,'
                        'iban_masque,iban_empreinte) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (did, f['id'], type_, chemin, empreinte, len(raw), texte(data.get('provenance'), 200) or PORTAILS[f['portail']], texte(data.get('numero_commande'), 60) or f['reference_distante'],
                         jour(data.get('emis_le')), maintenant(), ctrl, livrable, _masquer_iban(iban) if iban else '',
                         hashlib.sha256(('iban56109|' + iban).encode()).hexdigest() if iban else ''))
        desk.db.execute('UPDATE formalites56109 SET etat=?, version=version+1, modifie_le=? WHERE id=?', (nouvel, maintenant(), f['id']))
        _journal(desk, f['id'], 'document_' + type_, a.compte, {'chemin': chemin, 'sha256': empreinte, 'controle': ctrl, 'iban': _masquer_iban(iban) if iban else ''})
    if iban:
        try:      # une coordonnée reçue est versionnée « À vérifier » par le lot G1 (vérification indépendante) ; aucun paiement
            from .gestion56109 import changer_coordonnees
            ref = json.loads(f['donnees']).get('reference_affaire', {}).get('valeur') or f['matter']
            changer_coordonnees(desk, acteur, {'dossier': f['matter'], 'beneficiaire': 'CARPA — %s' % ref, 'iban': iban, 'provenance': 'saisie',
                                               'recu_le': maintenant()[:10]})
        except ImportError:
            pass
    return {'document': did, 'controle': ctrl, 'etat': nouvel, 'message': {'document_recu': 'Document officiel reçu et archivé inchangé.',
                                                                           'document_a_verifier': 'Document archivé, à vérifier (société non lisible).',
                                                                           'attente_carpa': 'Accusé enregistré : le RIB n’est pas encore délivré.',
                                                                           'rib_recu': 'RIB reçu et archivé ; coordonnées à vérifier.'}.get(nouvel, 'Document enregistré.')}


def facture(desk, acteur, data, client=None):
    """Facture d'achat : justificatif de frais DISTINCT du document officiel, inscrit aux frais du dossier par le lot G1 (aucun paiement)."""
    a = humain(acteur)
    f = _formalite(desk, acteur, data.get('formalite'))
    if not any(x['etat'] == 'confirme' and x['action'] in ('acheter_document_infogreffe', 'payer_inpi', 'payer_etranger') for x in effets(desk, f['id'])):
        raise Refus('champs_invalides', {'facture': 'Aucun achat confirmé pour cette formalité.'})
    chemin = str(data.get('chemin') or '')
    m = next((x for x in load_matters(desk.c) if x['id'] == f['matter']), None)
    if not m or not chemin.startswith(m['path'].rstrip('/') + '/'):
        raise Refus('champs_invalides', {'chemin': 'Facture hors du dossier.'})
    raw = _lire(desk, chemin, client)
    empreinte = hashlib.sha256(raw).hexdigest()
    from .gestion56109 import enregistrer_frais_achat
    frais = enregistrer_frais_achat(desk, acteur, f['matter'], {'cle': texte(data.get('cle'), 80) or 'fo-frais-' + empreinte[:20], 'nature': 'frais',
                                                                 'montant_ht': data.get('montant_ht'), 'jour': data.get('jour') or maintenant()[:10],
                                                                 'libelle': '%s — facture %s' % (PORTAILS[f['portail']], f['reference_distante'] or chemin.rsplit('/', 1)[-1])})
    with transaction(desk):
        desk.db.execute("INSERT OR IGNORE INTO formalites_documents56109(id,formalite,type,chemin,sha256,octets,provenance,numero_commande,recupere_le,controle,frais) "
                        "VALUES(?,?,'facture_achat',?,?,?,?,?,?,'ok',?)", ('do-' + uuid.uuid4().hex[:20], f['id'], chemin, empreinte, len(raw), PORTAILS[f['portail']],
                                                                         f['reference_distante'], maintenant(), str(frais.get('id') or '')))
        _journal(desk, f['id'], 'facture_achat', a.compte, {'chemin': chemin, 'frais': frais.get('id')})
    return {'frais': frais.get('id'), 'message': 'Facture d’achat inscrite aux frais du dossier (distincte du document officiel).'}


ROUTES = {'preparer': preparer, 'donnees': donnees, 'piece': lambda d, a, x: piece(d, a, x), 'synthese': synthese, 'etat': etat, 'authentification': authentification,
          'demander': demander, 'autoriser': autoriser, 'annuler': annuler, 'constater': constater, 'document': lambda d, a, x: document(d, a, x),
          'facture': lambda d, a, x: facture(d, a, x), 'panier': panier}


# ------------------------------------------------------------------------------------------------ écrans (Corporate, Cession, vue financière)
def _carte(desk, prefix, fid):
    from . import connexions5652 as cx
    f = desk.db.execute('SELECT * FROM formalites56109 WHERE id=?', (fid,)).fetchone()
    if f is None:
        return ''
    f = dict(f)
    tableau = json.loads(f['donnees'])
    lignes = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td><small>%s</small></td></tr>' % (
        e(v.get('libelle', k)), e(v.get('valeur') or '—'), e({'verifiee': 'vérifiée', 'manquante': 'manquante', 'contradictoire': 'contradictoire',
                                                              'a_verifier': 'à vérifier'}.get(v.get('statut'), v.get('statut', ''))), e(v.get('source') or '')) for k, v in tableau.items())
    pcs = ''.join('<li>%s <small>(%s, %d octets, %s…)%s</small></li>' % (e(p['chemin'].rsplit('/', 1)[-1]), e(p['rubrique']), p['octets'], e(p['sha256'][:12]),
                                                                          ' — téléversement à constater' if p['televersement'] == 'a_rapprocher' else '') for p in pieces(desk, fid))
    docs = ''.join('<li>%s — %s <small>(%s, %s)</small></li>' % (e(d['type']), e(d['chemin'].rsplit('/', 1)[-1]), e(d['controle']), e(d['iban_masque'] or d['sha256'][:12]))
                   for d in documents(desk, fid))
    effs = ''
    for x in effets(desk, fid):
        geste = ''
        if x['etat'] == 'a_autoriser':
            try:
                s = cx.sheet(desk, 'cabinet', 'administrateur', x['operation'])
                geste = _form('formalites/autoriser', _cache(effet=x['id'], version=s['version'], empreinte=s['empreinte']) +
                              '<label class="g3-case"><input type="checkbox" name="relu" value="1" required> J’ai relu ce qui engage le cabinet (compte, société, données, PDF, prix)</label>',
                              'Autoriser cet effet', 'ghost')
            except Stop:
                geste = ''
        elif x['etat'] == 'a_rapprocher':
            geste = _form('formalites/constater', _cache(effet=x['id']) + '<label>Constat<select name="constat"><option value="parti">Parti (référence lue)</option>'
                          '<option value="non_parti">Non parti</option></select></label><label>Référence<input name="reference"></label>', 'Enregistrer le constat', 'ghost')
        effs += '<li><strong>%s</strong> — %s%s%s</li>' % (e(ACTIONS[x['action']]), e(EFFETS[x['etat']]), (' · ' + e(x['reference_distante'])) if x['reference_distante'] else '', geste)
    demandes = ''.join(_form('formalites/demander', _cache(formalite=fid, action=act), 'Demander l’autorisation : ' + ACTIONS[act], 'ghost')
                       for act in ACTIONS_PAR_PORTAIL[f['portail']])
    profil = PROFILS.get(f['profil'], {})
    return ('<article class="g3-formalite"><h4>%s <span class="g3-badge">%s</span></h4><p class="ax-muted">%s · profil %s (%s)%s</p>'
            '<table class="o5636-grid"><thead><tr><th>Donnée</th><th>Valeur</th><th>Statut</th><th>Source</th></tr></thead><tbody>%s</tbody></table>'
            '<h5>Pièces exactes</h5><ul>%s</ul><h5>Effets (autorisations distinctes)</h5><ul>%s</ul>%s<h5>Documents reçus</h5><ul>%s</ul><ul class="ax-muted">%s</ul></article>') % (
        e(NATURES[f['nature']][1]), e(ETATS.get(f['etat'], f['etat'])), e(f['societe'] or f['matter']) + ((' · SIREN ' + e(f['siren'])) if f['siren'] else ''),
        e(profil.get('label', f['profil'])), 'non qualifié' if not profil.get('qualifie') else 'qualifié',
        (' · portail sans brouillon : préparez tout ici avant de l’ouvrir' if f['portail'] == 'etranger' and not profil.get('brouillon') else ''),
        lignes or '<tr><td colspan="4" class="ax-muted">Aucune donnée.</td></tr>', pcs or '<li class="ax-muted">Aucune pièce.</li>',
        effs or '<li class="ax-muted">Aucune autorisation demandée.</li>', demandes, docs or '<li class="ax-muted">Aucun document.</li>',
        ''.join('<li>%s</li>' % e(m) for m in _mentions(f)))


def fiche_operation_html(desk, prefix, owner, role, op_id):
    """5.6.109 (intégration I1) : fiche d'une autorisation de formalité ouverte depuis la file « À décider » (``/operation?id=…``, fournisseurs
    ``portail_*`` du registre commun ``connexion_ops5652``). Remplace le bouton générique « Valider et transmettre », qui refusait ces
    opérations : l'accord passe par la route des formalités (relecture, empreinte et version de la fiche, rôle de valideur, marque de la route
    POST). Dossier contrôlé avant tout contenu ; absente ou interdite : le même 404 neutre. Lecture seule."""
    from .closing56109 import HUMAINS, VALIDEURS
    acteur = (owner, role)
    ef = desk.db.execute('SELECT * FROM formalites_effets56109 WHERE operation=?', (str(op_id or ''),)).fetchone()
    if ef is None:
        raise Stop('introuvable')
    fe = fiche_effet(desk, acteur, ef['id'])                   # contrôle du dossier (404 neutre) puis fiche exacte du registre commun
    f = _formalite(desk, acteur, ef['formalite'])
    donnees = ''.join('<li><span>%s</span> %s</li>' % (e(str(k)), e(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))[:300])
                      for k, v in (fe['donnees'] or {}).items()) or '<li class="ax-muted">Aucune donnée.</li>'
    gestes = ''
    if fe['etat'] == 'a_autoriser' and str(role) in VALIDEURS:
        gestes += _form('formalites/autoriser', _cache(effet=fe['effet'], version=fe['version'], empreinte=fe['empreinte']) +
                        '<label class="g3-case"><input type="checkbox" name="relu" value="1" required> J’ai relu ce qui engage le cabinet (compte, société, données, PDF, prix)</label>',
                        'Autoriser cet effet')
    elif fe['etat'] == 'a_autoriser':
        gestes += '<p class="ax-muted">%s</p>' % e('Autorisation réservée à un avocat ou à l’administrateur du cabinet.')
    if fe['etat'] in ('a_autoriser', 'autorise', 'a_revalider') and str(role) in HUMAINS:
        gestes += _form('formalites/annuler', _cache(effet=fe['effet']), 'Annuler cette autorisation', 'ghost')
    profil = PROFILS.get(f['profil'], {})
    return ('<section class="cx53-sheet g3-fiche-effet"><h2>%s</h2><p class="cx52-state">%s</p><dl class="cx53-dl"><dt>Dossier</dt><dd>%s</dd>'
            '<dt>Formalité</dt><dd>%s — %s</dd><dt>Portail</dt><dd>%s (%s)</dd><dt>Montant</dt><dd>%s</dd></dl><h3>Ce qui engage le cabinet</h3>'
            '<ul class="cx53-list">%s</ul><p class="ax-muted">%s</p>%s</section>') % (
        e(fe['action_libelle']), e(fe['etat_libelle']), e(f['matter']), e(NATURES[f['nature']][1]), e(f['societe'] or ''),
        e(profil.get('label', f['profil'])), 'qualifié' if profil.get('qualifie') else 'non qualifié : aucun effet ne sera exécuté',
        e(('%.2f €' % (f['montant_minor'] / 100)).replace('.', ',')) if f.get('montant_minor') else '—', donnees,
        e('Autoriser n’envoie rien : l’effet part ensuite dans le navigateur supervisé, pour ce contenu exact, pendant %d minutes.' % PERMIS_MINUTES), gestes)


def section_operation_html(desk, prefix, op_kind, op_id, hidden=()):
    """Bloc « Formalités supervisées » d'une opération (Corporate ou Cession) : lecture seule ; ne lève jamais."""
    try:
        rows = desk.db.execute('SELECT id, matter, op_kind FROM formalites56109 WHERE op_kind=? AND op_id=? ORDER BY cree_le', (op_kind, str(op_id))).fetchall()
        cartes = ''.join(_carte(desk, prefix, r['id']) for r in rows if r['matter'] not in hidden)
        op_type = ''
        if op_kind == 'corporate5636':
            r = desk.db.execute('SELECT kind FROM operations5636 WHERE id=?', (str(op_id),)).fetchone()
            op_type = r['kind'] if r else ''
    except (Stop, sqlite3.Error):
        return ''
    ident = _cache(op_kind=op_kind, op_id=op_id)
    boutons = ''
    if op_kind == 'corporate5636' and op_type == 'siege':
        boutons += _form('formalites/preparer', ident + '<input type="hidden" name="nature" value="inpi:modification">', 'Préparer sur le Guichet unique (INPI)', 'ghost')
    boutons += _form('formalites/preparer', ident + '<input type="hidden" name="nature" value="infogreffe:kbis"><label>SIREN<input name="siren" inputmode="numeric"></label>'
                     '<label class="g3-case"><input type="checkbox" name="siren_confirme" value="1"> Société confirmée</label><label>Fraîcheur exigée (jours)<input name="fraicheur_jours" '
                     'inputmode="numeric"></label>', 'Obtenir un Kbis', 'ghost')
    boutons += _form('formalites/preparer', ident + '<input type="hidden" name="nature" value="infogreffe:endettement"><label>SIREN<input name="siren" inputmode="numeric"></label>'
                     '<label class="g3-case"><input type="checkbox" name="siren_confirme" value="1"> Société confirmée</label>', 'Obtenir un état d’endettement', 'ghost')
    etrangers = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v['label'])) for k, v in PROFILS.items() if v['portail'] == 'etranger')
    boutons += _form('formalites/preparer', ident + '<input type="hidden" name="nature" value="etranger:formalite"><label>Service étranger<select name="profil">%s</select></label>' % etrangers,
                     'Préparer une formalité étrangère', 'ghost')
    return ('<section class="ax-card g3-formalites" id="g3-formalites"><h3>Formalités supervisées</h3><p class="ax-muted">Préparation et suivi sur l’opération : chaque '
            'signature, paiement, achat ou transmission demande SON autorisation ; aucun portail réel n’est qualifié dans cette version.</p>%s<div class="g3-gestes">%s</div></section>') % (
        cartes, boutons)


def section_finances_html(desk, prefix, acteur, mid):
    """RIB CARPA documentaire dans la vue financière existante du dossier (sous « Fonds, frais et débours ») ; ne lève jamais."""
    try:
        dossier_visible(desk, acteur, mid)
        rows = desk.db.execute("SELECT id FROM formalites56109 WHERE matter=? AND portail='carpa' ORDER BY cree_le", (mid,)).fetchall()
    except (Stop, sqlite3.Error):
        return ''
    cartes = ''.join(_carte(desk, prefix, r['id']) for r in rows)
    ident = _cache(dossier=mid)
    gestes = (_form('formalites/preparer', ident + '<input type="hidden" name="nature" value="carpa:rib_existant">', 'Récupérer un RIB CARPA déjà émis', 'ghost') +
              _form('formalites/preparer', ident + '<input type="hidden" name="nature" value="carpa:rib_demande">', 'Préparer une demande de RIB CARPA', 'ghost'))
    return ('<section class="d51-block g3-carpa" id="g3-carpa"><h3>RIB CARPA (documentaire)</h3><p class="ax-muted">Demande et récupération du RIB de l’affaire : '
            'aucun virement, retrait ni libération de fonds n’est possible ici. Un accusé de demande ne vaut pas délivrance du RIB.</p>%s<div class="g3-gestes">%s</div></section>') % (
        cartes, gestes)


def habiller(desk, env, prefix, path, args, shell):
    """Point de montage dans la page Dossier (``web440``) : la section RIB CARPA est insérée après « Fonds, frais et débours » (lot G1),
    ou sous le bloc Honoraires et facturation. Un incident n'empêche jamais la page de s'afficher ; aucune écriture."""
    from .web567 import actor
    acteur = actor(env)

    def inserer(html):
        try:
            if path == '/dossier' and args.get('id'):
                sec = section_finances_html(desk, prefix, acteur, str(args['id']))
                if not sec:
                    return html
                for repere in ('id="g109-finances"', 'b53-matter-block'):
                    i = html.find(repere)
                    if i >= 0:
                        j = html.find('</section>', i)
                        if j >= 0:
                            return html[:j + 10] + sec + html[j + 10:]
        except Exception:   # noqa: BLE001 — la page existante reste affichée telle quelle
            import logging
            logging.getLogger(__name__).exception('formalites56109 : section non insérée (%s)', path)
        return html

    def habillee(title, html, *a, **k):
        return shell(title, inserer(html), *a, **k)
    return habillee
