"""5.6.88 : export intégral du cabinet dans des formats ouverts, et vérification de son manifeste.

Une archive ZIP : pour chaque domaine (dossiers, contacts, factures, temps, agenda, tâches, notes, messagerie interne, modèles, compétences), des fichiers
JSON et CSV lisibles sans AxiorHub ; ``MANIFEST.json`` donne la taille et l'empreinte SHA-256 de chaque fichier ; ``LISEZMOI.txt`` décrit le contenu.

Confidentialité :
- réservé à l'administrateur, chaque demande, téléchargement et suppression est tracé ;
- liste fermée de tables (jamais de table de comptes, de sessions ou de jetons) ; colonnes et clés JSON de nature secrète retirées ; valeurs reconnaissables
  (jetons, clés, coffre) remplacées ; le coffre et les fichiers de configuration n'y entrent pas ;
- dossiers réservés : exclus par défaut, avec tout ce qui s'y rattache ; les inclure est un choix explicite, tracé et rappelé dans le LISEZMOI ;
- fichier non chiffré : il vit dans le dossier privé de l'état, 24 heures au plus, et disparaît après son premier téléchargement complet (rétention).

Les documents restent dans Nextcloud : l'export en donne l'inventaire et les empreintes connues (``documents/inventaire``) ; les inclure est une option, bornée.
Volume borné : un plafond dépassé arrête l'export (jamais d'export silencieusement partiel), sauf le plafond des documents, où le reste est listé « non inclus ».
"""
import base64
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import unicodedata
import uuid
import zipfile

from .common import Stop, load_matters
from . import sauvegarde5688 as sv

JOB = 'export5688'
DOSSIER = 'exports5688'
FORMAT = 'axiorhub-export-1'
EXPIRATION_HEURES = 24
CONSERVES = 3                                  # fichiers prêts au plus : une demande de plus retire le plus ancien
HISTORIQUE = 100
MAX_LIGNES = 2_000_000                         # par table
MAX_OCTETS = 40 * 1024 ** 3                    # contenu total, documents compris
MAX_DOCUMENTS_OCTETS = 20 * 1024 ** 3
MAX_DOCUMENT = 100 * 1024 * 1024
MAX_MANIFESTE = 256 * 1024 * 1024
MORCEAU_DEPOT = 16 * 1024 * 1024               # le proxy du serveur limite une requête à 25 Mo : l'envoi d'un export à vérifier se fait par morceaux
MAX_DEPOT = MAX_OCTETS
MARGE_DISQUE = 2 * 1024 ** 3
EXPORT_ID = re.compile(r'^[0-9a-f]{32}$')
RETIRE = '[secret retiré]'
# Clés dont la valeur n'a pas à sortir, en colonne comme à l'intérieur d'un JSON.
CLE_SECRETE = re.compile(r'(?i)(^|[_\-.])(token|tokens|jeton|jetons|secret|secrets|password|passwd|passphrase|pass|pwd|mot_?de_?passe|api_?key|apikey|cle_?api|access_?key|private_?key|'
                         r'authorization|cookie|cookies|salt|credential|credentials|bearer|ciphertext)($|[_\-.])')
VALEUR_SECRETE = re.compile(r'AXIORHUB-VAULT-V1:\S+|\bgAAAAA[\w-]{40,}|\bBearer\s+[\w.\-~+/=]{16,}|\bsk-[\w-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b', re.S)

