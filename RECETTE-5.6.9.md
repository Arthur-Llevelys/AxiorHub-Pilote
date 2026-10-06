# AxiorHub Pilote 5.6.9 — couverture et recette

Date : 6 octobre 2026. Base : archive cumulative 5.6.8. La recette 5.6.8 ([RECETTE-5.6.8.md](RECETTE-5.6.8.md)) reste valable pour
les fonctions qu'elle décrit ; ce document couvre ce que la 5.6.9 ajoute ou corrige.

## 1. Audit de l'interface 5.6.8 (aperçu local, données fictives)

| Constat | Correction 5.6.9 |
|---|---|
| Requête `/favicon.ico` en erreur 400 sur chaque page | servie (icône AxiorHub) |
| Sur téléphone, menu latéral complet en tête de chaque page | replié par défaut sous 850 px ; choix mémorisé |
| Profil lu deux fois par page (deux scripts) | une lecture partagée |
| Cinq relectures périodiques de 10 à 20 s par onglet | 45–60 s quand le flux en direct est connecté (il déclenche déjà les mises à jour) |
| Lectures de fichiers d'état sans encodage (relance automatique, configuration, santé, agents, authentification) | UTF-8 explicite ; configuration héritée tolérée |
| `install-interface.py` non importable hors Linux (`pwd`) | import facultatif ; les tests passent partout |

Toutes les pages répondent 200 ; aucune erreur JavaScript ni trace serveur dans l'aperçu.

## 2. Tests automatisés ajoutés (tests/test_v569.py)

- Régime économe : activé par défaut, intervalles espacés, quota journalier qui reporte les suites automatiques mais jamais une demande
  explicite, second modèle sauté pour les préparations internes (consulté si un contrôleur est fourni), jetons Ollama comptés par
  fonction, profil « plus petit modèle », planificateur respectant les intervalles.
- Interface : /favicon.ico servi, menu mobile replié par défaut, profil lu une fois, plus de relecture fixe rapide, flux exposé.
- Portabilité : aucun `read_text()` sans encodage dans les modules concernés.
- Règles documentaires : sous-dossier fermé (PIECES accepté, `../ailleurs` refusé), dossiers plausibles conservés et proposés, bloc
  « À décider » sur Aujourd'hui, décision qui relance l'exécution, rien d'affiché quand il n'y a rien à décider.
- Accueil téléphonique : conversation en deux questions, motif fermé, propos cités comme données (une injection dans la transcription
  reste du texte), une seule tâche par appel, signature des étapes, paramètres d'adresse restreints, repli par touches sans consentement.
- Voix : lecture phrase par phrase, tours plus courts, capacité exposée.
- Invoice Ninja : facture en brouillon créée une fois et relue, aucun paramètre d'envoi, consentement / lien client / réglage requis,
  écriture incertaine jamais rejouée, temps transmis en `time_log` Unix avec taux.
- Mise en service : rapport couvrant services, workers, IMAP, Envoyés, Nextcloud, Ollama, transcription, synthèse, Invoice Ninja,
  Google, Talk, accueil, régime économe ; texte en ligne de commande ; page et entrée Outils.

Résultat de la régression complète depuis l'archive : voir `VALIDATION-5.6.9.json` (exécution Windows de livraison ; les échecs
listés sont les mêmes particularités Windows que pour la 5.6.8, et l'intégration continue GitHub exécute la suite sous Linux).

## 3. Recette sur les services réels (à faire sur le serveur)

1. Mise en service : lancer les contrôles ; chaque ligne doit être OK ou « non activé » ; tester le micro en HTTPS.
2. Régime économe : après 24 h, le tableau de consommation doit montrer des générations locales par fonction ; la file de travail ne
   doit plus accumuler de « Tests métier continus » ni d'« Actualisation du pilotage » toutes les 30 min.
3. « À décider » : déposer dans un dossier d'arrivée un avis dont le dossier est ambigu ; choisir le dossier suggéré ; l'exécution
   reprend et classe dans le sous-dossier de la règle.
4. Voix : vérifier la lecture progressive et l'interruption par la parole sur votre navigateur ; mesurer la latence du premier mot.
5. Téléphone : appel réel ; vérifier la tâche, l'absence de réponse juridique, la facturation Twilio de la reconnaissance vocale.
6. Invoice Ninja : brouillon sur un dossier de test ; vérifier le statut « brouillon » et l'absence d'envoi ; transmettre un temps.

## 4. Limites explicites

- Pas de duplex vocal intégral (tours successifs).
- Accueil téléphonique administratif seulement.
- Invoice Ninja : brouillons et temps ; pas d'envoi, de relance ni d'encaissement.
- Règles documentaires : ensemble fermé d'actions ; propositions à confirmer pour les délais non prouvés.
