# Confidentialité et secret professionnel

AxiorHub Pilote est conçu pour un cabinet d'avocats : le secret professionnel conditionne l'architecture.

## Où sont les données

| Donnée | Emplacement |
|---|---|
| Courriels | messagerie du cabinet (IMAP) ; AxiorHub Pilote lit et dépose des brouillons, il n'envoie rien |
| Dossiers et pièces | Nextcloud du cabinet (WebDAV) |
| Agenda et tâches | Nextcloud (CalDAV, Deck) |
| Analyses, index, règles de style, journaux | base SQLite locale (`/data` en Docker, `/var/lib/axiorhub-mail-agent` en installation système) |
| Mots de passe et clés | fichiers secrets locaux (droits 600), jamais dans la configuration ni dans les journaux |

**Aucune donnée n'est transmise à l'auteur du logiciel.** Il n'y a ni télémétrie ni compte en ligne. Les mises à jour
distantes sont désactivées tant que vous ne les configurez pas, et elles sont vérifiées par signature.

## Intelligence artificielle

- **Locale (par défaut)** : Ollama sur votre serveur ou votre réseau. Rien ne sort.
- **Externe (sur option)** : Anthropic, OpenAI, Mistral ou un service compatible. Activation par un administrateur,
  avec consentement explicite. Chaque texte envoyé est **pseudonymisé de façon réversible** :
  - noms de personnes et de sociétés, adresses, téléphones, courriels, IBAN, dates de naissance, numéros (sécurité
    sociale, SIREN/SIRET, carte), références et noms de dossiers sont remplacés par des marqueurs (`[PERSONNE_1]`,
    `[SOCIETE_1]`…), à partir de ce que le cabinet connaît (clients, correspondants, parties) et de motifs ;
  - la table de correspondance reste en mémoire sur le serveur, le temps de la requête ; la réponse est ré-identifiée
    localement ;
  - un **aperçu** (*Paramètres › IA externe*) montre exactement ce qui part ;
  - aucun appel à un fournisseur non local ne contourne cette étape.
  
  Limite : une pseudonymisation n'est pas une anonymisation ; un détail factuel peut encore permettre une
  réidentification. Réservez l'IA externe aux fournisseurs dont les engagements contractuels vous conviennent.
- **Mixte** : la locale traite le tri et les tâches courtes ; l'externe pseudonymisée traite les rédactions longues.

Vérifiez les conditions contractuelles du fournisseur (absence d'entraînement sur vos données, localisation,
conservation) et inscrivez le traitement à votre registre (RGPD, article 30).

## Recommandations d'exploitation

- Serveur dédié au cabinet, mises à jour de sécurité automatiques (`unattended-upgrades`).
- HTTPS obligatoire (Let's Encrypt via Certbot), HSTS activé par le script d'installation.
- Sauvegardes chiffrées, copiées hors du serveur, restauration testée.
- Comptes nominatifs, rôle minimal (voir [COMPTES-ET-ROLES.md](COMPTES-ET-ROLES.md)), comptes désactivés au départ
  d'un collaborateur.
- Compte Nextcloud technique dédié à AxiorHub Pilote, limité aux partages nécessaires, avec un mot de passe d'application.

## Le dépôt public ne contient aucune donnée de cabinet

Le code, les tests et les modèles de documents n'utilisent que des données fictives (`example.test`, « SAS EXEMPLE »,
« Me Exemple »). Le contrôle `python3 scripts/privacy-scan.py` bloque toute publication qui contiendrait une adresse
électronique, un numéro de téléphone, un chemin de serveur, une clé d'API ou le contenu d'un document réel (texte et
métadonnées des fichiers `.docx`/`.odt`). Un fichier personnel `.privacy-denylist` (jamais publié) permet d'y ajouter les
noms de vos clients et adversaires pour vérifier qu'aucun n'a été copié dans le code.
