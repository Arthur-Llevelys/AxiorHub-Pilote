# Conformité 5.6.35 — audit de la 5.6.34 (fiches F01 à F18) et anomalies signalées en production

Version de référence : 5.6.34. Cette version corrective traite les dix-huit fiches de l’audit « AxiorHub Pilote 5.6.34 », une partie
des améliorations fonctionnelles qu’il propose, et les anomalies relevées par l’avocat sur un serveur en 5.6.33 et dans
l’application Nextcloud 0.1.0.

Légende : **Réalisé** : livré et couvert par un test. **Partiel** : livré avec la limite indiquée. **Non réalisé** : hors de cette
livraison, avec la raison. Sauf mention contraire, les tests cités sont dans `tests/test_v5635.py`. Chaque contre-test de l’audit
y est repris comme test de non-régression.

## Fiches de l’audit

| Fiche | Objet | État 5.6.35 |
|---|---|---|
| F01 | Date et montant validés hors de l’extrait cité | **Réalisé.** Après `find_excerpt`, seul le passage exact est contrôlé, et son empreinte est conservée (`passage_sha256`). Une date absente du passage donne la cause `date_absente_de_l_extrait`. Un montant absent donne `montant_absent_du_passage`, un extrait introuvable `montant_non_verifiable`. Les deux pages du fragment ne se valident plus mutuellement. Le vrai passage reste accepté. Les DOCX sans pagination utilisent la position. La méthode passe à `5635.1`, ce qui invalide les relevés antérieurs : ils sont refaits et les corrections de l’avocat sont conservées. Une fiche produite avec l’ancienne méthode affiche un avis. Tests : `Evidence.test_date_is_checked_only_in_the_cited_excerpt`, `test_amount_of_another_page_never_validates_an_excerpt_without_amount`, `test_unpaginated_docx_uses_the_excerpt_position`, `test_method_version_invalidates_previous_extractions`. |
| F02 | Montants sans devise, signe ni nature | **Réalisé.** Un montant est désormais une valeur structurée : Decimal, devise, signe, texte et position. Un nombre sans devise n’est pas un montant, et une année ou un numéro de pièce ne l’est jamais. EUR et USD restent distincts, sans conversion. Les séparateurs ambigus (« 4,500 € ») sont signalés comme `montant_ambigu` plutôt que devinés. Le schéma de relevé gagne `devise` et `role`, et la clé de dédoublonnage inclut le rôle. Test : `Evidence.test_amounts_keep_currency_sign_and_never_take_a_year`. |
| F03 | Une seule occurrence d’une série dans le cache partagé | **Réalisé.** Chaque occurrence a une clé de cache propre. Le cache est reconstruit une fois à la mise à jour (marqueur `agenda5635_cache`). Un réimport ne crée pas de doublon. Test : `Agenda.test_each_occurrence_has_its_own_cache_entry`. |
| F04 | Identité iCalendar des occurrences exportées | **Réalisé.** Chaque occurrence exportée a un UID distinct et stable et porte `X-AXIORHUB-ID`, `-SERIES` et `-RECURRENCE-ID`. Un aller-retour export puis import ne crée ni perte ni doublon. Test : `Agenda.test_export_gives_each_occurrence_a_distinct_stable_identity`. |
| F05 | Modification partielle qui change durée et type | **Réalisé.** Seuls les champs envoyés sont modifiés : durée, journée entière et notes sont conservés, et la description est canonique. La version lue (`base`) est contrôlée : `evenement_modifie_entre_temps` est renvoyé au lieu d’écraser. Test : `Agenda.test_partial_update_keeps_duration_all_day_and_notes`. |
| F06 | EXDATE ignorés, UNTIL exclusif | **Réalisé.** EXDATE et RECURRENCE-ID sont appliqués et UNTIL est inclusif. Une règle non prise en charge est importée partiellement, avec ses motifs et l’horizon couvert. Les rappels VALARM sont repris. Tests : `Agenda.test_exdate_is_applied_and_until_is_inclusive`, `test_reminders_survive_an_export_import_round_trip`. |
| F07 | Restauration qui ne rétablit pas l’état | **Réalisé.** La sauvegarde contient la copie interne (JSON) en plus de l’ICS. La restauration se fait en deux temps : un aperçu (rien n’est modifié), puis la confirmation, précédée d’une sauvegarde de l’état actuel. Elle s’exécute en transaction et reconstruit le cache. Statuts, rappels et annulations sont rétablis. Une sauvegarde antérieure à 5.6.35 est signalée comme « import seulement ». Test : `Agenda.test_restore_brings_back_status_and_reminders_after_preview`. Vérifié dans le navigateur (aperçu, confirmation, rechargement). |
| F08 | Demande refusée qui bloque le travail et la clé | **Réalisé.** La validation a lieu avant toute réservation, et la réservation se fait en `BEGIN IMMEDIATE`. Une même clé avec un autre contenu donne 409 `cle_idempotence_deja_utilisee`. Une réservation orpheline est reprise, ou close comme `reservation_sans_travail`. Tests : `Contract.test_refused_request_reserves_nothing_and_key_is_bound_to_content`, `test_orphan_reservation_is_resumed_or_closed`. |
| F09 | Annulation sans travail oubliée | **Réalisé.** L’état `cancelled` est persistant, et un travail déjà terminé renvoie `already_finished`. Test : `Contract.test_cancel_without_engine_job_is_durable`. |
| F10 | Opération signée non comparée à la route | **Réalisé.** Le contexte signé porte `op = MÉTHODE + chemin`, comparé exactement à la route appelée (`operation_non_signee`, 403). Le contrat passe en version 1.1. Test : `Contract.test_signed_operation_must_match_the_called_route`. |
| F11 | Manifeste de pièces non appliqué | **Réalisé.** Taille et empreinte sont vérifiées. Chaque pièce est rattachée à l’index du dossier par ETag, puis par nom exact unique. Les contrôles portent aussi sur l’instance, l’utilisateur, le dossier et une durée de validité de 24 h. Le travail de fiche ne porte que sur ces pièces (`only`). Test : `Contract.test_manifest_is_checked_and_restricts_the_analysed_pieces`. |
| F12 | Résultat d’un ancien travail modifié | **Réalisé.** Le résultat est figé à la première lecture, avec la version de fiche réellement produite par ce travail et les corrections. La décision porte sur cette version ; une autre version donne 409. Test : `Contract.test_result_of_a_finished_job_stays_identical_after_a_new_preparation`. Limite : une décision enregistrée en 5.6.34 portait sur une empreinte calculée autrement. Elle reste dans l’historique avec sa version d’origine et ne vaut pas approbation du résultat figé en 5.6.35. |
| F13 | Contextes mal formés : exception | **Réalisé.** Le schéma du contexte est strict et un fuseau horaire est obligatoire (`contexte_sans_fuseau`). Toute erreur de forme donne 400 `requete_invalide`, tracée, jamais une exception. Test : `Contract.test_malformed_contexts_give_structured_4xx`. |
| F14 | Diagnostic Windows faussement vert | **Réalisé.** Le propriétaire et la DACL sont lus (`OWNER` et `DACL`), avec les entrées d’héritage seul écartées. Une DACL nulle est une erreur (`dacl_nulle`), et une DACL vide aussi (`dacl_vide_fichier_illisible`). Un propriétaire non reconnu (`proprietaire_non_reconnu`) ou un autre compte autorisé (`compte_non_reconnu`) donne « unknown » avec la liste des comptes. Seuls le système, les administrateurs, OWNER RIGHTS, le propriétaire et le compte qui exécute AxiorHub sont acceptés. Tests : classe `WindowsAcl`, dont un test sur l’ACL réelle d’un fichier sous Windows. Limite : les refus (Deny) ne compensent pas un accès large, ce qui donne un diagnostic prudent. |
| F15 | Fixtures dépendant de l’umask | **Réalisé.** La fixture crée le secret en 0600 explicitement (correctif publié en 5.6.34). Le test négatif refuse un secret en 0644 (`Contract.test_secret_readable_by_others_is_refused`). La recette GitHub exécute la suite sous umask 022 et le contrat sous umask 077. Le parcours Windows exécute aussi `test_v5634` et `test_v5635`, et les trois parcours archivent leurs journaux. |
| F16 | Approbation d’un résultat partiellement affiché | **Réalisé (application 0.2.0).** Le contenu complet est affiché par sections, avec le nombre de caractères. « Approuver », « Refuser » et « Demander une correction » (commentaire obligatoire) portent sur la version lue. Limite : vérifié par construction et compilation de l’interface ; l’installation sur un Nextcloud réel reste à faire, car aucun PHP n’est disponible sur le poste de construction. |
| F17 | Réponse tardive attribuée à un autre travail | **Réalisé (application 0.2.0).** Un compteur de génération, l’annulation des requêtes en cours (AbortController) et l’identifiant capturé au lancement empêchent qu’une réponse tardive modifie l’écran d’un autre travail. Même limite que F16. |
| F18 | Verrou SQLite pendant la copie Nextcloud | **Réalisé.** La copie est faite après validation de la transaction, en dehors de toute transaction ; la mise à jour de son état est conditionnée à la version de l’événement. Test : `Agenda.test_network_copy_happens_after_commit_and_outside_any_transaction`. |

