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

5.6.108 (lot C, B07) : périmètre déclaré. Liste fermée maintenue (jamais de copie aveugle des tables) : ``TABLES`` plus les tables que chaque module
métier déclare lui-même (``EXPORT`` et ``EXPORT_EXCLUES`` des modules de ``MODULES_DECLARANTS``) ; chaque autre table de la base doit être exclue avec
son motif (``EXCLUES``, motifs par famille, exclusions de ``reversibilite5689``). Le manifeste compare, domaine par domaine, les lignes attendues,
exportées et exclues (dossiers réservés) ; une table de données ni exportée ni exclue est « non couverte » et l'export ne se dit jamais complet.
"""
import base64
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
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
    ('dossiers', 'identites_des_documents', 'prepared_identity5693', DESK, 'matter_id'),   # 5.6.93 (N06) : doublons suggérés, scissions et relectures ambiguës
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
    ('contentieux', 'actes_qualifies', 'deadline_actes5696', DESK, 'matter'), ('contentieux', 'detections_ecartees', 'deadline_ecartes5696', DESK, 'matter'),   # 5.6.96 (D01, D05)
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
    # 5.6.97 (lot agenda) : détails de saisie, opérations idempotentes, salons Talk, écritures distantes et état des sources
    ('agenda', 'details_des_evenements', 'agenda5697', DESK, ('event_id', 'agenda5634', 'id', 'matter')), ('agenda', 'operations', 'agenda_ops5697', DESK, 'dossier'),
    ('agenda', 'salons_talk', 'agenda_salons5697', DESK, 'dossier'), ('agenda', 'ecritures_distantes', 'agenda_sorties5697', DESK, 'dossier'),
    ('agenda', 'etat_des_sources', 'agenda_sources5697', DESK, None), ('taches', 'priorites_et_creneaux', 'taches5697', DESK, ('task_id', 'tasks', 'id', 'matter')),
    ('taches', 'taches', 'tasks', DESK, 'matter'), ('taches', 'taches_de_travail', 'work_tasks_v211', DESK, 'matter'),
    ('taches', 'suivi_des_taches', 'taches5688', DESK, ('task_id', 'tasks', 'id', 'matter')),
    ('notes', 'notes', 'notes', DESK, None), ('notes', 'memos', 'memos5636', DESK, 'matter'), ('notes', 'reponses_ia_tardives', 'reponses_tardives5689', DESK, 'matter'),
    ('notes', 'journal_de_rangement', 'rangement5689', DESK, 'matter'), ('notes', 'notes_communes', 'note_commune5688', DESK, 'matter'), ('notes', 'versions_des_notes_communes', 'note_versions5688', DESK, 'matter'),
    ('messagerie_interne', 'fils', 'fils5650', DESK, 'matter'), ('messagerie_interne', 'messages', 'messages5650', DESK, ('thread', 'fils5650', 'id', 'matter')),
    ('messagerie_interne', 'participants', 'participants5650', DESK, ('thread', 'fils5650', 'id', 'matter')),
    ('courriels', 'suivi_des_fils', 'courriels5696_suivis', DESK, 'matter'), ('courriels', 'decisions_de_suivi', 'courriels5696_evenements', DESK, ('suivi_id', 'courriels5696_suivis', 'id', 'matter')),   # 5.6.96 (C02)
    ('courriels', 'commandes_de_preparation', 'courriels5697_commandes', DESK, ('suivi_id', 'courriels5696_suivis', 'id', 'matter')),   # 5.6.97 (B04)
    ('courriels', 'projets_gmail', 'gmail5697_projets', DESK, 'matter'), ('courriels', 'journal_gmail', 'gmail5697_journal', DESK, ('projet_id', 'gmail5697_projets', 'id', 'matter')),   # 5.6.97 (lot gmail)
    # 5.6.97 (lot gestion, § 18-21) : suivi des échéances, paquets gelés et accusés, frais, jalons, projection de la source externe, pièces demandées
    # (registre 5.6.57 compris), remises au client, clôtures. Les octets gelés partent en base64 ; le lien et le jeton d'un partage (url_jeton) jamais.
    ('contentieux', 'suivi_des_echeances', 'echeance_suivi5697', DESK, 'matter'), ('contentieux', 'depots_geles', 'depots5697', DESK, 'matter'),
    ('contentieux', 'fichiers_des_depots', 'depot_fichiers5697', DESK, ('depot_id', 'depots5697', 'id', 'matter')), ('contentieux', 'accuses_de_depot', 'depot_accuses5697', DESK, 'matter'),
    ('factures', 'frais', 'frais5697', DESK, 'matter'), ('factures', 'jalons', 'jalons5697', DESK, 'matter'), ('factures', 'projection_source_externe', 'fin_externes5697', DESK, 'matter'),
    ('factures', 'synchronisations', 'fin_synchros5697', DESK, None), ('factures', 'demandes_externes', 'fin_demandes5697', DESK, 'matter'),
    ('dossiers', 'registre_des_affaires', 'dossiers5697', DESK, 'id'), ('dossiers', 'parties_du_registre', 'dossier_parties5697', DESK, 'dossier_id'),   # 5.6.97 (lot dossiers)
    ('dossiers', 'alias_des_dossiers', 'dossier_alias5697', DESK, 'dossier_id'), ('dossiers', 'rangements_des_dossiers', 'dossier_outbox5697', DESK, 'dossier_id'),
    ('dossiers', 'journal_du_registre', 'dossier_journal5697', DESK, 'dossier_id'), ('courriels', 'classements_manuels', 'message_dossier5697', DESK, 'dossier_id'),
    ('dossiers', 'commandes_du_registre', 'dossier_commandes5697', DESK, None), ('dossiers', 'migrations_du_registre', 'dossier_migrations5697', DESK, None),
    ('dossiers', 'demandes_de_pieces', 'p57_requests', DESK, 'matter'), ('dossiers', 'elements_demandes', 'p57_request_items', DESK, ('request', 'p57_requests', 'id', 'matter')),
    ('dossiers', 'suivi_des_demandes_de_pieces', 'pieces_suivi5697', DESK, 'matter'), ('dossiers', 'pieces_recues', 'pieces_receptions5697', DESK, 'matter'),
    ('dossiers', 'remises_au_client', 'remises5697', DESK, 'matter'), ('dossiers', 'etat_de_cloture', 'dossiers_etat5697', DESK, 'matter'), ('dossiers', 'clotures', 'clotures5697', DESK, 'matter'),
    ('dossiers', 'entrees_sur_dossier_archive', 'entrees_archivees5697', DESK, 'matter'), ('dossiers', 'journal_de_gestion', 'gestion_journal5697', DESK, 'matter'),
    # 5.6.97 (lot documents-mission, § 13, § 16, § 17) : commandes et textes des versions de documents, demandes de mission et contrôles de conflits versionnés,
    # réservations de conversion, espace de préparation (octets en base64), portefeuille (prochaine action, attentes) et historique des attributions.
    ('documents', 'commandes_de_documents', 'documents5697_ops', DESK, 'dossier_id'),
    ('documents', 'textes_des_versions', 'documents5697_textes', DESK, ('document_id', 'prepared_items5689', 'document_id', 'matter_id')),
    ('dossiers', 'demandes_de_mission', 'demande_mission5697', DESK, 'dossier_id'),
    ('dossiers', 'controles_de_conflits_des_demandes', 'decision_conflits5697', DESK, ('demande_id', 'demande_mission5697', 'id', 'dossier_id')),
    ('dossiers', 'conversions_des_demandes', 'demande_conversion5697', DESK, 'dossier_id'),
    ('dossiers', 'journal_des_demandes', 'demande_mission5697_journal', DESK, ('demande_id', 'demande_mission5697', 'id', 'dossier_id')),
    ('dossiers', 'commandes_des_demandes', 'demande_mission5697_ops', DESK, ('demande_id', 'demande_mission5697', 'id', 'dossier_id')),
    ('dossiers', 'preparation_des_demandes', 'preparation5697', DESK, ('demande_id', 'demande_mission5697', 'id', 'dossier_id')),
    ('dossiers', 'prochaine_action', 'portefeuille5697', DESK, 'dossier_id'), ('dossiers', 'historique_du_portefeuille', 'portefeuille5697_historique', DESK, 'dossier_id'),
    ('dossiers', 'commandes_du_portefeuille', 'portefeuille5697_ops', DESK, 'dossier_id'),
    ('modeles', 'modeles_du_cabinet', 'cabinet_templates_v330', DESK, None), ('modeles', 'modeles_de_facturation', 'b53_templates', DESK, None), ('modeles', 'bibliotheque', 'bibliotheque5639', DESK, None),
    ('modeles', 'versions_de_modeles', 'template_versions5684', DESK, None),
    ('modeles', 'versions_actives', 'modeles56108_actifs', DESK, None),   # 5.6.108 (lot B) : version active désignée par droit, langue et type d'acte
    ('modeles', 'modeles_de_reponse', 'modeles_reponse56106', DESK, None),
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
    # 5.6.108 (lot C, B07) : routines sur message (5.6.106), leurs versions et leurs propositions ; file commune « À relire » et décisions de relecture
    ('routines', 'routines_sur_message', 'routines56106', DESK, 'matter'),
    ('routines', 'versions_des_routines_sur_message', 'routines56106_versions', DESK, ('routine_id', 'routines56106', 'id', 'matter')),
    ('routines', 'propositions_des_routines', 'routines56106_elements', DESK, 'matter'),
    ('relecture', 'elements_a_relire', 'file56100_items', DESK, 'matter'),
    ('relecture', 'decisions_de_relecture', 'file56100_historique', DESK, ('item', 'file56100_items', 'id', 'matter')),
)

# 5.6.108 (lot C, B07) : modules qui déclarent eux-mêmes leurs tables (``EXPORT`` : mêmes quintuplets que ``TABLES`` ; ``EXPORT_EXCLUES`` : (table, motif)).
MODULES_DECLARANTS = ('assistance35', 'export56108')   # 5.6.108 (lot X) : registre des tables métier restantes, rangées par module propriétaire
_SECRETS = 'Comptes, connexions, secrets ou jetons : jamais dans un export (sauvegarde chiffrée seulement).'
_TECHNIQUE = 'Suivi technique du logiciel (file de travaux, réglages, journaux, verrous, reçus d’idempotence), sans contenu propre au cabinet.'
_CACHE = 'Cache ou projection recalculés depuis une source exportée ou externe.'
EXCLUES = {
    'jobs': _TECHNIQUE, 'settings': 'Réglages du logiciel (peuvent contenir des secrets) : sauvegarde chiffrée seulement.', 'schema_migrations': _TECHNIQUE,
    'audit': 'Journal d’audit : il se consulte et s’exporte depuis l’écran d’audit.', 'audit_chain_v290': 'Chaîne d’intégrité du journal d’audit.',
    'audit_purges56102': 'Journal des purges du journal d’audit.', 'envoi_file56106': 'File d’envoi des courriels (états transitoires ; les messages sont dans la messagerie).',
    'routines56106_arrivees': 'File technique des courriels arrivés à confronter aux routines.', 'routines56106_passages': 'Journal technique des passages des routines '
    '(objets de courriels sans rattachement garanti à un dossier) ; les propositions sont exportées.',
    'file56100_commandes': _TECHNIQUE, 'file56100_intentions': _TECHNIQUE, 'file56100_reports': 'Reports d’affichage personnels de la file « À relire ».',
    'file56100_anomalies': _TECHNIQUE, 'fichiers56103_verrous': _TECHNIQUE, 'fichiers56103_ops': _TECHNIQUE, 'fichiers56103_local_etat': _CACHE,
    'fichiers56103_local_versions': _CACHE, 'calendar_cache': _CACHE, 'invoice_ninja_cache_v310': _CACHE, 'workstation_refresh_v310': _TECHNIQUE,
    'google_oauth568': _SECRETS, 'secu56101_cles': _SECRETS, 'journal56102_cles': _SECRETS,
    'chrono56101_comptes': 'Chronomètre personnel : jamais repris dans un export du cabinet (5.6.101).', 'chrono56101_durees': 'Chronomètre personnel : jamais repris '
    'dans un export du cabinet (5.6.101).', 'usage56101': _TECHNIQUE, 's59_usage': _TECHNIQUE, 'voice_usage_v568': _TECHNIQUE, 'ia_alerts5659': _TECHNIQUE,
    'supervision_requests': _TECHNIQUE, 'ai_reservations_v567': _TECHNIQUE, 'office_leases5684': _TECHNIQUE,
}
FAMILLES_EXCLUES = ((re.compile(r'recus(\d|_|$)'), 'Reçus d’idempotence (réponses déjà rendues), sans contenu propre.'),
                    (re.compile(r'^live_'), 'Flux d’activité en temps réel (technique).'),
                    (re.compile(r'^(connexion|microsoft56103_|liaison56103_)|oauth'), _SECRETS),
                    (re.compile(r'^(delta56100_|reliability_)'), _TECHNIQUE),
                    (re.compile(r'cache'), _CACHE))


def tables_exportees():
    """``TABLES`` complété par les déclarations des modules (une table déclarée deux fois n'est exportée qu'une fois)."""
    out, vues = list(TABLES), {t[2] for t in TABLES}
    for nom in MODULES_DECLARANTS:
        try:
            module = importlib.import_module('.' + nom, __package__)
        except ImportError:
            continue
        for t in getattr(module, 'EXPORT', ()) or ():
            if len(t) == 5 and t[3] == DESK and t[2] not in vues and (t[4] is None or isinstance(t[4], str) or (isinstance(t[4], tuple) and len(t[4]) == 4)):
                out.append(tuple(t))
                vues.add(t[2])
    return tuple(out)


def resolveurs():
    """5.6.108 (lot X) : {table: règle} de résolution du dossier déclarées par les modules (``RESOLVEURS``, voir ``export56108``)."""
    out = {}
    for nom in MODULES_DECLARANTS:
        try:
            module = importlib.import_module('.' + nom, __package__)
        except ImportError:
            continue
        for table, spec in (getattr(module, 'RESOLVEURS', None) or {}).items():
            if isinstance(spec, tuple) and spec:
                out.setdefault(str(table), spec)
    return out


def tables_exclues():
    """{table: motif} des exclusions déclarées (ce module, ``reversibilite5689``, modules déclarants)."""
    out = dict(EXCLUES)
    try:
        from .reversibilite5689 import EXCLUSIONS
        out.update(EXCLUSIONS)
    except ImportError:
        pass
    for nom in MODULES_DECLARANTS:
        try:
            module = importlib.import_module('.' + nom, __package__)
        except ImportError:
            continue
        for table, motif in getattr(module, 'EXPORT_EXCLUES', ()) or ():
            out[str(table)] = str(motif)
    return out


def motif_exclusion(table, exclues=None):
    exclues = tables_exclues() if exclues is None else exclues
    if table in exclues:
        return exclues[table]
    for regle, motif in FAMILLES_EXCLUES:
        if regle.search(table):
            return motif
    return ''


def perimetre(db, exportees, comptes):
    """Périmètre de l'export : par domaine, lignes attendues (table source), exportées et exclues (dossiers réservés) ; tables exclues avec
    leur motif ; tables non couvertes. ``comptes`` : {table: (exportees, attendues)}. Rend un dictionnaire écrit dans le manifeste."""
    noms = {t[2] for t in exportees}
    domaines = {}
    for domaine, nom, table, _base, _filtre in exportees:
        exporte, attendu = comptes.get(table, (0, 0))
        d = domaines.setdefault(domaine, {'tables': 0, 'attendu': 0, 'exporte': 0, 'exclu': 0, 'fichiers': [],
                                          'motif_exclusion': 'dossiers réservés exclus, et lignes dont le dossier n’est pas déterminable'})
        d['tables'] += 1
        if table in comptes:   # 5.6.108 (lot X) : seul un fichier réellement écrit est annoncé (table d'une fonction jamais utilisée : aucun fichier)
            d['fichiers'].append('%s/%s.json' % (domaine, nom))
        d['attendu'] += attendu
        d['exporte'] += exporte
        d['exclu'] += max(0, attendu - exporte)
    exclues, non_couvertes = {}, []
    declarees = tables_exclues()
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        if table in noms:
            continue
        motif = motif_exclusion(table, declarees)
        if motif:
            exclues[motif] = exclues.get(motif, 0) + 1   # par motif : le nom des tables techniques n'a rien à faire dans l'archive
        else:
            non_couvertes.append(table)
    return {'complet': not non_couvertes, 'domaines': domaines, 'tables_exclues': [{'motif': m, 'tables': n} for m, n in sorted(exclues.items())],
            'non_couvertes': non_couvertes}
LIBELLES = {'qualification': 'banc d’essai : protocoles, cas, configurations, résultats, avis de relecteur, coûts et réceptions ; états des fonctions', 'contentieux': 'prétentions et preuves, pièces par partie, actes par section, dossiers d’audience, paquets et autorisations, candidats délais', 'dossiers': 'registre, fiches, faits, synthèses, parties, événements, échéances, documents du registre, mémoire proposée', 'contacts': 'contacts, liens aux dossiers, sociétés, opportunités, sociétés suivies et annonces',
            'factures': 'factures et devis, versions, paiements, clients, accusés de paiement', 'temps': 'temps validés et proposés, conditions d’honoraires, contribution', 'agenda': 'événements de l’agenda du logiciel, audiences',
            'taches': 'tâches, responsables et rappels', 'notes': 'notes, mémos et notes communes de dossier', 'messagerie_interne': 'fils, messages et participants de la messagerie interne', 'modeles': 'modèles et bibliothèque',
            'routines': 'routines sur message, leurs versions et leurs propositions', 'relecture': 'file « À relire » et décisions de relecture',
            'competences': 'compétences et savoir-faire du cabinet', 'cabinet': 'profil du cabinet, adresses propres, preuves de capacité sans aucun secret, démonstrations fictives',
            'documents': 'inventaire des documents (et documents, si l’option a été choisie)',
            # 5.6.108 (lot X) : domaines du registre complété et du profil de départ
            'conversations': 'discussions de l’assistant et questions posées au dossier', 'courriels': 'associations aux dossiers, brouillons conservés, archives et règles de classement',
            'decisions': 'décisions à prendre, propositions et initiatives', 'missions': 'missions, plans, tâches de mission, productions et livrables',
            'recherche_juridique': 'recherches, autorités, consultations, contrôles de citations et veille retenue', 'societes': 'décisions, titres, dirigeants et opérations des sociétés suivies',
            'correspondances': 'originaux EML des courriels archivés et leur inventaire', 'rapports': 'exclusions et éléments non inclus',
            'dictionnaire': 'description de chaque fichier et de chaque colonne'}


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


def _exporter_table(paquet, db, net, reserves, domaine, nom, table, filtre, comptes=None, ctx=None, regles=None):
    colonnes_brutes = _colonnes(db, table)
    if not colonnes_brutes:
        return 0                            # table d'une fonction jamais utilisée sur cette installation
    if comptes is not None:   # 5.6.108 (B07) : lignes de la table source, pour le bilan par domaine du manifeste
        comptes[table] = (0, db.execute('SELECT COUNT(*) FROM "%s"' % table.replace('"', '""')).fetchone()[0])
    colonnes = [c for c in colonnes_brutes if not _secrete(c)]
    exclus_ids = _contacts_reserves(db, reserves) if table == 'contacts5636' else frozenset()
    via = filtre if isinstance(filtre, tuple) else None
    if table == 'notes':        # notes rattachées à un courriel : leur dossier se lit dans les associations du courriel
        via = ('key', 'proposals', 'key', 'matter')
    # 5.6.108 (lot X) : règle de résolution déclarée (courriel classé, chemin, portée, parent résolu…) ; à défaut, une colonne de filtre absente
    # de la table rend le dossier non déterminable : la ligne ne sort pas en clair dès qu'un dossier réservé est exclu (jamais un accès élargi).
    from . import export56108 as registre
    regle = (regles if regles is not None else resolveurs()).get(table)
    resoudre = registre.resolveur(ctx or registre.Contexte(None, db), regle, regles) if regle and table != 'notes' else None
    indeterminable = bool(reserves) and resoudre is None and (
        (isinstance(filtre, str) and filtre not in colonnes_brutes) or (via is not None and (via[0] not in colonnes_brutes or not _colonnes(db, via[1]))))
    if via and not resoudre:
        exclus_ids = _exclus_via(db, via[1], via[2], via[3], reserves)

    def lignes():
        if indeterminable:
            return
        for rangee in db.execute('SELECT * FROM "%s"' % table.replace('"', '""')):
            brut = dict(zip(colonnes_brutes, rangee))
            if resoudre is not None:
                if not registre.garder(resoudre(brut), reserves):
                    continue
            elif isinstance(filtre, str) and str(brut.get(filtre) or '') in reserves:
                continue
            if via and brut.get(via[0]) in exclus_ids:
                continue
            if table == 'contacts5636' and brut.get('id') in exclus_ids:
                continue
            ligne = {c: net.valeur(brut[c]) for c in colonnes}
            if table == 'notes' and _note_reservee(ligne.get('data'), reserves):
                continue
            yield ligne
    ecrites = _ecrire_table(paquet, domaine, nom, colonnes, lignes, net)
    if comptes is not None:
        comptes[table] = (ecrites, comptes[table][1])
    return ecrites


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
    if not rows:
        return                               # 5.6.108 (lot X) : aucun document connu, aucune connexion au stockage
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


def _lisezmoi_perimetre(meta):
    """5.6.108 (B07) : périmètre lisible : état complet ou incomplet, bilan par domaine, tables exclues et leur motif, tables non couvertes."""
    bilan = meta.get('perimetre') or {}
    if not bilan:
        return []
    lignes = ['Périmètre : ' + ('COMPLET : chaque table de données est exportée ou exclue avec son motif.' if bilan.get('complet') else
                                'INCOMPLET : %d table(s) de données ne sont pas encore couvertes par l’export (liste ci-dessous).' % len(bilan.get('non_couvertes') or []))]
    for domaine, d in sorted((bilan.get('domaines') or {}).items()):
        lignes.append('- %s : %d ligne(s) attendue(s), %d exportée(s), %d exclue(s)%s.' % (domaine, d['attendu'], d['exporte'], d['exclu'],
                                                                                      (' (' + d['motif_exclusion'] + ')') if d['exclu'] else ''))
    if bilan.get('non_couvertes'):
        lignes.append('Tables non couvertes : ' + ', '.join(bilan['non_couvertes']) + '.')
    lignes.append('Tables exclues volontairement : %d (motifs dans MANIFEST.json, rubrique perimetre).' % sum(int(x.get('tables') or 0) for x in bilan.get('tables_exclues') or []))
    return lignes


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
                                 else 'ils restent dans Nextcloud ; documents/inventaire en donne la liste et les empreintes connues.'),
               ] + ([   # 5.6.108 (lot X) : profil « départ vers un autre logiciel »
               'Profil : départ vers un autre logiciel. Ouvrez index.html (sommaire hors ligne). Agenda : agenda/evenements.ics ; tâches : taches/taches.ics ;',
               '  contacts : contacts/contacts.vcf ; courriels archivés : correspondances/<dossier>/*.eml ; dictionnaire des données : dictionnaire/ ;',
               '  exclusions et éléments non inclus : rapports/exclusions.json. Ce n’est pas une sauvegarde de restauration (voir la sauvegarde chiffrée).',
               ] if meta.get('profil') == 'depart' else []) + ['',
               'Absents par construction : mots de passe et leurs hachés, jetons, clés d’accès, coffre des secrets, journaux techniques, index de recherche.', ''] + _lisezmoi_perimetre(meta) + [
               '',
               'Ce fichier n’est pas chiffré et contient des données couvertes par le secret professionnel : conservez-le en lieu sûr et supprimez-le après usage.', '']
    return '\n'.join(lignes).encode('utf-8')


# ------------------------------------------------------------------------------------------------ 5.6.108 (lot X) : profil « départ vers un autre logiciel »
def _depart_lire(db, net, reserves):
    """Lecture sur l'instantané de l'export : événements, tâches, contacts, originaux EML archivés. Mêmes filtres que les tableaux."""
    from . import export56108 as registre

    def lignes(table, filtre='matter'):
        colonnes = _colonnes(db, table)
        if not colonnes:
            return []
        out = []
        for rangee in db.execute('SELECT * FROM "%s"' % table):
            brut = dict(zip(colonnes, rangee))
            if filtre and str(brut.get(filtre) or '') in reserves:
                continue
            out.append({c: net.valeur(brut[c]) for c in colonnes if not _secrete(c)})
        return out
    taches = [{**r, 'source': 'tache'} for r in lignes('tasks')] + [{**r, 'source': 'travail'} for r in lignes('work_tasks_v211')]
    seuls_reserves = _contacts_reserves(db, reserves) if reserves else frozenset()
    contacts = [r for r in lignes('contacts5636', None) if r.get('id') not in seuls_reserves]
    return {'evenements': lignes('agenda5634'), 'taches': taches, 'contacts': contacts, 'eml': registre.eml_a_exporter(db, reserves)}


def _depart_ecrire(desk, paquet, donnees, rows, reserves, verifier, progres):
    """Agenda (VEVENT), tâches (VTODO), contacts (vCard) et originaux EML. Un original absent ou modifié est signalé, jamais certifié."""
    from . import export56108 as registre, sauvegarde5688
    tz = sauvegarde5688._tz(desk)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    with paquet.fichier('agenda/evenements.ics', domaine='agenda', lignes=len(donnees['evenements'])) as sortie:
        sortie.write(registre.evenements_ics(donnees['evenements'], tz, stamp))
    with paquet.fichier('taches/taches.ics', domaine='taches', lignes=len(donnees['taches'])) as sortie:
        sortie.write(registre.taches_ics(donnees['taches'], tz, stamp))
    with paquet.fichier('contacts/contacts.vcf', domaine='contacts', lignes=len(donnees['contacts'])) as sortie:
        sortie.write(registre.vcards(donnees['contacts']))
    eml, pris = donnees['eml'], set()
    if eml:
        from . import office440
        dav = None
        for numero, item in enumerate(eml):
            if verifier:
                verifier()
            if numero % 25 == 0:
                progres('Courriels archivés (%d sur %d)' % (numero, len(eml)), 86)
            try:
                dav = dav or office440.dav_client(desk)
                dav.cfg = {**dav.cfg, 'max_file_bytes': MAX_DOCUMENT}
                data = dav.download({'path': item['chemin'], 'size': 0})
            except Stop as error:
                item.update(statut='non_disponible:' + str(error).split(':')[0], sha256_fichier='', dans_export='')
                continue
            sha = hashlib.sha256(data).hexdigest()
            if item['sha256_attendu'] and sha != item['sha256_attendu']:
                item.update(statut='non_inclus:empreinte_differente', sha256_fichier=sha, dans_export='')
                continue
            nom = 'correspondances/%s/%s.eml' % (_sain(item['dossier']), _sain(item['archive'] or uuid.uuid4().hex))
            if nom in pris:
                nom = nom[:-4] + '.' + uuid.uuid4().hex[:8] + '.eml'
            pris.add(nom)
            with paquet.fichier(nom, domaine='correspondances') as sortie:
                sortie.write(data)
            item.update(statut='inclus', sha256_fichier=sha, dans_export=nom)
    colonnes = ['dossier', 'archive', 'titre', 'chemin', 'taille', 'sha256_attendu', 'statut', 'sha256_fichier', 'dans_export']
    _ecrire_table(paquet, 'correspondances', 'inventaire_eml', colonnes, lambda: iter(eml))
    return {'courriels_non_inclus': [{k: e[k] for k in ('dossier', 'archive', 'statut')} for e in eml if e.get('statut') != 'inclus'],
            'documents_non_inclus': [{'dossier': r['dossier'], 'chemin': r['chemin'], 'statut': r['statut']} for r in rows if r.get('statut') != 'inclus'],
            'courriels_inclus': sum(1 for e in eml if e.get('statut') == 'inclus'), 'documents_inclus': sum(1 for r in rows if r.get('statut') == 'inclus')}


_FIXES = {'LISEZMOI.txt': 'Présentation de l’export, en texte.', 'MANIFEST.json': 'Manifeste : taille et empreinte SHA-256 de chaque fichier, périmètre par domaine.',
          'index.html': 'Sommaire ouvrable hors ligne, sans script ni ressource externe.', 'agenda/evenements.ics': 'Agenda au format iCalendar (RFC 5545) : un VEVENT par occurrence.',
          'taches/taches.ics': 'Tâches au format iCalendar (RFC 5545) : un VTODO par tâche.', 'contacts/contacts.vcf': 'Contacts au format vCard 4.0 (RFC 6350).',
          'rapports/exclusions.json': 'Exclusions par motif, lignes exclues par domaine, documents et courriels non inclus.',
          'dictionnaire/dictionnaire.json': 'Ce dictionnaire, en JSON.', 'dictionnaire/dictionnaire.csv': 'Ce dictionnaire, en CSV (une ligne par colonne).',
          'dictionnaire/dictionnaire.html': 'Ce dictionnaire, lisible dans un navigateur.'}


def _depart_fin(paquet, meta, structure, exportees, rapports):
    """Rapports d'exclusion, dictionnaire des données (chaque fichier, chaque colonne) et sommaire HTML."""
    from . import export56108 as registre
    bilan = meta.get('perimetre') or {}
    rapport = {'tables_exclues_par_motif': bilan.get('tables_exclues') or [], 'non_couvertes': bilan.get('non_couvertes') or [],
               'lignes_exclues_par_domaine': {d: {'exclu': v['exclu'], 'motif': v['motif_exclusion']} for d, v in (bilan.get('domaines') or {}).items() if v['exclu']},
               **(rapports or {})}
    with paquet.fichier('rapports/exclusions.json', domaine='rapports') as sortie:
        sortie.write(json.dumps(rapport, ensure_ascii=False, indent=1).encode('utf-8'))
    sources = {}
    for domaine, nom, table, _b, _f in exportees:
        for ext in ('json', 'csv'):
            sources['%s/%s.%s' % (domaine, nom, ext)] = table
    connues = {'documents/inventaire': ['dossier', 'chemin', 'etag', 'modifie', 'erreur_lecture', 'nature', 'date_document', 'empreinte_texte_sha256', 'statut',
                                        'sha256_fichier', 'dans_export'],
               'correspondances/inventaire_eml': ['dossier', 'archive', 'titre', 'chemin', 'taille', 'sha256_attendu', 'statut', 'sha256_fichier', 'dans_export']}
    entrees = []
    for f in paquet.fichiers + [{'chemin': c} for c in ('LISEZMOI.txt', 'MANIFEST.json', 'index.html', 'dictionnaire/dictionnaire.json',
                                                         'dictionnaire/dictionnaire.csv', 'dictionnaire/dictionnaire.html')]:
        chemin = f['chemin']
        base, _sep, ext = chemin.rpartition('.')
        table = sources.get(chemin)
        if table:
            colonnes = [{'nom': c, 'type': t or 'texte', 'description': registre.decrire_colonne(c)} for c, t in structure.get(table, []) if not _secrete(c)]
            description = 'Table « %s » du logiciel, %s (%s).' % (table, 'valeurs exactes' if ext == 'json' else 'version tableur, formules neutralisées',
                                                                LIBELLES.get(f.get('domaine', ''), f.get('domaine', '')))
        elif base in connues:
            colonnes = [{'nom': c, 'type': 'texte', 'description': registre.decrire_colonne(c)} for c in connues[base]]
            description = 'Inventaire : un fichier par ligne, avec son statut (inclus, non inclus et pourquoi) et son empreinte.'
        elif base == 'dossiers/registre':
            colonnes, description = [], 'Registre des dossiers (un objet par dossier).'
        else:
            colonnes = []
            description = _FIXES.get(chemin) or ('Original d’un courriel archivé (RFC 5322).' if chemin.startswith('correspondances/') else
                                                 'Original d’un document du cabinet.' if chemin.startswith('documents/') else 'Fichier de l’export.')
        entrees.append({'fichier': chemin, 'description': description, 'colonnes': colonnes})
    with paquet.fichier('dictionnaire/dictionnaire.json', domaine='dictionnaire') as sortie:
        sortie.write(json.dumps(entrees, ensure_ascii=False, indent=1).encode('utf-8'))
    with paquet.fichier('dictionnaire/dictionnaire.csv', domaine='dictionnaire') as sortie:
        texte = io.TextIOWrapper(sortie, encoding='utf-8-sig', newline='', write_through=True)
        ecrivain = csv.writer(texte, delimiter=';')
        ecrivain.writerow(['fichier', 'description_du_fichier', 'colonne', 'type', 'description_de_la_colonne'])
        for x in entrees:
            for c in x['colonnes'] or [{'nom': '', 'type': '', 'description': ''}]:
                ecrivain.writerow([cellule(x['fichier']), cellule(x['description']), cellule(c['nom']), cellule(c['type']), cellule(c['description'])])
        texte.flush()
        texte.detach()
    with paquet.fichier('dictionnaire/dictionnaire.html', domaine='dictionnaire') as sortie:
        sortie.write(registre.dictionnaire_html(entrees))
    with paquet.fichier('index.html') as sortie:
        sortie.write(registre.sommaire_html(meta, paquet.fichiers, LIBELLES))


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
        depart = options.get('profil') == 'depart'   # 5.6.108 (lot X) : « Préparer un départ vers un autre logiciel »
        from . import export56108 as registre
        db = sqlite3.connect(Path(desk.c['state_dir'], DESK).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
        try:
            db.execute('BEGIN')
            progres('Registre des dossiers', 2)
            _dossiers(desk, paquet, net, reserves)
            exportees, comptes, regles = tables_exportees(), {}, resolveurs()
            ctx = registre.Contexte(desk.c, db)
            for numero, (domaine, nom, table, _base, filtre) in enumerate(exportees, 1):
                if verifier:
                    verifier()
                progres('%s : %s' % (domaine, nom), 2 + int(70 * numero / len(exportees)))
                _exporter_table(paquet, db, net, reserves, domaine, nom, table, filtre, comptes, ctx, regles)
            bilan = perimetre(db, exportees, comptes)   # 5.6.108 (B07) : attendu / exporté / exclu par domaine, tables non couvertes
            structure = {t: [(r[1], r[2] or '') for r in db.execute('PRAGMA table_info("%s")' % t.replace('"', '""'))] for t in comptes} if depart else {}
            depart_donnees = _depart_lire(db, net, reserves) if depart else None
            db.rollback()
        finally:
            db.close()
        inclure = bool(options.get('documents')) or depart
        rows = _inventaire(desk, reserves, 'dans_nextcloud')
        if inclure:
            _documents(desk, paquet, rows, lambda texte: progres(texte, 80))
        colonnes = ['dossier', 'chemin', 'etag', 'modifie', 'erreur_lecture', 'nature', 'date_document', 'empreinte_texte_sha256', 'statut', 'sha256_fichier', 'dans_export']
        _ecrire_table(paquet, 'documents', 'inventaire', colonnes, lambda: iter(rows))
        rapports = _depart_ecrire(desk, paquet, depart_donnees, rows, reserves, verifier, progres) if depart else None
        progres('Manifeste', 92)
        meta = {'format': FORMAT, 'cree': desk.now(), 'version': __version__, 'documents_inclus': inclure,
                'dossiers_reserves': {'traitement': 'inclus' if inclure_reserves else 'exclus', 'nombre': len(reserves_toutes)}, 'secrets_retires': net.retires,
                'perimetre': bilan, 'profil': 'depart' if depart else 'integral'}
        if depart:
            _depart_fin(paquet, meta, structure, exportees, rapports)
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
    bilan = manifeste.get('perimetre') if isinstance(manifeste.get('perimetre'), dict) else {}
    lignes = {str(f.get('chemin')): f['lignes'] for f in manifeste.get('fichiers') or [] if isinstance(f, dict) and isinstance(f.get('lignes'), int)}
    for domaine, d in (bilan.get('domaines') or {}).items():   # 5.6.108 (B07) : le bilan par domaine doit correspondre aux fichiers présents
        try:
            ecart = int(d.get('exporte') or 0) != sum(lignes.get(str(n), 0) for n in d.get('fichiers') or [])
        except (AttributeError, TypeError, ValueError):
            ecart = True
        if ecart:
            signaler(domaine, 'bilan du domaine différent des fichiers présents')
        for nom in (d.get('fichiers') or []) if isinstance(d, dict) else []:   # 5.6.108 (lot X) : chaque fichier déclaré par le bilan est présent et haché
            if str(nom) not in attendus or str(nom) not in noms:
                signaler(nom, 'fichier déclaré par le bilan absent')
    return {'ok': not anomalies, 'fichiers': len(attendus), 'verifies': verifies, 'anomalies': anomalies, 'cree': manifeste.get('cree', ''), 'version': manifeste.get('version', ''),
            'dossiers_reserves': manifeste.get('dossiers_reserves', {}), 'documents_inclus': bool(manifeste.get('documents_inclus')),
            'complet': bool(bilan.get('complet')) and not anomalies, 'non_couvertes': list(bilan.get('non_couvertes') or [])[:500],
            'perimetre_declare': bool(bilan)}


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
    profil = str(data.get('profil') or 'integral')   # 5.6.108 (lot X) : « depart » inclut les originaux, ICS, vCard, EML et le dictionnaire
    if profil not in ('integral', 'depart'):
        raise Stop('option_export_invalide')
    options = {'documents': data.get('documents') in (True, 'on', '1', 'true') or profil == 'depart', 'dossiers_reserves': reserves, 'profil': profil}
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
    bilan = meta.get('perimetre') or {}   # 5.6.108 (B07) : complet ou non, gardé avec la demande (montré dans Réversibilité)
    options = {**json.loads(row['options'] or '{}'), 'perimetre': {'complet': bool(bilan.get('complet')), 'non_couvertes': len(bilan.get('non_couvertes') or [])}}
    desk.db.execute("UPDATE exports5688 SET statut='pret',fin=?,etape='Prêt',progression=100,taille=?,sha256=?,expire=?,options=? WHERE id=?",
                    (desk.now(), taille, sha.hexdigest(), expire, json.dumps(options), row['id']))
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
