"""5.6.108 (lot X) : export complet et départ vers un autre logiciel.

1. Registre des tables métier qui n'étaient dans aucune liste (B07 : « Export INCOMPLET : N tables ») : chaque table de la base est déclarée ici,
   table par table, soit exportée (``EXPORT`` : mêmes quintuplets que ``export5688.TABLES``), soit exclue avec son motif (``EXPORT_EXCLUES``).
   La liste reste fermée : une table nouvelle, ni exportée ni exclue, laisse l'export « INCOMPLET » (jamais de copie aveugle de toutes les tables).
   Le module est un module déclarant de ``export5688.MODULES_DECLARANTS`` ; les tables restent la propriété de leur module, rangées ici par module.

2. Filtre des dossiers réservés, ligne par ligne. Une table qui porte une colonne de dossier garde le filtre simple de ``export5688`` (colonne, ou
   rattachement à une table parente). Quand le dossier se lit autrement (courriel classé, chemin d'un fichier, portée d'une règle, liste JSON,
   empreinte de la conversation, table parente elle-même résolue), ``RESOLVEURS`` donne la règle : elle rend l'ensemble des dossiers de la ligne,
   vide (ligne du cabinet, sans dossier) ou ``None`` (dossier non déterminable). Dès qu'un dossier réservé est exclu de l'export, une ligne dont le
   dossier n'est pas déterminable n'est pas exportée en clair ; elle est comptée « exclue » dans le bilan du domaine.

3. Profil « Préparer un départ vers un autre logiciel » : originaux des documents (copie depuis le stockage), courriels archivés en EML originaux,
   agenda en ICS (VEVENT) et tâches en ICS (VTODO), contacts en vCard, sommaire HTML hors ligne, dictionnaire des données et rapports d'exclusion.
   Tout est local et déterministe : aucun appel de modèle, disponible pendant la pause de l'IA.
"""
import hashlib
import html
import json
from pathlib import PurePosixPath
import re

DESK = 'desk.sqlite3'

# ------------------------------------------------------------------------------------------------ motifs d'exclusion
_S = 'Comptes, connexions, appareils, secrets ou jetons : jamais dans un export (sauvegarde chiffrée seulement).'
_T = ('Suivi technique du logiciel (file de travaux, curseurs, verrous, reçus d’idempotence, états d’exécution), sans contenu propre au '
      'cabinet.')
_C = 'Cache, index ou projection recalculés depuis une source exportée ou externe (documents, messagerie, sources publiques).'
_IA = 'Mesures et journaux techniques de l’IA (routage, consommation, transmissions, diagnostics) ; les décisions et les résultats sont exportés.'
_TMP = 'Demande ou aperçu temporaire à durée de vie courte (jeton, défi de confirmation) ; le résultat confirmé est exporté.'
_PERSO = 'Préférence d’affichage personnelle (lu, épinglé, masqué) : jamais reprise dans un export du cabinet.'
_SECU = ('Journal de sécurité (constats, contrôle anti-fraude, adresses bloquées) : se consulte dans l’écran de sécurité, sans dossier '
         'déterminable.')
_ND = 'Dossier non déterminable (rattachement par empreinte ou par courriel non classé) : non exportée en clair.'
_AUTRE = ('Base locale distincte (index de recherche, mémoire des envois, état de lecture des courriels) : recalculée depuis la messagerie et les '
          'documents.')
_ENVOI = ('Journal des départs SMTP (destinataires, codes de retour) : traçabilité technique de l’envoi ; les messages envoyés restent dans '
          'la messagerie (Envoyés) et les courriels archivés sont exportés en EML.')
_SYNC = ('Journal de synchronisation entre postes (opérations signées, têtes, curseurs, clés publiques des postes) : les objets métier qu’il '
         'réplique sont exportés depuis leurs propres tables.')
MOTIFS = {'secrets': _S, 'technique': _T, 'cache': _C, 'ia': _IA, 'temporaire': _TMP, 'personnel': _PERSO, 'securite': _SECU,
          'non_determinable': _ND, 'autre_base': _AUTRE, 'envoi': _ENVOI, 'synchronisation': _SYNC}


def _x(motif, *tables):
    return tuple((t, motif) for t in tables)


def _e(domaine, *lignes):
    """(fichier, table, filtre) → quintuplets du registre."""
    return tuple((domaine, nom, table, DESK, filtre) for nom, table, filtre in lignes)