## Anomalies signalées par l’avocat

| Signalement | État 5.6.35 |
|---|---|
| Application Nextcloud : « secret_too_short » avec un secret de 8 caractères | **Réalisé.** Le minimum est de 8 caractères dans l’application, comme dans le moteur (`SECRET_MIN = 8` des deux côtés). Test moteur : `Contract.test_short_secret_of_eight_characters_is_accepted_and_shorter_refused`. |
| Application Nextcloud : « Aucun dossier ne vous est accessible » | **Réalisé.** Cause : la table de correspondance dossier/groupe de l’application n’était jamais remplie. Le moteur expose `GET /integration/v1/matters`, réservé au périmètre administrateur (`Contract.test_matter_list_is_reserved_to_the_nextcloud_administrator`). L’application importe les dossiers à l’enregistrement des réglages, à l’ouverture par un administrateur et périodiquement. Le groupe par défaut est réglable (« admin » à défaut). Un tableau d’administration rattache chaque dossier à un groupe, et un groupe choisi est conservé lors des imports suivants. |
| `info.xml` : liens du site, des anomalies et du code | **Réalisé.** Ils pointent vers `AxiorHub-Pilote-Nextcloud`, ainsi que la documentation utilisateur et administrateur. |
| Recette serveur : 2 échecs IntegrationContract (droits du secret) | **Réalisé** (voir F15). |
| Recette serveur : `No module named 'waitress'` | **Réalisé.** `recette.py` ajoute au chemin les paquets de l’environnement de l’interface (`/opt/axiorhub-mail-agent/ui-venv`), après ceux du système et seulement pour la même version de Python. Faute de Waitress, le test est ignoré avec son motif. Test : `Recette.test_interface_venv_packages_are_visible_to_the_self_test`. |
| « Traitements trop anciens : digest_pieces5632 · 3.4 h · running » | **Réalisé.** Cause : un passage traitait jusqu’à six pièces sans limite de durée, et une pièce longue peut demander des dizaines d’appels au modèle. Un passage est désormais borné à 20 minutes (`synthese5635.budget_minutes`). Le passage s’arrête entre deux fragments, et la pièce reste « à reprendre » sans échec compté. Les fragments faits sont conservés, la suite est programmée et l’avancement « fragment n/N » est affiché. Le travail est rejouable après un redémarrage. L’ancienneté d’un traitement en cours est mesurée depuis son démarrage. Tests : `DigestBudget`. Le traitement #140464 bloqué sur le serveur reprend seul au redémarrage du worker qui suit la mise à jour. |

