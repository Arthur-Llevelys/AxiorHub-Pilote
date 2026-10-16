# Conformité 5.6.52 — audit « AxiorHub Pilote 5.6.51 » du 16 octobre 2026

Version de référence : 5.6.51. L’audit comporte dix défauts reproduits par onze contre-tests (F01 à F10), le cahier des charges des
routines personnalisées de la page Aujourd’hui (R01 à R09) et celui des connexions API utilisables par l’IA (I01 à I15, architecture
commune et parcours SIREN → convention → signature → archive → facture → règlement).

**La 5.6.52 corrige F01 à F10, livre les routines personnalisées (R01 à R09) et le registre des quinze connexions avec leurs outils.**
Les onze contre-tests de l’audit passent (`tests/test_contre5651.py`). Chaque connexion est **codée et testée sur un serveur simulé**
qui reproduit les contrats des documentations officielles (adresses, authentification, en-têtes, corps) ; **aucune n’a été testée en
bac à sable réel ni reçue en production** : cela suppose les accès du cabinet (voir « Non vérifié ici »).

## Lot 0 à 3 — défauts F01 à F10

| Fiche | Priorité | État 5.6.52 | Où / recette |
|---|---|---|---|
| F01 Accès indirect à une règle d’un dossier réservé | P0 | **Corrigé.** Le contrôle central résout côté serveur le dossier **réel** des objets désignés par identifiant (règle, information, partie, procédure, décision du cabinet, lacune, relecture, élément de file, routine, opération de connexion) et l’unit au dossier indiqué : refus 403 sans lecture ni écriture. Le service des règles vérifie aussi le dossier attendu et ne renvoie plus que l’identifiant, l’état et la version. Inventaire des routes indirectes dans `membres5650.ROUTE_OBJECTS`. | `membres5650.route_objects/gate`, `apprentissage5651.authorize_rule/set_status`, `web5651` ; contre-test F01 et variantes |
| F02 Contexte de rédaction non entretenu | P1 | **Corrigé.** Le modèle structuré est remis à jour après chaque indexation et chaque relevé de pièces, et vérifié avant chaque rédaction (`ensure_current`) ; état « partiel » signalé au modèle s’il ne peut pas l’être. | `modele5651.ensure_current`, `desk._current5652`, `contexte5651.for_drafting` |
| F03 Informations d’anciennes versions courantes | P1 | **Corrigé.** Chaque information porte une validité (courante, historique, à recontrôler) d’après la version indexée de sa source ; les vues et les rédactions n’emploient que les courantes ; filtre « Historique » et badge. | `modele5651._refresh_validity`, `vue5651`, `contexte5651` |
| F04 Rappels faussement à jour | P1 | **Corrigé.** Empreinte canonique des informations utilisées (`recit5651.basis`), prise avant l’appel au modèle ; version réservée sous verrou ; rappel signalé périmé dès que l’empreinte change. | `recit5651`, `vue5651` |
| F05 Source modifiée sans invalidation | P1 | **Corrigé.** Une acceptation dont la source a changé est invalidée ; une nouvelle acceptation demande « sources vérifiées » (`source_a_recontroler`). | `relecture5651.decide/_changed_sources` |
| F06 Ancre visant un paragraphe identique | P1 | **Corrigé.** Localisation par position, texte et voisins (ou ancre unique) ; sinon proposition périmée. | `relecture5651.locate` |
| F07 Applications Word non cumulatives | P1 | **Corrigé.** Chaque application repart de l’original et cumule toutes les décisions acceptées dans un fichier numéroté « … relecture AxiorHub <id> vN.docx » ; même ensemble de décisions = même fichier (`deja_cree`). | `relecture5651.apply` (table `d51_review_exports`) |
| F08 Préparation non reprise après la pause | P1 | **Corrigé.** Les paliers « prévus » ou « bloqués » redeviennent éligibles à la reprise ; réservation atomique ; jamais deux préparations du même événement. | `missions5651` |
| F09 Règles « ce document » ignorées | P1 | **Corrigé.** Le chemin du document relu est transmis au contexte ; ses règles de portée document sont appliquées. | `contexte5651.rules_for`, `relecture5651.run` |
| F10 Routine perdue après un échec de mise en file | P1 | **Corrigé.** Réservation de l’occurrence et mise en file dans une même transaction ; file pleine = occurrence « à reprogrammer » avec nouvel essai, jamais perdue ni doublée. | `routines5652.tick`, `desk.enqueue(transactional=True)` |

## Routines personnalisées (R01 à R09)

