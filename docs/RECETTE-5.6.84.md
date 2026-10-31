# Recette de la 5.6.84 : état ligne par ligne

Référence : cahier des charges AxiorHub Avocat 5.6.84 et son annexe technique. 61 scénarios.

États :

- **Automatique** : couvert par `tests/test_v5684.py` ou `tests/core5684.cjs`, données fictives, modèle simulé.
- **Réel** : observé sur le vrai éditeur EuroOffice (9.3.7, compilation 60, conteneur local) à travers le serveur AxiorHub de développement, documents fictifs.
- **Partiel** : la partie serveur est prouvée, une partie réelle ne l'est pas ; le motif est indiqué.
- **Non essayé** : aucune preuve ici.

Aucun essai n'a été fait avec un vrai modèle : ni Ollama, ni Mistral, ni ChatGPT. Aucun essai n'a eu lieu sur un vrai Nextcloud, ni avec le jeton JWT de l'éditeur activé en conditions réelles.

## Cessions (CE)

| Ligne | État | Preuve ou limite |
|---|---|---|
| CE01 deux opérations | Automatique | Deux identifiants, même dossier, aucun dossier créé |
| CE02 Fonds puis Bail au clavier | Réel | Page réelle : liens natifs, état courant, contour de focus, adresse cohérente. La touche Entrée n'a pas pu être envoyée (panneau du navigateur masqué) ; ce sont des liens simples |
| CE03 avenant annoncé absent | Automatique | Décision « Avenant annoncé non lu », indépendante du modèle |
| CE04 deux prix | Automatique | Deux valeurs visibles, aucune confirmée, décision créée ; une décision n'est pas réécrite |
| CE05 bail manquant | Automatique | Le reste avance, réserves, « prêt à signer » refusé |
| CE06 décision modifiée | Automatique | Historique et auteurs, documents à réexaminer, étape rouverte |
| CE07 bail seul | Automatique | Pas de publication ni d'opposition du fonds |
| CE08 clause d'agrément | Automatique | Condition à vérifier, source citée, clause jamais retirée. Le repérage de la clause dans un vrai bail dépend du modèle : non essayé |
| CE09 dates autour de la réforme | Automatique | Ancien, nouveau, jours frontière à vérifier, calcul explicable, règle à valider |
| CE10 deux publications | Automatique | Dernière date retenue, preuve et règle conservées |
| CE11 fin de séquestre | Automatique | Aucune libération, aucune route d'exécution |
| CE12 2672 et 2676 | Partiel | 2672 : cases contrôlées visuellement sur le vrai formulaire 12-2018, totaux et ventilation contrôlés ; identité des parties laissée à l'avocat. 2676 : support officiel sans lignes d'inventaire, à compléter à la main |
| CE13 rejet de formalité | Automatique | Motif et preuve exigés et visibles ; transmise n'est pas acceptée |
| CE14 prix changé | Automatique | Acte signé intact, projets à réexaminer, nouvelle version distincte |
| CE15 hors périmètre | Automatique | Qualification complémentaire affichée, aucune conformité annoncée |

## Bibliothèque (BI)

| Ligne | État | Preuve ou limite |
|---|---|---|
| BI01 depuis Cessions | Réel | Même catalogue, filtre, lien de retour |
| BI02 catalogue minimal | Automatique | 43 références uniques : 14, 19, 10 |
| BI03 personnalisation | Automatique | Nouvelle version, modèle livré et documents anciens préservés |
| BI04 champ inconnu | Automatique | Refus lisible avant publication |
| BI05 acte complexe | Partiel | Acte généré ouvert dans le vrai éditeur et lu par le plugin. Mise en page non comparée page à page, faute de capture d'écran |
| BI06 version retirée | Automatique | Historique conservé, génération refusée |
| BI07 brouillon depuis la page | Réel | Préparer depuis la page : la ligne apparaît après rechargement automatique |
| BI08 modèle non validé | Automatique | Possible à la main, signalé ; refusé en autonomie |

## Plugin et éditeur (OF)