# ------------------------------------------------------------------------------------------------ tables exportées (par module propriétaire)
EXPORT = (
    # desk : discussions de l'assistant, courriels suivis, brouillons conservés, propositions d'échéances, retours
    _e('conversations', ('discussions_de_l_assistant', 'assistant_threads', 'matter'), ('messages_de_l_assistant', 'assistant_messages', 'thread_id'),
       # historique56109 (5.6.109, lot H) : propriétaire, participants, liens et contextes successifs de chaque discussion (aucun message recopié)
       ('proprietaires_des_discussions', 'assistant_thread_owners109', 'thread_id'), ('participants_des_discussions', 'assistant_thread_members109', 'thread_id'),
       ('elements_rattaches_aux_discussions', 'assistant_thread_links109', 'thread_id'), ('contextes_des_discussions', 'assistant_thread_contexts109', 'thread_id'),
       ('questions_au_dossier', 'conversations', 'scope'),
       ('pieces_televersees_dans_l_assistant', 'assistant_attachments_v360', 'scope'),                        # improvements36
       ('textes_des_pieces_televersees', 'assistant_attachment_parts_v363', 'attachment_id'),
       ('messages_du_cockpit', 'cockpit530_messages', 'matter')) +                                           # cockpit530
    _e('courriels', ('associations_courriel_dossier', 'proposals', 'matter'), ('courriels_a_traiter', 'work_items', 'matter'),
       ('brouillons_conserves', 'manual_drafts', 'mail_key'), ('revues_des_pieces_jointes', 'attachment_reviews', 'mail_key'),
       ('retours_sur_les_courriels', 'feedback', 'matter'),
       ('pieces_jointes_classees', 'attachments_filed5636', 'matter'),                                          # ged5636
       ('sous_dossiers_imap', 'boite56106_sous_dossiers', 'matter_id'),                                         # boite56106
       ('orchestrations', 'mail_orchestrations_v260', 'matter'),                                                # orchestrator
       ('archives_de_courriels', 'mail_archives', 'id'), ('rattachements_des_archives', 'mail_archive_links', 'matter_id'),   # correspondances5643
       ('fichiers_des_archives', 'mail_archive_assets', ('link_id', 'mail_archive_links', 'id', 'matter_id')),
       ('journal_des_archives', 'mail_archive_events', ('link_id', 'mail_archive_links', 'id', 'matter_id')),
       ('mandats_de_classement', 'mail_filing_mandates', 'matter_id'), ('regles_par_message', 'mail_filing_message_rules', 'matter_id'),
       ('politiques_de_classement', 'mail_filing_policies', None),
       ('regles_de_courrier', 'mailrules5632', None),                                                           # courrier5632
       ('regles_de_pertinence', 'mail_rules_v370', None),                                                       # relevance370
       ('pieces_par_fil', 'pj56102_fils', 'dossier'), ('attributions_des_fils', 'pj56102_attributions', 'dossier'),   # pj56102
       ('pieces_gmail', 'pj56102_gmail', 'dossier'),
       ('brouillons_outlook', 'outlook56103_brouillons', 'owner'),                                               # outlook56103
       ('actions_du_pilote', 'pilote56102_actions', ('thread', 'fils5650', 'id', 'matter')),                    # pilote_messagerie56102
       ('demandes_au_pilote', 'pilote56102_demandes', ('thread', 'fils5650', 'id', 'matter')),
       ('propositions_d_echeance', 'deadline_proposals', 'matter')) +
    _e('dossiers', ('instances_et_juridictions', 'dossier_instances5698', 'dossier_id'),                         # fiche5698
       # projection56109 (5.6.109, lot D) : fiche Conseil / Contentieux — décisions de l'avocat sur les mentions, qualités par instance, champs, propositions
       ('liens_des_mentions_lues', 'fiche_liens56109', 'dossier_id'), ('mentions_ecartees', 'fiche_decisions56109', 'dossier_id'),
       ('qualites_par_instance', 'fiche_participations56109', 'dossier_id'), ('champs_de_la_fiche', 'fiche_valeurs56109', 'dossier_id'),
       ('historique_des_champs_de_la_fiche', 'fiche_valeurs_historique56109', 'dossier_id'),
       ('propositions_pour_la_fiche', 'fiche_propositions56109', 'dossier_id'), ('profils_de_fiche', 'fiche_profils56109', 'dossier_id'),
       ('modeles_de_fiche_du_cabinet', 'fiche_modeles56109', None),
       ('types_de_dossier', 'dossier_types56100', 'cle'),                                                       # rangement56100
       ('corrections_de_la_fiche', 'fiche_corrections5634', 'matter'), ('liens_entre_parties', 'party_links5634', 'matter'),   # dossier5627
       ('corrections_de_la_fiche_5644', 'corrections5644', 'matter'),                                           # dossier5644
       ('alias_des_parties', 'd51_aliases', 'matter'), ('elements_d51', 'd51_assertions', 'matter'), ('documents_lus', 'd51_documents', 'matter'),   # modele5651
       ('decisions_jugees', 'd51_judgments', 'matter'), ('procedures', 'd51_procedures', 'matter'),
       ('participations', 'd51_participations', ('procedure_id', 'd51_procedures', 'id', 'matter')),
       ('decisions_sur_les_parties', 'd51_party_decisions', 'matter'), ('journal_de_relecture_du_modele', 'd51_review_log', 'matter'),
       ('decisions_du_cabinet', 'd51_cabinet_decisions', 'matter'), ('paragraphes_rediges', 'd51_draft_paragraphs', 'matter'),   # memoire5651
       ('informations_manquantes', 'd51_gaps', 'matter'), ('recits', 'd51_narratives', 'matter'),              # vue5651
       ('resumes', 'd59_resumes', 'matter'),                                                                    # resume5659
       ('chronologie', 'timeline_events', 'matter'), ('memoire_juridique', 'legal_memory_records', 'matter'),  # legal_memory
       ('historique_de_la_memoire_juridique', 'legal_memory_history', ('record_id', 'legal_memory_records', 'id', 'matter')),
       ('conflits_de_la_memoire_juridique', 'legal_memory_conflicts', 'matter'),
       ('propositions_de_l_analyse_et_decisions', 'reanalyse56109_propositions', 'matter'),                     # reanalyse56109 (5.6.109, lot A) : comparaison avant remplacement, décisions de l'avocat
       ('controles_de_conflits', 'conflicts500_checks', 'matter'),                                              # metier500
       ('journal_des_prescriptions', 'limitation500_journal', ('limitation_id', 'limitation500', 'id', 'matter')),
       ('fiches_de_rendez_vous', 'meeting500_fiches', 'matter'), ('comptes_rendus_de_rendez_vous', 'meeting500_reports', 'matter'),
       ('journal_des_echeances', 'deadline_journal450', ('deadline_id', 'deadlines450', 'id', 'matter')),       # echeances450
       ('demandes_de_documents', 'docreq520', 'matter'),                                                        # docrequest520
       ('projets_de_pieces', 'pieces510_drafts', 'matter'), ('communications_de_pieces', 'pieces520_comms', 'matter'),   # pieces510, pieces520
       ('pieces_du_registre', 'exhibit_registry_v240', 'matter'),                                               # legal_research
       ('liens_espace_de_travail', 'matter_workspace_links_v310', 'matter'), ('liens_facturation', 'matter_invoice_links_v310', 'matter'),   # workstation
       ('engagements_suivis', 'commitments_v568', 'matter'),                                                   # followup568
       ('preparations_d_evenement', 'p57_preparations', 'matter'), ('mises_a_jour_client', 'p57_updates', 'matter'),   # parcours5657
       ('comptes_rendus_d_audition', 'transcript_reports_v290', 'matter'),                                      # cabinet_pilot
       ('preparations_de_rendez_vous', 'meeting_preparations_v290', 'matter'),
       ('suivi_des_devis', 'suivi_devis56102', 'dossier'), ('plans_de_convention', 'accord56102_plans', 'dossier'),   # accord56102
       ('dossiers_d_operation', 'operation_dossiers5683', 'matter'),                                            # operations5683
       ('etapes_d_operation', 'workflow_steps5683', ('dossier', 'operation_dossiers5683', 'id', 'matter')),
       ('preuves_des_champs', 'field_evidence5683', ('dossier', 'operation_dossiers5683', 'id', 'matter')),
       ('formalites', 'filings5683', ('dossier', 'operation_dossiers5683', 'id', 'matter')),
       ('evenements_d_operation', 'events5683', ('dossier', 'operation_dossiers5683', 'id', 'matter')),
       ('livrables_d_operation', 'deliverables5683', ('dossier', 'operation_dossiers5683', 'id', 'matter')),
       ('decisions_de_cession', 'cession_decisions5684', ('dossier', 'operation_dossiers5683', 'id', 'matter')),   # cessions5684
       ('regles_de_cession_appliquees', 'cession_rules_used5684', ('dossier', 'operation_dossiers5683', 'id', 'matter')),
       ('annotations_des_fichiers', 'fichiers56103_notes', 'chemin'),                                            # fichiers56103
       ('sessions_du_navigateur_supervise', 'nav5673_sessions', 'matter')) +                                     # navigateur5673
    _e('relecture', ('sessions_de_relecture', 'd51_review_sessions', 'matter'), ('propositions_de_relecture', 'd51_proposals', 'matter'),   # relecture5651
       ('exports_de_relecture', 'd51_review_exports', 'matter'),
       ('file_de_relecture_d51', 'd51_queue', 'matter'), ('journal_de_la_file_d51', 'd51_queue_log', 'matter'),  # file5651
       ('relectures_de_production', 'production_reviews_v391', ('output_id', 'production_outputs_v390', 'id', 'matter')),   # production391
       ('validations_de_mission', 'validations5614', ('mission', 'missions5614', 'id', 'matter')),             # pilote5613, taches5614
       ('corrections_de_l_avocat', 'correction_events_v410', 'matter'),                                        # learning410
       ('exemples_de_correction', 'correction_examples_v370', 'matter'),                                       # relevance370
       ('corrections_de_style', 'corrections5667', 'kind'),                                                    # apprentissage5667
       ('annulations_de_decisions', 'decision_cancellations5634', 'ident'),                                    # decisions5634
       ('decisions_sur_les_signaux', 'signal_events5634', ('signal', 'proactive_signals', 'id', 'matter')),     # proactive
       ('resultats_valides', 'learning_outcomes_v392', 'output_id')) +                                         # learning392
    _e('decisions', ('decisions_a_prendre', 'cabinet_decisions_v290', 'matter'),                               # cabinet_pilot
       ('propositions_en_attente', 'autonomy_pending480', 'matter'),                                           # autonomy480
       ('propositions_de_diligence', 'diligence_proposals_v230', 'matter'), ('propositions_de_facturation', 'billing_proposals_v230', 'matter'),   # autonomy
       ('initiatives', 'initiatives5636', 'matter'),                                                           # initiatives5636
       ('propositions_de_regles', 'rule_proposals5613', 'matter'),                                             # pilote5613
       ('propositions_d_opportunite', 'opp5676_propositions', ('opp', 'opp5676', 'id', 'matter')),            # crm5676
       ('journal_des_opportunites', 'opp5676_journal', ('opp', 'opp5676', 'id', 'matter')),
       # decision56109 (5.6.109, lot K) : décisions locales (versions, actions proposées et retenues, sans contenu) et questions ciblées avec leur réponse
       ('decisions_locales', 'decisions56109', 'dossier'), ('questions_ciblees', 'decision_questions56109', 'dossier'),
       # simulation56109 (5.6.109, lot N) : simulations de décision (instantané, modifications d'hypothèse, résultat, sources figées), cadrage confirmé, corrections de l'avocat
       ('simulations_de_decision', 'simulation_runs56109', 'matter_id'), ('cadrages_de_simulation', 'simulation_cadrages56109', 'matter_id'),
       ('corrections_des_simulations', 'simulation_corrections56109', 'matter_id')) +
    _e('missions', ('missions', 'missions5614', 'matter'), ('taches_de_mission', 'tasks5614', ('mission', 'missions5614', 'id', 'matter')),   # taches5614
       ('productions_de_mission', 'artifacts5614', ('mission', 'missions5614', 'id', 'matter')),
       ('questions_de_mission', 'decisions5614', ('mission', 'missions5614', 'id', 'matter')),
       ('missions_v567', 'missions_v567', 'matter'), ('journal_des_missions', 'mission_events_v567', ('mission', 'missions_v567', 'id', 'matter')),   # missions567
       ('conseils_de_modeles', 'conseil56108', 'matter'),                                                       # conseil56108 (5.6.108, lot K)
       ('plans', 'plans_v568', 'matter'), ('etapes_des_plans', 'plan_steps_v568', ('plan_id', 'plans_v568', 'id', 'matter')),   # agents568
       ('missions_de_procedure', 'dp_missions5632', 'matter'), ('projets_de_procedure', 'dp_drafts5632', ('mission', 'dp_missions5632', 'id', 'matter')),   # procedure5632
       ('evenements_de_procedure', 'dp_events5632', ('mission', 'dp_missions5632', 'id', 'matter')),
       ('projets_de_courriel', 'maildraft5613', 'matter'),                                                     # maildraft5613
       ('deroulements_de_playbook', 'playbook_runs_v380', 'matter'),                                           # operating380
       ('etapes_de_playbook', 'playbook_steps_v380', ('run_id', 'playbook_runs_v380', 'id', 'matter')),
       ('deroulements_d_outils', 'playbook_runs5636', 'matter'),                                               # outils5636
       ('grilles_d_analyse', 'grids5636', 'matter'), ('cellules_des_grilles', 'grid_cells5636', ('grid', 'grids5636', 'id', 'matter')),
       ('regles_documentaires_appliquees', 'document_runs568', 'matter'),                                      # documents568
       ('livrables_produits', 'production_deliverables_v420', 'matter'), ('productions', 'production_outputs_v390', 'matter'),   # production390, 420
       ('studio_de_production', 'production_studio_v420', 'matter'),
       ('reception_telephonique', 'reception_v567', 'channel')) +                                              # reception567
    _e('documents', ('projets_de_documents', 'document_projects_v220', 'matter'),                              # document_projects
       ('etapes_des_projets_de_documents', 'document_project_steps_v230', ('project_id', 'document_projects_v220', 'id', 'matter')),
       ('fichiers_des_projets_de_documents', 'document_creation_files_v230', ('project_id', 'document_projects_v220', 'id', 'matter')),
       ('controles_des_projets_de_documents', 'document_controls_v230', 'matter'),                             # autonomy
       ('projets_word', 'word_projects_v250', 'matter'),                                                       # word_legal
       ('fichiers_des_projets_word', 'word_creation_files_v250', ('project_id', 'word_projects_v250', 'id', 'matter')),
       ('projets_d_audience', 'hearing_projects_v250', 'matter'),                                              # hearing
       ('ecritures_d_audience', 'hearing_writings_v250', ('project_id', 'hearing_projects_v250', 'id', 'matter')),
       ('fichiers_des_projets_d_audience', 'hearing_creation_files_v250', ('project_id', 'hearing_projects_v250', 'id', 'matter')),
       ('projets_sur_modele', 'cabinet_projects_v330', 'matter'),                                              # cabinet_docs33
       ('fichiers_des_projets_sur_modele', 'cabinet_files_v330', ('project_id', 'cabinet_projects_v330', 'id', 'matter')),
       ('projets_d_actes', 'act_projects', 'matter'), ('analyses_strategiques', 'strategy_analyses', 'matter'),   # strategic
       ('matrices_de_preuve', 'matrix_runs', 'matter'), ('lignes_des_matrices', 'evidence_matrix_rows', 'matter'),
       ('historique_strategique', 'strategic_history', 'entity_id'),
       ('courriers_generes', 'courriers5679', 'matter'),                                                       # courrier5679
       ('documents_generes', 'office_documents5684', 'matter'),                                                # office_gen5684
       ('controles_de_documents', 'controles5632', 'matter'),                                                  # controle5632
       ('controles_deterministes', 'deterministic_controls_v240', 'matter'),                                  # legal_research
       ('questionnaires_des_actes', 'questionnaires56108', 'matter'),                                          # questionnaires56108 (5.6.108)
       ('journal_des_questionnaires', 'questionnaires56108_journal', ('questionnaire', 'questionnaires56108', 'id', 'matter')),
       ('questions_posees', 'questionnaires56108_questions', ('questionnaire', 'questionnaires56108', 'id', 'matter')),
       ('signatures_par_lien', 'signaturepdf56108', 'matter')) +                                               # signaturepdf56108 (5.6.108, lot Z) : sans clé (coffre)
    _e('recherche_juridique', ('recherches', 'legal_research_queries_v240', 'matter'), ('pistes', 'legal_research_leads_v240', 'matter'),   # legal_research
       ('autorites', 'legal_authorities_v240', 'matter'),
       ('consultations', 'legal_opinion_projects_v260', 'matter'),                                             # opinions
       ('sources_des_consultations', 'legal_opinion_sources_v260', ('project_id', 'legal_opinion_projects_v260', 'id', 'matter')),
       ('controles_de_citations', 'citation_checks470', 'matter'),                                             # sources470
       ('preuves_de_recherche', 'legal_proofs5683', 'matter'),                                                 # liens5683
       ('veille_retenue', 'veille_items5632', 'matter')) +                                                     # veille5632
    _e('factures', ('journal_des_documents', 'b53_events', ('document', 'b53_documents', 'id', 'matter')),     # facturation5653
       ('numerotation', 'b53_sequences', None), ('suggestions', 'b53_suggestions', 'matter'),
       # catalogue56109 (5.6.109, lot D) : prestations du cabinet, versions publiées, simulations et lien vers les estimations
       ('prestations_du_cabinet', 'catalogue_offres56109', None), ('versions_publiees_des_prestations', 'catalogue_versions56109', None),
       ('simulations_d_honoraires', 'catalogue_simulations56109', 'dossier_id'), ('estimations_issues_du_catalogue', 'catalogue_estimations56109', 'dossier_id'),
       ('fichiers_emis', 'b56_issued_files', ('document', 'b53_documents', 'id', 'matter')),
       ('provisions', 'cabinet_provisions_v290', 'matter'),                                                    # cabinet_pilot
       ('relances', 'relances5636', 'matter'),                                                                 # relances5636
       ('factures_recues', 'ef5670_recues', None),                                                             # efacture5670
       ('suivi_facture_electronique', 'ef5670_suivi', ('document', 'b53_documents', 'id', 'matter'))) +
    _e('temps', ('propositions_de_temps', 'time500_proposals', 'matter'),) +                                    # metier500
    _e('societes', ('decisions_sociales', 'decisions5636', ('company', 'companies5636', 'id', 'matter')),       # corporate5636
       ('mouvements_de_titres', 'movements5636', ('company', 'companies5636', 'id', 'matter')),
       ('dirigeants', 'officers5636', ('company', 'companies5636', 'id', 'matter')),
       ('operations_sociales', 'operations5636', ('company', 'companies5636', 'id', 'matter'))) +
    _e('messagerie_interne', ('tableaux_blancs', 'tableau56103_scenes', ('thread', 'fils5650', 'id', 'matter')),   # tableau56103
       ('elements_des_tableaux', 'tableau56103_elements', ('thread', 'fils5650', 'id', 'matter')),
       ('fichiers_des_tableaux', 'tableau56103_fichiers', ('thread', 'fils5650', 'id', 'matter')),
       ('propositions_de_schema', 'tableau56103_propositions', ('thread', 'fils5650', 'id', 'matter'))) +
    _e('routines', ('routines_programmees', 'routines5652', 'matters_json'),                                   # routines5652
       ('versions_des_routines_programmees', 'routines5652_versions', 'routine_id'),
       ('occurrences_des_routines', 'routines5652_occ', 'routine_id')) +
    _e('modeles', ('regles_metier', 'business_rules_v410', 'scope_value'), ('documents_de_confiance', 'trusted_documents_v410', 'scope_value'),   # learning410
       ('regles_apprises', 'd51_learned', 'matter'),                                                           # apprentissage5651
       ('regles_d_ecriture', 'rules480', 'scope_value'), ('versions_de_regles', 'rule_versions5683', None),     # rules480, regles5683
       ('profils_de_ton', 'tone_profiles480', None), ('ajustements_de_ton', 'tone_overrides480', None),         # tone480
       ('habitudes_de_style', 'style550_habits', 'doc_type'),                                                  # style550
       ('regles_documentaires', 'document_rules568', None),                                                    # documents568
       ('definitions_de_playbook', 'playbook_definitions_v380', None), ('playbooks_d_outils', 'playbooks5636', None),   # operating380, outils5636
       ('logo_du_cabinet', 'identite56106_logos', None)) +   # identite56106 (modeles56108_actifs : déclarée dans TABLES)
    _e('qualification', ('cas_d_essai_des_modeles', 'banc5667_cases', 'matter'), ('essais_des_modeles', 'banc5667_runs', None),   # modeles5667
       ('cas_d_evaluation_d51', 'd51_eval_cases', 'id'), ('evaluations_d51', 'd51_eval_runs', None),          # apprentissage5651
       ('cas_d_evaluation', 'evaluation_cases_v380', None), ('evaluations', 'evaluation_runs_v380', None),     # operating380
       ('cas_de_reference_juridique', 'legal_benchmark_cases_v410', None), ('essais_de_reference_juridique', 'legal_benchmark_runs_v410', None),   # evaluation410
       ('resultats_de_reference_juridique', 'legal_benchmark_results_v410', None)) +
    # consommation56109 (5.6.109, lot M) : quantités de service du registre du budget (fournisseur, unité, quantité, état), liées aux
    # réservations déjà exportées (``budget_reservations5689``) par leur identifiant d'appel ; ni clé, ni texte, ni réservation réactivable
    _e('gouvernance', ('quantites_de_services', 'budget_quantites56109', None)) +
    # artefacts56109, modele_docx56109 (5.6.109, lot V) : contenu de chaque version des livrables structurés (lié au registre commun), plans des
    # sous-agents, modèles Word du cabinet (métadonnées et zones ; l'original reste dans le dossier privé de l'application) et productions contrôlées
    _e('dossiers', ('contenus_des_livrables_structures', 'livrables_contenus56109', 'matter_id'), ('plans_des_sous_agents', 'livrables_plans56109', 'matter_id'),
       ('actes_produits_depuis_un_modele', 'modeles_productions56109', 'matter_id')) +
    _e('modeles', ('modeles_word_du_cabinet', 'modeles_actes56109', None)) +
    # bland56109 (5.6.109, lot B) : téléphonie par IA — aperçu exact de chaque demande (numéro, texte, montant), agents IA enregistrés comme
    # routines, propositions relevées, rappels de rendez-vous et accords des clients ; jamais la clé (coffre), jamais de jeton
    _e('routines', ('demandes_de_telephonie_ia', 'bland56109_demandes', None), ('agents_ia_bland', 'bland56109_agents', None),
       ('propositions_des_agents_ia', 'bland56109_propositions', None), ('rappels_de_rendez_vous', 'bland56109_rappels', None),
       ('accords_des_clients_pour_les_rappels', 'bland56109_accords', None)) +
    # fonds_juridique56109 (5.6.109, lot Q, G23) : fonds juridique local — lots (manifeste, droits, couverture, corpus arrêté au, état),
    # textes publics versionnés (texte officiel, intervalles, empreintes) et désignations actives ; l'index (fichiers) est recalculé
    _e('recherche_juridique', ('lots_du_fonds_juridique_local', 'fonds56109_lots', None), ('documents_du_fonds_juridique_local', 'fonds56109_documents', None),
       ('lots_actifs_du_fonds_juridique_local', 'fonds56109_actifs', None)) +
    # gestion56109 (5.6.109, lot G1) : diligences de préfacturation et leurs traces (durée inconnue gardée inconnue), contrats d'assistance
    # régulière (versions immuables, périodes, consommations), mouvements complémentaires (fonds de tiers, débours) et changements de
    # coordonnées bancaires (IBAN masqué et empreinte seulement : jamais l'IBAN complet)
    _e('temps', ('diligences_de_prefacturation', 'prefact_diligences56109', 'matter'),
       ('traces_des_diligences', 'prefact_traces56109', ('diligence', 'prefact_diligences56109', 'id', 'matter'))) +
    _e('factures', ('contrats_d_assistance_reguliere', 'assistance_contrats56109', None), ('versions_des_contrats_d_assistance', 'assistance_versions56109', None),
       ('periodes_d_assistance', 'assistance_periodes56109', None), ('consommations_d_assistance', 'assistance_consommations56109', 'matter'),
       ('mouvements_fonds_frais_debours', 'mouvements56109', 'matter'), ('changements_de_coordonnees_bancaires', 'coordonnees56109', 'matter')) +
    # partage_client56109, appels56109, remplacements56109, kit_audience56109 (5.6.109, lot G2) : partages avec le client (versions exactes
    # du registre, demandes, réponses et reçus, fichiers reçus en quarantaine ; le jeton de retour n'existe qu'haché, colonne retirée), appels
    # (texte original, comptes rendus successifs, actions décidées), remplacements (instructions versionnées, preuves), kits d'audience
    # (manifeste et éléments, notes hors ligne) ; le contenant chiffré et les fichiers en quarantaine restent des fichiers du poste
    _e('dossiers', ('partages_avec_le_client', 'partages_client56109', 'matter_id'),
       ('ressources_des_partages_client', 'partages_client_ressources56109', ('partage_id', 'partages_client56109', 'id', 'matter_id')),
       ('demandes_au_client', 'partages_client_demandes56109', ('partage_id', 'partages_client56109', 'id', 'matter_id')),
       ('reponses_du_client', 'partages_client_reponses56109', ('partage_id', 'partages_client56109', 'id', 'matter_id')),
       ('fichiers_recus_en_quarantaine', 'partages_client_quarantaine56109', 'matter_id'),
       ('appels_consignes', 'appels56109', 'matter_id'),
       ('comptes_rendus_d_appel', 'appels_comptes_rendus56109', ('appel_id', 'appels56109', 'id', 'matter_id')),
       ('actions_decidees_apres_appel', 'appels_actions56109', ('appel_id', 'appels56109', 'id', 'matter_id')),
       ('remplacements_et_postulation', 'remplacements56109', 'matter_id'),
       ('instructions_aux_confreres', 'remplacements_instructions56109', ('remplacement_id', 'remplacements56109', 'id', 'matter_id')),
       ('preuves_des_remplacements', 'remplacements_preuves56109', ('remplacement_id', 'remplacements56109', 'id', 'matter_id')),
       ('kits_d_audience', 'kits_audience56109', 'matter_id'),
       ('elements_des_kits_d_audience', 'kits_audience_elements56109', ('kit_id', 'kits_audience56109', 'id', 'matter_id')),
       ('notes_hors_ligne_des_kits', 'kits_audience_notes56109', ('kit_id', 'kits_audience56109', 'id', 'matter_id'))) +
    # closing56109, suites56109, formalites56109 (5.6.109, lot G3) : « À finaliser » des opérations (conditions, dépendances, révisions des actes,
    # journal immuable des preuves, décisions qualifiées), suites et exécution (obligations sourcées, actions, échéancier, journal), formalités
    # supervisées (données sourcées, pièces exactes, effets et leurs états, documents reçus avec IBAN masqué seulement, réservations de budget,
    # journal) ; aucune session, aucun cookie, aucun PIN, aucune autorisation réactivable (le permis expire et se reconstruit sur l'opération)
    _e('dossiers', ('conditions_a_finaliser', 'closing_conditions56109', 'matter'),
       ('dependances_des_conditions', 'closing_dependances56109', ('condition', 'closing_conditions56109', 'id', 'matter')),
       ('revisions_des_actes_d_operation', 'closing_actes56109', 'matter'),
       ('journal_des_conditions', 'closing_journal56109', ('condition', 'closing_conditions56109', 'id', 'matter')),
       ('decisions_qualifiees_de_closing', 'closing_decisions56109', 'matter'),
       ('suites_et_execution', 'suites56109', 'matter'),
       ('obligations_des_suites', 'suites_obligations56109', ('suite', 'suites56109', 'id', 'matter')),
       ('actions_des_suites', 'suites_actions56109', ('suite', 'suites56109', 'id', 'matter')),
       ('echeanciers_des_suites', 'suites_echeancier56109', ('suite', 'suites56109', 'id', 'matter')),
       ('journal_des_suites', 'suites_journal56109', ('suite', 'suites56109', 'id', 'matter')),
       ('formalites_supervisees', 'formalites56109', 'matter'),
       ('pieces_des_formalites', 'formalites_pieces56109', ('formalite', 'formalites56109', 'id', 'matter')),
       ('effets_des_formalites', 'formalites_effets56109', ('formalite', 'formalites56109', 'id', 'matter')),
       ('documents_des_formalites', 'formalites_documents56109', ('formalite', 'formalites56109', 'id', 'matter')),
       ('budgets_reserves_des_formalites', 'formalites_reservations56109', ('formalite', 'formalites56109', 'id', 'matter')),
       ('journal_des_formalites', 'formalites_journal56109', ('formalite', 'formalites56109', 'id', 'matter'))) +
    # offre56109, collecte_portail56109, pappers_documents56109 (5.6.109, lot G4) : commandes d'offre et PDF joint (source, prix, version, empreinte
    # du PDF, reçu du brouillon), profils de portail déclaratifs (aucun secret, aucun cookie, aucun certificat), missions de collecte et leurs
    # messages, pièces, dates proposées, brouillons préparés et correspondances confirmées, règles Pappers du dossier, autorisations bornées et
    # appels en crédits (sans clé ni jeton documentaire)
    _e('dossiers', ('offres_et_pdf_joints', 'offres56109', 'dossier'), ('messages_de_portail_collectes', 'collecte_messages56109', 'dossier'),
       ('pieces_de_portail_classees', 'collecte_pieces56109', 'dossier'), ('dates_proposees_par_la_collecte', 'collecte_dates56109', 'dossier'),
       ('brouillons_d_information_prepares', 'collecte_brouillons56109', 'dossier'), ('correspondances_portail_dossier', 'collecte_correspondances56109', 'dossier'),
       ('regles_pappers_du_dossier', 'pappers_dossiers56109', 'dossier'), ('autorisations_de_collecte_pappers', 'pappers_autorisations56109', 'dossier')) +
    _e('routines', ('profils_de_portail', 'collecte_profils56109', None), ('missions_de_collecte_de_portail', 'collecte_missions56109', None)) +
    _e('gouvernance', ('appels_pappers_en_credits', 'pappers_appels56109', 'dossier'))
)

