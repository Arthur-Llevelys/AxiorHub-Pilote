# Application Microsoft intégrée d'AxiorHub (5.6.103) — inscription par l'éditeur

Ce document s'adresse à l'éditeur d'AxiorHub. Il décrit l'inscription, **une seule fois**, de l'application publique Microsoft que
toutes les installations d'AxiorHub utiliseront pour Outlook (courrier et agenda) et OneDrive / SharePoint. Les cabinets n'auront plus
rien à inscrire dans Entra : ils cliqueront « Se connecter à Microsoft » dans Paramètres › Connexions.

L'application est un **client public** : elle n'a **aucun secret**. Son identifiant (client) n'est pas confidentiel et figure dans le
dépôt public (`agent/microsoft56103.py`, constante `CLIENT_ID_INTEGRE`). Tant que cette constante est vide, AxiorHub affiche
« Application intégrée pas encore disponible : utilisez votre propre application » et n'appelle jamais Microsoft ; l'agenda Outlook
reste réglable avec l'application propre du cabinet (5.6.36), inchangée.

## 1. Choix faits et pourquoi

| Choix | Valeur | Raison |
| --- | --- | --- |
| Types de comptes | « Comptes dans un annuaire organisationnel quelconque et comptes Microsoft personnels » (`AzureADandPersonalMicrosoftAccount`) | Les cabinets sous Microsoft 365 (comptes professionnels) **et** les avocats seuls sur outlook.com. Contreparties lues : au plus 30 autorisations par ressource (nous en demandons 7 au plus), pas de cloud national, pas de paramètre dans l'adresse de retour. |
| Autorité | `common` | Comptes professionnels de tout locataire et comptes personnels. |
| Poste | Code d'autorisation + **PKCE (S256)**, retour de bouclage `http://localhost:<port>/axiorhub/microsoft-retour` | Recommandé par Microsoft pour les applications de bureau qui utilisent le navigateur du système ; le port est ignoré pour localhost. |
| Serveur | **Code d'appareil** | Le navigateur de l'avocat n'est pas sur la machine du serveur : il saisit un code sur microsoft.com/devicelogin. |
| Autorisations déléguées | `User.Read`, `offline_access`, `Mail.ReadWrite`, `Calendars.ReadWrite`, `Files.ReadWrite` ; facultatif SharePoint : `Files.ReadWrite.All`, `Sites.Read.All` | Le minimum pour lire le courrier et déposer des brouillons, l'agenda, OneDrive. **Jamais `Mail.Send`** : AxiorHub n'envoie rien (envoyer un brouillon exige `Mail.Send`, que l'application n'aura pas). |

## 2. Inscription dans Entra (étapes exactes)

Se connecter avec un **compte professionnel** du locataire Entra de l'éditeur (une application inscrite avec un compte Microsoft personnel
ne peut pas être vérifiée), disposant du rôle Développeur d'applications ou Administrateur d'applications.

1. Ouvrir https://entra.microsoft.com › **Identité › Applications › Inscriptions d'applications › Nouvelle inscription**.
2. **Nom** : `AxiorHub Pilote` (90 caractères au plus pour ce type de comptes).
3. **Types de comptes pris en charge** : « Comptes dans un annuaire organisationnel quelconque (tout annuaire Microsoft Entra ID –
   multilocataire) et comptes Microsoft personnels (par exemple, Skype, Xbox) ».
4. **URI de redirection** : plateforme « Client public/natif (mobile et bureau) », valeur exacte
   `http://localhost/axiorhub/microsoft-retour` (sans port : Microsoft ignore le port pour localhost ; le chemin doit être identique).
   Ne pas ajouter d'autre URI localhost qui ne différerait que par le port.
5. **Inscrire**. Copier l'**ID d'application (client)** affiché dans « Vue d'ensemble ».
6. **Authentification** : vérifier que la plateforme « Applications mobiles et de bureau » porte l'URI ci-dessus. Dans
   « Paramètres avancés », **Autoriser les flux clients publics : Oui** (nécessaire au code d'appareil), puis **Enregistrer**.
   Ne pas activer d'octroi implicite.
7. **Certificats et secrets** : **ne rien créer**. L'application n'a aucun secret.
8. **Autorisations d'API › Ajouter une autorisation › Microsoft Graph › Autorisations déléguées** : cocher `User.Read`, `offline_access`,
   `Mail.ReadWrite`, `Calendars.ReadWrite`, `Files.ReadWrite`, et pour SharePoint `Files.ReadWrite.All` et `Sites.Read.All`.
   **Ne pas ajouter `Mail.Send`** ni aucune autorisation d'application. AxiorHub refuse de se connecter si Microsoft lui accorde
   `Mail.Send` (ou une autre autorisation de la liste `INTERDITES`).
9. **Personnalisation et propriétés** : renseigner le **domaine de l'éditeur** (domaine vérifié de l'éditeur, pas `*.onmicrosoft.com`),
   le logo, et de préférence les adresses des conditions d'utilisation et de la déclaration de confidentialité (elles apparaissent sur
   l'écran de consentement).

