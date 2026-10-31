# Capacités EuroOffice réellement mesurées (5.6.84)

Essais du 31 octobre 2026 sur l'image `ghcr.io/euro-office/documentserver:latest`, version 9.3.7, compilation 60, empreinte
`sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2`. Documents fictifs, navigateur Chromium, deux sessions.
Le tag `9.3.7` n'existe pas sur le registre : épinglez l'empreinte ci-dessus, pas un nom de version.

| Capacité | Résultat | Conséquence pour AxiorHub |
|---|---|---|
| Plugin déclaré par `editorConfig.plugins.pluginsData` depuis une autre origine | Fonctionne, aucune installation sur le serveur bureautique | Le plugin est servi par AxiorHub |
| Plugin `background` avec `autostart` | Démarre seul à l'ouverture | Mode retenu pour la capture |
| Plugin `panel` ou `panelRight` avec `autostart` | Le panneau se ferme aussitôt ; cliquer son icône provoque une erreur de l'éditeur | Non utilisé : le panneau est dans la page AxiorHub, à côté de l'éditeur |
| `attachEditorEvent('onParagraphText')` | Paragraphe entier, identifiant et recalcId ; environ une seconde après la frappe réelle | Capture retenue ; aucun événement par touche |
| Modifications du plugin lui-même | Déclenchent aussi `onParagraphText` | Origine à rapprocher des commandes pour éviter toute boucle |
| Lecture de structure (`callCommand`) | 3 à 5 ms sur 8 éléments | Instantané et réconciliation possibles |
| Tableaux, cellules, en-tête, notes | Lus par l'API ; identifiants de paragraphe de cellule disponibles | Couverts en lecture ; l'écriture en cellule fonctionne |
| Identifiants internes | Identiques entre deux sessions ouvertes ensemble ; `GetParaId` vaut 0 sur un DOCX sans identifiant | Liés à la session : ancre = identifiant, texte attendu et empreinte |
| `ApiRange.SetText` | Absent de cette version | Non utilisé |
| `range.AddText(..., 'after')` puis `range.Delete()` sur la même plage | **Efface tout le paragraphe** | Interdit |
| `paragraph.GetRange(début, fin)` | Décalé par chaque limite de mise en forme, fin exclusive | Interdit pour une écriture |
| Recherche exacte, ajout, nouvelle recherche, suppression | Correct, y compris partiel, cellule, remplacement contenant le texte d'origine | Méthode retenue |
| Remplacement à cheval sur deux mises en forme | Le texte perd le gras | Refus automatique, proposition seulement |
| Cible ambiguë (plusieurs occurrences) | Détectée avant écriture | Conflit |
| Texte attendu périmé | Détecté dans la même exécution que l'écriture | Conflit, saisie préservée |
| Suivi des modifications | Suppression et insertion conservées comme révisions | Pris en charge, révisions existantes jamais acceptées ni rejetées |
| Une commande `callCommand` | Un seul pas dans l'historique | Annulation immédiate par un seul retour |
| Annulation native après l'écriture d'un autre utilisateur | **Texte corrompu** (« 110 000125 000 ») | Jamais d'annulation native depuis le plugin : modification inverse conditionnelle |
| Coédition, mode rapide | Écritures reçues en moins de trois secondes par l'autre session | Auto-application suspendue dès deux participants : propositions seulement |
| Enregistrement forcé (Ctrl+S) | Statut 6 avec `url`, `lastsave`, `forcesavetype` ; clé inchangée | Reçu de sauvegarde par `lastsave` |
| Moteur du plugin | Fourni par l'éditeur (`plugin_init`) ; seul un chargeur est nécessaire | Aucun fichier chargé depuis Internet |

Non mesurés : composition IME, collage de gros volumes, déplacement de paragraphes, réouverture avec remappage, statuts d'erreur 3 et 7 provoqués par un vrai serveur.

## Mise en service

1. Épinglez l'image du serveur bureautique par son empreinte, pas par `latest`.
2. Rien à installer sur ce serveur : AxiorHub lui présente le plugin à l'ouverture d'un document.
3. Le serveur bureautique doit joindre AxiorHub à l'adresse enregistrée dans Paramètres, et le navigateur doit pouvoir charger `/office/plugin/`.
4. Activez « Observation pendant l'édition » dans Paramètres, section Éditeur de documents. L'écriture automatique reste un réglage distinct.
5. Sans ce réglage, l'éditeur fonctionne comme avant, avec la clé stable et la sauvegarde prouvée.
