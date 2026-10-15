# Comptes et rôles du cabinet

**Un serveur = un cabinet.** Les comptes d'AxiorHub Pilote sont ceux des membres du cabinet. Ils partagent les mêmes dossiers,
la même messagerie et le même agenda : les droits portent sur ce que chacun peut **faire**, pas sur des cloisons entre
dossiers (les cloisons relèvent des partages Nextcloud et de la messagerie).

Cette gestion des comptes concerne la distribution autonome (Docker, VPS). L'installation système historique derrière
Apache utilise l'authentification de son hôte virtuel.

**Version serveur (5.6.48)** : connexion par la page d'AxiorHub (`/agent-courriel/connexion`), bouton **Se déconnecter** en pied
du menu.

- *Administrateur* : le compte de l'installation (identifiant et mot de passe de l'interface, repris tels quels). Sur une nouvelle
  installation, la première visite ouvre **Premier lancement** : saisissez le code privé lu sur le serveur
  (`sudo cat /var/lib/axiorhub-mail-agent/premier-compte.code`), l'identifiant, une adresse de récupération facultative et un mot de
  passe de 16 caractères au moins. Les inscriptions sont ensuite fermées.
- *Avocats et assistant(e)s* : **Paramètres › Comptes › Comptes du cabinet**, réservé à l'administrateur (ajout avec mot de passe
  provisoire de 12 caractères au moins, à changer à la première connexion ; changement de rôle ; désactivation ; nouveau mot de passe
  provisoire). Les droits suivent le tableau ci-dessous.
- *Mot de passe oublié* : lien valable 30 minutes envoyé à l'adresse de récupération (envoi par le serveur SMTP de Paramètres ›
  Messagerie), même réponse que le compte existe ou non. Sur le serveur, sans courriel :
  `sudo python3 /opt/axiorhub-mail-agent/current/reinitialiser-acces.py [identifiant]`.
- *Sessions* : cookie `HttpOnly`, `Secure`, `SameSite=Lax`, 12 heures (30 jours avec « Se souvenir de moi ») ; empreinte seule
  conservée sur le serveur ; un changement de mot de passe ou une désactivation ferme les sessions du compte. Tentatives limitées
  (5 par identifiant, 20 par adresse en 15 minutes).
- L'en-tête « Basic » de l'administrateur reste accepté pour les scripts du cabinet.

**Droits par compte (5.6.50)** : *Paramètres › Droits et accès* (administrateur). Sur la base du rôle, six droits s'accordent
ou se retirent compte par compte : envoyer des courriels, déposer des brouillons dans la messagerie, valider les décisions et les
productions, gérer l'agenda et les tâches, facturation et temps passé, réglages du cabinet. Un administrateur garde tous ses droits ;
la gestion des comptes et des droits reste réservée aux administrateurs, quel que soit le réglage. Les droits sont stockés dans
`acces5650.json` (dossier d'état) et contrôlés côté serveur dans la version serveur, la distribution autonome et l'ExApp.

**Dossiers réservés (5.6.50, facultatif)** : rubrique « Accès au dossier » de la page du dossier (administrateur). Un dossier réservé
n'est visible que des comptes cochés et des administrateurs : listes et sélecteurs de dossiers, recherche du cabinet, assistant,
agenda, messagerie ; toute requête qui le désigne est refusée (403). La boîte de réception reste commune (boîte unique du cabinet). Les travaux de l'agent pour le cabinet (indexation, rapprochement des
courriels) ne sont pas cloisonnés.

**Messagerie du cabinet (5.6.50)** : menu « Messagerie ». Conversations à deux, groupes et fil de chaque dossier ; chaque message
porte le nom et le rôle de son auteur ; un administrateur ne lit pas les conversations des autres ; l'agent dépose des avis dans le
fil du dossier et ne lit jamais les conversations.

**Versions poste (5.6.42)** : un seul compte, l'administrateur de l'interface. **Paramètres › Comptes** affiche son identifiant et
permet de changer l'identifiant et le mot de passe (mot de passe actuel exigé, 16 caractères au moins).

**ExApp Nextcloud (développement)** : les comptes sont ceux de Nextcloud ; le rôle vient des groupes `axiorhub-avocats`,
`axiorhub-collaborateurs` (avocat), `axiorhub-assistants` (assistant) et des administrateurs de Nextcloud (administrateur).

## Rôles

| | Administrateur | Avocat | Assistant(e) |
|---|:---:|:---:|:---:|
| Consulter le poste de pilotage, les dossiers, les courriels analysés | ✓ | ✓ | ✓ |
| Donner des instructions à l'agent, demander un document | ✓ | ✓ | ✓ |
| Agenda et tâches (créer, modifier, synchroniser) | ✓ | ✓ | ✓ |
| Préparer pièces et bordereaux (analyse, numérotation, contrôle) | ✓ | ✓ | ✓ |
| Valider, corriger ou écarter une production, déposer un brouillon | ✓ | ✓ | — |
| Style du cabinet : valider ou révoquer une règle apprise | ✓ | ✓ | — |
| Paramètres, IA et routage, automatismes, file de travail, extensions | ✓ | — | — |
| Assistant d'installation, comptes, mises à jour | ✓ | — | — |

Le contrôle s'applique côté serveur à chaque requête : un bouton masqué ne suffit pas, toute action non autorisée reçoit
une réponse *403 Accès refusé*.

## Premier compte

Après l'installation, ouvrez `https://agent.votre-cabinet.fr/signup` : **le premier compte créé est administrateur**.
Les inscriptions publiques sont ensuite fermées.

`AXIORHUB_ALLOW_SIGNUP=true` rouvre la page d'inscription, mais un compte ainsi créé reste **inactif**, avec le rôle
assistant, jusqu'à son activation par un administrateur.

## Gérer les comptes (administrateur)

Menu **Comptes** (pied du menu latéral) :

- **Créer un compte** : courriel, nom, rôle. Un mot de passe provisoire est affiché **une seule fois** ; la personne
  devra le changer à sa première connexion.
- **Changer le rôle**, **désactiver** ou **réactiver** un compte.
- **Réinitialiser le mot de passe** : nouveau mot de passe provisoire, changement obligatoire.
- Le **dernier administrateur actif** ne peut être ni désactivé ni rétrogradé.
- Chaque opération est consignée (date, auteur, compte concerné) dans le journal des comptes.

## Sécurité des sessions

- Mots de passe hachés (scrypt, sel individuel), 12 caractères minimum.
- Cookie de session signé, `HttpOnly`, `Secure`, `SameSite=Lax`, valable 12 heures ; contrôle d'origine et jeton
  anti-falsification sur les formulaires d'administration.
- Réinitialisation par courriel possible si un serveur SMTP est configuré (`AXIORHUB_SMTP_*`).
- AxiorHub Pilote reçoit l'identité et le rôle de l'utilisateur ; les journaux d'actions indiquent qui a validé quoi.