| Fiche | État 5.6.52 | Où |
|---|---|---|
| R01 Créer, modifier, supprimer depuis Aujourd’hui | **Fait.** Bloc « Routines » : création libre ou depuis un modèle (point du matin, nouveautés des dossiers, synthèse, mission dans chaque dossier…), modification, activation / pause, duplication (copie désactivée), suppression ; aperçu des prochains passages ; historique des passages ; « Lancer maintenant ». | `web5652.routines_block`, `static/v5652.js`, `aujourdhui5614.routines_bar_html` |
| R02 Autorisations | **Fait.** Création par avocat ou administrateur ; routine du cabinet ou personnelle ; dossiers choisis contrôlés à la création **et** à chaque passage (accès retiré = dossier ignoré, signalé) ; aucune routine créée par le modèle ; mode « préparation et relecture » : une routine n’envoie, ne signe, ne facture ni ne prélève jamais. | `routines5652.authorize/_clean/execute` |
| R03 Modèle de données et versions | **Fait.** Définition versionnée (`routines5652_versions`), contrôle de version (409), instantané de la définition dans chaque occurrence, clé de demande idempotente. | `routines5652` |
| R04 Planifier sans perdre ni doubler | **Fait.** Occurrence unique (routine, créneau) ; fuseau du cabinet ; heure d’été (créneau inexistant → minute valide suivante ; créneau double → une seule fois) ; rattrapage borné après un arrêt ; file pleine → à reprogrammer. | `routines5652.local_slot/_due/tick` |
| R05 Pause, reprise, suppression | **Fait.** Pause → occurrences « reportées » reprises au retour si elles restent utiles ; modification ou suppression → occurrences non commencées annulées, travaux en cours arrêtés au point de contrôle suivant ; comptes rendus conservés. | `routines5652.resume_paused/update/delete/checkpoint` |
| R06 API | **Fait.** `m5652/routine/{creer,modifier,activer,dupliquer,supprimer,lancer,apercu,historique}`, `m5652/routines`, `m5652/file/decider`, inscrites au registre des droits et au contrôle des dossiers réels. | `web5652`, `membres5650` |
| R07 Migration | **Fait.** Les routines 5.2.0 (briefing, tri, bilan) deviennent des routines historiques versionnées, avec leurs réglages ; copie de sauvegarde ; les réglages 5.2.0 restent synchronisés (retour arrière possible). Elles se désactivent mais ne se suppriment pas. | `routines5652.migrate`, `routines520` |
| R08 Travail utile et autonomie mesurable | **En partie.** Compte rendu par passage (dossiers traités, ignorés, erreurs, durée), relu dans la file « À relire » ; le temps gagné n’est pas encore mesuré. | `routines5652.execute/queue_items` |
| R09 Recette | **Faite sur données fictives** (`tests/test_v5652.py`) : migration stable, création idempotente, conflit de version, heure d’été, file pleine, pause et reprise, lancement manuel, accès personnel. | |

## Connexions API utilisables par l’IA (I01 à I15)

Interface : **Paramètres › Connexions › Connexions API utilisables par l’IA**, par famille (Rechercher le droit ; Signer et envoyer ;
Facturer et encaisser ; Messagerie, agenda et documents). Pour chaque fournisseur : fiche, lien « Obtenir mes accès », documentation,
authentification, environnement (test / production), capacités à activer une à une, « Tester » (lecture réelle sans effet),
« Connecter mon compte » (OAuth), « Déconnecter ». L’état affiché est celui du dernier test réel ; un secret ne suffit pas.