# (domaine, fichier, table, base, filtre) ; filtre : colonne du dossier, ou (colonne, table parente, clé parente, colonne du dossier) pour un rattachement indirect.
DESK, DOCS = 'desk.sqlite3', 'documents.sqlite3'
TABLES = (
    ('dossiers', 'portefeuille', 'matter_portfolio', DESK, 'matter'), ('dossiers', 'fiches', 'fiche5627', DESK, 'matter'), ('dossiers', 'faits', 'case_facts', DESK, 'matter'),
    ('dossiers', 'syntheses', 'case_briefs', DESK, 'matter'), ('dossiers', 'parties', 'd51_parties', DESK, 'matter'), ('dossiers', 'evenements', 'd51_events', DESK, 'matter'),
    ('dossiers', 'echeances', 'deadlines450', DESK, 'matter'), ('dossiers', 'prescriptions', 'limitation500', DESK, 'matter'), ('dossiers', 'parties_conflits', 'conflicts500_parties', DESK, 'matter'),
    ('dossiers', 'documents_prepares', 'prepared_items5689', DESK, 'matter_id'), ('dossiers', 'representations_des_documents', 'prepared_representations5689', DESK, ('item_id', 'prepared_items5689', 'id', 'matter_id')),
    ('dossiers', 'relectures_des_documents', 'prepared_reviews5689', DESK, ('item_id', 'prepared_items5689', 'id', 'matter_id')), ('dossiers', 'liens_des_documents', 'prepared_source_links5689', DESK, ('item_id', 'prepared_items5689', 'id', 'matter_id')),
    ('dossiers', 'anciens_marqueurs_de_relecture', 'prepared_legacy_marks5689', DESK, None), ('dossiers', 'controles_des_documents', 'document_checks5689', DESK, ('item_id', 'prepared_items5689', 'id', 'matter_id')),
    ('dossiers', 'propositions_de_modification', 'document_patches5689', DESK, ('item_id', 'prepared_items5689', 'id', 'matter_id')), ('dossiers', 'exports_des_documents', 'prepared_exports5689', DESK, ('item_id', 'prepared_items5689', 'id', 'matter_id')),
    ('dossiers', 'versions_des_sources', 'source_versions5689', DESK, 'matter'), ('dossiers', 'passages_des_sources', 'source_segments5689', DESK, ('version_id', 'source_versions5689', 'version_id', 'matter')),
    ('dossiers', 'elements_du_dossier', 'case_assertions5689', DESK, 'matter'), ('dossiers', 'relations_des_elements', 'assertion_relations5689', DESK, 'matter'), ('dossiers', 'decisions_sur_les_elements', 'assertion_events5689', DESK, 'matter'),
    ('dossiers', 'roles_des_personnes', 'person_roles5689', DESK, 'matter'), ('dossiers', 'rapprochements', 'rapprochements5689', DESK, 'matter_a'), ('dossiers', 'lectures_des_sources', 'extraction_runs5689', DESK, 'matter'),
    ('dossiers', 'assistances_contextuelles', 'assistances5689', DESK, 'matter'), ('dossiers', 'missions_de_conseil', 'missions_conseil5689', DESK, 'matter'), ('dossiers', 'etapes_des_missions', 'mission_etapes5689', DESK, ('mission', 'missions_conseil5689', 'mission_id', 'matter')),
    ('dossiers', 'revues_de_contrat', 'revues5689', DESK, 'matter'), ('dossiers', 'propositions_de_revue', 'revue_propositions5689', DESK, ('revue', 'revues5689', 'id', 'matter')),
    ('dossiers', 'accords_signes', 'accords5689', DESK, 'matter'), ('dossiers', 'obligations', 'obligations5689', DESK, 'matter'), ('dossiers', 'occurrences_des_obligations', 'occurrences5689', DESK, 'matter'),
    ('modeles', 'playbooks', 'playbooks5689', DESK, None), ('modeles', 'regles_des_playbooks', 'playbook_regles5689', DESK, None), ('modeles', 'propositions_de_regles', 'playbook_propositions5689', DESK, 'matter'),
    ('dossiers', 'documents_du_registre', 'registre_docs5688', DESK, 'matter'), ('dossiers', 'memoire_proposee', 'memoire5688', DESK, 'matter'),
    ('contentieux', 'pretentions', 'contentieux_claims5689', DESK, 'matter'), ('contentieux', 'liens_de_preuve', 'contentieux_links5689', DESK, 'matter'), ('contentieux', 'journal', 'contentieux_journal5689', DESK, 'matter'),
    ('contentieux', 'pieces_par_partie', 'contentieux_exhibits5689', DESK, 'matter'), ('contentieux', 'renumerotations', 'contentieux_renumerotations5689', DESK, 'matter'), ('contentieux', 'categories_des_documents', 'contentieux_categories5689', DESK, 'matter'),
    ('contentieux', 'actes', 'contentieux_actes5689', DESK, 'matter'), ('contentieux', 'sections_des_actes', 'contentieux_sections5689', DESK, ('acte_id', 'contentieux_actes5689', 'id', 'matter')), ('contentieux', 'dossiers_d_audience', 'contentieux_audiences5689', DESK, 'matter'),
    ('contentieux', 'paquets', 'contentieux_paquets5689', DESK, 'matter'), ('contentieux', 'autorisations_de_paquet', 'contentieux_autorisations5689', DESK, ('paquet_id', 'contentieux_paquets5689', 'id', 'matter')), ('contentieux', 'recus_de_depot', 'contentieux_recus5689', DESK, ('paquet_id', 'contentieux_paquets5689', 'id', 'matter')),
    ('contentieux', 'candidats_delais', 'deadline_candidates5689', DESK, 'matter'), ('contentieux', 'validations_de_regles', 'deadline_rule_validations5689', DESK, None),
    ('contacts', 'contacts', 'contacts5636', DESK, None), ('contacts', 'liens_dossiers', 'contact_links5636', DESK, 'matter'), ('contacts', 'societes', 'companies5636', DESK, 'matter'),
    ('contacts', 'opportunites', 'opp5676', DESK, 'matter'), ('contacts', 'fiches_des_registres', 'fiche_registre5688', DESK, None),
    ('gouvernance', 'decisions_sur_les_donnees', 'policy_decisions5689', DESK, None), ('gouvernance', 'destinations_observees', 'flux5689', DESK, None), ('gouvernance', 'plafonds', 'budget_caps5689', DESK, None),
    ('gouvernance', 'competences_figees_par_mission', 'mission_skills5689', DESK, None), ('gouvernance', 'prestataires', 'prestataires5689', DESK, None), ('gouvernance', 'couts_mensuels', 'couts_mensuels5689', DESK, None), ('gouvernance', 'reservations_budget', 'budget_reservations5689', DESK, None),
    ('gouvernance', 'credits_et_remises', 'budget_credits5689', DESK, None), ('gouvernance', 'journal_du_budget', 'budget_events5689', DESK, None),
    ('contacts', 'societes_suivies', 'societes5688', DESK, None), ('contacts', 'annonces_relevees', 'annonces5688', DESK, None),
    ('factures', 'documents_de_facturation', 'b53_documents', DESK, 'matter'), ('factures', 'versions', 'b53_versions', DESK, ('document', 'b53_documents', 'id', 'matter')),
    ('factures', 'paiements', 'b53_payments', DESK, ('document', 'b53_documents', 'id', 'matter')), ('factures', 'imputations', 'b53_allocations', DESK, ('document', 'b53_documents', 'id', 'matter')),
    ('factures', 'clients', 'b53_clients', DESK, 'matter'), ('factures', 'estimations', 'b53_estimates', DESK, 'matter'), ('factures', 'entites', 'b53_entities', DESK, None),
    ('factures', 'factures_importees', 'fees500_invoices', DESK, 'matter'), ('factures', 'accuses_de_paiement', 'accuses5688', DESK, 'matter'),
    ('temps', 'temps_valides', 'time500_entries', DESK, 'matter'), ('temps', 'temps_proposes', 'time500_items', DESK, 'matter'), ('temps', 'conditions', 'time500_terms', DESK, 'matter'),
    ('temps', 'contribution_parts', 'contrib5688_parts', DESK, None), ('temps', 'contribution_cles', 'contrib5688_cles', DESK, None),
    ('agenda', 'evenements', 'agenda5634', DESK, 'matter'), ('agenda', 'audiences', 'audiences5688', DESK, 'matter'),
    ('taches', 'taches', 'tasks', DESK, 'matter'), ('taches', 'taches_de_travail', 'work_tasks_v211', DESK, 'matter'),
    ('taches', 'suivi_des_taches', 'taches5688', DESK, ('task_id', 'tasks', 'id', 'matter')),
    ('notes', 'notes', 'notes', DESK, None), ('notes', 'memos', 'memos5636', DESK, 'matter'), ('notes', 'reponses_ia_tardives', 'reponses_tardives5689', DESK, 'matter'),
    ('notes', 'journal_de_rangement', 'rangement5689', DESK, 'matter'), ('notes', 'notes_communes', 'note_commune5688', DESK, 'matter'), ('notes', 'versions_des_notes_communes', 'note_versions5688', DESK, 'matter'),
    ('messagerie_interne', 'fils', 'fils5650', DESK, 'matter'), ('messagerie_interne', 'messages', 'messages5650', DESK, ('thread', 'fils5650', 'id', 'matter')),
    ('messagerie_interne', 'participants', 'participants5650', DESK, ('thread', 'fils5650', 'id', 'matter')),
    ('modeles', 'modeles_du_cabinet', 'cabinet_templates_v330', DESK, None), ('modeles', 'modeles_de_facturation', 'b53_templates', DESK, None), ('modeles', 'bibliotheque', 'bibliotheque5639', DESK, None),
    ('modeles', 'versions_de_modeles', 'template_versions5684', DESK, None),
    ('competences', 'competences', 'skills5632', DESK, None), ('competences', 'etat_des_competences', 'skill_state5632', DESK, None), ('competences', 'ressources', 'skill_resources5683', DESK, None),
    ('competences', 'savoir_faire', 's59_resources', DESK, None), ('competences', 'versions_savoir_faire', 's59_versions', DESK, 'origin_matter'),
    ('cabinet', 'profil', 'cabinet_profile5689', DESK, None), ('cabinet', 'versions_du_profil', 'cabinet_profile_versions5689', DESK, None), ('cabinet', 'adresses_propres', 'identity_aliases5689', DESK, None),
    ('cabinet', 'propositions', 'cabinet_proposals5689', DESK, None), ('cabinet', 'assistant_de_demarrage', 'cabinet_wizard5689', DESK, None), ('cabinet', 'preuves_de_capacite', 'capability_proofs5689', DESK, None),
    ('agenda', 'rappels', 'rappels5688', DESK, 'matter'), ('agenda', 'preferences_de_rappel', 'rappels_pref5688', DESK, None), ('taches', 'mentions_de_taches', 'mentions_taches5688', DESK, ('tache', 'tasks', 'id', 'matter')),
    ('gouvernance', 'ventilation_du_budget', 'budget_allocations5689', DESK, None), ('cabinet', 'cloisons_de_dossier', 'cloud_cloisons5688', DESK, 'matter'), ('cabinet', 'identites_du_stockage', 'cloud_identites5688', DESK, None),
    ('qualification', 'protocoles', 'banc_protocoles5689', DESK, None), ('qualification', 'cas', 'banc_cas5689', DESK, None), ('qualification', 'configurations', 'banc_configurations5689', DESK, None),
    ('qualification', 'essais', 'banc_runs5689', DESK, None), ('qualification', 'evenements', 'banc_evenements5689', DESK, None), ('qualification', 'resultats', 'banc_essais5689', DESK, None),
    ('qualification', 'controles', 'banc_controles5689', DESK, None), ('qualification', 'avis_de_relecteur', 'banc_avis5689', DESK, None), ('qualification', 'erreurs_critiques', 'banc_erreurs5689', DESK, None),
    ('qualification', 'couts', 'banc_couts5689', DESK, None), ('qualification', 'receptions', 'banc_receptions5689', DESK, None), ('qualification', 'etats_des_fonctions', 'fonction_releves5689', DESK, None),
    ('cabinet', 'ressources_de_test', 'capability_tests5689', DESK, None), ('cabinet', 'revocations', 'capability_revocations5689', DESK, None), ('cabinet', 'demonstrations_fictives', 'demo_missions5689', DESK, None),
)
LIBELLES = {'qualification': 'banc d’essai : protocoles, cas, configurations, résultats, avis de relecteur, coûts et réceptions ; états des fonctions', 'contentieux': 'prétentions et preuves, pièces par partie, actes par section, dossiers d’audience, paquets et autorisations, candidats délais', 'dossiers': 'registre, fiches, faits, synthèses, parties, événements, échéances, documents du registre, mémoire proposée', 'contacts': 'contacts, liens aux dossiers, sociétés, opportunités, sociétés suivies et annonces',
            'factures': 'factures et devis, versions, paiements, clients, accusés de paiement', 'temps': 'temps validés et proposés, conditions d’honoraires, contribution', 'agenda': 'événements de l’agenda du logiciel, audiences',
            'taches': 'tâches, responsables et rappels', 'notes': 'notes, mémos et notes communes de dossier', 'messagerie_interne': 'fils, messages et participants de la messagerie interne', 'modeles': 'modèles et bibliothèque',
            'competences': 'compétences et savoir-faire du cabinet', 'cabinet': 'profil du cabinet, adresses propres, preuves de capacité sans aucun secret, démonstrations fictives',
            'documents': 'inventaire des documents (et documents, si l’option a été choisie)'}


