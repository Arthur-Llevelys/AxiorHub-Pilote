# Journal des versions — application Nextcloud AxiorHub Pilote

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
