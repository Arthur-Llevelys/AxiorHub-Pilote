# Conformité 5.6.53 — audit « AxiorHub Pilote 5.6.52 et cahier des charges Facturation et honoraires » du 16 octobre 2026

Version de référence : 5.6.52 (archive `97df1029…`). L’audit comporte huit défauts AxiorHub reproduits (F01 à F08), deux défauts
Honorium (H01, H02) et une observation (H03), un constat de lecture (U01), le correctif de fond des opérations externes, puis le cahier
des charges de la page « Facturation et honoraires » (registre unique, règles applicables aux avocats, Honorium, Factur-X, IA) en cinq
lots. Demande complémentaire de l’avocat : un sous-dossier **FACTURATION** dans chaque dossier client.

**La 5.6.53 livre le lot 0 complet, les lots 1 et 2, le lot 3 sans qualification de plateforme et le lot 4 en partie.** Les huit
contre-tests AxiorHub de l’audit passent (`tests/test_contre5652.py`) ainsi que les deux contre-tests Honorium (sur Honorium V5.1.7
livrée à part) ; les 26 contre-tests antérieurs restent verts. Aucune facture n’a été transmise à une plateforme réelle.

## Lot 0 — opérations externes et routines

| Fiche | Priorité | État 5.6.53 | Où / recette |
|---|---|---|---|
| F01 Connexion vérifiée périmée | P1 | **Corrigé.** Génération de configuration par connexion : secret, environnement, paramètre d’identification ou partage PISTE modifiés → nouvelle génération, identité vérifiée, test et capacités vérifiées effacés, jetons en cache invalidés. Chaque opération retient génération, empreinte d’identité et environnement ; une différence à la validation donne « compte modifié : à revoir » (aucun POST) et « Préparer à nouveau sur le compte actuel ». Un test réussi ne revalide jamais une ancienne proposition ; un renouvellement de jeton OAuth ne change pas la génération. | `connexions5652.save/validate/reprepare` ; contre-test F01, `test_v5653.Lot0Extra` |
| F02 Échec avant envoi irréparable | P1 | **Corrigé.** Réservation par bail (`envoi_en_cours`, `locked_until`, tentative) ; lecture du document, empreinte et charge utile contrôlées avant tout appel distant ; panne à ce stade = « échec avant envoi » réessayable sans doublon ; bail expiré récupéré par le suivi (résultat à vérifier si l’appel distant avait commencé). | `connexions5652.validate/poll` ; contre-test F02 et reprise sans doublon |
| F03 Fin de signature ≠ fin d’archivage | P1 | **Corrigé.** État distant et archivage suivis séparément ; une tâche d’archivage durable par preuve (opération, type, version distante) avec empreinte, chemin et nouvel essai ; « terminée » seulement quand les preuves requises sont archivées ; sinon « terminée chez le prestataire — archivage à reprendre », puis « en échec » dans la file après 8 essais ; nom déjà pris : contenu comparé, jamais écrasé. | `connexions5652._archive_proofs/_archive` ; contre-test F03 |
| F04 Brouillon distant présenté comme envoyé | P1 | **Corrigé.** Journal durable des étapes distantes réussies (création, document, signataires, activation) ; référence distante retenue dès la création ; état distant « brouillon » ou étape manquante → « préparée chez le prestataire mais pas envoyée », jamais « envoyée » ni recréée ; états refusé/expiré/annulé terminaux par fournisseur. | `adaptateurs5652.Youtrust`, `connexions5652.REMOTE_DRAFT/REMOTE_FAILED/poll` ; contre-test F04 |
| F05 Connexion personnelle d’un autre compte | P1 | **Corrigé.** Opération, dossier réel et connexion contrôlés ensemble (lecture, fiche, annulation, validation, listes) ; une connexion personnelle ne sert qu’à son titulaire ou à un compte délégué (délégation enregistrée, retirable) — l’administrateur compris. | `connexions5652.can_use/_op_access` ; contre-test F05, délégation |
| F06 Routine du cabinet non idempotente | P2 | **Corrigé.** Clé `(auteur, clé de demande)` distincte du propriétaire de portée, empreinte du contenu : même clé et même contenu = même routine ; contenu différent = 409 ; collision concurrente rattrapée. | `routines5652.create` ; contre-test F06 |
| F07 Pause sans effet sur les passages en file | P1 | **Corrigé.** La pause suspend les passages automatiques non commencés (y compris en file) dans la même transaction ; définition relue au démarrage et à chaque point de contrôle ; un passage actif reçoit une demande d’arrêt ; « Lancer maintenant » reste permis comme exception manuelle tracée. | `routines5652.update/start_occurrence/checkpoint`, `routines520.run` ; contre-test F07 |
| F08 Droits fins ignorés | P1 | **Corrigé.** Catégorie de droit déclarée par route : configuration des connexions du cabinet = « réglages » (passerelle ET service), décision de relecture = « valider », validation d’opération = « envoyer » ou « facturation » (droit précis contrôlé par le service). Les connexions personnelles passent par des routes séparées soumises à la politique du cabinet. | `membres5650.M5652_CATEGORIES`, `web5652`, `connexions5652._may_configure` ; contre-test F08 |
| U01 Validation sans les éléments qui engagent | — | **Fait.** Fiche d’opération (`/operation?id=…`) : dossier et client lisibles, compte émetteur, environnement et identité, document ouvrable et empreinte, destinataires et ordre, données transmises, déclencheur, coût (non communiqué), champs de facture ; l’approbation porte sur l’empreinte de la fiche affichée ; accessible depuis la file (Aujourd’hui, Dossier) et la facturation. | `connexions5652.sheet/sheet_html`, `pages5636` |