# ------------------------------------------------------------------------------------------------ nettoyage
def _secrete(cle):
    return bool(CLE_SECRETE.search(re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', str(cle))))    # « accessToken » comme « access_token »


class Nettoyage:
    def __init__(self):
        self.retires = 0

    def texte(self, valeur):
        propre, n = VALEUR_SECRETE.subn(RETIRE, valeur)
        self.retires += n
        return propre

    def valeur(self, valeur):
        """Parcours récursif : une clé secrète perd sa valeur, un JSON stocké en texte est relu puis nettoyé."""
        if isinstance(valeur, dict):
            out = {}
            for k, v in valeur.items():
                if _secrete(k):
                    self.retires += 1
                    continue
                out[k] = self.valeur(v)
            return out
        if isinstance(valeur, (list, tuple)):
            return [self.valeur(v) for v in valeur]
        if isinstance(valeur, (bytes, memoryview)):
            return {'base64': base64.b64encode(bytes(valeur)).decode()}
        if isinstance(valeur, str):
            if valeur[:1] in '{[' and len(valeur) > 1:
                try:
                    return self.valeur(json.loads(valeur))
                except ValueError:
                    pass
            return self.texte(valeur)
        return valeur


def cellule(valeur):
    """Texte d'une cellule CSV. Une formule venue d'un courriel ne doit pas s'exécuter à l'ouverture dans un tableur : apostrophe de garde (le JSON reste exact)."""
    if valeur is None:
        return ''
    if isinstance(valeur, (dict, list)):
        return json.dumps(valeur, ensure_ascii=False, sort_keys=True)
    texte = str(valeur)
    return "'" + texte if texte[:1] in ('=', '+', '-', '@', '\t', '\r') else texte


# ------------------------------------------------------------------------------------------------ écriture du ZIP
class Paquet:
    """ZIP écrit au fil de l'eau ; chaque fichier est compté et haché pendant l'écriture. Plafond de volume et demande d'arrêt contrôlés ici."""

    def __init__(self, chemin, verifier=None):
        self.chemin, self.verifier = Path(chemin), verifier
        self.zip = zipfile.ZipFile(chemin, 'w', zipfile.ZIP_DEFLATED, allowZip64=True)
        self.fichiers, self.total, self.controle = [], 0, 0

    @contextmanager
    def fichier(self, nom, **infos):
        membre = self.zip.open(_info(nom), 'w', force_zip64=True)
        sortie = _Sortie(self, membre)
        try:
            yield sortie
        finally:
            membre.close()
        self.fichiers.append({'chemin': nom, 'taille': sortie.taille, 'sha256': sortie.sha.hexdigest(), **infos})

    def _compter(self, n):
        self.total += n
        if self.total > MAX_OCTETS:
            raise Stop('export_trop_volumineux')
        if self.total - self.controle >= 64 * 1024 * 1024:
            self.controle = self.total
            if self.verifier:
                self.verifier()
            if shutil.disk_usage(self.chemin.parent).free < MARGE_DISQUE:
                raise Stop('espace_disque_insuffisant')

    def close(self):
        self.zip.close()


def _info(nom):
    info = zipfile.ZipInfo(nom, date_time=datetime.now(timezone.utc).timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


class _Sortie(io.RawIOBase):
    def __init__(self, paquet, membre):
        super().__init__()
        self.paquet, self.membre, self.sha, self.taille = paquet, membre, hashlib.sha256(), 0

    def writable(self):
        return True

    def write(self, data):
        self.sha.update(data)
        self.taille += len(data)
        self.membre.write(data)
        self.paquet._compter(len(data))
        return len(data)


def _ecrire_table(paquet, domaine, nom, colonnes, lignes, net=None):
    """Un même jeu de lignes en JSON (exact) et en CSV (pour un tableur). Les lignes sont passées deux fois : ``lignes`` est une fonction."""
    compte = 0
    with paquet.fichier('%s/%s.json' % (domaine, nom), domaine=domaine) as sortie:
        sortie.write(b'[')
        for ligne in lignes():
            compte += 1
            if compte > MAX_LIGNES:
                raise Stop('export_trop_volumineux')
            sortie.write((',\n' if compte > 1 else '\n').encode() + json.dumps(ligne, ensure_ascii=False, default=str).encode('utf-8'))
        sortie.write(b'\n]\n')
    paquet.fichiers[-1]['lignes'] = compte
    retires = net.retires if net else 0
    with paquet.fichier('%s/%s.csv' % (domaine, nom), domaine=domaine, lignes=compte) as sortie:
        texte = io.TextIOWrapper(sortie, encoding='utf-8-sig', newline='', write_through=True)
        ecrivain = csv.writer(texte, delimiter=';')
        ecrivain.writerow(colonnes)
        for ligne in lignes():
            ecrivain.writerow([cellule(ligne.get(c)) for c in colonnes])
        texte.flush()
        texte.detach()
    if net:
        net.retires = retires               # seconde lecture des mêmes lignes : déjà comptées
    return compte


def _colonnes(db, table):
    return [r[1] for r in db.execute('PRAGMA table_info("%s")' % table.replace('"', '""'))]


def _exclus_via(db, parent, cle, colonne, reserves):
    if not reserves or not _colonnes(db, parent):
        return frozenset()
    marks = ','.join('?' * len(reserves))
    return frozenset(r[0] for r in db.execute('SELECT "%s" FROM "%s" WHERE "%s" IN (%s)' % (cle, parent, colonne, marks), tuple(reserves)))


def _contacts_reserves(db, reserves):
    """Un contact rattaché uniquement à des dossiers réservés suit ces dossiers."""
    if not reserves or not _colonnes(db, 'contact_links5636'):
        return frozenset()
    seuls = {}
    for contact, matter in db.execute('SELECT contact,matter FROM contact_links5636'):
        seuls.setdefault(contact, set()).add(matter)
    return frozenset(c for c, ms in seuls.items() if ms <= set(reserves))


def _note_reservee(data, reserves):
    if not isinstance(data, dict):
        return False
    result = data.get('result') if isinstance(data.get('result'), dict) else {}
    return any(str(x.get('matter') or x.get('matter_id') or '') in reserves for x in (data, result))


def _exporter_table(paquet, db, net, reserves, domaine, nom, table, filtre):
    colonnes_brutes = _colonnes(db, table)
    if not colonnes_brutes:
        return 0                            # table d'une fonction jamais utilisée sur cette installation
    colonnes = [c for c in colonnes_brutes if not _secrete(c)]
    exclus_ids = _contacts_reserves(db, reserves) if table == 'contacts5636' else frozenset()
    via = filtre if isinstance(filtre, tuple) else None
    if table == 'notes':        # notes rattachées à un courriel : leur dossier se lit dans les associations du courriel
        via = ('key', 'proposals', 'key', 'matter')
    if via:
        exclus_ids = _exclus_via(db, via[1], via[2], via[3], reserves)

    def lignes():
        for rangee in db.execute('SELECT * FROM "%s"' % table.replace('"', '""')):
            brut = dict(zip(colonnes_brutes, rangee))
            if isinstance(filtre, str) and str(brut.get(filtre) or '') in reserves:
                continue
            if via and brut.get(via[0]) in exclus_ids:
                continue
            if table == 'contacts5636' and brut.get('id') in exclus_ids:
                continue
            ligne = {c: net.valeur(brut[c]) for c in colonnes}
            if table == 'notes' and _note_reservee(ligne.get('data'), reserves):
                continue
            yield ligne
    return _ecrire_table(paquet, domaine, nom, colonnes, lignes, net)


def _dossiers(desk, paquet, net, reserves):
    rows = [net.valeur(m) for m in load_matters(desk.c, unfiltered=True) if m['id'] not in reserves]
    colonnes = list(dict.fromkeys(k for r in rows for k in r)) or ['id']
    return _ecrire_table(paquet, 'dossiers', 'registre', colonnes, lambda: iter(rows))


def _sain(partie):
    partie = unicodedata.normalize('NFC', str(partie))
    partie = re.sub(r'[\x00-\x1f<>:"|?*\\]', '_', partie).strip(' .')
    return partie[:120] or '_'


def _inventaire(desk, reserves, marque):
    """Documents connus du logiciel : emplacement, étiquette Nextcloud, date, empreinte du texte lu. Aucun texte de pièce."""
    state = Path(desk.c['state_dir'])
    lus = {}
    for ligne in desk.db.execute('SELECT matter,path,name,nature,doc_date,text_sha FROM d51_documents') if _colonnes(desk.db, 'd51_documents') else ():
        lus[(ligne[0], ligne[1])] = ligne
    rows = []
    fichier = state / DOCS
    if fichier.is_file():
        db = sqlite3.connect(fichier.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        try:
            for matter, path, etag, modified, error in db.execute('SELECT matter,path,etag,modified,error FROM docs ORDER BY matter,path'):
                if matter in reserves:
                    continue
                if len(rows) >= MAX_LIGNES:
                    raise Stop('export_trop_volumineux')
                connu = lus.get((matter, path))
                rows.append({'dossier': matter, 'chemin': path, 'etag': etag, 'modifie': modified, 'erreur_lecture': error or '', 'nature': connu[3] if connu else '', 'date_document': connu[4] if connu else '',
                             'empreinte_texte_sha256': connu[5] if connu else '', 'statut': marque, 'sha256_fichier': '', 'dans_export': ''})
        finally:
            db.close()
    return rows


def _documents(desk, paquet, rows, progres):
    """Option : copie des documents depuis Nextcloud, un par un. Une pièce illisible ou trop lourde est signalée, jamais perdue en silence."""
    from . import office440
    dav = office440.dav_client(desk)
    dav.cfg = {**dav.cfg, 'max_file_bytes': MAX_DOCUMENT}
    volume, pris = 0, set()
    for numero, row in enumerate(rows):
        if numero % 25 == 0:
            progres('Copie des documents (%d sur %d)' % (numero, len(rows)))
        if volume >= MAX_DOCUMENTS_OCTETS:
            row['statut'] = 'non_inclus_plafond'
            continue
        try:
            data = dav.download({'path': row['chemin'], 'size': 0})
        except Stop as error:
            row['statut'] = 'non_inclus:' + str(error).split(':')[0]
            continue
        parties = [_sain(p) for p in PurePosixPath(row['chemin']).parts if p not in ('/', '..', '.')]
        nom = 'documents/%s/%s' % (_sain(row['dossier']), '/'.join(parties[-4:]))
        if nom in pris:
            nom += '.' + uuid.uuid4().hex[:8]
        pris.add(nom)
        with paquet.fichier(nom, domaine='documents') as sortie:
            sortie.write(data)
        volume += len(data)
        row.update(statut='inclus', sha256_fichier=hashlib.sha256(data).hexdigest(), dans_export=nom)


def _lisezmoi(meta, fichiers):
    domaines = {}
    for f in fichiers:
        domaines.setdefault(f.get('domaine', ''), []).append(f)
    lignes = ['Export intégral AxiorHub Pilote', '=' * 31, '', 'Créé le %s avec la version %s.' % (meta['cree'], meta['version']), '',
              'Ce dossier contient les données du cabinet dans des formats ouverts, lisibles sans le logiciel.',
              '- Fichiers .json : valeurs exactes, encodées en UTF-8. Un fichier est une liste de lignes.',
              '- Fichiers .csv : mêmes données pour un tableur (séparateur point-virgule, UTF-8). Une cellule commençant par = + - ou @ reçoit une apostrophe, pour',
              '  qu’aucune formule ne s’exécute à l’ouverture : la valeur exacte est dans le .json.',
              '- MANIFEST.json : taille et empreinte SHA-256 de chaque fichier, pour vérifier que rien n’a changé (Paramètres, ou python -m agent.export5688 verifier FICHIER.zip).', '', 'Contenu :']
    for domaine, rows in sorted(domaines.items()):
        if domaine:
            lignes.append('- %s/ : %s (%d fichiers).' % (domaine, LIBELLES.get(domaine, ''), len(rows)))
    lignes += ['', 'Dossiers réservés : ' + ('INCLUS à la demande explicite de l’administrateur (%d). Cet export contient ces dossiers.' % meta['dossiers_reserves']['nombre']
                                             if meta['dossiers_reserves']['traitement'] == 'inclus'
                                             else 'exclus, avec tout ce qui s’y rattache (%d dossier(s)).' % meta['dossiers_reserves']['nombre']),
               'Documents : ' + ('copiés dans documents/, dans la limite indiquée par documents/inventaire (colonne statut).' if meta['documents_inclus']
                                 else 'ils restent dans Nextcloud ; documents/inventaire en donne la liste et les empreintes connues.'), '',
               'Absents par construction : mots de passe et leurs hachés, jetons, clés d’accès, coffre des secrets, journaux techniques, index de recherche.',
               'Ce fichier n’est pas chiffré et contient des données couvertes par le secret professionnel : conservez-le en lieu sûr et supprimez-le après usage.', '']
    return '\n'.join(lignes).encode('utf-8')


def construire(desk, chemin, options, verifier=None, progres=None):
    """Écrit le ZIP. Lecture sur une transaction unique : l'export reflète un seul instant, sans bloquer les écritures (journal WAL)."""
    from .membres5650 import load
    from . import __version__
    progres = progres or (lambda texte, pct=None: None)
    reserves_toutes = frozenset(load(desk.c['state_dir'])['reserves'])
    inclure_reserves = options.get('dossiers_reserves') == 'inclure'
    reserves = frozenset() if inclure_reserves else reserves_toutes
    net = Nettoyage()
    paquet = Paquet(chemin, verifier)
    try:
        db = sqlite3.connect(Path(desk.c['state_dir'], DESK).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
        try:
            db.execute('BEGIN')
            progres('Registre des dossiers', 2)
            _dossiers(desk, paquet, net, reserves)
            for numero, (domaine, nom, table, _base, filtre) in enumerate(TABLES, 1):
                if verifier:
                    verifier()
                progres('%s : %s' % (domaine, nom), 2 + int(70 * numero / len(TABLES)))
                _exporter_table(paquet, db, net, reserves, domaine, nom, table, filtre)
            db.rollback()
        finally:
            db.close()
        inclure = bool(options.get('documents'))
        rows = _inventaire(desk, reserves, 'dans_nextcloud')
        if inclure:
            _documents(desk, paquet, rows, lambda texte: progres(texte, 80))
        colonnes = ['dossier', 'chemin', 'etag', 'modifie', 'erreur_lecture', 'nature', 'date_document', 'empreinte_texte_sha256', 'statut', 'sha256_fichier', 'dans_export']
        _ecrire_table(paquet, 'documents', 'inventaire', colonnes, lambda: iter(rows))
        progres('Manifeste', 92)
        meta = {'format': FORMAT, 'cree': desk.now(), 'version': __version__, 'documents_inclus': inclure,
                'dossiers_reserves': {'traitement': 'inclus' if inclure_reserves else 'exclus', 'nombre': len(reserves_toutes)}, 'secrets_retires': net.retires}
        with paquet.fichier('LISEZMOI.txt') as sortie:
            sortie.write(_lisezmoi(meta, paquet.fichiers))
        with paquet.zip.open(_info('MANIFEST.json'), 'w', force_zip64=True) as sortie:
            sortie.write(json.dumps({**meta, 'fichiers': paquet.fichiers}, ensure_ascii=False, indent=1).encode('utf-8'))
    finally:
        paquet.close()
    return meta, len(paquet.fichiers)


# ------------------------------------------------------------------------------------------------ vérification du manifeste
def verifier_manifeste(chemin):
    """Compare un ZIP à son manifeste, en flux, sans rien extraire sur le disque. Rend {'ok', 'fichiers', 'verifies', 'anomalies', 'cree', 'version'}.

    Contrôle d'intégrité (rien n'a changé, rien ne manque) ; le manifeste n'est pas signé : pour prouver l'origine, conserver à part l'empreinte du ZIP affichée à la création."""
    anomalies, verifies = [], 0

    def signaler(fichier, motif):
        if len(anomalies) < 100:
            anomalies.append({'fichier': str(fichier)[:300], 'motif': motif})
    try:
        with zipfile.ZipFile(chemin) as paquet:
            noms = {i.filename: i for i in paquet.infolist()}
            if 'MANIFEST.json' not in noms or noms['MANIFEST.json'].file_size > MAX_MANIFESTE:
                raise Stop('manifeste_absent')
            try:
                manifeste = json.loads(paquet.read('MANIFEST.json'))
                attendus = {str(f['chemin']): {'taille': int(f['taille']), 'sha256': str(f['sha256'])} for f in manifeste['fichiers']}
            except (ValueError, KeyError, TypeError):
                raise Stop('manifeste_illisible') from None
            if manifeste.get('format') != FORMAT or sum(f['taille'] for f in attendus.values()) > MAX_OCTETS + MAX_MANIFESTE:
                raise Stop('manifeste_illisible')
            if len(noms) != len(paquet.infolist()):
                signaler('MANIFEST.json', 'nom de fichier en double dans le ZIP')
            for nom in noms:
                p = PurePosixPath(nom)
                if p.is_absolute() or '..' in p.parts or '\\' in nom:
                    signaler(nom, 'chemin dangereux')
                elif nom != 'MANIFEST.json' and nom not in attendus:
                    signaler(nom, 'fichier absent du manifeste')
            for nom, entree in attendus.items():
                if nom not in noms:
                    signaler(nom, 'fichier manquant')
                    continue
                sha, taille = hashlib.sha256(), 0
                try:
                    with paquet.open(nom) as flux:
                        while taille <= entree['taille']:             # jamais plus que le volume annoncé
                            data = flux.read(1024 * 1024)
                            if not data:
                                break
                            sha.update(data)
                            taille += len(data)
                except (zipfile.BadZipFile, OSError, EOFError, ValueError, __import__('zlib').error):
                    signaler(nom, 'fichier illisible')
                    continue
                if taille != entree['taille']:
                    signaler(nom, 'taille différente')
                elif sha.hexdigest() != entree['sha256']:
                    signaler(nom, 'empreinte différente')
                else:
                    verifies += 1
    except (zipfile.BadZipFile, OSError, ValueError):
        raise Stop('archive_zip_illisible') from None
    return {'ok': not anomalies, 'fichiers': len(attendus), 'verifies': verifies, 'anomalies': anomalies, 'cree': manifeste.get('cree', ''), 'version': manifeste.get('version', ''),
            'dossiers_reserves': manifeste.get('dossiers_reserves', {}), 'documents_inclus': bool(manifeste.get('documents_inclus'))}


# ------------------------------------------------------------------------------------------------ demandes, travail, rétention
def _racine(desk):
    path = Path(desk.c['state_dir']) / DOSSIER
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def schema(desk):
    desk.db.executescript('''CREATE TABLE IF NOT EXISTS exports5688(id TEXT PRIMARY KEY, par TEXT NOT NULL, demande TEXT NOT NULL, fin TEXT NOT NULL DEFAULT '', statut TEXT NOT NULL,
      etape TEXT NOT NULL DEFAULT '', progression INTEGER NOT NULL DEFAULT 0, taille INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '', expire TEXT NOT NULL DEFAULT '',
      options TEXT NOT NULL DEFAULT '{}', erreur TEXT NOT NULL DEFAULT '');
      CREATE TABLE IF NOT EXISTS depots5688(id TEXT PRIMARY KEY, par TEXT NOT NULL, debut TEXT NOT NULL, taille INTEGER NOT NULL DEFAULT 0, recu INTEGER NOT NULL DEFAULT 0, suivant INTEGER NOT NULL DEFAULT 0);''')


def demander(desk, data, owner):
    schema(desk)
    purge(desk)
    if desk.db.execute("SELECT 1 FROM exports5688 WHERE statut IN ('en_attente','en_cours') LIMIT 1").fetchone():
        raise Stop('export_deja_en_cours')
    reserves = str(data.get('dossiers_reserves') or 'exclure')
    if reserves not in ('exclure', 'inclure'):
        raise Stop('option_export_invalide')
    if reserves == 'inclure' and data.get('confirmation_reserves') not in (True, 'on', '1', 'true'):
        raise Stop('confirmation_dossiers_reserves_requise')
    options = {'documents': data.get('documents') in (True, 'on', '1', 'true'), 'dossiers_reserves': reserves}
    ident = uuid.uuid4().hex
    desk.db.execute("INSERT INTO exports5688(id,par,demande,statut,options) VALUES (?,?,?,'en_attente',?)", (ident, str(owner)[:120], desk.now(), json.dumps(options)))
    desk.db.commit()
    desk.enqueue(JOB, {'export': ident}, priority=20, origin='user')
    desk.audit('export5688_demande', {'id': ident, 'par': str(owner)[:120], **options})
    return {'ok': True, 'id': ident, 'message': 'Export mis en file : la progression s’affiche ici.'}


def _avancer(desk, ident, etape, pct=None):
    desk.db.execute('UPDATE exports5688 SET etape=?,progression=COALESCE(?,progression) WHERE id=?', (str(etape)[:200], pct, ident))
    desk.db.commit()


def executer(desk, ident):
    """Travail « export5688 » : un seul travail lourd à la fois (verrou commun avec la sauvegarde)."""
    from .pause5629 import checkpoint
    schema(desk)
    row = desk.db.execute('SELECT * FROM exports5688 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row or not EXPORT_ID.match(row['id']):
        raise Stop('export_inconnu')
    if row['statut'] not in ('en_attente', 'en_cours'):
        return {'export': row['id'], 'statut': row['statut'], 'sans_controle': True}
    with sv.verrou(desk):
        racine = _racine(desk)
        partie, final = racine / (row['id'] + '.zip.part'), racine / (row['id'] + '.zip')
        desk.db.execute("UPDATE exports5688 SET statut='en_cours',etape='Démarrage' WHERE id=?", (row['id'],))
        desk.db.commit()
        try:
            if shutil.disk_usage(racine).free < MARGE_DISQUE:
                raise Stop('espace_disque_insuffisant')
            options = json.loads(row['options'] or '{}')
            meta, nombre = construire(desk, partie, options, lambda: checkpoint(desk), lambda texte, pct=None: _avancer(desk, row['id'], texte, pct))
            _avancer(desk, row['id'], 'Vérification du manifeste', 95)
            controle = verifier_manifeste(partie)
            if not controle['ok']:
                raise Stop('export_verification_echouee')
            sha, taille = hashlib.sha256(), 0
            with open(partie, 'rb') as flux:
                for data in iter(lambda: flux.read(1024 * 1024), b''):
                    sha.update(data)
                    taille += len(data)
            os.replace(partie, final)
            os.chmod(final, 0o600)
        except Stop as error:
            partie.unlink(missing_ok=True)
            desk.db.execute("UPDATE exports5688 SET statut='echec',fin=?,erreur=?,etape='' WHERE id=?", (desk.now(), str(error).split(':')[0], row['id']))
            desk.db.commit()
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            partie.unlink(missing_ok=True)
            desk.db.execute("UPDATE exports5688 SET statut='echec',fin=?,erreur='erreur_disque',etape='' WHERE id=?", (desk.now(), row['id']))
            desk.db.commit()
            raise Stop('erreur_disque') from error
    expire = (datetime.now(timezone.utc) + timedelta(hours=EXPIRATION_HEURES)).isoformat()
    desk.db.execute("UPDATE exports5688 SET statut='pret',fin=?,etape='Prêt',progression=100,taille=?,sha256=?,expire=? WHERE id=?", (desk.now(), taille, sha.hexdigest(), expire, row['id']))
    desk.db.commit()
    desk.audit('export5688_termine', {'id': row['id'], 'taille': taille, 'sha256': sha.hexdigest(), 'fichiers': nombre, 'secrets_retires': meta['secrets_retires']})
    purge(desk)
    return {'export': row['id'], 'statut': 'pret', 'taille': taille, 'sans_controle': True, 'message': 'Export prêt à télécharger.'}


def _retirer_fichier(desk, ident):
    for suffixe in ('.zip', '.zip.part', '.depot'):
        (Path(desk.c['state_dir']) / DOSSIER / (ident + suffixe)).unlink(missing_ok=True)


def purge(desk, now=None):
    """Rétention : fichiers expirés, surplus au-delà de 3 exports prêts, restes d'un travail interrompu, dépôts abandonnés. Lancée à chaque demande et par la boucle des automatismes."""
    schema(desk)
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    for row in desk.db.execute("SELECT id FROM exports5688 WHERE statut='pret' AND expire<=?", (stamp,)).fetchall():
        _retirer_fichier(desk, row['id'])
        desk.db.execute("UPDATE exports5688 SET statut='expire',etape='' WHERE id=?", (row['id'],))
        desk.audit('export5688_expire', {'id': row['id']})
    for row in desk.db.execute("SELECT id FROM exports5688 WHERE statut='pret' ORDER BY demande DESC LIMIT -1 OFFSET ?", (CONSERVES,)).fetchall():
        _retirer_fichier(desk, row['id'])
        desk.db.execute("UPDATE exports5688 SET statut='expire',etape='' WHERE id=?", (row['id'],))
    for row in desk.db.execute("SELECT id FROM exports5688 WHERE statut IN ('en_cours','en_attente')").fetchall():     # travail annulé ou perdu : la demande ne bloque pas la suivante
        if not desk.db.execute("SELECT 1 FROM jobs WHERE kind=? AND json_extract(args,'$.export')=? AND status IN ('pending','running','cancel_requested','suspended') LIMIT 1", (JOB, row['id'])).fetchone():
            desk.db.execute("UPDATE exports5688 SET statut='echec',erreur='travail_interrompu',etape='',fin=? WHERE id=?", (stamp, row['id']))
    actifs = {r['id'] for r in desk.db.execute("SELECT id FROM exports5688 WHERE statut IN ('pret','en_cours','en_attente')")}
    limite = (now - timedelta(hours=6)).isoformat()
    for row in desk.db.execute('SELECT id FROM depots5688 WHERE debut<=?', (limite,)).fetchall():
        _retirer_fichier(desk, row['id'])
        desk.db.execute('DELETE FROM depots5688 WHERE id=?', (row['id'],))
    depots = {r['id'] for r in desk.db.execute('SELECT id FROM depots5688')}
    racine = Path(desk.c['state_dir']) / DOSSIER
    if racine.is_dir():
        for p in racine.iterdir():
            ident = p.name.split('.', 1)[0]
            if p.is_file() and ident not in actifs and ident not in depots:
                p.unlink(missing_ok=True)
    desk.db.execute('DELETE FROM exports5688 WHERE id NOT IN (SELECT id FROM exports5688 ORDER BY demande DESC LIMIT ?)', (HISTORIQUE,))
    desk.db.commit()


def supprimer(desk, ident, owner):
    schema(desk)
    if not EXPORT_ID.match(str(ident or '')):
        raise Stop('export_inconnu')
    _retirer_fichier(desk, ident)
    changed = desk.db.execute("UPDATE exports5688 SET statut='supprime',etape='' WHERE id=? AND statut='pret'", (ident,)).rowcount
    desk.db.commit()
    if not changed:
        raise Stop('export_inconnu')
    desk.audit('export5688_supprime', {'id': ident, 'par': str(owner)[:120]})
    return {'ok': True, 'message': 'Export supprimé.'}


def liste(desk, limite=5):
    schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM exports5688 ORDER BY demande DESC LIMIT ?', (limite,)).fetchall()]


def schedule(desk, now_ts=None):
    """Depuis la boucle des automatismes, au plus une fois par heure."""
    now_ts = now_ts or datetime.now(timezone.utc).timestamp()
    if now_ts - float(desk.settings('auto:purge_export5688', 0) or 0) >= 3600:
        desk.setting('auto:purge_export5688', now_ts)
        purge(desk)


class Telechargement:
    """Réponse en flux (le fichier peut dépasser la mémoire du service). Lu jusqu'au bout, il est supprimé : l'export ne reste pas sur le serveur."""

    def __init__(self, chemin, taille, fin):
        self.flux, self.reste, self.fin_appel = open(chemin, 'rb'), taille, fin

    def __iter__(self):
        return self

    def __next__(self):
        data = self.flux.read(1024 * 1024)
        if not data:
            raise StopIteration
        self.reste -= len(data)
        return data

    def close(self):
        self.flux.close()
        from .membres5650 import clear
        clear()
        if self.reste <= 0:
            self.fin_appel()


def telecharger(desk, ident, owner):
    schema(desk)
    purge(desk)
    if not EXPORT_ID.match(str(ident or '')):
        raise Stop('export_inconnu')
    row = desk.db.execute("SELECT * FROM exports5688 WHERE id=? AND statut='pret'", (ident,)).fetchone()
    chemin = Path(desk.c['state_dir']) / DOSSIER / (str(ident) + '.zip')
    if not row or not chemin.is_file():
        raise Stop('export_inconnu')
    desk.audit('export5688_telecharge', {'id': ident, 'par': str(owner)[:120], 'taille': row['taille']})
    config, nom = desk.c, 'axiorhub-export-%s.zip' % str(row['demande'])[:10]

    def fin():
        from .desk import Desk
        autre = Desk(config)
        try:
            _retirer_fichier(autre, ident)
            autre.db.execute("UPDATE exports5688 SET statut='telecharge',etape='' WHERE id=? AND statut='pret'", (ident,))
            autre.db.commit()
            autre.audit('export5688_fichier_retire', {'id': ident})
        finally:
            autre.db.close()
    return Telechargement(chemin, chemin.stat().st_size, fin), chemin.stat().st_size, nom


# ------------------------------------------------------------------------------------------------ dépôt d'un export à vérifier (par morceaux)
def depot_ouvrir(desk, taille, owner):
    schema(desk)
    purge(desk)
    try:
        taille = int(taille)
    except (TypeError, ValueError):
        raise Stop('depot_invalide') from None
    if not 0 < taille <= MAX_DEPOT:
        raise Stop('depot_trop_volumineux')
    if shutil.disk_usage(_racine(desk)).free < taille + MARGE_DISQUE:
        raise Stop('espace_disque_insuffisant')
    ident = uuid.uuid4().hex
    desk.db.execute('INSERT INTO depots5688(id,par,debut,taille) VALUES (?,?,?,?)', (ident, str(owner)[:120], desk.now(), taille))
    desk.db.commit()
    return {'id': ident, 'morceau': MORCEAU_DEPOT}


def depot_morceau(desk, ident, numero, flux, longueur):
    """Morceaux dans l'ordre, jamais au-delà de la taille annoncée ; le fichier temporaire est retiré après contrôle, ou par la rétention (6 h)."""
    if not EXPORT_ID.match(str(ident or '')) or not 0 < longueur <= MORCEAU_DEPOT:
        raise Stop('depot_invalide')
    schema(desk)
    row = desk.db.execute('SELECT * FROM depots5688 WHERE id=?', (ident,)).fetchone()
    if not row or int(numero) != row['suivant'] or row['recu'] + longueur > row['taille']:
        raise Stop('depot_invalide')
    with open(_racine(desk) / (ident + '.depot'), 'ab') as sortie:
        reste = longueur
        while reste:
            data = flux.read(min(reste, 1024 * 1024))
            if not data:
                raise Stop('depot_invalide')
            sortie.write(data)
            reste -= len(data)
    desk.db.execute('UPDATE depots5688 SET recu=recu+?,suivant=suivant+1 WHERE id=?', (longueur, ident))
    desk.db.commit()
    return {'ok': True, 'recu': row['recu'] + longueur, 'complet': row['recu'] + longueur == row['taille']}


def controle_fichier(desk, cible):
    """Contrôle demandé dans l'interface : un export encore présent, ou un dépôt (retiré aussitôt après)."""
    if not EXPORT_ID.match(str(cible or '')):
        raise Stop('export_inconnu')
    racine = Path(desk.c['state_dir']) / DOSSIER
    depot = racine / (cible + '.depot')
    if depot.is_file():
        try:
            return verifier_manifeste(depot)
        finally:
            depot.unlink(missing_ok=True)
            desk.db.execute('DELETE FROM depots5688 WHERE id=?', (cible,))
            desk.db.commit()
    chemin = racine / (cible + '.zip')
    if not chemin.is_file():
        raise Stop('export_inconnu')
    return verifier_manifeste(chemin)


def main(argv=None):
    """``python -m agent.export5688 verifier FICHIER.zip`` : pour un export conservé hors du serveur."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != 'verifier':
        print('Usage : python -m agent.export5688 verifier FICHIER.zip', file=sys.stderr)
        return 2
    try:
        out = verifier_manifeste(args[1])
    except Stop as error:
        print('Échec : %s' % str(error).split(':')[0], file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