# ------------------------------------------------------------------------------------------------ résolution du dossier quand il ne se lit pas dans une colonne
# ('colonne', col, strict) : la colonne porte le dossier ; vide = sans dossier (strict : non déterminable).
# ('courriel', col_dossier, col_courriel) : dossier de la ligne, sinon dossier du courriel classé (``proposals``) ; courriel non classé = non déterminable.
# ('chemin', col) : dossier dont le répertoire contient le chemin ; aucun = non déterminable.
# ('portee', col_genre, col_valeur, genres, col_dossier) : portée d'une règle (dossier) ; autre portée = règle du cabinet.
# ('json', col) : liste JSON de dossiers ; illisible = non déterminable.
# ('enfants', col, table, cle, col_dossier) : dossiers des lignes qui rattachent celle-ci (archive de courriel → rattachements) ; aucun = non déterminable.
# ('parent', col, table, cle, strict) : dossiers de la ligne parente (résolue par sa propre règle) ; strict : parent sans dossier = non déterminable.
# ('conversation', col) : empreinte de la discussion (``workspace.chat_scope``) recalculée pour chaque dossier ; autre = non déterminable.
# ('inconnu',) : aucun rattachement fiable (contenu tiré de courriels ou de documents sans dossier) : non déterminable.
RESOLVEURS = {
    'assistant_threads': ('courriel', 'matter', 'mail_key'),
    'assistant_messages': ('parent', 'thread_id', 'assistant_threads', 'id', False),
    'assistant_thread_owners109': ('parent', 'thread_id', 'assistant_threads', 'id', False),     # 5.6.109 (lot H) : même dossier que la discussion
    'assistant_thread_members109': ('parent', 'thread_id', 'assistant_threads', 'id', False),
    'assistant_thread_links109': ('parent', 'thread_id', 'assistant_threads', 'id', False),
    'assistant_thread_contexts109': ('parent', 'thread_id', 'assistant_threads', 'id', False),
    'conversations': ('conversation', 'scope'),
    'assistant_attachments_v360': ('conversation', 'scope'),
    'assistant_attachment_parts_v363': ('parent', 'attachment_id', 'assistant_attachments_v360', 'id', True),
    'cockpit530_messages': ('colonne', 'matter', True),
    'work_items': ('courriel', 'matter', 'mail_key'),
    'manual_drafts': ('courriel', '', 'mail_key'),
    'attachment_reviews': ('courriel', '', 'mail_key'),
    'feedback': ('courriel', 'matter', 'mail_key'),
    'proposals': ('colonne', 'matter', False),
    'deadline_proposals': ('courriel', 'matter', 'mail_key'),
    'mail_orchestrations_v260': ('courriel', 'matter', 'mail_key'),
    'initiatives5636': ('courriel', 'matter', 'mail_key'),
    'mail_archives': ('enfants', 'id', 'mail_archive_links', 'archive_id', 'matter_id'),
    'outlook56103_brouillons': ('inconnu',),
    'fichiers56103_notes': ('chemin', 'chemin'),
    'corrections5667': ('inconnu',),
    'decision_cancellations5634': ('inconnu',),
    'learning_outcomes_v392': ('inconnu',),
    'strategic_history': ('inconnu',),
    'reception_v567': ('inconnu',),
    'style550_habits': ('inconnu',),
    'd51_eval_cases': ('inconnu',),
    'routines5652': ('json', 'matters_json'),
    'routines5652_versions': ('parent', 'routine_id', 'routines5652', 'id', False),
    'routines5652_occ': ('parent', 'routine_id', 'routines5652', 'id', True),
    'business_rules_v410': ('portee', 'scope', 'scope_value', ('matter',), ''),
    'trusted_documents_v410': ('portee', 'scope', 'scope_value', ('matter',), ''),
    'rules480': ('portee', 'scope', 'scope_value', ('dossier',), ''),
    'd51_learned': ('portee', 'scope', 'scope_value', ('dossier',), 'matter'),
}