**Réparation des données (§ 3).** Au premier démarrage : opérations « terminées » sans preuve archivée → « archivage à reprendre » ;
opérations restées « validées » → échec avant envoi ou résultat à vérifier ; passages en file de routines suspendues → reportés. Compte
rendu au journal (`reparation5653`). Les opérations 5.6.52 encore « à valider » devront être revues si leur compte a changé.

## Lots 1 et 2 — honoraires, documents et registre de facturation

| Exigence | État 5.6.53 | Où |
|---|---|---|
| Portage d’Honorium (§ 6) | **Fait.** Moteur serveur en `Decimal` et centimes : coût de revient et taux cible (majoration nommée comme telle), niveaux de complexité et fourchette, taux de ligne > type > dossier, temps assistés par l’IA et part du gain au forfait ; montants PROPOSÉ, RETENU et SIGNÉ distincts ; coût et marge internes absents des documents clients. Bibliothèque Honorium (354 diligences, 50 modèles de dossiers) livrée comme hypothèses du cabinet, importable. Exemple de référence de l’audit retrouvé (1 200 € HT, coût 600 €, provision 360 €). | `honoraires5653`, `agent/data/honorium-diligences-2026-06-26.json` |
| H01 / H02 / H03 | **Corrigés** dans AxiorHub (fiscalité explicite, `None` ≠ zéro, gratuité motivée, provision au centime) **et dans Honorium V5.1.7** livrée à part. | `fiscal5653`, `honoraires5653` ; `test_v5653.FeeEngine/Fiscal` |
| Modèles (§ 10) | **Fait.** Les huit modèles Word de l’audit (facture, devis, lettre de mission, conventions forfait et temps passé, conditions générales, annexe consommateur, avoir) ; variables résolues par le serveur ; variable absente = « à compléter », bloquante pour l’approbation ; ligne de tableau répétée par ligne ; import de modèles Word contrôlé (macros, objets incorporés, liens externes, taille) ; logo PNG ou JPEG contrôlé ; original conservé ; choix d’un modèle par type. **Non fait :** variante de modèle par dossier, diff de la modification utilisateur. | `modeles5653` |
| Registre unique (§ 8) | **Fait.** Cabinet émetteur (identité, régime de TVA choisi, catégorie, conditions, outil de numérotation, trajet), client cocontractant (qualité, identifiants vérifiés, pas de SIREN fabriqué), documents et versions, approbations sur empreinte, séquences par série et année, réservations anti-double facturation (temps validés, provisions, soldes ; Invoice Ninja pris en compte), événements, règlements, propositions. Une facture émise n’est jamais modifiée (avoir lié). | `facturation5653` |
| Calcul et mentions (§ 5) | **Fait pour le périmètre honoraires / frais refacturés.** Lignes, ventilation de TVA par taux, net à payer après provisions imputées (sans seconde TVA), mentions socle, clause de retard selon la qualité du client (40 € seulement entre professionnels, taux de référence non inventé), TVA sur les débits, catégorie d’opération. **Bloqué :** débours (à qualifier, article 267), Guyane et Mayotte, aide juridictionnelle et rémunérations réglementées. | `fiscal5653`, `facturation5653.check` |
| Numérotation | **Fait.** Numéro attribué à l’émission seulement, sous verrou, dans la transaction d’émission ; unicité (entité, série, numéro) ; clé d’idempotence d’émission ; numéro jamais réutilisé ; publication des fichiers reprise si elle échoue. Aperçus et brouillons sans numéro fiscal. Outil de référence externe (Invoice Ninja, Pennylane) : AxiorHub ne délivre pas de second numéro. | `facturation5653.issue/publish` |
| Dossier FACTURATION (demande de l’avocat) | **Fait.** Sous-dossier par défaut de chaque dossier client (créé sur demande ou à la première émission), nom réglable ; factures, avoirs, devis, conventions, documents signés et preuves y sont rangés ; tout document qui s’y trouve est classé « gestion du cabinet », jamais parmi les pièces numérotées. | `dossiers5632`, `docrequest520`, `modele5651`, `facturation5653.billing_folder` |
| Page « Facturation et honoraires » (§ 4) | **Fait.** Dans « Toutes les fonctions » et le menu complémentaire, épinglable au menu principal ; recherche ; « Créer » ; vues À préparer, Documents, Règlements, Chiffrer une mission, Réglages et modèles ; éditeur avec aperçu à côté (dessous sur mobile), contrôles en langage clair, approbation et émission ; bloc « Honoraires et facturation » dans l’onglet Travaux du dossier (lecture partielle signalée). **Non fait :** redirection des anciennes pages (le suivi des temps reste dans Cabinet › Temps et honoraires). | `web5653`, `vue5651`, `shell501` |

