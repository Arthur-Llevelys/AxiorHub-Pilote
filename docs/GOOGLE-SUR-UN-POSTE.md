# Google Agenda et Gmail sur un poste (5.6.110)

Pour qui : l'avocat qui utilise AxiorHub Pilote sur son ordinateur (application Windows 11 ou Ubuntu 24.04), sans serveur.

## Ce qui a changé

Avant la 5.6.110, « Connecter » Google menait à la page Google « Erreur 400 : redirect_uri_mismatch ». AxiorHub envoyait à Google une
adresse de retour en `https` (souvent la valeur d'exemple `https://agent.example.com/…`). Un poste n'écoute qu'en
`http://127.0.0.1:<port>` : cette adresse ne pouvait ni être déclarée chez Google, ni revenir au poste.

Depuis la 5.6.110, sur un poste :

- AxiorHub **calcule lui-même** l'adresse de retour du poste, par exemple
  `http://127.0.0.1:8769/agent-courriel/api440/m5689/oauth/google/callback` (Gmail) et
  `http://127.0.0.1:8769/agent-courriel/api440/m568/google/callback` (Google Agenda). Elle s'affiche dans la carte Google, avec un
  bouton « Copier ». Laissez le réglage « URI de retour » vide.
- Une adresse fausse ou d'exemple est refusée **avant** d'aller chez Google, avec un message clair.
- Le retour de Google se termine **dans la fenêtre AxiorHub** (voir « Le retour de Google » plus bas).

Sur un serveur du cabinet, rien ne change : l'adresse de retour reste une adresse `https` déclarée à l'identique chez Google.

## Créer le client chez Google (une seule fois)

Les intitulés ci-dessous sont ceux déjà cités dans AxiorHub (carte « Compte Google (Gmail) », relevés le 15/11/2026 dans la
documentation de Google). Le type « Application de bureau » n'a **pas** été vérifié dans la console Google pour ce lot : si l'intitulé
diffère, choisissez le type prévu pour une application installée sur un ordinateur.

1. Ouvrez la console Google Cloud (https://console.cloud.google.com/) avec le compte Google qui servira. Créez un projet (par exemple
   « AxiorHub Pilote »).
2. API et services › Bibliothèque : activez « Google Calendar API » pour l'agenda, « Gmail API » pour Gmail.
3. Google Auth Platform › Branding : nom de l'application, adresse d'assistance. Audience : « Interne » si le cabinet a Google Workspace
   (seuls les comptes du domaine) ; sinon « Externe », et ajoutez votre adresse comme utilisateur de test.
4. Data Access : seulement les autorisations utiles (agenda : `calendar.events` et la liste des agendas ; Gmail : `gmail.readonly` et
   `gmail.compose`). Jamais `gmail.modify`, `gmail.send` ni `mail.google.com`.
5. Google Auth Platform › Clients › « Create client » :
   - **le plus simple** : type « Application de bureau ». Il n'y a aucune adresse à déclarer ;
   - **ou** type « Web application » : dans « Authorized redirect URIs », « Add URI », collez **exactement** l'adresse affichée par
     AxiorHub (bouton « Copier »). Un caractère de différence (port, `localhost` au lieu de `127.0.0.1`, barre finale) donne l'erreur 400.
6. Google affiche l'identifiant du client (…apps.googleusercontent.com) et son secret. Gardez-les pour l'étape suivante.

Bon à savoir (Google) : en audience « Externe » non validée, Google affiche un avertissement « application non validée » et limite
les comptes de test ; les autorisations Gmail sont « restreintes » et une ouverture large demande une validation de Google.

## Coller le client dans AxiorHub

- **Gmail** : Paramètres › Connexions, réglages « Client OAuth Google » et « Secret du client OAuth Google ». Laissez « URI de retour
  OAuth » vide. Cabinet sous Google Workspace avec audience « Interne » : la carte « Compte Google (Gmail) », méthode 2, fait la même
  chose (gardez l'adresse locale proposée).
- **Google Agenda** : Paramètres › Connexions, réglages « Client OAuth Google Calendar » et « Secret OAuth Google Calendar ». Laissez
  « URI de retour OAuth » vide.

Le secret est rangé dans le coffre local et n'est plus jamais affiché.

## Le retour de Google

- **Windows** : la page Google s'ouvre dans la fenêtre AxiorHub. Après votre accord, une page « Réponse de Google reçue » s'affiche :
  cliquez « Terminer la connexion ».
- **Ubuntu** : la page Google s'ouvre dans votre navigateur habituel. Après votre accord, ce navigateur affiche « Réponse de Google
  reçue » : fermez-le et revenez à la fenêtre AxiorHub ; la connexion s'y termine seule en quelques secondes (« En attente de la
  réponse de Google… » puis la carte indique la connexion).

Pourquoi : par sécurité, la session de la fenêtre AxiorHub ne voyage pas avec Google. AxiorHub garde seulement la réponse de Google
quelques minutes, puis la fenêtre AxiorHub, avec votre session, termine la connexion. Une réponse arrivée d'ailleurs, rejouée, ou plus
vieille que dix minutes est refusée.

## Si ça ne marche pas

- « Erreur 400 : redirect_uri_mismatch » chez Google : l'adresse déclarée chez Google n'est pas celle affichée par AxiorHub. Recopiez-la
  avec « Copier », ou utilisez un client « Application de bureau ».
- « L'adresse de retour est encore celle de l'exemple » : videz le réglage « URI de retour OAuth » (poste) ou indiquez l'adresse publique
  du serveur.
- « Google n'a pas encore répondu pour cette fenêtre » : recommencez « Connecter » depuis la même fenêtre AxiorHub.
- Le port de l'interface a changé (lancement avec un autre port) : l'adresse calculée change aussi. Avec un client « Web application »,
  déclarez la nouvelle adresse chez Google ; avec un client « Application de bureau », rien à faire.

## Ce qui n'a pas été essayé

Ce lot n'a pas été essayé avec un vrai compte Google (il faut le compte du cabinet). Les essais automatiques utilisent un faux Google.
À vérifier lors du premier essai réel : acceptation par Google d'un client « Application de bureau » avec cette adresse (port et chemin),
et le parcours de retour décrit ci-dessus sous Windows et sous Ubuntu.