# ------------------------------------------------------------------------------------------------ tables exclues, avec leur motif
EXPORT_EXCLUES = (
    _x(_S, 'users', 'sessions', 'login_attempts', 'reset_tokens', 'account_log', 'attempts', 'log', 'resets', 'membres5650',
       'appareils5674', 'appareils5674_invitations', 'push_subs490', 'chatgpt_tentatives', 'ia_liaisons', 'ia_liaisons_debit', 'ia_liaisons_poste',
       'claude5675_acces', 'claude5675_appels', 'claude5675_clients', 'claude5675_codes', 'claude5675_demandes', 'claude5675_jetons', 'claude5675_masques',
       'google5697_connexions', 'google5697_etats', 'gmail_simple56103_essais', 'avocatfr56106_essais', 'calendar_targets568', 'talk_rooms_v568',
       'connection_receipts5631', 'guide_sessions5631', 'ecosystem_services_v380', 'integration_keys5636', 'integration_nonces5634',
       'integration_sessions5637', 'send_pending490', 'pseudo540_log') +
    _x(_T, 'access500', 'accord56102_lus', 'agenda_rappels5634', 'analyses5624', 'assistant56103_etapes', 'autonomy_history480', 'b55_matter_seen',
       'batches5614', 'business_rule_uses_v410', 'business_test_runs_v290', 'cabinet_runs_v290', 'calendar_deposits568', 'charge5693_arbre',
       'charge5693_dossiers', 'charge5693_mesures', 'cloture_suites56100', 'cockpit5624_dismissed', 'conflicts500_matters', 'crm57_cursors',
       'crm57_retries', 'crm57_runs', 'd51_changes', 'd51_mission_requests', 'd51_preparations', 'd51_rebuilds', 'd51_retries', 'd59_seen',
       'deadline_reminders450', 'deck530_cards', 'deck530_tasks', 'deferred_jobs569', 'directories', 'document_content568', 'document_effects568',
       'document_identities5693', 'dp_effects5632', 'ecosystem_events_v380', 'ef5670_rapports', 'fees500_alerts', 'fiche_requests5634',
       'file56101_items_migration', 'gmail5697_synchro', 'initiative_children5639', 'integration_approvals5634', 'integration_decisions5635',
       'integration_jobs5634', 'integration_manifests5634', 'integration_pair_failures5636', 'invoice_ninja_ops5614', 'invoice_ninja_writes_v569',
       'invoice_ninja_entries5614', 'learning_uses_v392', 'lecteur56106_liens', 'limitation500_reminders', 'mail_archive_operations',
       'mail_archive_sources', 'mailrule_log5632', 'missions_ordres5697', 'ms_links5636', 'office_analyses5684', 'office_blocks5684',
       'office_commands5684', 'office_events5684', 'office_executors5684', 'office_generations5684', 'office_mirrors5684', 'office_observations5684',
       'office_rejections5684', 'office_saves440', 'office_saves5684', 'office_sessions440', 'office_sessions5684', 'opp5676_lus', 'ops5614',
       'orchestration_echecs_5682', 'orchestration_notifications_v260', 'outlook56103_journal', 'p57_trail', 'production_flows_v391',
       'production_rule_influence_v420', 'production_triggers_v390', 'relances_exclues5636', 'relances_runs5636', 'reply_drafts5687',
       'signal_versions5634', 'tool_bindings5683', 'trace_events480', 'traces5694_spans', 'veille_runs5632', 'boite56106_operations',
       'messages', 'action_reviews_v380') +
    _x(_T, 'pseudo56109_revision') +   # 5.6.109 (lot P) : compteur de révision du registre des identités (aucun nom), recréé au démarrage
    _x(_T, 'pieces56109_lots') +   # 5.6.109 (lot L) : intentions de classement des pièces jointes (clé d'idempotence, états de reprise) ; les pièces classées sont des fichiers du dossier
    _x(_T, 'analyses5624_campagnes', 'reanalyse56109_ops') +   # 5.6.109 (lot A) : campagnes d'analyse d'un dossier (état d'exécution, reprise), reçus d'idempotence du bloc « Analyse du dossier »
    _x(_C, 'bland56109_outils') +   # 5.6.109 (lot B) : liste des outils Bland découverte au test de la connexion (révisions), relue chez Bland à chaque test
    _x(_T, 'bland56109_ops') +      # 5.6.109 (lot B) : reçus d'idempotence des actions de téléphonie (aucun contenu, aucune clé)
    _x(_C, 'analyses5624_couverture') +   # 5.6.109 (lot A) : couverture de l'analyse (état de chaque pièce et courriel), recalculée depuis l'index à chaque analyse
    _x(_T, 'sources_publiques56109_sondes', 'sources_publiques56109_ops', 'international56109_ops') +   # 5.6.109 (lot S) : bail et délai des sondes des sources publiques, reçus d'idempotence ; les documents publics étrangers classés sont exportés avec ``registre_docs5688``, les identités de registre avec ``contacts5636``
    _x(_C, 'assistant_attachment_analysis_v363', 'assistant_attachment_meta_v363', 'association_groups', 'autonomy_mail_observations_v230',
       'crm57_seen', 'd51_party_candidates', 'daily_dashboards', 'deadline_obs450', 'docs5667', 'dossier56100_resultats', 'events_v568',
       'gmail5697_messages', 'grilles56108', 'hearing_writing_analysis_v363', 'invoice_ninja_map5614', 'invoice_ninja_sync5614',
       'latest_writings_v240', 'lecteur56106_resumes', 'long_document_chunks_v365', 'long_document_runs_v365', 'long_document_signatures5613',
       'matter_graph_edges_v380', 'matter_graph_nodes_v380', 'matter_operational_memory_v230', 'matter_watch', 'morning_briefings_v340',
       'news_v568', 'notices440', 'outcomes480', 'paragraph_provenance_v240', 'piece_digests5632', 'piece_digests5634', 'piece_fragments5634',
       'piece_state5634', 'portfolio_calendar_links', 'portfolio_document_activity', 'portfolio_mail_links', 'proactive_cycles_v340',
       'proactive_notices_v340', 'proactive_signals', 'proactive_sources_v340', 'production_metric_snapshots_v420', 'readings5614',
       'report500_snapshots', 'routines520', 'snapshots5614', 'style550_docs', 'vision56106_lectures', 'pilote56102_resumes', 'oral5632',
       'docs', 'search', 'knowledge_chunks', 'knowledge_embeddings', 'knowledge_fts', 'inventory_scans', 'inventory_seen', 'vector_deleted5680',
       'vector_sync5680', 's490', 's490_fts', 'mail_cursor490', 'timing490', 'articles', 'normes', 'meta') +
    _x(_IA, 'ai_transmissions_v391', 'ai_usage_v391', 'conso_5682', 'decisions_5682', 'reservations_5682', 'routage5695_agregats',
       'routage5695_choix', 'routage5695_meta', 'hybrid_decisions_v400', 'quality_reviews_v370', 'references56108', 'secours564_log',
       'skill_envois56108', 'skill_runs5683', 'skill_usage5632', 'trace_calls480', 'vision56106_transmissions',
       'ia_locale56108_operations', 'ia_locale56108_configurations') +   # ia_locale56108 (5.6.108, lot L) : téléchargements, essais, instantanés des réglages de l'IA locale
    _x(_TMP, 'bibliotheque5639_fichiers', 'bibliotheque5639_trames', 'bibliotheque5642_liens', 'cabinet_confirmation_batches_v290',
       'hybrid_previews_v400', 'mail_archive_proposals', 'work_plan_proposals',
       'editeur56108_seances', 'editeur56108_ops',   # wordcraft56108 (5.6.108, lot W) : séances d'édition ; les versions gardées sont des fichiers du dossier
       'pdf_formulaire56108_ops') +   # pdf_formulaire56108 (5.6.108, lot F) : reçus d'idempotence ; les copies produites sont des fichiers du dossier
    _x(_PERSO, 'boite56106_epingles', 'cockpit530_reviewed', 'd51_reads', 'lecteur56106_preferences', 'missions_hidden5636') +
    _x(_T, 'catalogue_recus56109') +         # 5.6.109 (lot D) : reçus d'idempotence du catalogue de prestations
    _x(_T, 'partages_client_ops56109', 'appels_ops56109', 'remplacements_ops56109', 'kits_audience_ops56109') +   # 5.6.109 (lot G2) : reçus d'idempotence (aucun contenu, aucun jeton)
    _x(_T, 'operations_recus56109') +        # 5.6.109 (lot G3) : reçus d'idempotence des routes « À finaliser », suites et formalités (aucun contenu métier, aucun secret)
    _x(_T, 'pappers_ops56109') +             # 5.6.109 (lot G4) : reçus d'idempotence des collectes Pappers (les documents classés sont exportés avec ``registre_docs5688``)
    _x(_T, 'gestion_recus56109') +           # 5.6.109 (lot G1) : reçus d'idempotence de la préfacturation, de l'assistance régulière et des mouvements (aucun contenu métier)
    _x(_T, 'livrables_operations56109', 'craft56109_installations', 'craft56109_exports', 'craft56109_plans') +   # 5.6.109 (lot V) : reçus d'idempotence des livrables structurés, installations Craft vérifiées (empreintes), intentions d'export et plans de commandes IA des séances (les fichiers produits sont des fichiers du dossier)
    _x(_T, 'decision_recus56109') +          # 5.6.109 (lot K) : reçus d'idempotence du réglage de l'organisation locale et des réponses aux questions ciblées
    _x(_T, 'simulation_ops56109') +          # 5.6.109 (lot N) : reçus d'idempotence de l'onglet « Simulation de décision » (aucun contenu)
    _x('Texte brut du modèle avant masquage des pourcentages inventés (sans valeur probante, jamais restitué) : la simulation exportée porte la '
       'version masquée.', 'simulation_bruts56109') +   # 5.6.109 (intégration I1) : brut gardé hors export, résultat masqué dans ``simulation_runs56109``
    _x('Texte brut du modèle avant masquage des pourcentages inventés (sans valeur probante) : le projet d’avis exporté porte la version masquée.',
       'legal_opinion_bruts56109') +   # 5.6.109 (intégration I1) : brut du projet d'avis hors export, ``data`` masqué dans ``legal_opinion_projects_v260``
    _x(_PERSO, 'assistant_thread_preferences109', 'assistant_groups109', 'assistant_drafts109') +   # 5.6.109 (lot H) : épingles, archives, groupes et brouillons propres à un compte
    _x(_T, 'assistant_ops109') +             # 5.6.109 (lot H) : reçus d'idempotence des actions sur les discussions et l'apparence
    _x(_PERSO, 'fiche_preferences56109') +   # 5.6.109 (lot D) : ordre et cartes masquées de la fiche, propres à un compte (le modèle du cabinet, lui, est exporté)
    _x(_C, 'fiche_mentions56109') +          # 5.6.109 (lot D) : mentions relues dans les actes et contrats exportés (les décisions prises dessus sont exportées)
    _x(_TMP, 'poste56109_autorisations') +   # 5.6.109 (lot O, F12) : autorisations temporaires de mission sur un poste : jamais réactivées sur un autre appareil (les étapes sont au journal de la mission)
    _x(_T, 'poste56109_recus', 'poste56109_ordres', 'poste56109_baux') +   # 5.6.109 (lot O, F12, G13) : reçus d'idempotence des étapes, ordres signés et baux du compagnon (aucun contenu, aucun secret)
    _x(_S, 'poste56109_postes') +            # 5.6.109 (lot O, G13) : postes associés (clés publiques) : un nouvel appairage est demandé après restauration
    _x(_T, 'fonds56109_ops', 'local56109_ops', 'local56109_inventaires', 'local56109_vus') +   # 5.6.109 (lot Q) : reçus d'idempotence, curseurs d'inventaire des emplacements locaux
    _x(_C, 'local56109_documents') +         # 5.6.109 (lot Q, F13) : versions lues des fichiers d'un emplacement local (recalculées depuis les fichiers du poste)
    _x('Autorisation d’accès propre à un poste (chemin local privé du poste) : jamais réactivée sur un autre appareil ; l’emplacement est accordé '
       'à nouveau sur le poste après restauration.', 'local56109_emplacements') +   # 5.6.109 (lot Q, F13)
    _x(_SECU, 'acces5670_allow', 'acces5670_bans', 'acces5670_events', 'fraude56106_avis', 'fraude56106_exceptions', 'fraude56106_ibans',
       'fraude56106_retours', 'fraude56106_verdicts', 'secu56101_accuses', 'secu56101_constats', 'secu56101_fiches', 'secu56101_rapports') +
    _x(_AUTRE, 'example_reviews', 'examples_v2', 'excluded_examples', 'forgotten', 'memory_meta', 'pairs5667', 'preferences', 'seen') +
    _x(_ENVOI, 'send_log490') +
    _x(_SYNC, 'sync56108_adhesions', 'sync56108_capacites', 'sync56108_capture', 'sync56108_echanges', 'sync56108_espaces', 'sync56108_garde',
       'sync56108_membres', 'sync56108_objets', 'sync56108_operations', 'sync56108_pairs', 'sync56108_partages', 'sync56108_personnes',
       'sync56108_tetes', 'sync56108_documents', 'sync56108_missions', 'sync56108_pauses', 'sync56108_recus',
       'sync56108_fichiers_vus', 'sync56108_fichiers_signal', 'sync56108_fichiers_releve')
)


