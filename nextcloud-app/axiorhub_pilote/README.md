# AxiorHub Pilote pour Nextcloud (0.2.0, version de développement)

Application native Nextcloud (PHP + Vue 3) raccordée au moteur AxiorHub par le contrat versionné `/integration/v1`
(moteur 5.6.35 ou plus récent, contrat 1.1). Cycle de version distinct du moteur.

## Dépôts

- Application Nextcloud : dépôt dédié `AxiorHub-Pilote-Nextcloud` (https://github.com/Arthur-Llevelys/AxiorHub-Pilote-Nextcloud),
  application à la racine, prête à être placée dans `apps/axiorhub_pilote` ; signalements : onglet « Issues » de ce dépôt.
- Moteur : dépôt `AxiorHub-Pilote` (https://github.com/Arthur-Llevelys/AxiorHub-Pilote), qui en garde une copie dans `nextcloud-app/`
  pour les archives du moteur. Les deux copies sont identiques à chaque livraison.

## Ce que l'application fait et ne fait pas

- Nextcloud gère la session, l'utilisateur, les permissions de fichiers, les métadonnées propres à l'application et l'interface.
- Le moteur AxiorHub gère l'indexation, les modèles, les missions, les contrôles, les connecteurs et les résultats versionnés.
- L'installation de l'application **n'installe ni Python, ni Ollama, ni un serveur de traitement**. Le moteur doit être installé sur le
  serveur du cabinet (paquet Debian, archive ou Docker) et joignable depuis le serveur Nextcloud. Un poste Windows ou Ubuntu qui peut
  être éteint n'est pas un moteur fiable pour un Nextcloud hébergé ailleurs.
- Parcours natifs de cette version : liaison au moteur, choix d'un dossier accessible, préparation de la fiche du dossier, recherche
  juridique, suivi borné des travaux, annulation, consultation et approbation d'une version précise d'un résultat.
  Les autres fonctions restent dans l'interface du moteur.

## Sécurité

- Contexte signé à chaque appel (HMAC-SHA256 avec un secret d'instance, utilisateur et périmètre déduits côté serveur, expiration de
  2 minutes, nonce à usage unique) ; le moteur revérifie le périmètre de chaque dossier, travail et résultat.
- Secret chiffré et marqué sensible dans la configuration de l'application ; jamais transmis au navigateur.
- Fichiers lus par les API publiques (`IRootFolder`, nœuds et permissions), jamais par le répertoire `data`.
- Routes métier avec protection CSRF (aucun `NoCSRFRequired` ni `PublicPage` hors de la page d'entrée).

## Activer la liaison

1. Sur le moteur (`config.json`) : `"integration5634": {"enabled": true, "instance_id": "<identifiant>", "secret_file": "<chemin du secret>"}`
   (fichier lisible par le seul compte de service, droits 600). Le secret compte **8 caractères au moins** ; 32 caractères aléatoires
   sont recommandés (`openssl rand -base64 48`). Les deux côtés appliquent la même règle.
2. Dans Nextcloud : Paramètres d'administration › Paramètres supplémentaires › AxiorHub : adresse HTTPS du moteur (par exemple
   `https://courriel.exemple.fr/agent-courriel`), même identifiant d'instance, même secret, groupe autorisé par défaut (`admin` sauf
   choix contraire). L'enregistrement teste la liaison et **importe les dossiers du moteur** ; le tableau « Dossiers et groupes
   autorisés » permet ensuite de donner chaque dossier à un groupe. La liste est actualisée toutes les 5 minutes (nouveaux dossiers,
   intitulés) sans jamais écraser un groupe choisi.
3. Les administrateurs voient tous les dossiers ; les autres utilisateurs, ceux de leurs groupes.

### Installation manuelle

Décompresser l'archive dans `apps/` avec le compte du serveur web (sinon Nextcloud signale « Propriétaire des répertoires
d'applications ») :

```bash
sudo -u www-data tar -xzf axiorhub_pilote-0.2.0.tar.gz -C /chemin/nextcloud/apps/
sudo -u www-data php /chemin/nextcloud/occ app:enable axiorhub_pilote
```

## Compatibilité

Cibles : Nextcloud 32 (transition, fin de support en septembre 2026), 33, 34 et 35 ; PHP 8.1 à 8.5 selon la version du serveur.
**Aucune compatibilité n'est annoncée tant que l'installation réelle et les tests n'ont pas été exécutés** sur chaque combinaison
(workflow `.github/workflows/nextcloud-app.yml`, lancé manuellement). La signature et la publication sur l'App Store relèvent du
responsable du projet (certificat, clé privée hors dépôt).

## Construire

```
npm ci && npm run build      # actifs compilés dans js/
```

Le paquet de diffusion (`scripts/build-package.py` dans le dépôt dédié, `scripts/build-nextcloud-app.py` dans le dépôt du moteur) contient un seul dossier `axiorhub_pilote`, les actifs compilés, `appinfo`,
`lib`, `templates`, `l10n`, `img`, la licence et cette documentation ; ni `node_modules`, ni sources de développement, ni secret.