## Améliorations fonctionnelles proposées par l’audit

| Proposition | État 5.6.35 |
|---|---|
| Agenda : Modifier, annuler une occurrence ou une série, recherche et filtre par dossier, au-delà de 30 événements | **Réalisé.** « Modifier » sous chaque événement, « Annuler la série », recherche et filtre par dossier, 200 événements visibles. Vérifié dans le navigateur. |
| Agenda : distinguer import, copie vers Nextcloud et synchronisation | **Réalisé.** Les libellés présentent la copie comme « à sens unique, pas une synchronisation bidirectionnelle », y compris sur le bandeau de la page. L’import ajoute des événements et la restauration rétablit l’agenda (texte d’explication). |
| Agenda : vues jour / semaine / mois | **Non réalisé.** La liste filtrable couvre la consultation ; une vue calendrier relève d’une version fonctionnelle. |
| Fiche dossier : preuve, version, page et causes de réserve pour chaque fait ; filtre « faits contrôlés » ; sections restructurées | **Partiel.** Pièce, page ou paragraphe, extrait, causes et empreinte du passage sont conservés pour chaque élément (F01). La restructuration en sections (parties, juridiction, arguments par partie, scénarios) et le filtre restent à faire. |
| Missions complexes en graphe de sous-tâches | **Non réalisé.** Il s’agit d’une évolution d’architecture, hors du périmètre d’une version corrective. |
| Application Nextcloud : liste des travaux, réouverture d’un résultat ancien, historique des approbations, refus et correction, formulaire d’administration des groupes | **Réalisé (0.2.0).** Les travaux récents et la réouverture sont disponibles (`GET /api/jobs`). L’historique complet des décisions passe par la table append-only `integration_decisions5635`. Refus et correction avec commentaire. Tableau d’administration des dossiers et des groupes. La sélection native des pièces dans Fichiers reste à faire. |

## Données existantes : règles de reprise appliquées

- Rien n’est supprimé : fiches, corrections, événements et décisions restent en place.
- Le cache de l’agenda est reconstruit une fois à partir de la table des événements.
- Les relevés de pièces de l’ancienne méthode ne sont plus utilisés et sont refaits au prochain passage.
- Une réservation d’intégration restée sans job depuis plus de deux minutes, y compris celles créées en 5.6.34, est remise en file
  si la demande est complète. Sinon, elle est close en échec avec le motif `reservation_sans_travail`.
- Les décisions 5.6.34 restent lisibles dans l’historique.

## Distribution

| Point | État |
|---|---|
| Archive cumulative installable depuis 5.6.6 | Réalisé (`SUPPORTED_PREVIOUS` reconstruit explicitement jusqu’à 5.6.34 ; test `test_upgrade_from_5634_is_supported_and_reversible`). |
| Paquets Debian serveur et poste, installateur Windows, application Nextcloud 0.2.0 | Réalisé ; sommes SHA-256 publiées avec chaque fichier. |
| Recette exécutée depuis l’archive livrée | Réalisé ; résultats dans `VALIDATION-5.6.35.json`. |