# ------------------------------------------------------------------------------------------------ résolution
def _txt(v):
    return '' if v is None else str(v)


class Contexte:
    """Ce que les règles de résolution doivent connaître : configuration (registre des dossiers, empreintes de discussion) et base lue."""

    def __init__(self, config, db):
        self.config, self.db, self._dossiers, self._courriels, self._parents = config, db, None, None, {}

    def dossiers(self):
        if self._dossiers is None:
            from .common import load_matters
            try:
                self._dossiers = list(load_matters(self.config, unfiltered=True)) if self.config else []
            except Exception:   # noqa: BLE001 — registre illisible : rien n'est déterminable par chemin ni par empreinte
                self._dossiers = []
        return self._dossiers

    def courriels(self):
        if self._courriels is None:
            self._courriels = {}
            if _colonnes(self.db, 'proposals'):
                for cle, matter in self.db.execute('SELECT key, matter FROM proposals'):
                    self._courriels[_txt(cle)] = _txt(matter)
        return self._courriels


def _colonnes(db, table):
    return [r[1] for r in db.execute('PRAGMA table_info("%s")' % table.replace('"', '""'))]


def _chemin(p):
    p = '/' + _txt(p).replace('\\', '/').strip('/')
    return p.lower()


def _empreintes_conversation(ctx):
    from .common import digest
    c = ctx.config or {}
    mail, nc = c.get('mail') or {}, c.get('nextcloud') or {}
    out = {}
    for m in ctx.dossiers():
        cle = digest(json.dumps([mail.get('username'), mail.get('host'), nc.get('url'), nc.get('username'), m['id'], m.get('path') or '', ''],
                                ensure_ascii=False))
        out[cle] = m['id']
    return out


