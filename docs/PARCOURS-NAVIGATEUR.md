# Parcours navigateur (5.6.94)

`scripts/parcours-navigateur.py` joue des parcours de bout en bout dans un vrai navigateur, contre l'application telle qu'elle est servie en production : `agent.web.App` derrière `waitress`, page de connexion de la version serveur, mêmes en-têtes de sécurité (CSP comprise). C'est un **outil de vérification** : il tourne sur le poste de développement et dans la recette GitHub (travail `navigateur`), jamais sur le serveur du cabinet, et n'entre dans aucun paquet.

## Ce que le banc prépare

- Un dossier d'état temporaire, supprimé à la fin de chaque parcours : dossiers fictifs DOS-001 à DOS-003 (DURAND, MARTIN, ZORVAL), stockage local à la place de Nextcloud, aucune messagerie.
- Le compte administrateur de la version serveur, créé au lancement : identifiant et mot de passe aléatoires, gardés en mémoire. Seule l'empreinte scrypt est écrite ; rien n'est affiché ; les captures masquent le bloc du compte et les champs de connexion.
- Un faux fournisseur de modèle : petit serveur sur `127.0.0.1` qui parle comme Ollama, compte ses appels et peut simuler une panne (HTTP 503).
- Aucun travailleur de fond : le banc exécute lui-même la file (`Desk.work_once`) au moment voulu par le parcours. C'est ce qui permet de vérifier qu'aucun appel au modèle ne part pendant une pause.
- Le serveur écoute sur `127.0.0.1` et un port libre. Toute requête du navigateur vers une autre adresse est bloquée et fait échouer le parcours.

## Parcours

| Id | Parcours | Résultat vérifié |
|----|----------|------------------|
| P02a | Connexion puis déconnexion | Cookie de session posé ; après « Se déconnecter », l'ancien jeton n'ouvre plus aucune page (session close sur le serveur). |
| P02b | Pause de l'IA (`#a92-pause`, en-tête d'Aujourd'hui) | Pause enregistrée (`missions567:pause`) et affichée après rechargement ; une demande faite pendant la pause attend ; la file passe trois fois sans aucun appel au modèle ; après la reprise, un seul appel et la proposition aboutit. |
| P02c | Annulation d'une demande | « Arrêter ce travail » dans Aujourd'hui ; après rechargement, travail et demande restent annulés, sans appel au modèle ; « Relancer dans une nouvelle opération » crée une demande distincte, l'ancienne reste annulée. |
| P02d | Erreur simulée du fournisseur | Une panne : un seul appel, demande en échec, aucune rafale ; un double clic sur « Relancer » donne une seule nouvelle demande et un seul nouvel essai (deux appels au total). |
| P02e | Facture en brouillon | Page Facturation › Nouveau document : une facture du dossier DOS-001, état brouillon, sans numéro, sans émission, approbation ni envoi ; visible après rechargement. |
| P02f | Document du registre | Ouvert depuis Documents préparés, puis « Télécharger » : octets identiques (SHA-256) au fichier du dossier. |
| P02g | Tour du menu | Chaque page du menu latéral et de « Toutes les fonctions » répond 200, sans erreur JavaScript, sans violation de la CSP, sans requête externe. |
| P02h | Charge du serveur, Voix | La section Charge du serveur affiche des mesures ; la page Voix se charge. Si `agent/static/brindille/` existe, un essai d'écoute Brindille doit produire un son non vide, sans réseau externe ; sinon cette partie est ignorée avec un message. |

## Lancer le banc en local

Installer Playwright dans un environnement virtuel **hors de l'arbre** (exemple sous Windows, Edge déjà installé : aucun navigateur à télécharger) :

```
python -m venv %TEMP%\axh-parcours-venv
%TEMP%\axh-parcours-venv\Scripts\python -m pip install playwright==1.63.0 waitress==3.0.2 cryptography==44.0.0 tzdata==2024.2
%TEMP%\axh-parcours-venv\Scripts\python scripts\parcours-navigateur.py --navigateur msedge
```

Sous Linux : même environnement virtuel, puis `python -m playwright install --with-deps chromium` et `--navigateur chromium` (valeur par défaut hors Windows). Image Docker possible : `mcr.microsoft.com/playwright/python:v1.63.0-noble`.

Options : `--parcours P02b,P02c` (quelques parcours), `--sortie DOSSIER` (par défaut `axiorhub-parcours` dans le dossier temporaire), `--voir` (navigateur visible).

Dans la suite `unittest`, `tests/test_v5694_parcours.py` vérifie le banc sans navigateur ; le parcours complet ne s'y joue qu'avec Playwright installé et `AXIORHUB_PARCOURS=1`.

## Lire un échec

- Code de sortie : `0` tout réussi, `1` au premier échec (les parcours suivants ne sont pas joués), `2` Playwright absent.
- La ligne `ÉCHEC :` donne le résultat métier attendu et ce qui a été constaté (par exemple « le modèle a été appelé pendant la pause (1 appel(s)) »).
- Dans le dossier de sortie : `P02x-echec.png` (page entière au moment de l'échec, compte masqué), `P02x-console.log` (messages de la console, requêtes externes bloquées, motif), `resultats.json` (parcours joués, résultat, durée).
- Une violation de la CSP apparaît dans la console sous la forme « violates the following Content Security Policy directive » : le plus souvent un gestionnaire en ligne (`onclick=`, `onchange=`) ou un `<script>` sans fichier. La règle du projet : pas de JS en ligne, scripts en `defer`.
- Dans la recette GitHub, ces fichiers sont dans l'artefact `parcours-navigateur`, publié seulement en cas d'échec.