## Lot 3 — facturation électronique

| Exigence | État 5.6.53 |
|---|---|
| Factur-X / CII et UBL | **Produits** depuis le même instantané figé que le rendu lisible : XML CII profil EN 16931 (type 380, 386 provision, 381 avoir), UBL 2.1 Invoice ou CreditNote par un adaptateur distinct ; PDF/A-3b par LibreOffice avec `factur-x.xml` joint (relation Alternative, métadonnées XMP Factur-X), relu après écriture. |
| Contrôles | **Internes et explicites** : XML bien formé, BR-CO-10/13/14/15/16, TVA par taux, motif d’exonération, parties et SIREN, règle française du SIREN acheteur, concordance avec l’instantané et avec le rendu lisible, absence de termes confidentiels. **Non exécutés ici :** XSD officielle, Schematron EN 16931 complet et règles de la plateforme, validation PDF/A (veraPDF), acceptation en bac à sable. |
| Secret professionnel | **Fait par défaut** (profil « Avocat — secret professionnel ») : libellés génériques dans le XML, l’UBL et le PDF structuré ; détail réservé au client ; test de non-divulgation (nom du dossier, adversaires). **À obtenir :** mapping exact du cas d’usage 36 AFNOR dans la version et chez la plateforme retenues. |
| Transmission | **Un seul trajet** par facture, préparé comme opération et validé sur sa fiche (U01) ; SuperPDP avec Factur-X ; B2Brouter et Chorus Pro bloqués tant que leurs charges utiles ne sont pas qualifiées ; statuts distants projetés sur la facture (un dépôt n’est pas un règlement). **Non fait :** e-reporting et reporting des encaissements. |
| gofact (§ 7) | **Non intégré** (licence AGPL compatible, mais génération HTML→PDF séparée des montants et numérotation autonome) : AxiorHub produit ses propres CII/UBL depuis le registre ; comparaison avec gofact à faire en prototype isolé. |

## Lot 4 — proactivité

**En partie.** Propositions « À préparer » avec déclencheur, source et un seul bouton : mission sans convention, temps validés non
facturés, provision prévue par une convention signée, budget presque atteint ; clé unique (dossier, événement, version) ; refus et
report durables ; présentes dans la file unique. **Non fait :** détection d’un accord par courriel, des phases accomplies, mission
complète « Prendre en charge » pour la facturation, mesure des gains, jeu de 50 situations d’évaluation.

## Honorium V5.1.7 (livrée à part)

H01 : régime de TVA par dossier ; mention 293 B seulement si la franchise est choisie ; zéro non qualifié signalé. H02 : forfait vide =
à confirmer, zéro explicite conservé, « mission gratuite / remise totale » motivée, migration des anciens zéros. H03 : provision au
centime. Contre-tests de l’audit : 2/2 passent (0/2 sur V5.1.6).

## Non vérifié ici

Installation du cabinet ; ouverture des fichiers Word et PDF/A dans les outils du cabinet ; validation XSD, Schematron, PDF/A et
acceptation par une plateforme agréée ; qualification des connecteurs réels ; exactitude juridique des modèles pour chaque situation
(à relire par l’avocat avant usage client) ; calendrier d’obligation selon la catégorie réelle du cabinet.
