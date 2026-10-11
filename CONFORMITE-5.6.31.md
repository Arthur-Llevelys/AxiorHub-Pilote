# Conformité 5.6.31 — cahier des charges additionnel « Guide d’installation et veille »

Version révisée du cahier des charges : 10 octobre 2026. Le cahier prévoit des livraisons par lots avec un périmètre annoncé (§ 25, R20) :
« Créer le socle R00, R02 et R17, puis un parcours minimum R01 R03 R04 ». La 5.6.31 livre ce socle, le menu unique (R18) et le guide
d’utilisation (R19). Les autres fiches sont planifiées dans les versions suivantes ; aucune fonction incomplète n’est présentée comme
disponible dans l’interface.

Légende : **Réalisé** — livré et testé ; **Partiel** — livré avec la limite indiquée ; **Lot suivant** — non commencé dans cette version
(l’écran existant reste utilisable et le guide y renvoie).

## Fiches de réalisation

| Fiche | Objet | État 5.6.31 |
|---|---|---|
| R00 | Prérequis et premier démarrage | Réalisé : choix des fonctions, prérequis par fonction, messagerie désactivable (`mail.enabled`), `load_config` tolérant pour un service désactivé et strict pour un service activé, finalisation complète ou partielle reconnue, aucune connexion IMAP si la messagerie n’est pas choisie. |
| R01 | Écrans du parcours guidé | Réalisé pour profil, coordonnées, langue, documents, IA, réception et envoi des courriels, récapitulatif ; étapes agenda, sources, modèles, veille, facturation et téléphonie informatives (renvoi vers l’écran existant). |
| R02 | Réglages, secrets et preuves | Réalisé : session serveur, coffre de session, tests sur configuration candidate, preuves ciblées, publication avec révision et conflit 409, nettoyage à l’annulation. |
| R03 | Stockage local et WebDAV | Partiel : lecture locale et WebDAV, écriture de test locale (créée, relue, supprimée, disparition vérifiée) ; écriture de test WebDAV et adaptateur WebDAV générique sans chemins Nextcloud : lot suivant. |
| R04 | Choisir et tester les IA | Partiel : Ollama (service, modèles réellement installés, génération fictive), deux rôles de modèle ; cartes Claude/OpenRouter/OpenAI/Mistral dans le guide et écran de téléchargement de modèle : lot suivant (l’écran « Fournisseurs et budgets » existant reste utilisé). |
| R05 | Réception, brouillons, envoi | Partiel : test IMAP sans marquer de message lu, liste réelle des dossiers, test SMTP sans envoi ; envoi d’essai facultatif et dépôt d’un brouillon fictif : lot suivant. |
| R06 | Google Agenda et CalDAV | Lot suivant (lot 3). |
| R07 | Quatre connecteurs juridiques | Lot suivant (lot 3) ; liens officiels d’inscription déjà présents dans l’étape « Sources juridiques ». |
| R08 | Modèles Word | Lot suivant (lot 4) ; l’import et l’aperçu existants (« Importer et prévisualiser ») restent la référence. |
| R09 | ONLYOFFICE / Euro-Office | Lot suivant (lot 4). |
| R10 | Invoice Ninja | Lot suivant (lot 4). |
| R11 | Téléphonie | Lot suivant (lot 4). |
| R12 | Veille planifiée et cadre Aujourd’hui | Lot suivant (lot 5). |
| R13 | Tavily | Lot suivant (lot 5). |
| R14 | Sources des écrits | Lot suivant (lot 6). |
| R15 | Document → agenda → brouillons | Lot suivant (lot 9). |
| R16 | Compétences et trois compétences par défaut | Lot suivant (lot 9). |
| R17 | Traduction | Partiel : mécanisme complet (catalogues fr/en/de/es à clés stables, variables nommées, pluriels, échappement, langue par requête, préférence par utilisateur, cookie de session, contrôle de catalogue) appliqué au menu, à la barre du haut, au guide d’installation et au guide d’utilisation ; les autres écrans restent en français (lot 7). |
| R18 | Menu unique | Réalisé. |
| R19 | Guide d’utilisation | Réalisé pour les 63 fonctions du registre en quatre langues ; captures annotées et visite de découverte : lot suivant ; essai avec cinq avocats : à organiser par le cabinet. |
| R20 | Migrer, vérifier, livrer | Réalisé pour ce lot : tables créées de façon réexécutable, réglages existants conservés (`mail.enabled` vaut « oui » par défaut), installable depuis 5.6.6, suite complète. |

## Exigences

