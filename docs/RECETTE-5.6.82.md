# Recette de la 5.6.82 : état ligne par ligne

Base : cahier des charges du 31 octobre 2026, section 12. Tests automatisés : `tests/test_v5682.py`, avec réponses de fournisseur simulées. Le cahier exige aussi un essai avec une vraie connexion ChatGPT : il reste à faire.

| ID | État | Preuve ou motif |
|---|---|---|
| RT01–RT07 | Testé | `Selection` |
| RT08 | Partiel | Catalogue et connexion isolés par compte (CG05). Le cache de réponses de la 5.6.80 n'a pas été revu. |
| CG01–CG11, CG13, CG14 | Testé | `AbonnementStrict` |
| CG12 | Testé en simulation | `Liaison` ; transfert réel poste vers serveur non essayé |
| UX01 | Testé | Recherche approchée, dossier réservé écarté |
| UX02 | Partiel | Filtres et position repris au retour arrière ; essai navigateur sur la seule page Paramètres |
| UX03 | Vérifié en navigateur | Lecteur PDF.js dans l'aperçu, ouverture à la page citée, repli sur le lecteur du navigateur |
| UX04, UX09, UX10 | Existant | Comportements antérieurs, non retestés ici |
| UX05, UX06 | Partiel | Page Modèles d'IA vérifiée à 390 px et en sombre ; pas de passage complet au zoom 200 % ni sur toutes les pages |
| UX07, UX08 | Testé en partie | Bordereau : glisser-déposer, Monter, Descendre, Annuler ; version contrôlée à l'enregistrement (conflit expliqué) |
| QL01–QL03 | Existant | Comportements antérieurs, non retestés ici |
| QL04 | Non livré | Reclassement documentaire reporté |
| OP01–OP03 | Existant ou partiel | Pas de nouveau superviseur de durée ; budget de mission conservé après redémarrage (testé) |

## Décisions techniques

- **Base graphique** : Carbon retenu, en une seule base. Composants Carbon pour les états et notifications (`carbon5682.js`, chargé à la demande) ; échelle typographique et formes Carbon sur tout le logiciel (`v5682.css`). Les formulaires restent natifs : noms de champs, contrôles serveur et protection CSRF inchangés.
- **Fuse.js** : remplacé par un classement côté serveur. Les droits sont appliqués avant le calcul et aucun index ne part vers le navigateur.
- **PDF.js** : intégré au volet de lecture, redimensionnable à la souris et au clavier.
- **vis-timeline et Pragmatic drag and drop** : remplacés par une frise et un glisser-déposer écrits pour AxiorHub, sans dépendance. La liste de chronologie reste la vue accessible et la seule sur téléphone. Une date au mois ou à l'année s'affiche en plage, jamais sur un jour.
- **Reranker, Haystack, vLLM Semantic Router, RouteLLM, Carbon AI Chat, Ragas, OpenTelemetry** : non intégrés. Lot E et priorité P2 : optionnels, ou à décider sur mesure.
- **Une seule connexion ChatGPT par installation** : la séparation par membre du cabinet reste à faire avant un usage partagé.

## Audit des appels automatiques (31 octobre 2026)

Lecture du code, tâche par tâche, depuis le planificateur. Aucune mesure sur une installation réelle.

- Sans élément nouveau : aucun appel. Santé, tests continus, indexation, surveillance, tableau de bord et pilotage ne génèrent aucun texte.
- Coût réel : la cascade d'un élément nouveau (tri, réponse, contrôle, projet).
- Non modifié : le contrôle de chaque brouillon avant dépôt reste en place, car le texte vise un tiers. La routine orale du matin garde son appel quotidien.

## Reste à constituer par le cabinet

Le banc de cinquante cas exige des résultats attendus revus par un avocat. Il n'existe pas encore : les choix de modèle sont marqués « provisoire » tant qu'aucune mesure ne les confirme.
