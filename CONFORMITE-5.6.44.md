# Conformité 5.6.44 — audit 5.6.43 et demandes du 14 octobre 2026

Version de référence : 5.6.43. Demandes de l’avocat du 14 octobre 2026 : démarrage tolérant au verrou de la base, test d’affichage
de Talk corrigé, corrections des bugs de l’audit « AxiorHub Pilote 5.6.43 », et correction dans la page du dossier des informations
erronées de l’IA (par exemple des parties en double).

## Demandes de l’avocat

| Demande | État 5.6.44 | Où / recette |
|---|---|---|
| Démarrage tolérant au verrou (journal du 14 octobre : `database is locked` à la préparation, services relancés en boucle) | **Fait.** Préparation reprise après attente croissante jusqu’à 5 minutes (services) ; lignes « base de données occupée… nouvel essai » puis « préparation terminée » ; verrou de fichier relâché pendant l’attente ; écritures de démarrage seulement si nécessaires. | `agent/desk.py` (`_prepare_waiting`), `operating380.py`, `hybrid400.py` ; tests `DemarrageVerrou` (aucune écriture sans changement avec un verrou tenu ; attente puis succès ; échec expliqué au-delà du délai). |
| Test de Talk : `frame-ancestors` l’emporte sur `X-Frame-Options` | **Fait.** Toutes les politiques reçues, origine d’AxiorHub lue dans l’en-tête `Origin`, sources `'self'`, schéma, joker de sous-domaine et port. | `agent/talk5643.py` (`framing_verdict`) ; tests `TalkRegleNavigateurs`. |
| Page du dossier : ajouter, modifier ou supprimer les informations erronées de l’IA (parties en double) | **Fait** pour les parties, la juridiction et le numéro de RG (carte Synthèse, onglets Parties et Juridiction et échéances) ; les faits restent corrigeables dans « Faits et procédure ». | `agent/dossier5644.py`, `web5644.py`, migration 0646 ; tests `CorrectionsDuDossier`. |

## Audit 5.6.43

| Fiche | Priorité | État 5.6.44 | Recette |
|---|---|---|---|
| F01 Arrêt incomplet après les en-têtes | P0 | **Corrigé.** Socket retenue dès la connexion, fermée sur arrêt ; arrêt demandé avant l’ouverture = connexion refermée dès son ouverture ; isolement par appel ; journal distinguant transport fermé et arrêt non confirmé chez le fournisseur ; message de pause sans promesse de durée. | Contre-test F01 ; arrêt avant connexion ; deux appels simultanés. Essai Ollama réel : à mener. |
| F02 Absence de PJ bloquant le courriel | P1 | **Corrigé.** Actions indépendantes, bilan « aucune pièce jointe » ; demande PJ seule toujours refusée explicitement. | Contre-test F02 ; PJ seule. |
| F03 Travaux de classement sans dossier | P0 | **Corrigé.** Dossier dans les arguments, travaux existants rattachés (une fois), pause du dossier relue sur le classement avant chaque dépôt automatique. | Contre-test F03. |
| F04 Autorisation retirée non recontrôlée | P0 | **Corrigé.** Garde commune avant chaque dépôt automatique : politique actuelle, exception du message (`allow` subsiste, `deny` bloque), dossier du mandat ; commande manuelle hors de cette garde. | Contre-test F04 ; `allow` ; `deny` ; commande manuelle. |
| F05 Lot des 30 derniers messages | P1 | **Corrigé.** Curseur par compte et boîte, UID croissants, messages différés (erreur, dossier en pause ou à préciser) repris plus tard, remise à zéro sur changement d’UIDVALIDITY, rien consommé pendant la pause. | Contre-test F05 ; UIDVALIDITY ; dossier en pause ; pause. |
| F06 Mandat pendant la pause jamais exécuté | P1 | **Corrigé.** Mandat durable (migration 0645) avec actions et sélection exactes, exécuté une fois à la reprise, retiré par une exclusion ; sélection enregistrée dans la règle du message. | Contre-test F06 ; sélection d’une pièce ; exclusion. |
| F07 Classement annulé non relançable | P0 | **Corrigé.** Rejoindre seulement un travail actif ; état réconcilié (confirmation, route « Arrêter », affichage). | Contre-test F07 ; double clic ; état affiché. |
| F08 Dérivés non versionnés | P1 | **Corrigé.** Original immuable ; dérivés versionnés à un nouveau chemin, version précédente « remplacée » après vérification ; date du manifeste stable ; lecture partielle régénérée par une nouvelle commande ; une seule lecture indexée. | Contre-test F08 ; exécutions identiques ; version 2 ; régénération. |
| F09 Pause active devenue annulation | P1 | **Corrigé.** Le motif de pause donne une suspension reprenable ; reprise avant acquittement : la demande d’arrêt est retirée ; reprise pendant l’interruption : remise en file sans second clic. | Contre-test F09 ; deux courses de reprise. |
| F10 Arrêter pendant une pause | P0 | **Corrigé.** Promotion atomique en annulation définitive (motif « user »), descendants compris ; la reprise ne relance pas. | Contre-test F10 ; pause, arrêt, reprise. |

Reste à mener sur l’installation réelle (non remplaçable par la suite) : coupure d’un appel Ollama réel, classement sur le serveur
IMAP du cabinet, redémarrage des services pendant une écriture longue, affichage de Talk, installation de l’ExApp par HaRP.
Les sections 23 à 30 du cahier v9 restent reportées (voir `CONFORMITE-5.6.43.md`).
