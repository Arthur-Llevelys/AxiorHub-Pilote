# Conformité de la 5.6.56 à l'audit 5.6.55

Audit du 17 octobre 2026 : 27 anomalies (N01 à N27), deux campagnes de contre-tests (33 tests Python et un test JavaScript) et une
feuille de route (section 15, évolutions A01 à A10).

La 5.6.56 livre le **lot 0 — stabilisation** recommandé par l'audit (§ 15.14) :

- les 27 anomalies ;
- les migrations correspondantes ;
- les corrections d'interface des sections 3 à 8.

Les évolutions A01 à A10 sont des spécifications. Elles ne sont pas commencées dans cette version (voir la fin de ce document).

Les contre-tests de l'audit font désormais partie de la suite :

- `tests/test_contre5655.py` : 17 tests ;
- `tests/test_contre5655b.py` : 16 tests ;
- `tests/navigation5655.cjs` : test JavaScript, exécuté par `tests/test_v5656.py` lorsque Node.js est présent.

Ils réussissent tous.

## Anomalies N01 à N27

| Réf. | État | Correction |
|---|---|---|
| N01 | Fait | Connexion depuis la racine : retour à Aujourd'hui ; les liens profonds sont conservés. |
| N02 | Fait | Aujourd'hui : routines, blocs du jour, veille sur toute la largeur, puis file unique en bas, dans le même conteneur. |
| N03 | Fait | Registre unique des identités du cabinet (`mailbox.own_addresses` = `sens5643.identities`) pour la proactivité. |
| N04 | Fait | Même registre pour les propositions de correspondants. |
| N05 | Fait | Même registre pour l'association à un dossier (« adresse_du_cabinet »). |
| N06 | Fait | Ancien profil de veille matérialisé une fois et réellement planifié ; une veille désactivée explicitement n'est pas réactivée. |
| N07 | Fait | Passage de veille « en attente » sans travail réconcilié ; « Actualiser » ne rejoint qu'un travail vivant et répare un passage orphelin. |
| N08 | Fait | Import CRM : tous les rôles canoniques des correspondants convertis ; les anciens « Autre » non corrigés sont reclassés. |
| N09 | Fait | Six formulaires de facturation ouvrent l'objet créé ; `v500.js` suit en outre tout `goto` validé (contrat commun). |
| N10 | Fait | Qualité du client modifiée : blocage avec montants avant/après ; les brouillons du dossier sont recalculés après enregistrement du client. |
| N11 | Fait | Régime du cabinet modifié : blocage « Enregistrer et recalculer » avec TVA et TTC après recalcul ; aucun montant réécrit en silence. |
| N12 | Fait | IBAN : longueur du pays et clé MOD 97 ; un IBAN invalide déjà enregistré bloque les brouillons ; SIREN et SIRET : clé de Luhn (avertissement). |
| N13 | Fait | Intention réelle (réponse au fond, demande de précision, relance, accusé, rendez-vous) transmise à l'identique à la rédaction et au contrôle ; « répond à la question » vérifié. |
| N14 | Fait | Rédaction de documents : contexte structuré du dossier (décisions, arguments abandonnés, parties, procédure) et consignes permanentes. |
| N15 | Fait | Un contact relié à un dossier masqué au lecteur lui est soustrait (fiche, liste, modification, archivage). Le cloisonnement de la requête est effacé à la fin de chaque réponse. |
| N16 | Fait | Émission : ligne relue sous verrou ; état, version et empreinte approuvée exigés dans l'UPDATE ; un abandon ou une modification concurrente l'emportent. |
| N17 | Fait | Paquet d'émission figé (émetteur, client, conditions, profil fiscal, modèle). Le Word, le PDF, l'aperçu et le XML en dérivent ; les octets originaux sont conservés. |
| N18 | Fait | Cumul des avoirs émis contrôlé à la préparation, au contrôle et sous le verrou d'émission (HT et TTC). |
| N19 | Fait | Toute provision émise est imputée au solde, réglée ou non (avoirs déduits). |
| N20 | Fait | Agrégats sur tous les documents ; la liste reste paginée. |
| N21 | Fait | Même référence avec un autre contenu : conflit explicite ; répétition identique : aucun nouvel événement ; clé de requête pour les règlements sans référence. |
| N22 | Fait | Homonymes avec des courriels différents jamais fusionnés. |
| N23 | Fait | Fiche et lien validés avant écriture, puis enregistrés ensemble. |
| N24 | Fait | Chaque fichier publié est relu et comparé à son empreinte. Un fichier modifié donne l'état « conflit », sans écrasement, avec une copie de l'original émis disponible ; un fichier absent est redéposé à l'identique. |
| N25 | Fait | Dates du calendrier réelles (règlements, dates des brouillons) ; les dates impossibles déjà enregistrées sont signalées. |
| N26 | Fait | Tableau de bord : documents et règlements limités aux dossiers visibles du lecteur. |
| N27 | Fait | `executescript` n'effectue plus de validation implicite dans une transaction (connexion de l'agent) : la numérotation est annulée avec l'émission. |

## Autres points des sections 3 à 9

| Point | État |
|---|---|
| § 3.1 « Vos adresses reconnues par l'agent » | Fait : Paramètres › Messagerie, avec la provenance et un test de classification d'après les en-têtes. |
| § 3.2 Propositions visant le cabinet | Fait : écartées (« ecartee_cabinet ») au démarrage, historique conservé. |
| § 5.3 Veille trop étroite | Fait : pleine largeur. |
| § 6.3 États de la veille | Fait : « en file », « en cours », « mise en file refusée », en plus des états existants. |
| § 7.2 Choix d'une entreprise | Fait : candidats affichés et bouton « Utiliser cette entreprise ». La fiche est remplie sans être enregistrée ; la provenance est conservée. |
| § 7.5 Aperçu | En partie : renommé « Aperçu textuel — le Word et le PDF font foi ». La prévisualisation PDF depuis le DOCX et les nouveaux gabarits sont à réaliser. |
| § 7.6 États séparés | Fait : calculs, données, format structuré, fichiers et transmission affichés séparément. |
| § 8.1 Filtre CRM vide | Fait : « 0 résultat pour ce filtre (N au total) » et « Réinitialiser les filtres ». |
| § 8.3 Collecte IMAP vers le CRM | Non commencé : il faut un collecteur incrémental (UID/UIDVALIDITY). |
| § 9 Vue de diagnostic transversale | Non commencé : l'« État du logiciel » existant reste la référence. |
| § 4.4 États métier des travaux | Non commencé : il faut une évolution A01 (missions). |

## Feuille de route A01 à A10 (section 15)

Non commencée. L'audit recommande de terminer d'abord la stabilisation (lot 0, livré ici), puis trois parcours de bout en bout (lot 1).