def regle(table, specs=None):
    """Règle de résolution d'une table : ``RESOLVEURS`` des modules déclarants (celle de ce module par défaut)."""
    return (specs if specs is not None else RESOLVEURS).get(table)


def resolveur(ctx, spec, specs=None, profondeur=0):
    """Fonction ligne (dict) → frozenset des dossiers, ou ``None`` (non déterminable)."""
    specs = RESOLVEURS if specs is None else specs
    genre = spec[0]
    if genre == 'colonne':
        _g, col, strict = spec
        return lambda r: frozenset((_txt(r.get(col)),)) if _txt(r.get(col)) else (None if strict else frozenset())
    if genre == 'courriel':
        _g, col, mail = spec
        courriels = ctx.courriels()

        def par_courriel(r):
            if col and _txt(r.get(col)):
                return frozenset((_txt(r.get(col)),))
            cle = _txt(r.get(mail))
            if not cle:
                return None
            m = courriels.get(cle)
            if m is None:
                return None              # courriel jamais classé : on ne sait pas
            return frozenset((m,)) if m else None
        return par_courriel
    if genre == 'chemin':
        racines = sorted(((_chemin(m.get('path')), m['id']) for m in ctx.dossiers() if m.get('path')), key=lambda x: -len(x[0]))

        def par_chemin(r):
            p = _chemin(r.get(spec[1]))
            for racine, ident in racines:
                if racine != '/' and (p == racine or p.startswith(racine + '/')):
                    return frozenset((ident,))
            return None
        return par_chemin
    if genre == 'portee':
        _g, col, valeur, genres, extra = spec

        def par_portee(r):
            out = set()
            if _txt(r.get(col)) in genres:
                if not _txt(r.get(valeur)):
                    return None
                out.add(_txt(r.get(valeur)))
            if extra and _txt(r.get(extra)):
                out.add(_txt(r.get(extra)))
            return frozenset(out)
        return par_portee
    if genre == 'json':
        def par_liste(r):
            try:
                valeurs = json.loads(_txt(r.get(spec[1])) or '[]')
            except ValueError:
                return None
            if not isinstance(valeurs, list):
                return None
            return frozenset(_txt(v) for v in valeurs if _txt(v))
        return par_liste
    if genre == 'enfants':
        _g, col, enfant, cle, colonne = spec
        carte = {}
        if _colonnes(ctx.db, enfant):
            for k, m in ctx.db.execute('SELECT "%s", "%s" FROM "%s"' % (cle, colonne, enfant)):
                carte.setdefault(_txt(k), set()).add(_txt(m))

        def par_enfants(r):
            ms = carte.get(_txt(r.get(col)))
            if not ms or '' in ms:
                return None
            return frozenset(ms)
        return par_enfants
    if genre == 'parent':
        _g, col, parent, cle, strict = spec
        if profondeur > 4 or not _colonnes(ctx.db, parent):
            return lambda r: None
        cache_cle = (parent, cle)
        if cache_cle not in ctx._parents:
            sous = regle(parent, specs)
            colonnes = _colonnes(ctx.db, parent)
            f = resolveur(ctx, sous, specs, profondeur + 1) if sous else (
                lambda r: frozenset((_txt(r.get('matter')),)) if _txt(r.get('matter')) else frozenset())
            ctx._parents[cache_cle] = {_txt(dict(zip(colonnes, ligne)).get(cle)): f(dict(zip(colonnes, ligne)))
                                       for ligne in ctx.db.execute('SELECT * FROM "%s"' % parent)}
        carte = ctx._parents[cache_cle]

        def par_parent(r):
            k = _txt(r.get(col))
            if not k:
                return None if strict else frozenset()
            if k not in carte:
                return None
            ms = carte[k]
            if ms is None or (strict and not ms):
                return None
            return ms
        return par_parent
    if genre == 'conversation':
        empreintes = _empreintes_conversation(ctx)

        def par_empreinte(r):
            m = empreintes.get(_txt(r.get(spec[1])))
            return frozenset((m,)) if m else None
        return par_empreinte
    return lambda r: None   # 'inconnu' ou règle inconnue : jamais un accès élargi