## 3. Vérification de l'éditeur (gratuite)

But : le badge bleu « vérifié » sur l'écran de consentement, et la levée du blocage « éditeur non vérifié » dans les locataires qui
l'appliquent. Conditions relevées dans la documentation Microsoft :

- un compte **Microsoft AI Cloud Partner Program** (ancien MPN) vérifié ; l'identifiant utilisé est celui du **compte partenaire global**
  (PGA), pas celui d'un « emplacement » ;
- le locataire Entra où l'application est inscrite est associé à ce compte partenaire ;
- le **domaine de l'éditeur** de l'application est défini, et le domaine de l'adresse électronique utilisée lors de la vérification du
  compte partenaire est ce domaine (ou un domaine personnalisé vérifié du locataire) ;
- la personne qui lance la vérification a un rôle Administrateur d'applications (ou Administrateur d'applications cloud) dans Entra et
  Administrateur partenaire (ou Administrateur de compte) dans l'Espace partenaires, et se connecte avec l'authentification multifacteur ;
- acceptation des conditions d'utilisation de la plateforme d'identité Microsoft.

Démarche : Espace partenaires (inscription au programme et vérification du compte), puis dans l'inscription de l'application
**Personnalisation et propriétés › Vérification de l'éditeur** : saisir l'identifiant Partner One et valider. Microsoft indique qu'un
éditeur qui remplit déjà les conditions est vérifié en quelques minutes, et ne facture rien.

## 4. Ce que verra un cabinet (consentement)

Constats tirés de la documentation actuelle (pages listées au § 7) :

- **Politique gérée par Microsoft** (« Laisser Microsoft gérer vos paramètres de consentement »), réglage par défaut d'un **nouveau
  locataire** : l'utilisateur peut consentir à toute autorisation déléguée consentable par un utilisateur **sauf** une liste qui comprend
  notamment `Mail.Read`, `Mail.ReadWrite`, `Calendars.Read`, `Calendars.ReadWrite`, `Files.Read.All`, `Files.ReadWrite.All`,
  `Sites.Read.All`, `Sites.ReadWrite.All`. **Dans un tel locataire, un administrateur Microsoft 365 du cabinet doit consentir** pour le
  courrier, l'agenda et SharePoint, vérification de l'éditeur ou non. `Files.ReadWrite` (OneDrive de la personne) ne figure pas dans
  cette liste.
- **Consentement progressif selon le risque** (activé par défaut, effectif seulement si le consentement utilisateur est permis) : une
  demande vers une application multilocataire **non vérifiée**, inscrite après novembre 2020, avec des autorisations au-delà de la
  connexion de base, exige un administrateur (message AADSTS90094 si le circuit de demande à l'administrateur n'est pas activé).
- Locataire réglé sur « Ne pas autoriser le consentement utilisateur » : administrateur obligatoire.
- Comptes **personnels** (outlook.com) : ces politiques de locataire ne s'appliquent pas ; la personne consent elle-même (non vérifié en
  réel dans ce lot).

Consentement de l'administrateur pour tout le cabinet, sans passer par un utilisateur : adresse documentée par Microsoft (à ouvrir par un
administrateur du cabinet ; `redirect_uri` doit être une URI inscrite) :

```
https://login.microsoftonline.com/organizations/v2.0/adminconsent?client_id=<ID>&scope=https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Calendars.ReadWrite https://graph.microsoft.com/Files.ReadWrite&redirect_uri=http://localhost/axiorhub/microsoft-retour&state=<aléatoire>
```

Après l'approbation, Microsoft renvoie vers `http://localhost/axiorhub/microsoft-retour?admin_consent=True&tenant=…` ; si aucun AxiorHub
n'écoute sur cette machine, le navigateur affiche une erreur de connexion, mais (non vérifié en réel) le consentement est enregistré à
l'approbation. Variante : activer dans le locataire du cabinet le circuit de demande de consentement à l'administrateur.

