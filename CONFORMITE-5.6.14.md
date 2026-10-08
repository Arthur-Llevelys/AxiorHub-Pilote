# Matrice de conformité — AxiorHub Pilote 5.6.14

Base : archive 5.6.13 (commit d385d35, SHA-256 031f77c1…a4ec). Chaque exigence du cahier des charges est reliée à son implémentation et
à son test. « Livré » : exigence réalisée et testée avec données fictives et services simulés ; « Partiel » : réalisée pour l'essentiel
avec les limites indiquées ; « Non livré » : à réaliser dans une version suivante. Aucun parcours n'a encore été recetté sur les services
réels du cabinet (section 28) : la recette de production (Mise en service) et les cas R16–R30 doivent être joués par l'avocat.

| Réf. | Exigence | État | Implémentation | Test |
|---|---|---|---|---|
| M01 | Hiérarchie persistante et bornée | Livré | `taches5614` (tables, `check_graph`, bornes `missions5614:limits`, lots) | test_v5614 Graph, Execution.batched |
| M02 | Entrées et résultats explicites | Livré | artefacts typés et versionnés, `_inputs` (références type/version/hash), contenu complet | Execution.assignation |
| M03 | Dépendances fondées sur un résultat accepté | Livré | états tâche / résultat / validation séparés, `_runnable`, branche facultative | Execution.research_blocked |
| M04 | Reprise sans double effet | Livré | bail, génération, `_publish` (tentative périmée), `_deposit` (journal ops5614) | Execution.lease, deposit_resumes |
| M05 | Parallélisme et budget | Partiel | tâches indépendantes lancées jusqu'à `concurrency` (plusieurs workers), budget d'appels, suspension et reprise ; coût estimé non affiché par mission | Execution.lease |
| M06 | Plan révisable, décisions utiles | Livré | `control(revise)`, décisions regroupées (`champs_manquants`), lectures continuent | Execution.missing_fields, revise |
| M07 | Rôles et modèles | Livré | `parcours5614.ROLES`, routage par fonction tracé (`trace.route`), rôle désactivé bloqué | Execution.assignation |
| M08 | Superviseur soumis aux contrats | Partiel | sources = données (consigne système), aucune action outil depuis une sortie modèle, mandat inchangé ; pas de test d'injection dédié sur le superviseur | — |
| M09 | Paquet final | Livré | `_exec_presentation` (Word, bordereau, inventaire, fiche de contrôle, décisions résiduelles) | Execution.assignation |
| M10 | Rédaction par section avec entrées | Livré | sections T12a–e avec dépendances explicites, dispositif depuis la matrice, bordereau depuis les pièces citées | Execution.assignation |
| M11 | Registre daté des procédures | Livré | `profils5614` (7 profils, références à vérifier, approbation, révocation), page /profils | Profils |
| M12 | Conclusions et réponse aux écritures | Partiel | parcours C01–C18 (sélection des écritures par décision, extraction des moyens, comparaison facultative, demandes maintenues) ; non exécuté de bout en bout en test | Graph (validité du graphe) |
| M13 | Adaptateurs MCP opérationnels | Livré | `mcp5614` (session, pagination, schémas, diagnostic 3 niveaux), `extensions364` | MCP |
| M14 | Chaîne de preuve des données | Partiel | `sources5614` (fichiers : id, version, hash, pages ; courriels et agenda inventoriés) ; OCR par page et image de page non conservés | Sources |
| M15 | Contrôles par assertion et livrable | Livré | `controle5614` (assertions, déterministe, blocs, exécution ≠ résultat) | Controle |
| M16 | Apprentissage validé et réversible | Partiel | règles proposées après correction (5.6.13) avec portée dossier/cabinet ; pas d'évaluation sur corpus ni de recalcul des travaux à la révocation | test_v5613 |
| A01 | Fiche dossier qui fait autorité | Non livré | fiche parties/chronologie produites par mission ; pas d'état de dossier persistant transversal | — |
| A02 | Autonomie lisible | Partiel | pause des automatismes visible, niveaux existants conservés | Interface |
| A03 | Plan qui se poursuit jusqu'au résultat | Livré | missions complexes (entrées, sorties, reprise) | Execution |
| A04 | Nouveautés et travail périmé | Partiel | `sources5614.changed_since` (delta) ; invalidation automatique sur nouvelle pièce non branchée | Sources |
| A05 | Lecture avec couverture vérifiable | Livré | `read_file` (pages, blanches, illisibles), manifeste probant | Sources |
| A06 | Matrice arguments / preuves / demandes | Livré | T11 (matrice), C06/C10 (moyens adverses, réponse point par point) | Execution |
| A07 | Recherche juridique datée | Partiel | T08 via connecteurs + `verify470` ; une recherche sans texte récupéré bloque ; pistes distinguées des références vérifiées | Execution.research_blocked |
| A08 | Mémoire sans généralisation abusive | Partiel | portées cabinet / dossier des règles proposées | test_v5613 |
| A09 | Déclencheurs et priorités | Non livré | déclencheurs 5.6.8 conservés ; pas de déduplication mail/Nextcloud nouvelle | — |
| A10 | Échéances et préparation anticipée | Non livré | — | — |
| A11 | Contrôle puis correction ciblée | Livré | `_exec_correction` (défauts localisés, deux cycles, décision « réserve ») | Execution |
| A12 | Mesure des progrès et état fiable | Livré | `get()` : prochaine action, progression par livrables acceptés, blocages, budget | Execution |
| U01 | Routines en tête de page | Livré | `aujourdhui5614.routines_bar_html`, v5614.js (sans double clic, pause) | Interface |
| U02 | Centre « À décider » compact | Livré | résumé replié, panneau latéral, cartes et actions (valider, modifier, compléter, reporter, annuler) | Interface.decisions |
| U03 | Agenda en semaine | Livré | `agenda36` (défaut semaine, préférence) | Interface |
| U04 | Pilote clair et contextualisé | Livré | commandes de contexte, livrable annoncé, intentions mission/facturation | Interface.pilot |
| U05 | Voix contrôlable | Partiel | micro arrêté à la fermeture (5.6.13), capture/transcription/synthèse distinguées ; transcription à confirmer avant effet non ajoutée | Interface.readiness |
| U06 | Documents prêts à relire | Partiel | notes internes hors du corps, fiche de contrôle séparée ; vue côte à côte projet/appuis non réalisée | Execution, Corrections |
| N01 | Client facturé et contacts | Livré | `ensure_client`, `search_clients`, `update_client` | Facturation |
| N02 | Projet rattaché au dossier | Livré | `ensure_project` | Facturation |
| N03 | Devis en brouillon | Livré | `preview_quote`, `draft_quote` (jamais envoyé ni converti) | Facturation |
| N04 | Factures au-delà du temps | Non livré | forfaits et provisions non ajoutés au producteur de factures | — |
| N05 | Tâches et temps de projet | Partiel | tâches par temps (5.6.11) avec représentation fidèle ; liaison projet si connue | Facturation |
| N06 | Adaptateur API unique | Livré | `InvoiceNinjaClient` | Facturation |
| N07 | Synchronisation avec conflits | Partiel | `pull` périodique (statuts, paiements, temps facturés à distance) ; webhooks et conflits champ à champ non réalisés | Facturation.sync |
| N08 | Écritures préparées et autonomie par action | Livré | journal `invoice_ninja_ops5614`, rapprochement après timeout | Facturation |
| N09 | Parcours intégré au Pilote | Partiel | intention « facturation », données manquantes en une carte, actions depuis Honoraires | Interface.pilot |
| C01 | Reprise IMAP sans duplication | Livré | `maildraft5613` (`find_existing`, MIME conservé) | Corrections.imap |
| C02 | Vérification complète du brouillon | Livré | `verify`, `recheck` | Corrections.imap |
| C03 | Révision Word sans perte de structure | Livré | `pilote5613.revise_body` (mode complet explicite) | Corrections.word |
| C04 | Contrôle exécuté ≠ réussi | Livré | `control_document` (execution/outcome), `document_checks` | Controle |
| C05 | Contrôler avec les sources | Livré | `controle5614.review` (blocs, sources pertinentes, fin de l'acte couverte) | Controle |
| C06 | Manifeste probant | Livré | `sources5614.manifest` | Sources |
| C07 | Changements sensibles | Livré | `revision_diff` normalisé et ordonné | Corrections.diff |
| C08 | Fiche audience | Livré | `audience5613` (likely_questions, erreurs visibles, liens encodés) | Corrections.hearing |
| C09 | Routage des mails | Livré | `_mail_model` (mail_drafting) | Corrections.imap |
| C10 | Modèles Word et champs obligatoires | Livré | `build_from_template_ex`, repli « cabinet » retiré | Corrections.template |
| C11 | Cache et empreinte du modèle | Livré | `_model_fingerprint(digest)`, routage retiré | test_v5613 Cache (inchangé) |
| C12 | Dépendances et validation attachées au contenu | Livré | `plans568.DONE`, `validations5614` | Execution.migrate, Corrections.template |
| C13 | Recette représentative | Livré | `recette5613` (trois niveaux, erreur injectée, interruption) | Interface.recette |
| C14 | Rapprochement des factures | Livré | `_check_invoice` + `check_lines` | Facturation |
| C15 | Temps fidèles | Livré | `time_log`, identifiant conservé | Facturation.time |
| C16 | Réserver chaque temps | Livré | `invoice_ninja_entries5614` | Facturation.time |
| C17 | Session MCP et schémas | Livré | `mcp5614` | MCP |
| C18 | Tous les résultats parents transmis | Livré | `_inputs` | Execution.assignation |
| C19 | Hiérarchie et contrats de sortie | Livré | `tasks5614` | Graph |
| C20 | Rôle transmis au producteur | Livré | `_model(role)`, trace | Execution.assignation |

Limites à conserver dans les notes de livraison : les profils procéduraux initiaux doivent être vérifiés et approuvés par l'avocat
avant tout usage ; les autres procédures sont hors couverture. La version des services du cabinet (Invoice Ninja, MCP) et leurs
schémas doivent être vérifiés à l'installation (Mise en service, tests de connexion, recette de production).