| Exigence | État | Détail |
|---|---|---|
| GI01 | Réalisé | « Guide d’installation » en haut de Paramètres ; premier lancement autonome : « Commencer la configuration guidée » ou « Découvrir puis configurer plus tard » ; installation existante : « Vérifier mes réglages » et « Ajouter une fonctionnalité ». |
| GI02 | Réalisé | Une question, 3 à 5 champs visibles, « Plus de détails », « Étape n sur N » sur les seules étapes retenues. Sous-progressions Google, modèles et MCP : avec ces sous-guides (lots 3–4). |
| GI03 | Réalisé | Explication, exemple fictif, « Où trouver cette information », fonctionnalité débloquée sur chaque écran ; IMAP, SMTP, CalDAV, MCP, WebDAV expliqués avant usage. |
| GI04 | Partiel | Liens officiels dans un nouvel onglet, progression conservée au retour ; guides illustrés par fournisseur : lot suivant. |
| GI05 | Réalisé | Niveaux séparés et affichés : lecture / dossiers ; service / modèle / génération ; écriture de test ; identification SMTP. |
| GI06 | Réalisé | Retour, Continuer, Passer, Enregistrer et reprendre plus tard ; envoi SMTP activé seulement par une case explicite et expliquée. |
| GI07 | Réalisé | Choix initial des sept fonctions ; étapes facultatives selon les choix ; « Prêt à analyser » exige stockage et IA ; la veille RSS seule n’exige ni stockage ni IA. |
| GI08 | Partiel | Profil, coordonnées, domaines multiples, langue, fuseau, langue de rédaction ; choix de langue avant connexion sur la page de connexion autonome : lot suivant. |
| GI09 | Partiel | Cartes local / Nextcloud / WebDAV / Plus tard ; lecture sans indexation ; écriture de test locale ; voir R03. |
| GI10 | Partiel | Voir R04. |
| GI11 | Partiel | Liens officiels et avertissement sur les données et la facturation ; configuration par l’écran existant « Fournisseurs et budgets ». |
| GI12 | Partiel | Serveur, identifiant, secret, port et chiffrement en « Plus de détails » ; dossiers réels listés par le test ; choix de la boîte dans une liste déroulante : lot suivant. |
| GI13 | Partiel | Test d’identification sans envoi ; message d’essai facultatif : lot suivant. |
| GI14–GI16 | Lot suivant | Étape informative ; réglage dans Paramètres › Connexions. |
| GI17–GI22 | Lot suivant | Étape informative avec les quatre pages officielles ; réglage dans Connecteurs MCP. |
| GI23–GI25 | Lot suivant | Étape informative ; Documents du cabinet existant. |
| GI26–GI28 | Lot suivant | — |
| GI29 | Lot suivant | — |
| GI30 | Lot suivant | — |
| GI31 | Partiel | Le guide couvre les réglages du socle ; les autres réglages gardent leur aide dans Paramètres. |
| GI32 | Partiel | Récapitulatif sans secret : fonctions disponibles, connexions à compléter, IA externe, prochaine étape (« Ouvrir Aujourd’hui », guide d’utilisation) ; « Tester avec un dossier fictif » : lot suivant. |
| GI33 | Réalisé | Contrôleur au-dessus de `config567`, du coffre, du profil `pieces510` et des tests existants ; aucune seconde configuration. |
| GI34 | Réalisé | Session serveur (propriétaire, version, langue, étapes, candidats sans secret), coffre de session, expiration 30 jours, annulation qui nettoie seulement les secrets candidats. |
| GI35 | Réalisé | Preuve : connexion, capacité, empreinte des seuls champs utiles, version du secret, horodatage, validité, résultat ; invalidation ciblée. |
| GI36 | Réalisé | Propriétaire pris de la session d’authentification ; guide réservé à l’administrateur ; langue modifiable par chaque membre ; 403 sinon. |
| VJ01–VJ14 | Lot suivant | La veille existante (« Veille juridique ») reste disponible. |
| DP01–DP05 | Lot suivant | — |
| SK01–SK13 | Lot suivant | — |
| LG01 | Partiel | Quatre langues, préférence par utilisateur, cookie de session, noms des langues (pas de drapeaux), langue proposée aux nouveaux comptes (API) ; choix avant connexion : lot suivant. |
| LG02 | Partiel | Menu, barre du haut, guide d’installation, guide d’utilisation, erreurs du guide traduits ; écrans métier : lot 7. |
| LG03 | Réalisé | Catalogues JSON à clés stables par langue, interpolation nommée, pluriels, sous-ensemble transmis aux scripts. |
| LG04 | Partiel | Langue propre à chaque requête, variables échappées, attribut `lang` ; langue des notifications et travaux de fond : lot 7. |
| LG05 | Partiel | Zéro message manquant dans le périmètre livré (contrôle automatique) ; relecture juridique des traductions par un traducteur : à faire ; pseudo-traduction : lot 7. |
| LG06 | Réalisé | Langue de l’interface, de rédaction et droit du dossier distincts. |
| NV01 | Réalisé | Six accès fréquents + « Toutes les fonctions » ; « Rubriques » et « Outils » supprimés. |
| NV02 | Réalisé | Libellés orientés action (« Style et apprentissage », « État du logiciel et aide »), anciens noms conservés pour la recherche. |
| NV03 | Réalisé | Même menu partout ; fonction active surlignée ; clavier, Échap, retour du focus ; ordinateur et mobile. |
| NV04 | Réalisé | « Configurer » vers l’étape du guide quand un prérequis manque ; liste filtrée par rôle, routes protégées côté serveur. |
| NV05 | Réalisé | Registre unique `fonctions5631` (identifiant, groupe, route, alias, prérequis, aide) consommé par le menu, la recherche et le guide d’utilisation ; contrôle automatique. |
| GU01 | Réalisé | « Guide d’installation » et « Guide d’utilisation » en haut de Paramètres ; « Aide sur cette page » sur chaque écran ; lien depuis « Toutes les fonctions ». |
| GU02 | Réalisé | Rechercher dans le guide, Découvrir les fonctions, Réaliser une tâche (sept parcours par besoin). |
| GU03 | Partiel | Fiche complète par fonction avec « Ouvrir cette fonction » et « Configurer le prérequis manquant » ; captures annotées : lot suivant. |
| GU04 | Lot suivant | Visite de découverte facultative. |
| GU05 | Réalisé | Fiches en quatre langues, versionnées ; test qui vérifie que chaque bouton cité existe sur l’écran réel. |
| GU06 | À organiser | Essai avec cinq avocats peu familiers du logiciel. |