def garder(dossiers, reserves):
    """Une ligne sort si aucun dossier réservé n'est exclu, ou si son dossier est connu et n'est pas réservé."""
    if not reserves:
        return True
    return dossiers is not None and not (dossiers & reserves)


# ------------------------------------------------------------------------------------------------ dictionnaire des données
COLONNES_CONNUES = {
    'id': 'Identifiant de la ligne (stable : sert aux liens entre fichiers).', 'matter': 'Identifiant du dossier (voir dossiers/registre).',
    'matter_id': 'Identifiant du dossier (voir dossiers/registre).', 'dossier': 'Dossier : identifiant du dossier ou de l’opération parente.',
    'dossier_id': 'Identifiant du dossier (voir dossiers/registre).', 'created': 'Date de création (ISO 8601, UTC sauf mention).',
    'created_at': 'Date de création (ISO 8601).', 'updated': 'Date de dernière modification (ISO 8601).', 'updated_at': 'Date de dernière modification (ISO 8601).',
    'cree': 'Date de création (ISO 8601).', 'cree_le': 'Date de création (ISO 8601).', 'maj': 'Date de dernière modification (ISO 8601).',
    'modifie': 'Date de dernière modification (ISO 8601).', 'at': 'Horodatage de l’événement (ISO 8601).', 'quand': 'Horodatage (ISO 8601).',
    'version': 'Numéro de version (s’incrémente à chaque modification).', 'status': 'État de l’objet.', 'state': 'État de l’objet.', 'etat': 'État de l’objet.',
    'title': 'Titre.', 'titre': 'Titre.', 'label': 'Libellé.', 'owner': 'Personne propriétaire (identifiant de compte).', 'auteur': 'Auteur (identifiant de compte).',
    'author': 'Auteur (identifiant de compte).', 'mail_key': 'Clé interne du courriel (voir courriels/associations_courriel_dossier).',
    'path': 'Chemin du fichier dans le stockage du cabinet.', 'chemin': 'Chemin du fichier dans le stockage du cabinet.',
    'sha256': 'Empreinte SHA-256 (hexadécimal) du contenu désigné.', 'data': 'Données structurées (JSON).', 'payload': 'Données structurées (JSON).',
    'thread': 'Identifiant du fil de messagerie interne (messagerie_interne/fils).', 'mission': 'Identifiant de la mission parente.',
    'project_id': 'Identifiant du projet parent.', 'document': 'Identifiant du document de facturation parent (factures/documents_de_facturation).',
    'company': 'Identifiant de la société (contacts/societes).', 'job_id': 'Numéro du travail du logiciel qui a produit la ligne (référence technique).',
}


def decrire_colonne(nom):
    nom = str(nom)
    if nom in COLONNES_CONNUES:
        return COLONNES_CONNUES[nom]
    if nom.endswith('_json'):
        return 'Données structurées (JSON).'
    if nom.endswith(('_at', '_le')) or nom in ('debut', 'fin', 'starts', 'ends', 'due', 'day', 'jour'):
        return 'Date ou horodatage (ISO 8601).'
    if nom.endswith(('_id', '_key')):
        return 'Identifiant d’un objet lié.'
    return 'Valeur « %s » telle qu’enregistrée dans le logiciel.' % nom


