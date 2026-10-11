# Journal des versions — application Nextcloud AxiorHub Pilote

## 0.1.0 — version de développement (avec le moteur 5.6.34)

- Squelette natif PHP + Vue 3 : navigation Nextcloud, réglages administrateur, page de l'application.
- Contrat moteur `/integration/v1` : contexte signé (HMAC-SHA256, nonce, expiration courte), périmètre par groupe Nextcloud,
  manifestes de sources (file_id, ETag, taille), travaux avec clé d'idempotence, suivi borné, annulation, résultat versionné,
  approbation d'une version précise.
- Parcours natifs : choix d'un dossier accessible, préparation de la fiche du dossier, recherche juridique, suivi et approbation.
- Non qualifiée : aucune compatibilité n'est annoncée avant installation réelle et tests sur Nextcloud 32 à 35 (workflow manuel).