| Ligne | État | Preuve ou limite |
|---|---|---|
| OF01 démarrage | Réel | Capacités consignées dans `docs/OFFICE-CAPACITES-5.6.84.md` ; ni Office.js ni API commerciale |
| OF02 deux sessions | Réel | Identités distinctes, même clé |
| OF03 enregistrement forcé | Réel | Clé identique après enregistrement et réouverture |
| OF04 saisie pendant l'instantané | Partiel | Logique prouvée sous Node, non provoquée dans l'éditeur réel |
| OF05 écrire puis pause | Réel | Observation et analyse sans action manuelle (modèle simulé) |
| OF06 coller, supprimer, annuler | Partiel | Suppression et annulation côté serveur ; gros collage et déplacement de paragraphe non mesurés |
| OF07 tableau, en-tête, note | Partiel | Lecture et écriture en cellule mesurées, en-tête et notes lus ; observation continue limitée aux notifications de paragraphe de l'éditeur |
| OF08 mise en forme seule | Non essayé | L'éditeur ne notifie pas un changement de mise en forme seul. Limite connue : pas d'analyse sur ce cas |
| OF09 réouverture | Partiel | Ancien patch refusé, nouvelle session ; remappage réel non mesuré |
| OF10 clause répétée | Réel | Cible ambiguë détectée, conflit |
| OF11 réponse tardive | Automatique | Marquée périmée, rien d'appliqué |
| OF12 deux exécuteurs | Automatique | Un seul effet, ancien bail refusé |
| OF13 saisie dans la cible | Réel | Conflit, saisie préservée |
| OF14 coéditeurs | Réel | Annulation native corrompue en coédition, donc jamais utilisée ; écriture automatique suspendue à deux |
| OF15 annuler une écriture IA | Réel | Modification inverse, texte non reproposé |
| OF16 retour après autre édition | Réel | Inversion conditionnelle |
| OF17 suivi des modifications | Partiel | Révisions conservées mesurées ; aucune acceptation ni rejet dans le code ; cas avec révisions déjà présentes non rejoué |
| OF18 écriture IA observée | Réel | Pas de boucle |
| OF19 prix modifié | Automatique | Proposition distincte, décision non écrasée |
| OF20 connexion perdue | Partiel | Curseur, doublons, séquence ; coupure réseau non provoquée |

Non mesurés : composition IME, collage de gros volumes, déplacement de paragraphes.

## Sauvegarde (SV)

| Ligne | État | Preuve ou limite |
|---|---|---|
| SV01 sauvegarde réussie | Réel | Relecture, empreinte, « Enregistré à …, vérifié », fichier conforme |
| SV02 statuts 3 et 7 | Partiel | Échec durable en test ; pas provoqué par un vrai serveur |
| SV03 fichier changé | Automatique | Copie de conflit vérifiée, reprise dans la même copie |
| SV04 doublons et désordre | Automatique | Aucun doublon, aucun recul |
| SV05 redémarrage | Automatique | Reprise par la tâche de santé, rapprochement, pas de double publication |
| SV06 IA sans éditeur | Partiel | Nouvelle version contrôlée avec Nextcloud simulé ; réouverture réelle de cette version non faite |
| SV07 ouverture pendant le travail | Automatique | Résultat converti en proposition |

## Exploitation (OP)

| Ligne | État | Preuve ou limite |
|---|---|---|
| OP01 frappe continue | Automatique | Une active, une suivante ; bornes. Dix minutes réelles non jouées |
| OP02 pause | Automatique | Réponse tardive inactive, aucune application |
| OP03 annulation | Automatique | Terminale et persistante |
| OP04 fournisseurs | Partiel | Même contrat et mêmes contrôles avec réponses simulées. Aucun vrai fournisseur |
| OP05 mode local | Partiel | Erreur du modèle : état d'échec, aucun autre appel. Non joué avec une vraie panne |

## Sécurité (SE)

| Ligne | État | Preuve ou limite |
|---|---|---|
| SE01 autre dossier | Automatique | Refus avant lecture ; dossier déduit par le serveur |
| SE02 instruction dans une pièce | Automatique | Patch déclaratif uniquement ; le bouclier de la 5.6.59 s'applique aux appels réels |
| SE03 document signé | Automatique | Signé gelé, prêt à signer retiré |
| SE04 message ou jeton falsifié | Automatique | Origine et fenêtre, jeton, empreintes |
| SE05 adresse interdite | Automatique | Téléchargement refusé, aucun secret côté plugin |

## Navigation (UX)

| Ligne | État | Preuve ou limite |
|---|---|---|
| UX01 Honoraires et facturation | Réel | Une entrée dans Toutes les fonctions, absente du menu principal |

## Écarts assumés

- Le panneau est dans la page AxiorHub, pas dans l'éditeur : un panneau de plugin en démarrage automatique se ferme et fait échouer l'éditeur (mesure réelle).
- Le retour arrière est une modification inverse conditionnelle, jamais l'annulation native.
- L'observation et l'écriture automatique sont désactivées après mise à jour. Elles coûtent des appels au modèle : c'est au cabinet de les activer.
- Les 43 modèles sont des brouillons. Aucun n'est validé juridiquement, et aucune génération autonome n'en utilise un tant qu'un avocat ne l'a pas validé.
- Les règles de droit sont à valider par version ; tant qu'elles ne le sont pas, leurs dates restent « À vérifier ».
- Taxes additionnelles non chiffrées. Article 1724 du CGI à revérifier pour l'arrondi. Jours frontière du régime des salariés à vérifier.
