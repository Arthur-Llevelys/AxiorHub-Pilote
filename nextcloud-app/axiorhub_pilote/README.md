# AxiorHub Pilote pour Nextcloud (0.3.0, version de développement)

Application native Nextcloud (PHP + Vue 3) raccordée au moteur AxiorHub par le contrat versionné `/integration/v1`
(moteur 5.6.36 ou plus récent, contrat 1.2). Cycle de version distinct du moteur.

## Dépôts

- Application Nextcloud : dépôt dédié `AxiorHub-Pilote-Nextcloud` (https://github.com/Arthur-Llevelys/AxiorHub-Pilote-Nextcloud),
  application à la racine, prête à être placée dans `apps/axiorhub_pilote` ; signalements : onglet « Issues » de ce dépôt, sans
  aucune donnée du cabinet.
- Moteur : dépôt `AxiorHub-Pilote` (https://github.com/Arthur-Llevelys/AxiorHub-Pilote), qui en garde une copie dans `nextcloud-app/`
  pour les archives du moteur. Les deux copies sont identiques à chaque livraison.

## Ce que l'application fait

Après liaison du compte (voir ci-dessous), la page « AxiorHub Pilote » de Nextcloud propose :

- **Pilote** : une instruction écrite, avec le dossier (facultatif) et le résultat attendu — réponse, analyse, projet Word déposé
  dans le dossier, ou brouillon de courriel déposé dans Brouillons. **Rien n'est envoyé** ;
- **Rechercher dans les dossiers** : courriels, documents, notes et agenda indexés par le moteur ;
- **Fiche du dossier** : préparation et lecture complète, décisions Approuver / Refuser / Demander une correction, historique ;
- **Recherche juridique** : question anonymisée avant tout envoi ;
- **Travaux récents** : suivi, annulation et réouverture d'un résultat.

Le moteur garde l'indexation, les modèles, les missions et les contrôles ; l'installation de l'application **n'installe ni Python, ni
Ollama, ni serveur de traitement**. Un poste qui peut être éteint n'est pas un moteur fiable pour un Nextcloud hébergé ailleurs.

## Mise en service

### 1. Dans AxiorHub (une fois, par l'administrateur)

Paramètres › Connexions › **Application Nextcloud** : cochez « Autoriser la liaison des comptes depuis Nextcloud », puis
« Enregistrer ». Les comptes reliés apparaissent ensuite dans ce même tableau, avec « Révoquer ».

### 2. Dans Nextcloud (chaque avocat, pour son compte)

Paramètres personnels › **AxiorHub Pilote** :

1. adresse d'AxiorHub Pilote (par exemple `https://courriel.exemple.fr/agent-courriel`) ;
2. identifiant et mot de passe AxiorHub ;
3. « Relier mon compte ».

Le mot de passe sert **une seule fois** à obtenir une clé personnelle ; il n'est ni conservé, ni journalisé. La clé est chiffrée dans
les préférences de l'utilisateur et se révoque des deux côtés (« Délier » dans Nextcloud, « Révoquer » dans AxiorHub).
**Les utilisateurs Nextcloud qui n'ont pas relié de compte n'ont aucun accès** : un collaborateur non avocat ne voit rien.

Si AxiorHub est sur le même serveur que Nextcloud et joint par une adresse locale, cochez « Moteur sur ce serveur (adresse locale) ».
Si un serveur web placé devant AxiorHub demande une connexion, autorisez sans connexion le chemin `/integration/v1/` (chaque appel y est
signé).

### Mode d'instance (facultatif, hérité de 0.2.0)

Paramètres d'administration › Paramètres supplémentaires › AxiorHub : adresse, identifiant d'instance et secret partagé (8 caractères
au moins, identiques à `integration5634` sur le moteur), groupe par défaut ; les dossiers du moteur sont rattachés à des groupes
Nextcloud. Un utilisateur relié personnellement passe toujours par sa clé personnelle.

## Sécurité

- Contexte signé à chaque appel (HMAC-SHA256 : clé personnelle ou secret d'instance, opération signée, expiration de 2 minutes, nonce
  à usage unique) ; le moteur revérifie les droits du compte pour chaque dossier, travail et résultat.
- Aucune redirection suivie ; une réponse non JSON du moteur (page d'attente, page de connexion d'un frontal) est qualifiée
  (`moteur_occupe`, `acces_refuse_par_le_frontal`, `reponse_moteur_invalide`) au lieu d'un « injoignable » trompeur ; une lecture est
  réessayée une fois quand le moteur est occupé, une écriture jamais.
- Essais de liaison limités par le moteur (5 échecs en 15 minutes).
- Fichiers lus par les API publiques (`IRootFolder`, nœuds et permissions), jamais par le répertoire `data`.
- Routes métier avec protection CSRF (aucun `NoCSRFRequired` ni `PublicPage` hors de la page d'entrée).

## Installation manuelle

Décompresser l'archive dans `apps/` avec le compte du serveur web (sinon Nextcloud signale « Propriétaire des répertoires
d'applications ») :

```bash
sudo -u www-data tar -xzf axiorhub_pilote-0.3.0.tar.gz -C /chemin/nextcloud/apps/
sudo -u www-data php /chemin/nextcloud/occ app:enable axiorhub_pilote
```

Mise à jour depuis 0.2.0 : remplacer le dossier `apps/axiorhub_pilote` de la même façon, puis `sudo -u www-data php occ upgrade`.

## Compatibilité et tests

Cibles : Nextcloud 32 à 35 ; PHP 8.1 à 8.5 selon la version du serveur. À chaque envoi, la recette GitHub exécute la syntaxe PHP
(8.1 et 8.4), les tests unitaires (`composer install`, `vendor/bin/phpunit -c phpunit-unit.xml`) et la construction de l'interface.
L'installation réelle sur chaque version de Nextcloud (workflow `nextcloud-app.yml`) se lance manuellement ; **aucune compatibilité
n'est annoncée** avant son succès. La signature et la publication sur l'App Store relèvent du responsable du projet.

## Construire

```
npm ci && npm run build      # actifs compilés dans js/ et css/
```

Le paquet de diffusion (`scripts/build-package.py` dans le dépôt dédié, `scripts/build-nextcloud-app.py` dans le dépôt du moteur)
contient un seul dossier `axiorhub_pilote`, les actifs compilés, `appinfo`, `lib`, `templates`, `l10n`, `img`, la licence et cette
documentation ; ni `node_modules`, ni `vendor`, ni sources de développement, ni secret.