## 5. Où coller l'identifiant

- **Livraison** : `agent/microsoft56103.py`, `CLIENT_ID_INTEGRE = '<ID d'application (client)>'` (minuscules, format GUID). Aucun autre
  réglage n'est nécessaire ; l'identifiant n'est pas un secret.
- **Essai avant livraison** (facultatif) : section `microsoft56103` de la configuration du serveur, clé `client_id`. Une valeur qui n'est
  pas un GUID est ignorée.

## 6. Vérifications après inscription (à faire à la main, non faites dans ce lot)

1. Poste : Paramètres › Connexions › Compte Microsoft › « Se connecter à Microsoft » : la page Microsoft s'ouvre, le retour arrive sur
   `http://localhost:<port>/axiorhub/microsoft-retour`, le compte apparaît « Relié ».
2. Serveur : même bouton : un code s'affiche ; le saisir sur https://microsoft.com/devicelogin ; la page passe à « Relié ».
3. Outlook : un projet « Retenu » n'apparaît pas dans Outlook ; « Déposer dans Brouillons » le crée dans Brouillons avec ses pièces ;
   l'état « Déposé » n'apparaît qu'après relecture chez Microsoft ; rien n'est envoyé.
4. Agenda : Paramètres › Connexions › Agenda Microsoft 365 › « Application intégrée » ; copie d'une audience et lecture des rendez-vous.
5. Locataire sur la politique gérée par Microsoft : constater le besoin d'un administrateur pour courrier et agenda ; vérifier l'adresse
   de consentement administrateur.
6. Déconnexion : les jetons locaux sont effacés ; retirer aussi l'accès dans https://myapps.microsoft.com (compte professionnel) ou
   https://account.live.com/consent/Manage (compte personnel).

## 7. Documentation Microsoft relue (lecture seule, 15/11/2026)

- Code d'appareil : https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code (point `/devicecode`, 15 minutes par
  défaut, `authorization_pending`, `authorization_declined`, `bad_verification_code`, `expired_token` ; `slow_down` n'y figure pas, il
  vient de la RFC 8628 § 3.5 et AxiorHub le traite).
- Code d'autorisation et PKCE : https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow (clients publics sans
  secret, `http://localhost` pour le navigateur du système, code valable environ une minute, rotation du jeton de renouvellement).
- Adresses de retour : https://learn.microsoft.com/en-us/entra/identity-platform/reply-url (port ignoré pour localhost, chemin
  significatif, pas de paramètre de requête avec les comptes personnels, `[::1]` non pris en charge).
- Flux clients publics : https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-applications et
  https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration.
- Types de comptes : https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation.
- Jetons de renouvellement : https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens (90 jours par défaut hors
  applications monopage, remplacés à chaque usage, révocations).
- Consentement : https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent,
  https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-app-consent-policies (politique gérée par Microsoft et sa
  liste), https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-risk-based-step-up-consent,
  https://learn.microsoft.com/en-us/entra/identity-platform/v2-admin-consent.
- Vérification de l'éditeur : https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview.
- Graph : https://learn.microsoft.com/en-us/graph/api/user-post-messages (brouillon, `Mail.ReadWrite`),
  https://learn.microsoft.com/en-us/graph/api/message-send (`Mail.Send` exigé pour envoyer),
  https://learn.microsoft.com/en-us/graph/outlook-large-attachments (moins de 3 Mo en une fois, jusqu'à 150 Mo par session, pas d'en-tête
  `Authorization` vers l'adresse de session), https://learn.microsoft.com/en-us/graph/api/singlevaluelegacyextendedproperty-get (filtre
  sur propriété étendue), https://learn.microsoft.com/en-us/graph/api/driveitem-put-content (`Files.ReadWrite`, 250 Mo),
  https://learn.microsoft.com/en-us/graph/api/driveitem-list-versions, https://learn.microsoft.com/en-us/graph/api/driveitem-delete
  (`if-match` → 412).

Non relu, repris de l'existant : l'en-tête `If-Match` sur le remplacement du contenu d'un fichier OneDrive (comportement déjà utilisé par
`stockage_graph5688`, 5.6.88), `restoreVersion`, et la présence de `@microsoft.graph.downloadUrl` dans la métadonnée d'un fichier.
