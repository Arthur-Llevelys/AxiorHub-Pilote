# Comptes et rôles du cabinet

**Un serveur = un cabinet.** Les comptes d'AxiorHub Pilote sont ceux des membres du cabinet. Ils partagent les mêmes dossiers,
la même messagerie et le même agenda : les droits portent sur ce que chacun peut **faire**, pas sur des cloisons entre
dossiers (les cloisons relèvent des partages Nextcloud et de la messagerie).

Cette gestion des comptes concerne la distribution autonome (Docker, VPS). L'installation système historique derrière
Apache utilise l'authentification de son hôte virtuel.

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