# ------------------------------------------------------------------------------------------------ formats ouverts : ICS, vCard, EML
def _ics_esc(texte):
    return str(texte or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r\n', '\n').replace('\n', '\\n')


def _ics_fold(ligne):
    from .agenda5634 import _fold
    return _fold(ligne)


def _ics_date(valeur, tz):
    """Horodatage ISO → (propriété, valeur) iCalendar ; date seule → VALUE=DATE."""
    from datetime import datetime, timezone
    v = _txt(valeur).strip()
    if not v:
        return None
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
        return ';VALUE=DATE', v.replace('-', '')
    try:
        d = datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return '', d.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def evenements_ics(lignes, tz, stamp):
    """VEVENT, même forme que ``agenda5634.export_ics`` (UID par occurrence, série d'origine dans X-AXIORHUB-SERIES), mais lu sur l'instantané de
    l'export : l'agenda et les tableaux décrivent le même instant. ``lignes`` : dicts déjà filtrés (dossiers réservés)."""
    from datetime import datetime, timezone
    from .agenda5634 import KINDS, _export_uid
    out = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//AxiorHub Pilote//Export de depart//FR', 'CALSCALE:GREGORIAN']
    for r in lignes:
        try:
            a, b = datetime.fromisoformat(_txt(r.get('starts'))), datetime.fromisoformat(_txt(r.get('ends')) or _txt(r.get('starts')))
        except ValueError:
            continue
        if a.tzinfo is None:
            a = a.replace(tzinfo=tz)
        if b.tzinfo is None:
            b = b.replace(tzinfo=tz)
        r = {**r, 'uid': _txt(r.get('uid')) or 'axiorhub-%s@export.invalid' % _txt(r.get('id')), 'recurrence_id': _txt(r.get('recurrence_id'))}
        out += ['BEGIN:VEVENT', 'UID:' + _export_uid(r), 'DTSTAMP:' + stamp]
        if r.get('all_day'):
            out += ['DTSTART;VALUE=DATE:' + a.astimezone(tz).strftime('%Y%m%d'), 'DTEND;VALUE=DATE:' + b.astimezone(tz).strftime('%Y%m%d')]
        else:
            out += ['DTSTART:' + a.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'DTEND:' + b.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')]
        out += ['SUMMARY:' + _ics_esc(r.get('title')), 'CATEGORIES:' + _ics_esc(KINDS.get(r.get('kind'), r.get('kind') or ''))]
        if r.get('location'):
            out.append('LOCATION:' + _ics_esc(r['location']))
        desc = _txt(r.get('description')) + (('\nDossier : ' + _txt(r['matter'])) if r.get('matter') else '')
        if desc.strip():
            out.append('DESCRIPTION:' + _ics_esc(desc.strip()))
        if _txt(r.get('status')) not in ('', 'actif'):
            out.append('STATUS:CANCELLED')
        out += ['X-AXIORHUB-KIND:' + _ics_esc(r.get('kind')), 'X-AXIORHUB-ID:' + _ics_esc(r.get('id'))]
        if r['recurrence_id']:
            out += ['X-AXIORHUB-SERIES:' + _ics_esc(r['uid']), 'X-AXIORHUB-RECURRENCE-ID:' + _ics_esc(r['recurrence_id'])]
        if r.get('matter'):
            out.append('X-AXIORHUB-MATTER:' + _ics_esc(r['matter']))
        try:
            rappels = json.loads(_txt(r.get('reminders')) or '[]')
        except ValueError:
            rappels = []
        for minutes in rappels if isinstance(rappels, list) else []:
            if isinstance(minutes, int) and 0 <= minutes < 100000:
                out += ['BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + _ics_esc(r.get('title')), 'TRIGGER:-PT%dM' % minutes, 'END:VALARM']
        out.append('END:VEVENT')
    out.append('END:VCALENDAR')
    return ('\r\n'.join(_ics_fold(x) for x in out) + '\r\n').encode('utf-8')


def taches_ics(lignes, tz, stamp):
    """VTODO : UID stable (identifiant de la tâche), échéance, état, dossier. ``lignes`` : dicts déjà filtrés (dossiers réservés)."""
    out = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//AxiorHub Pilote//Export de depart//FR', 'CALSCALE:GREGORIAN']
    etats = {'done': 'COMPLETED', 'termine': 'COMPLETED', 'terminee': 'COMPLETED', 'fait': 'COMPLETED', 'cancelled': 'CANCELLED', 'annulee': 'CANCELLED',
             'in_progress': 'IN-PROCESS', 'en_cours': 'IN-PROCESS'}
    for r in lignes:
        out += ['BEGIN:VTODO', 'UID:axiorhub-tache-%s@export.invalid' % re.sub(r'[^0-9A-Za-z_.-]', '_', _txt(r.get('source')) + '-' + _txt(r.get('id'))),
                'DTSTAMP:' + stamp, 'SUMMARY:' + _ics_esc(r.get('title') or 'Tâche')]
        for prop, cle in (('DTSTART', 'starts'), ('DUE', 'due')):
            d = _ics_date(r.get(cle), tz)
            if d:
                out.append(prop + d[0] + ':' + d[1])
        desc = _txt(r.get('description')) + (('\nDossier : ' + _txt(r['matter'])) if r.get('matter') else '')
        if desc.strip():
            out.append('DESCRIPTION:' + _ics_esc(desc.strip()))
        out.append('STATUS:' + etats.get(_txt(r.get('status')).lower(), 'NEEDS-ACTION'))
        if r.get('matter'):
            out.append('X-AXIORHUB-MATTER:' + _ics_esc(r['matter']))
        out.append('END:VTODO')
    out.append('END:VCALENDAR')
    return ('\r\n'.join(_ics_fold(x) for x in out) + '\r\n').encode('utf-8')


def _vc_esc(texte):
    return str(texte or '').replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\r\n', '\n').replace('\n', '\\n')


def vcards(lignes):
    """vCard 4.0 (RFC 6350), une fiche par contact ; l'identifiant interne sert d'UID."""
    out = []
    for r in lignes:
        nom, prenom = _txt(r.get('name')), _txt(r.get('first_name'))
        complet = (prenom + ' ' + nom).strip() or _txt(r.get('company')) or 'Contact'
        carte = ['BEGIN:VCARD', 'VERSION:4.0', 'UID:urn:axiorhub:contact:' + re.sub(r'[^0-9A-Za-z_.-]', '_', _txt(r.get('id'))),
                 'FN:' + _vc_esc(complet), 'N:%s;%s;;;' % (_vc_esc(nom), _vc_esc(prenom))]
        if r.get('kind') in ('societe', 'organisation', 'personne_morale'):
            carte.append('KIND:org')
        if r.get('company'):
            carte.append('ORG:' + _vc_esc(r['company']))
        if r.get('function'):
            carte.append('TITLE:' + _vc_esc(r['function']))
        for adresse in re.split(r'[,;\s]+', _txt(r.get('email'))):
            if '@' in adresse:
                carte.append('EMAIL:' + _vc_esc(adresse))
        if r.get('phone'):
            carte.append('TEL;VALUE=text:' + _vc_esc(r['phone']))
        if r.get('address'):
            carte.append('ADR:;;%s;;;;' % _vc_esc(r['address']))
        if r.get('category'):
            carte.append('CATEGORIES:' + _vc_esc(r['category']))
        note = _txt(r.get('notes'))
        if r.get('siren'):
            note = (note + '\n' if note else '') + 'SIREN : ' + _txt(r['siren'])
        if note:
            carte.append('NOTE:' + _vc_esc(note))
        carte.append('END:VCARD')
        out += carte
    return ('\r\n'.join(_ics_fold(x) for x in out) + ('\r\n' if out else '')).encode('utf-8')


def eml_a_exporter(db, reserves):
    """Originaux EML des courriels archivés (``mail_archive_assets`` de genre ``original``) dont le dossier n'est pas réservé."""
    if not _colonnes(db, 'mail_archive_assets') or not _colonnes(db, 'mail_archive_links'):
        return []
    out, vus = [], set()
    for row in db.execute("SELECT a.path, a.sha256, a.size_bytes, l.matter_id, l.archive_id, l.title FROM mail_archive_assets a "
                          "JOIN mail_archive_links l ON l.id=a.link_id WHERE a.kind='original' AND a.state IN ('verified','uncertain') "
                          "ORDER BY l.matter_id, a.path"):   # original déposé et relu (« incertain » : l'empreinte est recontrôlée à la copie)
        path, sha, taille, matter, archive, titre = (_txt(x) for x in row)
        if not matter or matter in reserves or (matter, archive) in vus:
            continue
        vus.add((matter, archive))
        out.append({'dossier': matter, 'chemin': path, 'sha256_attendu': sha, 'taille': int(taille or 0), 'archive': archive, 'titre': titre})
    return out


# ------------------------------------------------------------------------------------------------ sommaire HTML hors ligne
def sommaire_html(meta, fichiers, libelles):
    """Page autonome (aucun script, aucune ressource externe, liens relatifs) ; aucune donnée de courriel n'y est injectée."""
    e = html.escape
    bilan = meta.get('perimetre') or {}
    domaines = {}
    for f in fichiers:
        domaines.setdefault(f.get('domaine') or '', []).append(f)
    etat = ('Export complet : chaque table de données est exportée ou exclue avec son motif.' if bilan.get('complet') else
            'Export INCOMPLET : %d table(s) non couverte(s). Voir MANIFEST.json.' % len(bilan.get('non_couvertes') or []))
    lignes = ['<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>Export AxiorHub</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:60rem;margin:1rem auto;padding:0 1rem;color:#1b1b1b;'
              'background:#fff}table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:.25rem .5rem;text-align:left;vertical-align:top}'
              'a{color:#0b4f9c}a:focus{outline:3px solid #0b4f9c}@media (prefers-color-scheme:dark){body{background:#161616;color:#f2f2f2}a{color:#8ab4f8}'
              'th,td{border-color:#555}}</style></head><body>',
              '<h1>Export AxiorHub Pilote</h1>',
              '<p>Créé le %s avec la version %s. Profil : %s.</p>' % (e(_txt(meta.get('cree'))), e(_txt(meta.get('version'))),
                                                                       e('départ vers un autre logiciel' if meta.get('profil') == 'depart' else 'export intégral')),
              '<p><strong>%s</strong></p>' % e(etat),
              '<p>Les fichiers <code>.json</code> gardent les valeurs exactes ; les <code>.csv</code> (UTF-8, point-virgule) neutralisent les formules. '
              'Le dictionnaire des données décrit chaque fichier et chaque colonne : <a href="dictionnaire/dictionnaire.html">dictionnaire</a>. '
              'Les exclusions et les éléments non disponibles sont dans <a href="rapports/exclusions.json">rapports/exclusions.json</a>.</p>',
              '<p>Dossiers réservés : %s.</p>' % e('inclus à la demande de l’administrateur' if (meta.get('dossiers_reserves') or {}).get('traitement') == 'inclus'
                                                    else 'exclus, avec tout ce qui s’y rattache')]
    lignes.append('<h2>Contenu par domaine</h2><table><thead><tr><th scope="col">Domaine</th><th scope="col">Description</th><th scope="col">Lignes attendues / exportées / exclues</th>'
                  '<th scope="col">Fichiers</th></tr></thead><tbody>')
    for domaine, rows in sorted(domaines.items()):
        if not domaine:
            continue
        d = (bilan.get('domaines') or {}).get(domaine)
        compte = '%d / %d / %d' % (d['attendu'], d['exporte'], d['exclu']) if d else '—'
        liens = ' '.join('<a href="%s">%s</a>' % (e(f['chemin'], quote=True), e(PurePosixPath(f['chemin']).name)) for f in rows[:60])
        if len(rows) > 60:
            liens += ' … (%d fichiers en tout)' % len(rows)
        lignes.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(domaine), e(libelles.get(domaine, '')), e(compte), liens))
    lignes.append('</tbody></table><p>Ce fichier n’est pas chiffré : il contient des données couvertes par le secret professionnel.</p></body></html>')
    return '\n'.join(lignes).encode('utf-8')


def dictionnaire_html(entrees):
    e = html.escape
    out = ['<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Dictionnaire des données</title>'
           '<style>body{font:15px/1.45 system-ui,sans-serif;margin:1rem;color:#1b1b1b;background:#fff}td,th{border:1px solid #bbb;padding:.2rem .4rem;'
           'text-align:left;vertical-align:top}table{border-collapse:collapse;margin-bottom:1rem}@media (prefers-color-scheme:dark){body{background:#161616;'
           'color:#f2f2f2}td,th{border-color:#555}}</style></head><body><h1>Dictionnaire des données</h1><p><a href="../index.html">Retour au sommaire</a></p>']
    for f in entrees:
        out.append('<h2 id="%s">%s</h2><p>%s</p>' % (e(f['fichier'], quote=True), e(f['fichier']), e(f['description'])))
        if f.get('colonnes'):
            out.append('<table><thead><tr><th scope="col">Colonne</th><th scope="col">Type</th><th scope="col">Description</th></tr></thead><tbody>%s</tbody></table>' % ''.join(
                '<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(c['nom']), e(c['type']), e(c['description'])) for c in f['colonnes']))
    out.append('</body></html>')
    return '\n'.join(out).encode('utf-8')


def empreinte(data):
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------------------------ annonce avant lancement (lecture seule)
def estimer(desk):
    """Volume, catégories, droits et exclusions annoncés avant de lancer le profil de départ. Lecture seule (aucune écriture, aucun schéma créé) :
    elle peut servir à l'affichage de la page. Les lignes des dossiers réservés ne sont pas retirées du compte par domaine (compte de la table) ;
    le nombre de dossiers réservés exclus est donné à part."""
    import sqlite3
    from pathlib import Path
    from . import export5688 as ex
    from .membres5650 import load
    etat = Path(desk.c['state_dir'])
    reserves = frozenset(load(desk.c['state_dir'])['reserves'])
    out = {'domaines': {}, 'documents': 0, 'courriels_eml': 0, 'octets_eml': 0, 'dossiers_reserves': len(reserves), 'motifs': {}, 'non_couvertes': 0}
    db = sqlite3.connect((etat / ex.DESK).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        noms = set()
        for domaine, _nom, table, _b, _f in ex.tables_exportees():
            noms.add(table)
            if _colonnes(db, table):
                d = out['domaines'].setdefault(domaine, {'tables': 0, 'lignes': 0})
                d['tables'] += 1
                d['lignes'] += db.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        exclues = ex.tables_exclues()
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if table in noms:
                continue
            motif = ex.motif_exclusion(table, exclues)
            if motif:
                out['motifs'][motif] = out['motifs'].get(motif, 0) + 1
            else:
                out['non_couvertes'] += 1
        for item in eml_a_exporter(db, reserves):
            out['courriels_eml'] += 1
            out['octets_eml'] += item['taille']
    finally:
        db.close()
    docs = etat / ex.DOCS
    if docs.is_file():
        d = sqlite3.connect(docs.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            out['documents'] = sum(1 for (m,) in d.execute('SELECT matter FROM docs') if m not in reserves)
        except sqlite3.Error:
            out['documents'] = 0
        finally:
            d.close()
    return out
