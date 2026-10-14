# Conformité 5.6.43 — cahier des charges v9 (IA, courriels, agendas, ergonomie et navigation)

Version de référence : 5.6.42. Demande de l’avocat du 14 octobre 2026 : générer une version 5.6.43 (version serveur, ExApp, Windows
et poste Ubuntu) sur la base du cahier des charges version 9 et du dossier développeur joint. Le cahier couvre 31 sections ; cette
version livre les exigences prioritaires (16 à 22) et le classement des courriels (10 à 14). Les sections 23 à 30 (mémoire du
dossier, recherche hybride, profils de modèles, évaluation sur 50 cas) sont **reportées** : elles demandent une mesure sur
l’installation réelle et un corpus d’évaluation qui ne peuvent pas être produits ici.

| Section | Exigence | État 5.6.43 | Où / recette |
|---|---|---|---|
| 16 | Pause et annulation : contrôleur unique, transitions atomiques, interruption du modèle, reprise | **Fait.** États `suspended` et `cancel_requested`, reprise des seuls travaux suspendus par la pause, appel au modèle coupé dans la seconde, travaux enfants arrêtés, planificateurs retenus, travaux sans IA maintenus. | `agent/arret5643.py`, `desk.py`, `pause5629.py`, `model.py` ; tests `PauseAnnulationContreTests`, `PauseCourrielsContreTests` (R01 à R15), `PauseReprenable`, `InferenceInterruptible`, `PauseRoute`. |
| 11.3 | Pause et arrêt du classement des courriels | **Fait.** « Arrêter » sur le bloc ; fichiers déjà vérifiés conservés ; dépôt incertain revérifié avant toute nouvelle copie. | `correspondances5643`, `web5643`. |
| 21 | Courriels du cabinet : garde commune, aucune réponse à soi-même, rédaction avec le dossier | **Fait.** Garde dans `make_draft` (tous les circuits), sens par message, routine du matin vers `prepare_reply`, « À COMPLÉTER » signalé. | `agent/sens5643.py`, `mailbox.py`, `routines520.py`, `intelligence.py` ; tests `CourrielsDuCabinet`. |
| 7.1 à 7.3 | Vraie consigne, sélection des compétences par phase, budget unifié | **Fait.** | `agent/competences5643.py`, `competences5632.py`, `model.py` ; tests `CompetencesParPhase`. |
| 3 | Cinq compétences ciblées | **Fait** (méthodes fournies, désactivées par défaut, à activer et évaluer par le cabinet). | `resources/skills/methode-*`. |
| 7.4, 1, 2, 4, 5, 6, 8, 9 | Choix de produire, étapes vérifiables, dossier de contexte, corrections du cabinet, paramètres par phase, mesure | **En partie / reporté** : la sélection par phase est en place ; le dossier de contexte structuré, les paramètres par phase et la mesure relèvent des sections 24 à 30. | — |
| 10 | Conservation des courriels dans CORRESPONDANCES | **Fait.** Octets IMAP (`BODY.PEEK[]`, contrôle `UIDVALIDITY`), `.eml` d’origine, lecture `.md`, arborescence `AAAA-MM/…`, taille bornée, images intégrées. | `agent/correspondances5643.py`, `mailbox.fetch_raw`, `dav.put_archive`/`read_archive`. |
| 11 | Modes de classement, champs modifiables, autorisations générale ou par courriel, pièces jointes | **Fait** (assisté par défaut, règle du compte, règle du message, mandat à l’IA, classement automatique des nouveaux messages d’un correspondant confirmé). Restent : réaffectation à un autre dossier et retrait d’un classement. | idem ; tests `Correspondances`. |
| 12 | Interfaces Courriels et Dossier | **Fait.** Bloc du courriel, onglet « Correspondances » (liste filtrable, lecture, original, notes). Reste : rendu PDF de la lecture. | `static/v5643.js`, `dossier5627.py`. |
| 13 | Architecture, données (8 tables), API, transaction et reprise, concurrence, indexation | **Fait** (migration 0644, API `m5643/correspondances/…`, dépôt exclusif, relecture par empreinte, reprise idempotente, représentations secondaires non indexées). Reste : registre générique des opérations. | `migrations/0644_correspondances.sql`, `web5643.py`, `index.py`. |
| 14 | Recette, migration sans changement implicite | **Fait** pour les tests obligatoires reproductibles hors serveur de messagerie réel ; aucun classement automatique sans choix explicite. Reste : outil de reprise de l’historique. | tests `Correspondances`. |
| 17 | Agenda en ligne et Agenda personnel | **Fait.** Deux sous-vues, préférence par utilisateur, aucune copie de l’Agenda personnel, anciens événements copiés à répartir, onglet actif visible. | `agenda5634.py`, `agenda5643.py`, `agenda36.py`, `workplan.py` ; tests `AgendaPersonnelJamaisCopie`, `AgendaSousVues`. |
| 18 | Produire et interface : lisibilité | **Fait.** Nom du dossier, états en français, dates lisibles, texte de base 16 px, boutons de 44 px, contrastes vérifiés dans les deux thèmes, aucun défilement horizontal à 320 px. | `production420_ui.py`, `v420.css`, `style.css` ; tests `ProduireLisible`. |
| 19 | Playbooks | **Fait.** Reprise d’étape, arrêt du parcours, détail affiché d’abord, états et noms métier, réservation atomique, acteur vérifié. | `operating380.py`, `operating380_ui.py` ; tests `Playbooks`. |
| 20 | Navigation sans doublons | **Fait.** Registre `navigation5643` (principaux, conditionnel, menu complémentaire, parents, masqués), alias et redirections conservés. | `navigation5643.py`, `fonctions5631.py`, `shell501.py` ; tests `NavigationSansDoublons`. |
| 22 | Page Talk facultative | **Fait.** Réglage par utilisateur, adresse limitée à l’instance Nextcloud configurée, politique `frame-src` et `Permissions-Policy` sur cette seule page, test d’affichage. | `talk5643.py`, `web5636.py`, `parametres5621.py` ; tests `PageTalk`. |
| 23 à 30 | Mémoire du dossier, recherche hybride, profils de modèles, pipeline commun, intégrations, évaluation sur 50 cas | **Reporté** à une version ultérieure. | — |
| 31 | Implémentation, migrations, livraison | **Fait** pour cette version : migrations 0643 et 0644, chaîne de mise à jour, liste de livraison. | `RELEASE-FILES.json`. |

Versions livrées : serveur et postes Windows et Ubuntu (moteur 5.6.43), application Nextcloud (inchangée, 0.4.3), ExApp 0.1.2
(moteur 5.6.43).

Recette native à mener sur l’installation réelle (non remplaçable par la suite automatique) : coupure d’un appel Ollama réel par
« Pause de l’IA », classement d’un courriel sur le serveur IMAP du cabinet, affichage de Talk dans le navigateur du cabinet,
installation de l’ExApp par HaRP.
