# Historique des versions

AxiorHub — créé par Timo RAINIO. Les notes détaillées des versions antérieures à la publication ouverte ne sont pas
reprises ici ; seules les grandes étapes le sont.

## 5.6.5 — File fluide et relecture plus claire

- **La file ne bloque plus vos demandes** : les travaux automatiques (surveillance, indexation, recherche de faits…) ont leur propre
  plafond (60 en attente, 8 par type) ; vos demandes entrent toujours. Avant, 100 travaux en attente, quels qu'ils soient, suffisaient à
  refuser toute nouvelle demande (« file attente pleine »).
- **Plus d'inondation par les anciens dossiers** : un dossier lu pour la première fois ou modifié n'est analysé que si un fichier a été
  modifié depuis moins de 30 jours (date inconnue : analysé, par prudence). Une file pleine ne fait plus échouer la surveillance des
  documents (analyse simplement reportée). Les suites d'un travail terminé (lot suivant d'indexation…) et « Indexer tous les dossiers »
  ne sont pas soumises au plafond automatique.
- **Rôles des correspondants** : juridiction / greffe, expert, administration, commissaire de justice, autre partie (assureur, caution,
  mandataire…), contact personnel (hors dossier). Rien du dossier n'est communiqué à ces destinataires sans validation de l'avocat ; aucun
  brouillon n'est préparé pour un contact personnel.
- **Relecture d'un brouillon** : le courriel d'origine (expéditeur, date, extrait) est affiché, avec des liens vers l'original et vers le
  brouillon dans la messagerie, et « Ouvrir dans Courriels à relire » ouvre directement ce brouillon.
- **Document préparé** : emplacement exact (dossier › sous-dossier › fichier) dans le volet et dans le fil ; bouton « Modifier le
  document » (Nextcloud / OnlyOffice) en premier, éditeur AxiorHub en second.

## 5.6.4 — Secours externe

- **Modèle local trop lent** : pour les rédactions et les demandes de l'avocat (assistant, brouillons, documents, analyse, audiences,
  Roundcube), une génération locale qui dépasse le délai (3 minutes par défaut, réglable de 1 à 15 minutes) est abandonnée et confiée au
  premier fournisseur externe prêt, dans l'ordre Mistral, Claude, ChatGPT, OpenRouter ; un travail qui a attendu son tour plus longtemps
  que ce délai part directement chez le fournisseur externe. Le tri des courriels, la lecture des pièces jointes et le contrôle
  indépendant restent locaux.
- Seuls les fournisseurs activés **et autorisés** sont utilisés ; tout envoi est **pseudonymisé** sur le serveur ; si aucun fournisseur
  ne répond, le modèle local termine le travail.
- **Dépôt GitHub complété** : la règle `data/` du `.gitignore` excluait aussi `agent/data` (règles de délais de procédure et de
  prescription) et `tests/data` ; un clone du dépôt 5.6.1 à 5.6.3 n'avait ni échéances ni prescriptions (l'archive livrée n'était pas
  concernée). Règles ancrées à la racine, fichiers ajoutés, contrôle des données personnelles étendu à ces dossiers ; un test empêche
  le retour de l'erreur.
- Réglage dans *IA externe sûre → Secours externe* (désactivé par défaut) ; chaque bascule est visible dans le détail du travail et dans
  le journal des bascules.

## 5.6.3 — Authentification, permissions et flux d'activité

Défauts corrigés, chacun couvert par un test qui échoue sur la 5.6.2 (échanges HTTP réels, serveur lancé dans le test) :

- **Flux d'activité (SSE)** : le mandataire du mode autonome attendait la fin de chaque réponse (événements livrés en bloc toutes les
  25 s) ; Waitress retenait les petits envois (`send_bytes=1`) ; une coupure avant le premier envoi ne rendait jamais la place de flux
  (« Flux actifs trop nombreux » après quelques coupures) ; un `Last-Event-ID` non numérique provoquait une erreur 500 ; le flux de
  l'administrateur était redirigé vers l'assistant d'installation ; Apache (installation système et VPS) ne transmet plus le flux par
  paquets ni compressé.