| Fiche | Fournisseur | Authentification | Capacités | État |
|---|---|---|---|---|
| I01 | AR24 | Jeton + clé privée (requêtes signées, réponses chiffrées) | préparer, envoyer un recommandé validé, suivre, preuves | Codé ; testé sur serveur simulé ; signature et déchiffrement vérifiés sur le vecteur officiel. Hôte de production à confirmer. |
| I02 | Youtrust (Yousign) | Clé API (Bearer) | préparer, envoyer validé, suivre, signé + piste d’audit | Codé ; testé sur serveur simulé. |
| I03 | Légifrance (PISTE) | OAuth client credentials | rechercher à une date, lire une version d’article, contrôler une référence | Codé ; testé sur serveur simulé ; accès PISTE des sources juridiques réutilisables. |
| I04 | Judilibre (PISTE) | OAuth client credentials | rechercher, lire une décision, contrôler un pourvoi / RG | Codé ; testé sur serveur simulé. |
| I05 | Pappers Justice | Clé API (Bearer) | rechercher, lire ; question à l’IA juridique externe (autorisation distincte) | Codé ; testé sur serveur simulé. |
| I06 | Pennylane | Jeton d’API société | rechercher un client, facture en brouillon (validée), état, règlements | Codé ; testé sur serveur simulé. |
| I07 | DocuSign | OAuth code + PKCE | préparer, envoyer validé, suivre l’enveloppe, signé + certificat | Codé ; testé sur serveur simulé. Go-Live DocuSign requis en production. |
| I08 | SuperPDP | OAuth client credentials | recevoir, suivre, télécharger, transmettre une facture validée | Codé ; testé sur serveur simulé. API bêta : formats à qualifier. |
| I09 | B2Brouter | Clé X-B2B-API-Key | recevoir, suivre, télécharger, transmettre une facture validée | Codé ; testé sur serveur simulé. |
| I10 | GoCardless | Jeton d’accès (+ Idempotency-Key) | préparer un mandat, soumettre un prélèvement autorisé, suivre | Codé ; testé sur serveur simulé. |
| I11 | Chorus Pro | PISTE + compte technique (cpro-account) | contrôler la structure destinataire, soumettre validée, suivre | Codé ; testé sur serveur simulé. Charges utiles à qualifier. |
| I12 | Maileva (La Poste) | Identifiants du contrat | préparer, envoyer un courrier validé, suivre, preuve de dépôt | Codé d’après la documentation publique ; testé sur serveur simulé. Contrat technique à confirmer. |
| I13 | Microsoft Graph | OAuth délégué par capacité | agenda, courriels, brouillon (désactivé par défaut, jamais d’envoi), contacts, dossier OneDrive choisi | Codé ; testé sur serveur simulé. |
| I14 | Google Workspace | OAuth utilisateur par capacité | agenda, Gmail, brouillon (désactivé par défaut), dossier Drive choisi, export PDF | Codé ; testé sur serveur simulé. |
| I15 | Axonaut | Clé API (userApiKey) | rechercher une société ou un client, devis (validé) | Codé ; testé sur serveur simulé. |

**Architecture commune.** Registre des fournisseurs (`connexions5652.PROVIDERS`) et adaptateurs (`adaptateurs5652`) : contrat
d’authentification, capacités, normalisation des erreurs. Secrets dans le coffre existant, injectés par le serveur au dernier moment,
jamais rendus à l’écran, dans un prompt, un journal ou le navigateur ; hôtes fixés par le fournisseur et l’environnement (HTTPS seul),
jamais choisis par le modèle. OAuth : état à usage unique lié au compte et à l’adresse de retour déclarée, PKCE, jetons renouvelés avec
contrôle de version. Déconnexion : secrets effacés, jetons révoqués localement, opérations non envoyées annulées.

**Outils de l’agent.** Seules les connexions **vérifiées et activées** exposent leurs outils, et seulement les capacités activées.
L’assistant choisit au plus trois appels de **lecture** (droit, jurisprudence, annuaire des entreprises si la question vise une
entreprise, facturation, agenda…) que le serveur exécute ; les résultats deviennent des sources citées. La recherche juridique utilise
les connexions Légifrance / Judilibre même sans serveur MCP. Une capacité à effet externe n’est jamais exécutée par le modèle : elle
devient une **opération** préparée (document figé par empreinte, destinataires contrôlés), validée par un avocat ou un administrateur,
puis transmise une seule fois (clé d’idempotence du fournisseur quand elle existe). Réponse perdue → « résultat à vérifier » et
rapprochement, jamais de nouvel envoi aveugle. Suivi périodique (toutes les 15 minutes) des opérations transmises ; preuves et
documents signés archivés dans le dossier. L’IA juridique externe exige sa propre autorisation.

**Parcours SIREN → convention → signature → archive → facture → règlement.** Les étapes existent séparément : identification du client
par SIREN ou nom (API Recherche d’entreprises, données publiques), signature (Youtrust ou DocuSign) d’un PDF du dossier figé, archive
du signé et de sa preuve, facture en brouillon (Pennylane ou Axonaut), transmission (SuperPDP, B2Brouter, Chorus Pro), prélèvement et
règlements. Panneau « Envois, signatures et vérifications externes » dans l’onglet Travaux du dossier. **Non fait :** enchaînement en
une seule mission avec reprise à l’étape défaillante ; création de la convention depuis le modèle Word du cabinet dans ce parcours.

## Migration et retour arrière

Tables ajoutées (`routines5652*`, `connexions5652`, `connexion_*5652`, `d51_review_exports`) et colonnes ajoutées (`validity`,
`validity_note`, ancres de relecture) : migration automatique au démarrage, sans modification des originaux. Retour à la 5.6.51 :
les tables nouvelles sont ignorées ; les réglages des routines 5.2.0 restent synchronisés ; les connexions API ne sont pas utilisées.

## Non vérifié ici

Accès en bac à sable puis en production des quinze fournisseurs (aucun compte du cabinet utilisé) ; notifications entrantes
(webhooks) non implémentées : suivi périodique seulement ; contenu structuré des factures électroniques (Factur-X / UBL) fourni par
l’appelant, sans écran dédié ; installation du cabinet ; qualité des comptes rendus de routines avec le modèle local ; ouverture des
révisions Word dans les éditeurs du cabinet.