## Tests de réception (§ 26) couverts par cette version

| Test | État | Preuve |
|---|---|---|
| GI T01 | Réalisé | Guide ouvert depuis Paramètres et au premier lancement ; étapes selon les choix (`test_start_shows_only_chosen_steps_and_progress`). |
| GI T02 | Réalisé | Dossier local + Ollama sans messagerie : configuration rechargée par `load_config`, aucune connexion IMAP (`test_apply_without_messaging_and_first_start_is_recognised`). |
| GI T03 | Partiel | Parcours « veille seule » sans IA ni stockage ; synthèse IA de la veille : lot 5. |
| GI T04 | Réalisé | Reprise après fermeture, retour arrière, aucun secret réaffiché. |
| GI T05 | Réalisé | Modifier le SMTP conserve la preuve IMAP ; changer le mot de passe IMAP la périme (`test_targeted_proof_invalidation`). |
| GI T06 | Réalisé | Conflit avec un autre écran : 409 ciblé, aucune perte (`test_conflict_with_another_screen_is_reported_not_overwritten`). |
| GI T07 | Partiel | Lecture et écriture locales ; WebDAV : lecture. |
| GI T08 | Partiel | Ollama absent, injoignable ou modèle non installé signalés ; actualisation de la liste par le test. |
| GI T10 | Partiel | IMAP et SMTP distingués, aucun envoi. |
| GI T24 | Réalisé | Rôles avocat et assistant : langue permise, guide refusé. |
| GI T25 | Réalisé | Export de diagnostic sans secret ni référence de coffre ; secrets candidats effacés à l’annulation. |
| LG T01 | Partiel | Guides et menu dans les quatre langues ; autres écrans : lot 7. |
| LG T02 | Réalisé | Deux fils, deux langues simultanées. |
| LG T03 | Réalisé | Pluriels, variables, dates et nombres ; valeurs stockées inchangées. |
| LG T04 | Partiel | Fiche allemande vérifiée au navigateur en largeur mobile (375 px, aucun défilement horizontal). |
| LG T05 | Réalisé | Clé absente : repli français tracé ; HTML dans une variable échappé. |
| LG T06 | Réalisé | Interface anglaise sans effet sur la langue de rédaction ni sur les racines du dossier. |
| NV T01 | Réalisé | Toutes les entrées Rubriques, Outils et menu principal retrouvées dans le registre ; chaque route servie. |
| NV T02 | Réalisé | Recherche par nom, besoin ou ancien nom ; vérifiée au clavier dans le navigateur. |
| NV T03 | Réalisé | Anciens liens conservés, « Configurer » proposé, rôles filtrés. |
| GU T01 | Réalisé | Deux guides depuis Paramètres. |
| GU T02 | Réalisé | Chaque fiche, chaque bouton cité présent à l’écran. |
| GU T04 | Partiel | Fiche affichée dans la langue choisie ; repli français signalé s’il manquait une traduction. |

Les scénarios des lots suivants (GI T09, T11–T23 ; VJ, DP, SK) seront exécutés avec les fonctions correspondantes.
