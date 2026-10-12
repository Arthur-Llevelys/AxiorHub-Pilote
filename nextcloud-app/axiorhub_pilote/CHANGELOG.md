# Journal des versions — application Nextcloud AxiorHub Pilote

## 0.3.0 — version de développement (avec le moteur 5.6.36, contrat 1.2)

- **Compte relié personnellement** : Paramètres personnels › AxiorHub Pilote (adresse, identifiant et mot de passe AxiorHub) ; le mot
  de passe sert une fois à obtenir une clé personnelle chiffrée, révocable des deux côtés. Les utilisateurs Nextcloud non reliés n'ont
  aucun accès ; le mode d'instance (secret partagé, groupes) reste disponible.
- **Interface complète** : Pilote (instruction, résultat attendu), Rechercher dans les dossiers, Fiche du dossier, Recherche juridique,
  Travaux récents ; choix du dossier par recherche.
- **« Moteur injoignable » corrigé** : une page HTML du moteur (base occupée, frontal demandant une connexion) n'est plus prise pour une
  panne réseau ; motif précis affiché ; une lecture est réessayée une fois quand le moteur est occupé.
- Tests unitaires PHP exécutés à chaque envoi (syntaxe PHP 8.1 et 8.4, API publique OCP), construction vérifiée.

## 0.2.0 — version de développement (avec le moteur 5.6.35)

- **Dossiers importés automatiquement** : l'enregistrement des réglages teste la liaison et importe les dossiers du moteur (groupe par
  défaut « admin »), actualisés toutes les 5 minutes ; tableau « Dossiers et groupes autorisés » dans la page d'administration (plus
  d'appel d'API manuel). Corrige « Aucun dossier ne vous est accessible ».
- **Secret partagé** : même règle que le moteur, 8 caractères au moins (32 aléatoires recommandés).
- **Résultat entièrement consultable** avant décision (sections, sources, corrections ; plus d'aperçu tronqué) ; décisions Approuver,
  Refuser, Demander une correction, rattachées à la version figée du résultat (audit F16, F12).
- **Suivi rattaché au bon travail** : une réponse tardive d'un ancien travail ne modifie plus l'écran (identifiant capturé, génération,
  requêtes abandonnées) (audit F17).
- **Travaux récents** : liste des 30 derniers travaux de l'utilisateur, réouverture d'un résultat ancien (figé) ; **historique des
  décisions** (version, auteur, date, commentaire) sous chaque résultat.
- Contrat 1.1 : opération signée vérifiée par le moteur, sélection de pièces réellement appliquée (audit F10, F11).
- `info.xml` : site, signalements et source pointent vers le dépôt dédié.

## 0.1.0 — version de développement (avec le moteur 5.6.34)

- Squelette natif PHP + Vue 3 : navigation Nextcloud, réglages administrateur, page de l'application.
- Contrat moteur `/integration/v1` : contexte signé (HMAC-SHA256, nonce, expiration courte), périmètre par groupe Nextcloud,
  manifestes de sources (file_id, ETag, taille), travaux avec clé d'idempotence, suivi borné, annulation, résultat versionné,
  approbation d'une version précise.
- Parcours natifs : choix d'un dossier accessible, préparation de la fiche du dossier, recherche juridique, suivi et approbation.
- Non qualifiée : aucune compatibilité n'est annoncée avant installation réelle et tests sur Nextcloud 32 à 35 (workflow manuel).