- **Authentification (mode autonome)** : sessions enregistrées sur le serveur (déconnexion et révocation réelles) ; changement ou
  réinitialisation du mot de passe et désactivation d'un compte déconnectent ses autres sessions ; 5 échecs par adresse (20 par poste)
  en 15 minutes ; 3 liens « mot de passe oublié » par heure ; lien refusé pour un compte désactivé, tous les liens en cours annulés
  après usage ; temps de réponse identique pour une adresse inconnue ; en-têtes `Referrer-Policy` et `X-Frame-Options`.
- **Permissions** : réglages du cabinet réservés à l'administrateur (niveau d'autonomie, services, extensions, profil du cabinet,
  correspondance des dossiers, profil et modèle de bordereau, barème) ; API `/api/v1` en lecture seule pour les avocats et assistants ;
  chemins ambigus (`//`, `..`, barre finale) normalisés ou refusés avant le contrôle des droits ; seuls `/static/<fichier>` et
  « À propos » sont servis sans connexion ; en-têtes `X-AxiorHub-*` venant du navigateur supprimés ; base des comptes en 0600, dossier
  en 0700, secrets en 0700.
- **Poste de pilotage** : bouton « Effacer la discussion » (les travaux, documents et brouillons ne sont pas touchés) ; bouton « Arrêter »
  sous chaque demande en attente ou en cours (annulée si elle n'a pas commencé, arrêt demandé sinon) ; « Annuler ce travail » dans le
  détail d'un travail de « Ce que fait l'agent ».
- **Surveillance des documents** : un nom de fichier ambigu dans Nextcloud est ignoré et signalé au lieu de bloquer la lecture du dossier ;
  un dossier illisible n'arrête plus la surveillance des autres (avant : surveillance de tout le cabinet suspendue après trois échecs).
- **Distribution** : bibliothèque `cryptography` ajoutée à l'image Docker (les notifications mobiles ne pouvaient pas fonctionner) ;
  tests de notifications sautés, et non en échec, sans cette bibliothèque.
- **Intégration continue** : dépendances de l'image installées ; tests exécutés aussi depuis l'archive livrée ; motifs des échecs
  publiés en annotations lisibles sans connexion.

## 5.6.2 — Questions sur les courriels

- **« Résume les mails reçus aujourd'hui »** (ou hier, cette semaine, à une date) : réponse immédiate dans le poste de pilotage, classée
  par suite donnée (à traiter, brouillon prêt, sans réponse nécessaire), sans IA ni file de travail. Auparavant, la question partait en
  rédaction de document dans un dossier deviné à tort.
- **Choix du dossier** : un seul mot courant du nom de dossier (« mails », « pièces », « dossier »…) ne suffit plus à choisir le dossier
  d'office ; il reste proposé.
- **État du système** : les cartes en incident montrent les fichiers en cause (chemin et motif) ; les fichiers facultatifs absents
  (intégration Open WebUI, clé de mises à jour désactivées) ne sont plus des incidents ; les redémarrages du service ne comptent plus comme
  des boucles pour les contrôles automatiques.
- **Boutons d'action immédiate** (« Contrôler maintenant »…) : « Fait ✓ », puis bouton rétabli et page actualisée, au lieu de rester
  « Enregistré ✓ » et désactivés.
- Lecture des fichiers JSON toujours en UTF-8 (accents corrects quel que soit le système) ; titre des anciennes pages « AxiorHub Pilote ».

## 5.6.1 — Interface fiable

- **Survol lisible** : les anciennes règles de style donnaient à tous les boutons un texte blanc au survol, y compris aux éléments à fond clair
  (cartes « À relire », boutons secondaires), qui devenaient illisibles. Elles ne visent plus que les boutons sans style propre ; un contrôle
  automatique du contraste au survol couvre les pages principales, en mode clair et sombre.
- **Volet de relecture** : l'arrière-plan n'est plus un bouton ; il reste un voile semi-transparent au lieu de devenir bleu plein.
- **Liens du poste de pilotage** : le préfixe de l'interface (par exemple `/agent-courriel`) est transmis ; « Ouvrir dans Courriels à relire »
  et les autres liens des fragments mènent à la bonne page.
- **« Ce que fait l'agent »** : chaque travail s'ouvre sur son détail (document demandé, pièce ou courriel concerné, avancement, résultat,
  « Ouvrir dans l'éditeur », « Ouvrir dans Nextcloud », « Relancer ») ; la liste indique déjà le nom de la pièce ou l'objet de la demande.
- **« Envoyer… »** : si le brouillon a été modifié, il est enregistré puis la confirmation d'envoi s'ouvre, au lieu d'un refus.
- **Recette automatique** : motif de chaque échec affiché ; deux tests dépendants de l'environnement corrigés (horloge, LibreOffice présent).
- **Diagnostic** : les minuteries d'analyse et de nettoyage sont réactivées par l'installateur d'interface ; commande de relance indiquée.
- **File de travail** : vieillissement des priorités ; un travail automatique n'attend plus indéfiniment, sans passer devant vos demandes.

## 5.6.0 — Distribution

- **Assistant d'installation** au premier démarrage : identité du cabinet, messagerie, Nextcloud (découverte des
  agendas), IA locale, mixte ou externe, mode observation ou brouillons ; chaque connexion se teste avant enregistrement.
- **Ensemble Docker pour VPS** (`deploy/vps/`) : AxiorHub, Nextcloud (MariaDB, Redis, cron), Roundcube, OnlyOffice,
  Ollama en option ; **hôtes virtuels Apache** et **certificats Let's Encrypt (Certbot)** ; script d'installation,
  réglages Nextcloud (Agenda, Tâches, Deck, OnlyOffice) et sauvegarde.
- **Comptes et rôles du cabinet** : administrateur, avocat, assistant(e) ; contrôle côté serveur ; création de comptes
  avec mot de passe provisoire, désactivation, réinitialisation, journal ; premier compte administrateur, inscriptions
  publiques fermées.
- **Mention d'auteur** : page « À propos », pied du menu, `NOTICE` (condition AGPL article 7 b), `AUTHORS.md`.
- **Identité du cabinet** issue de la configuration (signature, prompts, ressort) au lieu de valeurs écrites en dur.
- **Documentation publique** : README, installation VPS, installation système, comptes et rôles, confidentialité,
  architecture.
- **Contrôle de confidentialité renforcé** (`scripts/privacy-scan.py`) : texte et métadonnées des modèles `.docx`,
  courriels, téléphones, chemins de serveur, adresses IP, clés d'API, liste personnelle non publiée. Les tests et les
  modèles de documents n'utilisent plus que des données fictives.
- Emplacement de Roundcube détecté automatiquement (`AXIORHUB_ROUNDCUBE_ROOT` pour l'imposer).

## 5.5.0 — Style du cabinet

Apprentissage des formulations validées par l'avocat, règles visibles et révocables ; interface alignée sur la maquette
du poste de pilotage ; correction du cache des feuilles de style.

## 5.4.0 — IA externe sûre

Pseudonymisation réversible obligatoire hors IA locale, aperçu de ce qui part, connecteur Anthropic natif, mode mixte
local et API.

## 5.3.0 — Poste de pilotage

Écran unique : ce qui attend une décision, ce qui est prêt, ce qui bloque ; zone de saisie qui devine le dossier ;
Deck et Tâches Nextcloud.

## 5.2.x — Pièces, agenda, interface et fiabilité

Demandes de documents, « Pourquoi peu de brouillons ? », file de travail débloquée.

## 5.1.0 — Pièces et bordereaux

Numérotation, tampon, bordereau de communication de pièces, assemblage PDF.

## 5.0.x — Fonctions métier

Temps et honoraires, rendez-vous, conflits d'intérêts, prescription, pilotage ; agenda et tâches ; interface unique.

## 4.x — Assistant vivant

Routage hybride des modèles, apprentissage métier, installation autonome cumulative, production utile et mesurée,
surveillance IMAP en continu, deux workers, atelier de correction des courriels et documents.

## 3.x — Poste de travail

Interface guidée, documents du cabinet, préparation proactive, assistance métier, documents longs, distribution Docker
autonome sous AGPL.

## 1.x – 2.x — Assistant courriel

Brouillons de réponse dans la messagerie, mémoire des réponses envoyées, rattachement aux dossiers Nextcloud, chronologie,
analyse stratégique, projets d'actes, agenda et tâches, recherche juridique tracée.
