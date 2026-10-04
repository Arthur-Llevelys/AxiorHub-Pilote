# Historique des versions

AxiorHub — créé par Timo RAINIO. Les notes détaillées des versions antérieures à la publication ouverte ne sont pas
reprises ici ; seules les grandes étapes le sont.

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
